"""Re-run the paper ablation with the current code, replaying unchanged LLM calls.

Actions
  plan     Offline. Rebuild every report in a scratch tree and count, per step, condition
           and replicate, the calls that would replay an original response and the calls
           that would reach the model. No model is called.
  run      Execute reports in this process (no subprocess per agent), resumable through
           per-report status files. A call whose request hash equals the original logged
           call of the same step, entity, condition and replicate receives the original
           response; every other call goes to the configured transport.
           --replay-from adds the responses of earlier re-run workspaces to the replay
           store under the same keys; the original workspace takes precedence.
  collect  Assemble final_reports/, strategy_decisions/ and manifest.json.

Deterministic steps (share information per --share-info, news selection, random sample,
context exports, condition inputs, financial facts, market summary and valuation) are
recomputed with the current code from the frozen collected data. Nothing is collected
from the network. Model names stay logical (gpt-5.4, gpt-5.6-luna).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import logging
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Iterable, Iterator
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ONE_TEAM_RUNTIME_DIR = SRC / "Agent_Team" / "Unified_Agent" / "runtime"
for _entry in (str(ROOT), str(SRC)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

COMPANIES = ("현대건설", "두산", "BGF리테일", "아모레퍼시픽", "SK바이오팜")
CONDITIONS = ("full", "random_news", "no_subdata", "no_peer", "one_team")
REPLICATES = ("r01", "r02", "r03")
SEPARATE_CONDITIONS = ("full", "random_news", "no_subdata")
SUMMARY_CONDITIONS = ("full", "random_news")
ANALYSIS_MODEL = "gpt-5.4"
SUMMARY_MODEL = "gpt-5.6-luna"
RANDOM_SEED = 20251031
TIMEOUT_SECONDS = 300
ONE_TEAM_RUN_ID = "one_team_gpt54_r01"
LLM_OUTPUT_NAMES = frozenset({"final_report.json", "final_report.md", "actual_llm_request.json", "news_agent_handoff.json"})
SHARE_INFORMATION_FILES = ("dart_main.json", "dart_master.json", "dart_lightweight.json", "dart_2y_handoff.json")
MODELS_BY_STAGE = {
    "monthly_summary": SUMMARY_MODEL, "news": ANALYSIS_MODEL, "financial": ANALYSIS_MODEL,
    "market": ANALYSIS_MODEL, "unified": ANALYSIS_MODEL, "competitor": ANALYSIS_MODEL,
    "strategy": ANALYSIS_MODEL, "writer": ANALYSIS_MODEL,
}
# Logical calls per stage and the step label recorded by the call sites.
STAGE_CALLS = {"monthly_summaries": 12, "news": 1, "financial": 1, "market": 1, "unified": 1,
               "peer_analysis": 1, "strategy": 1, "writer": 1}
STAGE_STEP = {"monthly_summaries": "news:period_summary", "news": "news:analysis",
              "financial": "financial:analyst_report", "market": "yfinance:analyst_report",
              "unified": "unified:domain_agent", "peer_analysis": "competitor:comparison_analysis",
              "strategy": "strategy", "writer": "writer:html_report"}
STEP_GROUP = {"news:period_summary": "monthly_summary", "news:analysis": "news",
              "financial:analyst_report": "financial", "yfinance:analyst_report": "market",
              "unified:domain_agent": "unified", "competitor:comparison_analysis": "competitor"}
CHAT_STEPS = frozenset({"news:period_summary", "competitor:comparison_analysis", "writer:html_report"})
FINAL_STAGES = ("peer_dataset", "peer_analysis", "strategy", "chart_catalog", "writer", "charts", "render")
SUB_ANALYSIS_GROUPS = ("news", "financial", "market", "unified", "competitor")
ORIGINAL_LABEL = "original"
# Report stage that performs the calls of each step group.
GROUP_STAGE = {"monthly_summary": "monthly_summaries", "news": "news", "financial": "financial", "market": "market",
               "unified": "unified", "competitor": "peer_analysis", "strategy": "strategy", "writer": "writer"}
# Saved outputs compared after a fully replayed stage: (scope, files relative to the entity or report root).
FIDELITY_FILES = {
    "monthly_summaries": ("entity", ("News/{day}/context_exports/month/llm_period_summaries.json",)),
    "news": ("entity", ("News/{day}/output/news_agent_handoff.json",)),
    "financial": ("entity", ("Financial/{day}/final_report.json",)),
    "market": ("entity", ("Y_Finance/{day}/final_report.json",)),
    "unified": ("entity", ("runs/{day}/unified_domain_team/unified_report.json",)),
    "peer_analysis": ("final", ("Competitor/{day}/peer_comparison_output.json",
                                "Competitor/{day}/peer_comparison_report.json")),
    "strategy": ("final", ("Strategy/{day}/strategy_decision_output.json", "Strategy/{day}/strategy_report.json")),
    "writer": ("final", ("Writer/{day}/llm_writer_output.json", "Writer/{day}/writer_report_payload.json")),
}
# Top-level fields that are not part of the replayed response: run bookkeeping, creation time, and
# report_charts, which the later charts stage adds to the saved Writer payload.
FIDELITY_IGNORED = {"llm_writer_output.json": frozenset({"cache_status", "run_status"}),
                    "writer_report_payload.json": frozenset({"report_charts"}),
                    "strategy_report.json": frozenset({"created_at"})}


def step_group(step: str) -> str:
    """Return the reporting group of a call-site step label."""
    if step in STEP_GROUP:
        return STEP_GROUP[step]
    return step.split(":", 1)[0]


# ---------------------------------------------------------------------------
# Small file helpers


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: Path, value: Any) -> Path:
    """Write indented JSON atomically (same formatting as the original runners)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def sha_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def request_sha256(payload: Any) -> str:
    """Hash a request exactly like shared.llm_clients.measure_request."""
    from shared.llm_clients import compact_json
    return hashlib.sha256(compact_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Replay layer at the LLM transport boundary


class PlannedNewCall(BaseException):
    """Plan mode: a call that would reach the model. BaseException so agent fallbacks cannot swallow it."""


class FakeTransportStop(BaseException):
    """Offline fake transport: no canned response is available for a non-replayed call."""


class CaptureStop(BaseException):
    """Request capture: stop right before transport."""


@dataclass(frozen=True)
class CallContext:
    replicate: str
    condition: str
    target_company: str
    role: str
    company_name: str


@dataclass
class ReplayEntry:
    step: str
    sha256: str
    kind: str
    source: str
    usage: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    label: str = ORIGINAL_LABEL


class ReplayUsage:
    """Usage object with the attribute and model_dump interface of the SDK types."""

    def __init__(self, values: dict[str, Any] | None) -> None:
        self._values = copy.deepcopy(values or {})
        for key, value in self._values.items():
            setattr(self, key, value)

    def model_dump(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)


def responses_usage(normalized: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a normalized usage row back to the Responses API usage shape."""
    values = normalized or {}
    return {
        "input_tokens": int(values.get("input_tokens", 0)),
        "input_tokens_details": {"cached_tokens": int(values.get("cached_input_tokens", 0)),
                                 "cache_write_tokens": int(values.get("cache_write_input_tokens", 0))},
        "output_tokens": int(values.get("output_tokens", 0)),
        "output_tokens_details": {"reasoning_tokens": int(values.get("reasoning_tokens", 0))},
        "total_tokens": int(values.get("total_tokens", 0)),
    }


def chat_usage(normalized: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a normalized usage row back to the Chat Completions usage shape."""
    values = normalized or {}
    return {"prompt_tokens": int(values.get("input_tokens", 0)),
            "completion_tokens": int(values.get("output_tokens", 0)),
            "total_tokens": int(values.get("total_tokens", 0))}


def response_schema(request: dict[str, Any]) -> dict[str, Any]:
    text = request.get("text") or {}
    if "format" in text:
        return text["format"]["schema"]
    return request["response_format"]["json_schema"]["schema"]


def resolve_ref(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow local JSON-schema references such as #/$defs/name."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node:
        pointer = node["$ref"]
        if not pointer.startswith("#/") or seen > 20:
            raise ValueError(f"Unsupported schema reference: {pointer}")
        node = root
        for part in pointer[2:].split("/"):
            node = node[part]
        seen += 1
    return node


def _ordered(item: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in properties if key in item}


def group_context_issues(items: list[dict[str, Any]], by_domain_schema: dict[str, Any],
                         root: dict[str, Any]) -> dict[str, Any]:
    """Invert flatten_context_issues: regroup normalized issues by domain in schema key order."""
    result: dict[str, Any] = {}
    for domain, schema in resolve_ref(by_domain_schema, root)["properties"].items():
        schema = resolve_ref(schema, root)
        matching = [item for item in items if item.get("source_domain") == domain]
        if schema.get("type") == "array":
            properties = resolve_ref(schema["items"], root)["properties"]
            result[domain] = [_ordered(item, properties) for item in matching]
        else:
            if len(matching) != 1:
                raise ValueError(f"Expected one context issue for {domain}")
            result[domain] = _ordered(matching[0], schema["properties"])
    unknown = {item.get("source_domain") for item in items} - set(result)
    if unknown:
        raise ValueError(f"Saved context issues use domains outside the schema: {sorted(unknown)}")
    return result


def rebuild_financial_output(saved: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Recover the model JSON from a saved apply_financial_analysis report."""
    properties = schema["properties"]
    main = saved["main_view"]
    rebuilt = {
        "main_view": {"summary": main["summary"], "direction": main["direction"],
                      "primary_evidence_ids": main["analysis_evidence_ids"],
                      "context_ids": main.get("context_ids") or []},
        "dimension_assessments": {
            dimension: {key: saved["financial_statement_view"][dimension][key]
                        for key in ("stance", "reasoning", "primary_evidence_ids")}
            for dimension in resolve_ref(properties["dimension_assessments"], schema)["properties"]
        },
        "secondary_context_assessment_by_domain": group_context_issues(
            saved.get("secondary_context_assessment") or [], properties["secondary_context_assessment_by_domain"], schema),
    }
    missing = set(properties) - set(rebuilt)
    if missing:
        raise ValueError(f"Financial schema fields cannot be recovered: {sorted(missing)}")
    return {key: rebuilt[key] for key in properties}


def rebuild_market_output(saved: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Recover the model JSON from a saved market report (fields added after the call are dropped)."""
    rebuilt = {}
    for key, value_schema in schema["properties"].items():
        if key == "secondary_context_assessment_by_domain":
            rebuilt[key] = group_context_issues(saved.get("secondary_context_assessment") or [], value_schema, schema)
        elif key in saved:
            rebuilt[key] = saved[key]
        else:
            raise ValueError(f"Saved market report lacks schema field: {key}")
    return rebuilt


def rebuild_news_output(saved: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Recover the model JSON from a saved News handoff output.

    Validation rewrites each context issue without its anchor fields; the merged ID lists
    start with the anchor, so the anchor is the first ID. Fields added after the call
    (evidence_map_path, context_policy_version, secondary_context) are dropped.
    """
    rebuilt = {}
    for key, value_schema in schema["properties"].items():
        if key not in saved:
            raise ValueError(f"Saved News output lacks schema field: {key}")
        value = saved[key]
        if key == "secondary_context_assessment":
            properties = resolve_ref(resolve_ref(value_schema, schema)["items"], schema)["properties"]
            issues = []
            for item in value:
                issue = dict(item)
                for anchor, ids in (("primary_anchor_evidence_id", "primary_evidence_ids"),
                                    ("secondary_anchor_evidence_id", "secondary_evidence_ids")):
                    if anchor in properties and anchor not in issue:
                        issue[anchor] = (issue.get(ids) or [""])[0]
                issues.append(_ordered(issue, properties))
            value = issues
        rebuilt[key] = value
    return rebuilt


class ReplayStore:
    """Earlier responses keyed by (replicate, condition, target, role, step) and request hash.

    The first source that indexes a key and hash keeps it: the original workspace is
    indexed first, then each --replay-from workspace in the given order.
    """

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str, str, str], dict[str, ReplayEntry]] = {}
        self.canned: dict[tuple[str, str, str, str], ReplayEntry] = {}
        self.notes: list[str] = []

    def add(self, key: tuple[str, str, str, str, str], entry: ReplayEntry) -> bool:
        bucket = self.entries.setdefault(key, {})
        if entry.sha256 in bucket:
            return False
        bucket[entry.sha256] = entry
        return True

    def has(self, key: tuple[str, str, str, str, str], sha: str) -> bool:
        return sha in self.entries.get(key, {})

    def count_by_source(self) -> dict[str, dict[str, int]]:
        """Indexed responses per source label and step group."""
        result: dict[str, dict[str, int]] = {}
        for key, bucket in self.entries.items():
            group = step_group(key[4])
            for entry in bucket.values():
                counts = result.setdefault(entry.label, {})
                counts[group] = counts.get(group, 0) + 1
        return {label: dict(sorted(counts.items())) for label, counts in result.items()}

    def lookup(self, context: CallContext | None, step: str, sha: str) -> ReplayEntry | None:
        if context is None:
            return None
        key = (context.replicate, context.condition, context.target_company, context.role, step)
        return self.entries.get(key, {}).get(sha)

    def canned_for(self, context: CallContext | None, step: str) -> ReplayEntry | None:
        if context is None:
            return None
        group = step_group(step)
        return self.canned.get((context.replicate, context.condition, context.target_company, group))

    def count(self) -> int:
        return sum(len(value) for value in self.entries.values())

    def response(self, entry: ReplayEntry, step: str, request: dict[str, Any]) -> Any:
        content, usage, response_id = self._content(entry, request)
        if step.startswith("strategy:"):
            return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "usage": usage or {}, "id": response_id}
        if step in CHAT_STEPS:
            message = SimpleNamespace(role="assistant", content=content, refusal=None)
            return SimpleNamespace(id=response_id, model=request.get("model"),
                                   choices=[SimpleNamespace(index=0, message=message, finish_reason="stop")],
                                   usage=ReplayUsage(usage))
        return SimpleNamespace(id=response_id, model=request.get("model"), status="completed",
                               output_text=content, output=[], incomplete_details=None, usage=ReplayUsage(usage))

    def _content(self, entry: ReplayEntry, request: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str]:
        response_id = f"replay-{entry.sha256[:16]}"
        if entry.kind == "inline":
            return entry.extra["content"], entry.usage, response_id
        source = read(entry.source)
        if entry.kind == "summary":
            result = source["period_results"][entry.extra["index"]]
            return json.dumps({"periods": [result["output"]]}, ensure_ascii=False), result.get("usage"), response_id
        if entry.kind == "news":
            output = rebuild_news_output(source["output"], response_schema(request))
            return json.dumps(output, ensure_ascii=False), source.get("usage"), response_id
        if entry.kind == "financial":
            output = rebuild_financial_output(source, response_schema(request))
            return json.dumps(output, ensure_ascii=False), responses_usage(entry.usage), response_id
        if entry.kind == "market":
            output = rebuild_market_output(source, response_schema(request))
            return json.dumps(output, ensure_ascii=False), responses_usage(entry.usage), response_id
        if entry.kind == "unified":
            return (json.dumps(source["output"], ensure_ascii=False), responses_usage(source.get("usage")),
                    source.get("response_id") or response_id)
        # Earlier re-runs saved these model JSON objects exactly as parsed from the response.
        if entry.kind == "competitor":
            return json.dumps(source, ensure_ascii=False), chat_usage(entry.usage), response_id
        if entry.kind == "strategy":
            return json.dumps(source["decision_output"], ensure_ascii=False), chat_usage(entry.usage), response_id
        if entry.kind == "writer":
            return json.dumps(source["raw_payload"], ensure_ascii=False), source.get("usage") or {}, response_id
        if entry.kind == "canned_competitor":
            return json.dumps(source, ensure_ascii=False), {}, "fake-canned"
        if entry.kind == "canned_strategy":
            # Offline smoke only: generation-time artifacts carried a version suffix the current contract dropped.
            decision = dict(source["decision_output"])
            version = str(decision.get("decision_version") or "")
            if "_v" in version and version.rsplit("_v", 1)[1].isdigit():
                decision["decision_version"] = version.rsplit("_v", 1)[0]
            return json.dumps(decision, ensure_ascii=False), {}, "fake-canned"
        if entry.kind == "canned_writer":
            return json.dumps(source["raw_payload"], ensure_ascii=False), {}, "fake-canned"
        raise ValueError(f"Unknown replay entry kind: {entry.kind}")


class ReplayLayer:
    """Decide per call between replaying an original response and the configured transport.

    mode: "real" sends misses to the original transport; "plan" raises PlannedNewCall;
    "fake-stop" raises FakeTransportStop; "fake-canned" answers misses with the original
    response of the same step (offline smoke runs only).
    """

    def __init__(self, store: ReplayStore, *, mode: str = "real", call_log: Path | None = None) -> None:
        if mode not in {"real", "plan", "fake-stop", "fake-canned"}:
            raise ValueError(f"Unknown transport mode: {mode}")
        self.store = store
        self.mode = mode
        self.call_log = call_log
        self.context: CallContext | None = None
        self.capturing: list[dict[str, Any]] | None = None
        self.counters = {"replayed": 0, "new": 0}
        self.by_source: dict[str, int] = {}

    @contextlib.contextmanager
    def scope(self, context: CallContext) -> Iterator[None]:
        previous = self.context
        self.context = context
        try:
            yield
        finally:
            self.context = previous

    @contextlib.contextmanager
    def capture(self) -> Iterator[list[dict[str, Any]]]:
        previous = self.capturing
        self.capturing = []
        try:
            yield self.capturing
        finally:
            self.capturing = previous

    def record(self, row: dict[str, Any]) -> None:
        if self.call_log is not None:
            append_jsonl(self.call_log, row)

    def execute(self, original: Callable[..., Any], call: Callable[[], Any], *, request_payload: Any,
                model: str, step: str, **kwargs: Any) -> Any:
        sha = request_sha256(request_payload)
        if self.capturing is not None:
            self.capturing.append({"step": step, "model": model, "sha256": sha,
                                   "request": copy.deepcopy(request_payload)})
            raise CaptureStop(step)
        context = self.context
        entry = self.store.lookup(context, step, sha)
        row = {"recorded_at": now(), "transport": self.mode,
               **({"replicate": context.replicate, "condition": context.condition,
                   "target_company": context.target_company, "role": context.role,
                   "company_name": context.company_name} if context else {}),
               "step": step, "model": model, "request_sha256": sha, "replayed": entry is not None}
        if entry is not None:
            response = self.store.response(entry, step, request_payload)
            self.counters["replayed"] += 1
            self.by_source[entry.label] = self.by_source.get(entry.label, 0) + 1
            self.record({**row, "outcome": "replayed", "source": entry.source, "source_kind": entry.kind,
                         "source_label": entry.label})
            return response
        self.counters["new"] += 1
        if self.mode == "plan":
            self.record({**row, "outcome": "planned_new"})
            raise PlannedNewCall(step)
        if self.mode in {"fake-stop", "fake-canned"}:
            canned = self.store.canned_for(context, step) if self.mode == "fake-canned" else None
            if canned is None:
                self.record({**row, "outcome": "fake_stop"})
                raise FakeTransportStop(step)
            self.record({**row, "outcome": "fake_canned", "source": canned.source})
            return self.store.response(canned, step, request_payload)
        self.record({**row, "outcome": "sent"})
        return original(call, request_payload=request_payload, model=model, step=step, **kwargs)


_ACTIVE: dict[str, ReplayLayer | None] = {"layer": None}


def _wrap_execute(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_ablation_replay", False):
        return original

    def execute_with_replay(call: Callable[[], Any], *, request_payload: Any, model: str, step: str,
                            **kwargs: Any) -> Any:
        layer = _ACTIVE["layer"]
        if layer is None:
            return original(call, request_payload=request_payload, model=model, step=step, **kwargs)
        return layer.execute(original, call, request_payload=request_payload, model=model, step=step, **kwargs)

    execute_with_replay._ablation_replay = True  # type: ignore[attr-defined]
    execute_with_replay._original = original  # type: ignore[attr-defined]
    return execute_with_replay


def _patch_llm_clients(module: ModuleType) -> None:
    original = module.execute_with_telemetry
    wrapped = _wrap_execute(original)
    module.execute_with_telemetry = wrapped
    # Modules imported before installation keep a direct reference; repoint them too.
    for other in list(sys.modules.values()):
        try:
            if getattr(other, "execute_with_telemetry", None) is original:
                other.execute_with_telemetry = wrapped
        except Exception:  # pragma: no cover - exotic lazy modules
            continue


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader, hook: Callable[[ModuleType], None]) -> None:
        self.wrapped = wrapped
        self.hook = hook

    def create_module(self, spec: Any) -> ModuleType | None:
        creator = getattr(self.wrapped, "create_module", None)
        return creator(spec) if creator is not None else None

    def exec_module(self, module: ModuleType) -> None:
        self.wrapped.exec_module(module)
        self.hook(module)


class _ReplayFinder(importlib.abc.MetaPathFinder):
    """Patch shared.llm_clients whenever it is (re)imported, before any call site binds the name."""

    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None) -> Any:
        if fullname != "shared.llm_clients":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _PatchLoader(spec.loader, _patch_llm_clients)
        return spec


def install_replay(layer: ReplayLayer | None) -> None:
    """Activate a replay layer for this process (None deactivates it but keeps the hook)."""
    if not any(isinstance(finder, _ReplayFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _ReplayFinder())
    module = sys.modules.get("shared.llm_clients")
    if module is not None:
        _patch_llm_clients(module)
    _ACTIVE["layer"] = layer


def uninstall_replay() -> None:
    """Remove the hook and restore the original execute_with_telemetry everywhere."""
    sys.meta_path[:] = [finder for finder in sys.meta_path if not isinstance(finder, _ReplayFinder)]
    for module in list(sys.modules.values()):
        try:
            current = getattr(module, "execute_with_telemetry", None)
            if getattr(current, "_ablation_replay", False):
                module.execute_with_telemetry = current._original
        except Exception:  # pragma: no cover - exotic lazy modules
            continue
    _ACTIVE["layer"] = None


# ---------------------------------------------------------------------------
# Module modes: one-team downstream stages need the integrated-report import hook


_MODULE_MODE: dict[str, Any] = {"one_team": False, "finder": None}
_PURGE_PREFIXES = ("Agent_Team", "shared", "orchestration", "ablation_suite", "integrated_handoff", "model_policy")


def _purge_modules() -> None:
    for name in list(sys.modules):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in _PURGE_PREFIXES):
            del sys.modules[name]


def set_module_mode(one_team: bool) -> None:
    """Switch between the standard modules and the one-team runtime hook (re-imports agent modules)."""
    if _MODULE_MODE["one_team"] == one_team:
        return
    _purge_modules()
    finder = _MODULE_MODE["finder"]
    if finder is not None and finder in sys.meta_path:
        sys.meta_path.remove(finder)
    if one_team:
        if str(ONE_TEAM_RUNTIME_DIR) not in sys.path:
            sys.path.append(str(ONE_TEAM_RUNTIME_DIR))
        previous = os.environ.pop("ONE_TEAM_RUNTIME", None)
        spec = importlib.util.spec_from_file_location("ablation_one_team_runtime_hook",
                                                      ONE_TEAM_RUNTIME_DIR / "sitecustomize.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)  # type: ignore[union-attr]
        if previous is not None:
            os.environ["ONE_TEAM_RUNTIME"] = previous
        finder = hook._PatchFinder()
        sys.meta_path.insert(0, finder)
        os.environ.update(ONE_TEAM_RUNTIME="1", ONE_TEAM_SINGLE_REPORT="1")
        _MODULE_MODE["finder"] = finder
    else:
        if str(ONE_TEAM_RUNTIME_DIR) in sys.path:
            sys.path.remove(str(ONE_TEAM_RUNTIME_DIR))
        for key in ("ONE_TEAM_RUNTIME", "ONE_TEAM_SINGLE_REPORT"):
            os.environ.pop(key, None)
        _MODULE_MODE["finder"] = None
    _MODULE_MODE["one_team"] = one_team


def agents() -> SimpleNamespace:
    """Import the agent modules of the current module mode."""
    import ablation_suite.config as sampling_config
    sampling_config.FINAL_SRC = SRC
    from ablation_suite import annual_random
    from ablation_suite.utils import stable_seed
    from orchestration import full_report_pipeline as flow
    from orchestration.ablation import config_from_args
    from orchestration.company_resolver import CompanyIdentity
    from orchestration.config import build_run_key
    from Agent_Team.News_Agent import context_export, analysis_agent
    from Agent_Team.News_Agent.collectors.candidate_preparation import require_common_candidate_pool
    from Agent_Team.News_Agent.collectors.report_snippets import require_prepared_summary_snippets
    from Agent_Team.Financial_Agent.langgraph_flow import build_financial_analyst_output
    from Agent_Team.Financial_Agent import financial_analysis_agent
    from Agent_Team.Financial_Agent.share_information_extractor import extract_share_information
    from Agent_Team.Financial_Agent.models import Filing, TargetReport
    from Agent_Team.YFinance_Agent import reporting
    from Agent_Team.Unified_Agent import report as unified_report
    from Agent_Team.Unified_Agent.inputs import prepare_entity
    from shared.domain_llm import call_domain_response
    from shared.llm_clients import normalize_usage
    from shared.news_articles import build_article_packet
    return SimpleNamespace(
        annual_random=annual_random, stable_seed=stable_seed, flow=flow, config_from_args=config_from_args,
        CompanyIdentity=CompanyIdentity, build_run_key=build_run_key, context_export=context_export,
        analysis_agent=analysis_agent, require_common_candidate_pool=require_common_candidate_pool,
        require_prepared_summary_snippets=require_prepared_summary_snippets,
        build_financial_analyst_output=build_financial_analyst_output, financial_analysis_agent=financial_analysis_agent,
        extract_share_information=extract_share_information, Filing=Filing, TargetReport=TargetReport,
        reporting=reporting, unified_report=unified_report, prepare_entity=prepare_entity,
        call_domain_response=call_domain_response, normalize_usage=normalize_usage,
        build_article_packet=build_article_packet)


# ---------------------------------------------------------------------------
# Frozen source workspace and output layout


def report_relpath(replicate: str, condition: str) -> Path:
    """Relative report root of one condition and replicate (same layout as the original run)."""
    if replicate == "r01":
        if condition == "one_team":
            return Path("reports/one_team") / ONE_TEAM_RUN_ID
        return Path("reports") / condition / "replicate_01"
    return Path("reports/repeated_standard_5companies") / f"replicate_{replicate[1:]}" / condition


def execution_id(replicate: str, condition: str, stock_code: str) -> str:
    if replicate == "r01":
        return f"{ONE_TEAM_RUN_ID}_{stock_code}" if condition == "one_team" else f"h2_2025_{condition}_{stock_code}_r01"
    return f"repeat_standard_{condition}_{stock_code}_{replicate}"


@dataclass
class Entity:
    role: str
    company_name: str
    target_company: str
    ticker: str
    corp_code: str
    stock_code: str
    selected_date: str
    company_config: Path
    collected_root: Path
    news_report: Path

    @property
    def run_key(self) -> str:
        return f"{self.company_name}_{self.selected_date}"


@dataclass
class Company:
    name: str
    selected_date: str
    entities: list[Entity]

    @property
    def target(self) -> Entity:
        return self.entities[0]

    @property
    def peer(self) -> Entity:
        return self.entities[1]


class Source:
    """Read-only view of the frozen original workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.collection = read(self.root / "run_config/collection_manifest.json")
        self.recorded_root = str(self.collection.get("workspace") or self.root).rstrip("/")
        self.status = read(self.root / "status/data_collection_status.json")

    def local(self, value: str | Path) -> Path:
        text = str(value)
        if text.startswith(self.recorded_root):
            return Path(str(self.root) + text[len(self.recorded_root):])
        return Path(text)

    def companies(self) -> dict[str, Company]:
        result = {}
        for row in self.collection["companies"]:
            name, day = row["target"]["company_name"], row["report_date"]
            entities = []
            for role in ("target", "peer"):
                spec = row[role]
                state = self.status["entities"][f"{name}:{role}:{spec['company_name']}"]
                config = self.root / "run_config/companies" / name / f"{role}_{spec['company_name']}.json"
                entities.append(Entity(
                    role=role, company_name=spec["company_name"], target_company=name, ticker=spec["ticker"],
                    corp_code=spec["corp_code"], stock_code=spec["stock_code"], selected_date=day,
                    company_config=config, collected_root=self.local(state["output_root"]) / spec["company_name"],
                    news_report=self.local(state["steps"]["news_collect"]["outputs"][0])))
            result[name] = Company(name, day, entities)
        return result

    def report_dir(self, replicate: str, condition: str, company: str) -> Path:
        return self.root / report_relpath(replicate, condition) / company

    def usage_files(self) -> list[Path]:
        return [self.root / "status/report_generation_usage.jsonl",
                self.root / "status/one_team" / f"{ONE_TEAM_RUN_ID}_usage.jsonl",
                self.root / "reports/repeated_standard_5companies/llm_usage.jsonl"]


def entity_dir(report_root: Path, entity: Entity) -> Path:
    """Company folder of an entity inside one report (peers live under 비교기업/)."""
    if entity.role == "target":
        return report_root
    return report_root / "비교기업" / entity.company_name


class Layout:
    """Output workspace layout, mirroring the original reports/ and prepared_inputs/ trees."""

    def __init__(self, out: Path) -> None:
        self.out = out.resolve()

    def report_root(self, replicate: str, condition: str) -> Path:
        return self.out / report_relpath(replicate, condition)

    def prepared(self, company: str, condition: str, role: str) -> Path:
        return self.out / "prepared_inputs/replicate_01" / company / condition / role

    def prepare_status(self, company: str) -> Path:
        return self.out / "prepared_inputs/replicate_01" / company / "prepare_status.json"

    def financial_dir(self, source: Source, entity: Entity) -> Path:
        relative = entity.collected_root.relative_to(source.root)
        return self.out / relative / "Financial" / entity.selected_date

    def status_file(self, job: "Job") -> Path:
        return self.out / "status/reports" / job.replicate / job.condition / f"{job.company}.json"

    @property
    def usage_manifest(self) -> Path:
        return self.out / "status/llm_usage.jsonl"

    @property
    def call_log(self) -> Path:
        return self.out / "status/llm_calls.jsonl"

    @property
    def fidelity_log(self) -> Path:
        return self.out / "status/replay_fidelity.jsonl"

    @property
    def settings(self) -> Path:
        return self.out / "run_settings.json"


@dataclass(frozen=True)
class Job:
    replicate: str
    condition: str
    company: str

    @property
    def key(self) -> str:
        return f"{self.replicate}/{self.condition}/{self.company}"

    @property
    def level(self) -> int:
        if self.replicate == "r01" and self.condition in SEPARATE_CONDITIONS:
            return 0
        if self.replicate != "r01" and self.condition == "no_peer":
            return 2
        return 1

    def dependencies(self) -> list[tuple["Job", tuple[str, ...]]]:
        """Jobs and stages that must be finished before this job can start."""
        if self.condition == "no_peer":
            return [(Job(self.replicate, "full", self.company), ("target_news", "target_financial", "target_market"))]
        if self.condition == "one_team":
            needed = tuple(f"{role}_{kind}" for role in ("target", "peer")
                           for kind in ("inputs", "monthly_summaries", "financial"))
            return [(Job("r01", "full", self.company), needed)]
        if self.replicate != "r01":
            needed = tuple(f"{role}_inputs" for role in ("target", "peer"))
            if self.condition in SUMMARY_CONDITIONS:
                needed += tuple(f"{role}_monthly_summaries" for role in ("target", "peer"))
            return [(Job("r01", self.condition, self.company), needed)]
        return []


def stage_closure(job: Job, stages: Iterable[str]) -> frozenset[str]:
    """Add the same-report stages that the given entity stages read (inputs, monthly summaries)."""
    result = set(stages)
    for name in list(result):
        kind = stage_kind(name)
        if kind == name or kind == "inputs":
            continue
        role = name.split("_", 1)[0]
        result.add(f"{role}_inputs")
        if kind in {"financial", "market"} and job.replicate == "r01" and job.condition in SUMMARY_CONDITIONS:
            result.add(f"{role}_monthly_summaries")
    return frozenset(result)


def plan_jobs(selected: list[Job], *, add_dependencies: bool = True) -> tuple[list[Job], dict[str, frozenset[str]]]:
    """Selected jobs plus the unselected jobs they depend on, which run only the needed stages.

    Returns the jobs in execution order and, for each added dependency job, its stage set.
    """
    if not add_dependencies:
        return sorted(selected, key=lambda job: job.level), {}
    chosen = {job.key for job in selected}
    jobs = with_dependencies(selected)
    needed: dict[str, set[str]] = {}
    # Dependencies always have a lower level, so dependents are resolved first.
    for job in sorted(jobs, key=lambda item: -item.level):
        for dependency, stages in job.dependencies():
            if dependency.key not in chosen:
                needed.setdefault(dependency.key, set()).update(stages)
    by_key = {job.key: job for job in jobs}
    return jobs, {key: stage_closure(by_key[key], stages) for key, stages in needed.items()}


def select_jobs(companies: Iterable[str], conditions: Iterable[str], replicates: Iterable[str]) -> list[Job]:
    return [Job(r, c, n) for r in REPLICATES if r in set(replicates)
            for c in CONDITIONS if c in set(conditions) for n in COMPANIES if n in set(companies)]


def with_dependencies(jobs: list[Job]) -> list[Job]:
    result = {job.key: job for job in jobs}
    pending = list(jobs)
    while pending:
        for dependency, _ in pending.pop().dependencies():
            if dependency.key not in result:
                result[dependency.key] = dependency
                pending.append(dependency)
    order = {job.key: index for index, job in enumerate(select_jobs(COMPANIES, CONDITIONS, REPLICATES))}
    return sorted(result.values(), key=lambda job: (job.level, order[job.key]))


# ---------------------------------------------------------------------------
# Original responses


def _usage_context(row: dict[str, Any], stock_to_target: dict[str, str]) -> tuple[str, str, str] | None:
    """Return (replicate, condition, target) for an original usage row."""
    execution = row.get("execution_id", "")
    if execution.startswith(f"{ONE_TEAM_RUN_ID}_"):
        return "r01", "one_team", stock_to_target.get(execution.rsplit("_", 1)[1], "")
    for prefix in ("h2_2025_", "repeat_standard_"):
        if execution.startswith(prefix):
            body = execution[len(prefix):]
            condition, stock, replicate = body.rsplit("_", 2)
            return replicate, condition, stock_to_target.get(stock, "")
    return None


def build_replay_store(source: Source, layer: ReplayLayer, *, canned: bool = False) -> ReplayStore:
    """Index every original paper-scope call that has a recoverable response."""
    store = layer.store
    companies = source.companies()
    stock_to_target = {company.target.stock_code: name for name, company in companies.items()}
    logged: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for path in source.usage_files():
        for row in read_jsonl(path):
            context = _usage_context(row, stock_to_target)
            if context is None or row.get("status") != "ok" or context[2] not in COMPANIES:
                continue
            replicate, condition, target = context
            logged.setdefault((replicate, condition, target, row["run_role"], row["step"]), []).append(row)
    a = agents()
    for replicate in REPLICATES:
        for condition in CONDITIONS:
            for name in COMPANIES:
                company = companies[name]
                day = company.selected_date
                original_root = source.report_dir(replicate, condition, name)
                if not original_root.is_dir():
                    store.notes.append(f"missing original report: {replicate}/{condition}/{name}")
                    continue
                for entity in company.entities:
                    base = entity_dir(original_root, entity)
                    if condition in SEPARATE_CONDITIONS:
                        files = {"news:analysis": ("news", base / "News" / day / "output/news_agent_handoff.json"),
                                 "financial:analyst_report": ("financial", base / "Financial" / day / "final_report.json"),
                                 "yfinance:analyst_report": ("market", base / "Y_Finance" / day / "final_report.json")}
                    elif condition == "one_team":
                        files = {"unified:domain_agent": ("unified", base / "runs" / day / "unified_domain_team/unified_response.json")}
                    else:
                        files = {}
                    for step, (kind, path) in files.items():
                        rows = logged.get((replicate, condition, name, entity.role, step), [])
                        if not rows or not path.is_file():
                            store.notes.append(f"no original response: {replicate}/{condition}/{name}/{entity.role}/{step}")
                            continue
                        if len(rows) > 1:
                            store.notes.append(f"{len(rows)} successful logged calls, last one used: "
                                               f"{replicate}/{condition}/{name}/{entity.role}/{step}")
                        row = rows[-1]
                        store.add((replicate, condition, name, entity.role, step),
                                  ReplayEntry(step, row["request"]["request_sha256"], kind, str(path), row.get("usage")))
                    if replicate == "r01" and condition in SUMMARY_CONDITIONS:
                        _index_summaries(store, layer, a, base / "News" / day / "context_exports/month",
                                         (replicate, condition, name, entity.role),
                                         {r["request"]["request_sha256"] for r in
                                          logged.get((replicate, condition, name, entity.role, "news:period_summary"), [])})
                if canned:
                    final = original_root
                    candidates = {
                        "competitor": ("canned_competitor", final / "Competitor" / day / "peer_comparison_output.json"),
                        "writer": ("canned_writer", final / "Writer" / day / "llm_writer_output.json"),
                    }
                    attempts = sorted((final / "Strategy" / day / "strategy_response_attempts").glob("*.json"))
                    if attempts:
                        candidates["strategy"] = ("canned_strategy", attempts[-1])
                    for group, (kind, path) in candidates.items():
                        if path.is_file():
                            store.canned[(replicate, condition, name, group)] = ReplayEntry(group, "", kind, str(path))
    return store


def _index_summaries(store: ReplayStore, layer: ReplayLayer, a: SimpleNamespace, month_dir: Path,
                     key: tuple[str, str, str, str], logged_hashes: set[str], label: str = ORIGINAL_LABEL) -> None:
    """Map each saved monthly summary to the transport hash the current code builds for it."""
    summaries = month_dir / "llm_period_summaries.json"
    request_path = month_dir / "llm_summary_request.json"
    if not summaries.is_file() or not request_path.is_file():
        store.notes.append(f"no {label} monthly summaries: {'/'.join(key)}")
        return
    saved = read(summaries)
    periods = [result["period"] for result in saved["period_results"]]
    for period, monthly in a.context_export._build_period_llm_requests(read(request_path)):
        with layer.capture() as captured:
            try:
                a.context_export._call_llm_summary(None, monthly)
            except CaptureStop:
                pass
        sha = captured[0]["sha256"]
        if period not in periods or sha not in logged_hashes:
            if label == ORIGINAL_LABEL:
                store.notes.append(f"monthly summary not replayable: {'/'.join(key)}/{period}")
            continue
        store.add((*key, "news:period_summary"),
                  ReplayEntry("news:period_summary", sha, "summary", str(summaries), None,
                              {"index": periods.index(period), "period": period}, label))


# ---------------------------------------------------------------------------
# Responses of earlier re-runs (--replay-from)


def latest_strategy_attempt(directory: Path) -> Path | None:
    """Strategy response behind the saved decision: the last valid attempt with the final fingerprint."""
    cache = directory / "strategy_decision_cache.json"
    if not cache.is_file():
        return None
    fingerprint = read(cache).get("fingerprint")
    attempts = [path for path in sorted((directory / "strategy_response_attempts").glob("*.json"))
                if not path.name.endswith(".failure.json") and not path.with_suffix(".failure.json").exists()
                and read(path).get("fingerprint") == fingerprint]
    return attempts[-1] if attempts else None


def saved_response(report_root: Path, entity: Entity | None, step: str,
                   day: str) -> tuple[str, Path | None, Path | None]:
    """(kind, saved response file, saved request file) of one step in a re-run report folder."""
    base = entity_dir(report_root, entity) if entity is not None else report_root
    if step == "news:analysis":
        output = base / "News" / day / "output"
        return "news", output / "news_agent_handoff.json", output / "news_agent_llm_request.json"
    if step == "financial:analyst_report":
        return "financial", base / "Financial" / day / "final_report.json", base / "Financial" / day / "actual_llm_request.json"
    if step == "yfinance:analyst_report":
        return "market", base / "Y_Finance" / day / "final_report.json", None
    if step == "unified:domain_agent":
        directory = base / "runs" / day / "unified_domain_team"
        return "unified", directory / "unified_response.json", directory / "unified_request.json"
    if step == "competitor:comparison_analysis":
        return "competitor", report_root / "Competitor" / day / "peer_comparison_output.json", None
    if step.startswith("strategy:"):
        return "strategy", latest_strategy_attempt(report_root / "Strategy" / day), None
    if step == "writer:html_report":
        writer = report_root / "Writer" / day
        output = writer / "llm_writer_output.json"
        cache = writer / "writer_execution_cache.json"
        if output.is_file() and cache.is_file() and read(output).get("fingerprint") != read(cache).get("fingerprint"):
            return "writer", None, None
        return "writer", output, None
    raise ValueError(f"No saved response layout for step {step}")


def build_previous_store(source: Source, layer: ReplayLayer, root: Path) -> dict[str, Any]:
    """Index the responses of an earlier re-run workspace (real transport only).

    Each logged call of a finished stage is keyed like the original calls. Only the last
    call of a step can be recovered (later calls overwrite the saved outputs), so earlier
    superseded calls of the same step are skipped. Keys already indexed from an earlier
    source are kept from that source.
    """
    root = root.resolve()
    label = str(root)
    settings_path = root / "run_settings.json"
    if not settings_path.is_file():
        raise SystemExit(f"--replay-from {root}: run_settings.json is missing; only re-run workspaces can be replayed")
    transport = read(settings_path).get("transport")
    if transport != "real":
        raise SystemExit(f"--replay-from {root}: produced with transport {transport!r}; only model responses can be replayed")
    store = layer.store
    companies = source.companies()
    layout = Layout(root)
    usage = {row["request"]["request_sha256"]: row.get("usage") for row in read_jsonl(layout.usage_manifest)
             if row.get("status") == "ok"}
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in read_jsonl(layout.call_log):
        if row.get("transport") != "real" or row.get("outcome") not in {"sent", "replayed"}:
            continue
        if row.get("target_company") not in companies or not row.get("request_sha256"):
            continue
        grouped.setdefault((row["replicate"], row["condition"], row["target_company"], row["role"], row["step"]),
                           []).append(row)
    summary = {"label": label, "indexed": 0, "already_indexed": 0, "superseded": 0, "unrecoverable": 0}
    a = agents()
    for key, rows in grouped.items():
        replicate, condition, name, role, step = key
        company = companies[name]
        day = company.selected_date
        entity = next((e for e in company.entities if e.role == role), None)
        stage = GROUP_STAGE.get(step_group(step), "")
        if entity is not None:
            stage = f"{role}_{stage}"
        status_path = layout.status_file(Job(replicate, condition, name))
        stages = read(status_path).get("stages", {}) if status_path.is_file() else {}
        if (stages.get(stage) or {}).get("outcome") != "done":
            store.notes.append(f"{label}: stage {stage} not finished: {'/'.join(key[:4])}")
            summary["unrecoverable"] += len(rows)
            continue
        report_root = layout.report_root(replicate, condition) / name
        if step == "news:period_summary":
            hashes = {row["request_sha256"] for row in rows}
            missing = {sha for sha in hashes if not store.has(key, sha)}
            summary["already_indexed"] += len(hashes) - len(missing)
            if missing and entity is not None:
                before = store.count()
                _index_summaries(store, layer, a, entity_dir(report_root, entity) / "News" / day / "context_exports/month",
                                 key[:4], missing, label)
                summary["indexed"] += store.count() - before
                summary["unrecoverable"] += len(missing) - (store.count() - before)
            continue
        last = rows[-1]
        sha = last["request_sha256"]
        summary["superseded"] += sum(row["request_sha256"] != sha for row in rows)
        if store.has(key, sha):
            summary["already_indexed"] += 1
            continue
        kind, path, request_path = saved_response(report_root, entity if role != "final" else None, step, day)
        if path is None or not path.is_file():
            store.notes.append(f"{label}: no saved response: {'/'.join(key)}")
            summary["unrecoverable"] += 1
            continue
        if request_path is not None and (not request_path.is_file() or request_sha256(read(request_path)) != sha):
            store.notes.append(f"{label}: saved request does not match the logged hash: {'/'.join(key)}")
            summary["unrecoverable"] += 1
            continue
        if store.add(key, ReplayEntry(step, sha, kind, str(path), usage.get(sha), label=label)):
            summary["indexed"] += 1
    return summary


# ---------------------------------------------------------------------------
# Deterministic preparation (current code, frozen collected inputs)


@contextlib.contextmanager
def offline_network() -> Iterator[None]:
    with patch.object(socket.socket, "connect", side_effect=RuntimeError("Offline step: network is blocked")):
        yield


def share_information(source: Source, entity: Entity, a: SimpleNamespace) -> dict[str, Any]:
    """Re-extract share information from the cached primary DART XML with the current extractor."""
    day = entity.selected_date
    frozen = read(entity.collected_root / "Financial" / day / "dart_main.json").get("share_information") or {}
    origin = frozen.get("source") or {}
    receipt = origin.get("receipt_no", "")
    xmls = sorted((entity.collected_root / "News" / day / "inputs/dart").rglob("*.xml"))
    match = [path for path in xmls if receipt and path.parent.name.endswith(receipt)]
    result: dict[str, Any] = {"company_name": entity.company_name, "receipt_no": receipt, "frozen": frozen,
                              "xml": str(match[0]) if match else None, "extracted": None}
    if match:
        period_end = date.fromisoformat(origin["period_end"])
        target = a.TargetReport(role="primary", fiscal_year=period_end.year, period_type=origin["period_type"],
                                period_end=period_end, dart_detail_type="", report_keyword="")
        filing = a.Filing(rcept_no=receipt, report_nm=origin.get("report_name", ""),
                          rcept_dt=origin["receipt_date"].replace("-", ""))
        text = match[0].read_text(encoding="utf-8", errors="replace")
        result["extracted"] = a.extract_share_information(text, target=target, filing=filing)
    return result


def build_financial_snapshot(ctx: "RunContext", entity: Entity, a: SimpleNamespace) -> Path:
    """Copy the collected Financial folder and apply the selected share-information mode."""
    destination = ctx.layout.financial_dir(ctx.source, entity)
    source_dir = entity.collected_root / "Financial" / entity.selected_date
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_dir, destination)
    info = share_information(ctx.source, entity, a)
    info["mode"] = ctx.share_info
    used = info["frozen"]
    if ctx.share_info == "recompute" and info["extracted"] is not None:
        used = info["extracted"]
    info["used"] = used
    info["changed"] = used != info["frozen"]
    if info["changed"]:
        for name in SHARE_INFORMATION_FILES:
            path = destination / name
            if path.is_file():
                payload = read(path)
                if "share_information" in payload:
                    payload["share_information"] = used
                    save(path, payload)
    save(ctx.layout.out / "prepared_inputs/share_information" / f"{entity.company_name}.json", info)
    return destination


def prepare_company(ctx: "RunContext", company: Company) -> None:
    """Rebuild replicate-1 condition inputs of one company (port of prepare_condition_inputs)."""
    status_path = ctx.layout.prepare_status(company.name)
    lock_path = status_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if status_path.is_file() and read(status_path).get("state") == "success":
            return
        set_module_mode(False)
        a = agents()
        day = company.selected_date
        boundary = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
        records = []
        with offline_network(), patch.object(a.context_export, "_build_openai_client",
                                             side_effect=RuntimeError("Preparation makes no model calls")):
            for entity in company.entities:
                name = entity.company_name
                financial_dir = build_financial_snapshot(ctx, entity, a)
                market_dir = entity.collected_root / "Y_Finance" / day
                report = read(entity.news_report)
                a.require_common_candidate_pool(report)
                a.require_prepared_summary_snippets(report)
                frozen_articles = entity.collected_root / "News" / day / "context_exports/month/selected_articles.json"
                if a.build_article_packet(report) != read(frozen_articles):
                    raise ValueError(f"Selected articles changed: {name}")
                seed = a.stable_seed(RANDOM_SEED, day, f"{name}:replicate:1")
                randomized, audit = a.annual_random.select_annual_random_events(report, seed=seed)
                for condition, selected in (("full", report), ("random_news", randomized)):
                    dest = ctx.layout.prepared(company.name, condition, entity.role)
                    if dest.exists():
                        shutil.rmtree(dest)
                    context = {key: copy.deepcopy(value) for key, value in selected.items() if not key.startswith("news_events_")}
                    context.update(news_events_weekly=copy.deepcopy(selected["news_events_weekly"]),
                                   news_events_final=copy.deepcopy(selected["news_events_final"]))
                    context["frozen_candidate_source"] = str(entity.news_report)
                    save(dest / "selected_context.json", context)
                    a.context_export.build_context_exports(
                        report_context_path=dest / "selected_context.json", output_dir=dest / "context_exports/month",
                        llm_model=SUMMARY_MODEL, split_by_period=True, run_llm=False)
                    paths = a.analysis_agent._resolve_paths(
                        project_root=ROOT, context_export_dir=dest / "context_exports", granularity="month",
                        as_of_date=boundary, dart_lightweight_path=str(financial_dir / "dart_lightweight.json"),
                        market_summary_path=str(market_dir / "market_summary.json"), output_dir=str(dest / "news"))
                    news = a.analysis_agent.build_analysis_input_payload(
                        company_name=name, ticker=entity.ticker, corp_code=entity.corp_code, as_of_date=boundary,
                        paths=paths, max_raw_events_per_period=1)
                    save(dest / "news/news_agent_input_payload.json", news)
                    save(dest / "news/news_agent_llm_request.json",
                         a.analysis_agent.build_llm_request(input_payload=news, model=ANALYSIS_MODEL))
                    if condition == "random_news":
                        save(dest / "selection_audit.json", audit)
                dest = ctx.layout.prepared(company.name, "no_subdata", entity.role)
                full_dir = ctx.layout.prepared(company.name, "full", entity.role)
                paths = a.analysis_agent._resolve_paths(
                    project_root=ROOT, context_export_dir=full_dir / "context_exports", granularity="month",
                    as_of_date=boundary, dart_lightweight_path=str(financial_dir / "dart_lightweight.json"),
                    market_summary_path=str(market_dir / "market_summary.json"), output_dir=str(dest / "news"))
                no_sub = a.analysis_agent.build_analysis_input_payload(
                    company_name=name, ticker=entity.ticker, corp_code=entity.corp_code, as_of_date=boundary,
                    paths=paths, max_raw_events_per_period=1, include_secondary_context=False)
                if no_sub["news_context"] != read(full_dir / "news/news_agent_input_payload.json")["news_context"]:
                    raise ValueError(f"No-subdata changed primary news: {name}")
                save(dest / "news_agent_input_payload.json", no_sub)
                save(dest / "news_request.json", a.analysis_agent.build_llm_request(input_payload=no_sub, model=ANALYSIS_MODEL))
                records.append({"role": entity.role, "company_name": name, "random_seed": seed,
                                "financial_dir": str(financial_dir)})
        save(status_path, {"state": "success", "prepared_at": now(), "seed_base": RANDOM_SEED,
                           "share_information": ctx.share_info, "entities": records})


# ---------------------------------------------------------------------------
# Report execution


@dataclass
class RunContext:
    source: Source
    layout: Layout
    share_info: str
    mode: str
    env_file: Path
    companies: dict[str, Company] = field(default_factory=dict)
    replay_from: list[Path] = field(default_factory=list)

    def reference_report(self, label: str, job: "Job") -> Path:
        """Report folder of a job in the workspace a replay source label points to."""
        if label == ORIGINAL_LABEL:
            return self.source.report_dir(job.replicate, job.condition, job.company)
        return Layout(Path(label)).report_root(job.replicate, job.condition) / job.company


class DependencyMissing(RuntimeError):
    """A required earlier report stage has not finished."""


def stage_names(job: Job) -> list[str]:
    roles = ("target",) if job.condition == "no_peer" else ("target", "peer")
    names = []
    for role in roles:
        names.append(f"{role}_inputs")
        if job.condition == "one_team":
            names.append(f"{role}_unified")
        elif job.condition != "no_peer":
            if job.replicate == "r01" and job.condition in SUMMARY_CONDITIONS:
                names.append(f"{role}_monthly_summaries")
            names += [f"{role}_{kind}" for kind in ("news", "financial", "market")]
    finals = [name for name in FINAL_STAGES if not (job.condition == "no_peer" and name.startswith("peer_"))]
    return names + finals


def stage_kind(name: str) -> str:
    for prefix in ("target_", "peer_"):
        if name.startswith(prefix) and name not in {"peer_dataset", "peer_analysis"}:
            return name[len(prefix):]
    return name


def identity(entity: Entity, a: SimpleNamespace) -> Any:
    config = read(entity.company_config)
    return a.CompanyIdentity(config["company_name"], config["corp_code"], config["stock_code"],
                             config["ticker"].rsplit(".", 1)[-1], config["ticker"], {"provider": "frozen_collection_manifest"})


class _CurrentStderr:
    """Logging stream that follows sys.stderr redirection per stage."""

    def write(self, text: str) -> int:
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()


def _configure_logging() -> None:
    root = logging.getLogger()
    if not any(getattr(handler, "_ablation_stage", False) for handler in root.handlers):
        handler = logging.StreamHandler(_CurrentStderr())
        handler._ablation_stage = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)


class ReportRun:
    """One report: materialize inputs, run LLM stages in the original order, publish.

    only: run just these stages (a dependency of another selected report); the report is
    then left in state "partial" and is neither rendered nor published.
    """

    def __init__(self, ctx: RunContext, job: Job, only: frozenset[str] | None = None) -> None:
        self.ctx = ctx
        self.job = job
        self.only = only
        self.writer_reference: str | None = None
        self.company = ctx.companies[job.company]
        self.status_path = ctx.layout.status_file(job)
        self.state = read(self.status_path) if self.status_path.is_file() else {
            "key": job.key, "replicate": job.replicate, "condition": job.condition, "company": job.company,
            "state": "pending", "stages": {}}
        self.blocked: set[str] = set()

    # -- status ---------------------------------------------------------------
    def update(self, **values: Any) -> None:
        self.state.update(values, updated_at=now())
        save(self.status_path, self.state)

    def done(self, name: str) -> bool:
        return (self.state["stages"].get(name) or {}).get("outcome") == "done"

    def record_dependency(self, name: str) -> None:
        kind = stage_kind(name)
        role = name.split("_", 1)[0] if name.startswith(("target_", "peer_")) and kind != name else "final"
        for _ in range(STAGE_CALLS.get(kind, 0)):
            self.ctx_layer_record({"step": STAGE_STEP[kind], "role": role, "outcome": "dependency_new",
                                   "replayed": False, "request_sha256": None})

    def ctx_layer_record(self, row: dict[str, Any]) -> None:
        layer = _ACTIVE["layer"]
        if layer is not None:
            layer.record({"recorded_at": now(), "transport": layer.mode, "replicate": self.job.replicate,
                          "condition": self.job.condition, "target_company": self.job.company,
                          "model": SUMMARY_MODEL if row["step"] == "news:period_summary" else ANALYSIS_MODEL, **row})

    def stage(self, name: str, action: Callable[[], None], *, after: Iterable[str] = ()) -> None:
        if self.done(name) or (self.only is not None and name not in self.only):
            return
        layer = _ACTIVE["layer"]
        if self.ctx.mode == "plan" and (self.blocked.intersection(after) or "*" in self.blocked):
            self.state["stages"][name] = {"outcome": "dependency_new", "at": now()}
            self.record_dependency(name)
            self.blocked.add(name)
            self.update()
            return
        before = dict(layer.counters) if layer else {"replayed": 0, "new": 0}
        sources_before = dict(layer.by_source) if layer else {}
        log_dir = self.paths.execution_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        print(now(), self.job.key, name, "started", flush=True)
        self.update(state="running", current_stage=name)
        try:
            with (log_dir / f"{name}.log").open("a", encoding="utf-8") as stream, \
                    contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                action()
        except PlannedNewCall:
            self.state["stages"][name] = {"outcome": "planned_new", "at": now()}
            self.blocked.add(name)
            self.update()
            print(now(), self.job.key, name, "planned new call", flush=True)
            return
        calls = {key: (layer.counters[key] - before[key]) if layer else 0 for key in before}
        sources = {label: count - sources_before.get(label, 0) for label, count in (layer.by_source if layer else {}).items()
                   if count != sources_before.get(label, 0)}
        self.state["stages"][name] = {"outcome": "done", "completed_at": now(), "calls": calls,
                                      **({"replayed_from": sources} if sources else {})}
        self.update()
        print(now(), self.job.key, name, "done", json.dumps(calls), flush=True)
        if calls["replayed"] and not calls["new"]:
            self.check_fidelity(name, sources)
        if name == "render" and self.writer_reference is not None:
            self.check_report_html(self.writer_reference)

    # -- main -----------------------------------------------------------------
    def execute(self) -> None:
        job, ctx = self.job, self.ctx
        if self.state.get("state") == "success" and Path(self.state.get("report", "")).is_file():
            print(now(), job.key, "already complete; skipped", flush=True)
            return
        if self.only is not None and all(self.done(name) for name in self.only):
            print(now(), job.key, "dependency stages already complete; skipped", flush=True)
            return
        self.state.pop("error", None)
        self.check_dependencies()
        if job.condition in SEPARATE_CONDITIONS and job.replicate == "r01":
            prepare_company(ctx, self.company)
        set_module_mode(job.condition == "one_team")
        a = agents()
        self.a = a
        day = self.company.selected_date
        target = self.company.target
        root = ctx.layout.report_root(job.replicate, job.condition)
        self.paths = a.flow.FullPipelinePaths(root, a.build_run_key(job.company, day), job.company, day,
                                              execution_id(job.replicate, job.condition, target.stock_code))
        self.paths.ensure_directories()
        args, ablation, target_identity, peer_identity, commands = self.downstream_commands()
        a.flow._write_resolved_inputs(
            args=args, ablation=ablation, paths=self.paths, selected_date=day, target=target_identity,
            peer=peer_identity, peer_resolution={"status": "frozen_manual_pair" if peer_identity else "disabled",
                                                 "source": {"provider": "collection_manifest"},
                                                 "selection_basis": {"method": "frozen_experiment_pair"}})
        os.environ.update(OPENAI_MODEL=ANALYSIS_MODEL, NEWS_AGENT_LLM_MODEL=ANALYSIS_MODEL,
                          LLM_TIMEOUT_SECONDS=str(TIMEOUT_SECONDS), LLM_TRANSPORT_RETRIES="0",
                          LLM_USAGE_MANIFEST=str(ctx.layout.usage_manifest))
        entities = self.company.entities[:1] if job.condition == "no_peer" else self.company.entities
        for entity in entities:
            role = entity.role
            self.stage(f"{role}_inputs", lambda entity=entity: self.materialize(entity))
            self.set_telemetry(entity.role, entity.run_key, entity.company_name)
            with self.layer_scope(entity.role, entity.company_name):
                if job.condition == "one_team":
                    self.stage(f"{role}_unified", lambda entity=entity: self.generate_unified(entity),
                               after=(f"{role}_inputs",))
                elif job.condition != "no_peer":
                    if job.replicate == "r01" and job.condition in SUMMARY_CONDITIONS:
                        self.stage(f"{role}_monthly_summaries", lambda entity=entity: self.summarize_months(entity),
                                   after=(f"{role}_inputs",))
                    for kind in ("news", "financial", "market"):
                        needs = (f"{role}_inputs",) + ((f"{role}_monthly_summaries",) if kind != "news" else ())
                        self.stage(f"{role}_{kind}", lambda entity=entity, kind=kind: self.generate_domain(entity, kind),
                                   after=needs)
        self.set_telemetry("final", self.paths.run_key, job.company)
        save(self.paths.execution_dir / ("one_team_commands.json" if job.condition == "one_team" else "commands.json"),
             [{"stage": name, "command": command} for name, command in commands])
        upstream = [name for name in stage_names(job) if name not in FINAL_STAGES]
        with self.layer_scope("final", job.company):
            for name, command in commands:
                self.stage(name, lambda command=command: self.run_cli(command), after=tuple(upstream))
                upstream.append(name)
        if self.only is not None:
            self.update(state="planned" if ctx.mode == "plan" else "partial", current_stage="dependency stages done",
                        dependency_stages=sorted(self.only))
            return
        if ctx.mode == "plan":
            self.update(state="planned")
            return
        if read(self.paths.writer_dir / "writer_run_status.json")["status"] != "success":
            raise RuntimeError("Writer did not complete successfully")
        a.flow._publish_final_report(self.paths)
        decision = self.paths.strategy_dir / a.flow.DECISION_OUTPUT_FILENAME
        self.update(state="success", completed_at=now(), current_stage="complete",
                    report=str(self.paths.published_report), report_sha256=sha_file(self.paths.published_report),
                    decision=str(decision) if decision.is_file() else None)

    def check_dependencies(self) -> None:
        for dependency, stages in self.job.dependencies():
            path = self.ctx.layout.status_file(dependency)
            state = read(path) if path.is_file() else {"stages": {}}
            outcomes = {name: (state["stages"].get(name) or {}).get("outcome") for name in stages}
            missing = [name for name, value in outcomes.items() if value not in {"done", "planned_new"}]
            if missing:
                raise DependencyMissing(f"{self.job.key} needs {dependency.key} stages {missing}; run that report first")
            if self.ctx.mode == "plan" and any(value == "planned_new" for value in outcomes.values()):
                if self.job.condition == "no_peer":
                    self.blocked.add("*")

    @contextlib.contextmanager
    def layer_scope(self, role: str, company_name: str) -> Iterator[None]:
        layer = _ACTIVE["layer"]
        if layer is None:
            yield
            return
        with layer.scope(CallContext(self.job.replicate, self.job.condition, self.job.company, role, company_name)):
            yield

    def set_telemetry(self, role: str, run_id: str, company_name: str) -> None:
        os.environ.update(LLM_EXECUTION_ID=self.paths.execution_id, LLM_RUN_ROLE=role, LLM_RUN_ID=run_id,
                          LLM_COMPANY_NAME=company_name)

    def entity_root(self, entity: Entity) -> Path:
        return entity_dir(self.paths.company_dir, entity)

    # -- inputs -----------------------------------------------------------------
    def materialize(self, entity: Entity) -> None:
        job, layout, a = self.job, self.ctx.layout, self.a
        day = entity.selected_date
        destination = self.entity_root(entity)
        if job.condition == "no_peer":
            source = entity_dir(layout.report_root(job.replicate, "full") / job.company, entity)
            for domain in ("Financial", "Y_Finance", "News"):
                if not (source / domain / day / "final_report.json").is_file() and self.ctx.mode != "plan":
                    raise DependencyMissing(f"No-peer needs completed same-replicate Full outputs: {source}")
            for domain in ("Financial", "Y_Finance", "News"):
                if (source / domain / day).is_dir():
                    shutil.copytree(source / domain / day, destination / domain / day, dirs_exist_ok=True)
            return
        if job.condition == "one_team" or job.replicate != "r01":
            source_condition = "full" if job.condition == "one_team" else job.condition
            source = entity_dir(layout.report_root("r01", source_condition) / job.company, entity)
            for domain in ("Financial", "Y_Finance", "News"):
                for path in sorted((source / domain / day).rglob("*")):
                    if path.is_file() and path.name not in LLM_OUTPUT_NAMES:
                        target = destination / path.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
            if job.condition == "one_team":
                return
            payload_path = destination / "News" / day / "output/news_agent_input_payload.json"
            payload = read(payload_path)
            payload["evidence_map_path"] = str(payload_path.parent / "news_agent_evidence_map.json")
            save(payload_path, payload)
            rebuilt = a.analysis_agent.build_llm_request(input_payload=payload, model=ANALYSIS_MODEL)
            if rebuilt != read(payload_path.parent / "news_agent_llm_request.json"):
                raise ValueError("News request changed while relocating inputs")
            return
        financial = layout.financial_dir(self.ctx.source, entity)
        shutil.copytree(financial, destination / "Financial" / day, dirs_exist_ok=True)
        shutil.copytree(entity.collected_root / "Y_Finance" / day, destination / "Y_Finance" / day, dirs_exist_ok=True)
        prepared_full = layout.prepared(job.company, "full", entity.role)
        prepared = prepared_full if job.condition == "no_subdata" else layout.prepared(job.company, job.condition, entity.role)
        shutil.copytree(prepared / "context_exports", destination / "News" / day / "context_exports", dirs_exist_ok=True)
        if job.condition == "no_subdata":
            payload = read(layout.prepared(job.company, "no_subdata", entity.role) / "news_agent_input_payload.json")
        else:
            payload = read(prepared / "news/news_agent_input_payload.json")
        news_dir = destination / "News" / day / "output"
        payload["evidence_map_path"] = str(news_dir / "news_agent_evidence_map.json")
        save(news_dir / "news_agent_input_payload.json", payload)
        save(news_dir / "news_agent_evidence_map.json", payload["evidence_map"])
        save(news_dir / "news_agent_llm_request.json",
             a.analysis_agent.build_llm_request(input_payload=payload, model=ANALYSIS_MODEL))

    # -- LLM stages -------------------------------------------------------------
    def summarize_months(self, entity: Entity) -> None:
        from openai import OpenAI
        a = self.a
        directory = self.entity_root(entity) / "News" / entity.selected_date / "context_exports/month"
        request = read(directory / "llm_summary_request.json")
        client = OpenAI(timeout=TIMEOUT_SECONDS, max_retries=0)
        period_results, planned = [], 0
        for period, monthly in a.context_export._build_period_llm_requests(request):
            try:
                result = a.context_export._call_llm_summary(client, monthly)
            except PlannedNewCall:
                planned += 1
                continue
            if result["output"].get("parse_error"):
                raise ValueError("Monthly summary JSON parsing failed; no automatic regeneration")
            period_results.append({"period": period, "status": "success", "usage": result["usage"],
                                   "output": a.context_export._extract_period_output(period, result["output"])})
            save(directory / "llm_period_summaries.json", a.context_export._split_summary_payload(request, period_results))
        if planned:
            raise PlannedNewCall("news:period_summary")

    def generate_domain(self, entity: Entity, kind: str) -> None:
        a = self.a
        day = entity.selected_date
        root = self.entity_root(entity)
        f, m, n = root / "Financial" / day, root / "Y_Finance" / day, root / "News" / day
        no_sub = self.job.condition == "no_subdata"
        if kind == "news":
            payload = read(n / "output/news_agent_input_payload.json")
            request = read(n / "output/news_agent_llm_request.json")
            result = a.analysis_agent.execute_analysis_request(llm_request=request, input_payload=payload,
                                                               model=ANALYSIS_MODEL, timeout_seconds=TIMEOUT_SECONDS)
            if result["output"].get("parse_error"):
                raise ValueError("News JSON parsing failed; no automatic regeneration")
            save(n / "output/news_agent_handoff.json", result)
            save(n / "final_report.json", result)
        elif kind == "financial":
            factual = a.build_financial_analyst_output(
                {"target_entity": read(n / "output/news_agent_input_payload.json")["target_entity"]},
                {"dart_main": read(f / "dart_main.json"), "dart_master": read(f / "dart_master.json"),
                 "yfinance_market_summary": {} if no_sub else read(m / "market_summary.json"),
                 "news_weekly_summaries": {} if no_sub else read(n / "context_exports/month/llm_period_summaries.json")})
            agent = a.financial_analysis_agent
            save(f / "actual_llm_request.json", agent.build_financial_request(factual, model=ANALYSIS_MODEL))
            save(f / "final_report.json",
                 agent.apply_financial_analysis(factual, agent.generate_financial_analysis_with_llm(factual, model=ANALYSIS_MODEL)))
        else:
            a.reporting.generate_analyst_report(
                market_json=m / "market_full_dataset.json", dart_json=f / "dart_lightweight.json",
                news_json=n / "context_exports/month/llm_period_summaries.json", valuation_json=m / "valuation_snapshot.json",
                report_md=m / "final_report.md", report_json=m / "final_report.json", company_name=entity.company_name,
                ticker=read(entity.company_config)["ticker"], model=ANALYSIS_MODEL, primary_data_only=no_sub)

    def generate_unified(self, entity: Entity) -> None:
        """One integrated analysis per entity, from the Full r01 inputs of this re-run."""
        a = self.a
        day = entity.selected_date
        root = self.entity_root(entity)
        full_root = entity_dir(self.ctx.layout.report_root("r01", "full") / self.job.company, entity)
        directory = root / "runs" / day / "unified_domain_team"
        spec = {"role": entity.role, "company_name": entity.company_name, "selected_date": day,
                "company_config": str(entity.company_config)}
        prepared = a.prepare_entity(source_root=full_root, entity=spec, model=ANALYSIS_MODEL)
        request = a.unified_report.build_request(prepared["semantic_input"], model=ANALYSIS_MODEL)
        save(directory / "preprocessed_input_bundle.json", prepared)
        request_path = save(directory / "unified_request.json", request)
        raw_path = directory / "unified_response.json"
        request_hash = sha_file(request_path)
        raw = read(raw_path) if raw_path.is_file() else None
        if raw is None or raw.get("request_sha256") != request_hash:
            response = a.call_domain_response(request, step="unified:domain_agent", timeout_seconds=TIMEOUT_SECONDS)
            raw = {"request_sha256": request_hash, "output": json.loads(response.output_text),
                   "usage": a.normalize_usage(response.usage), "response_id": getattr(response, "id", None)}
            save(raw_path, raw)
        output, changes = a.unified_report.normalize_source_domains(raw["output"], prepared["semantic_input"])
        a.unified_report.validate_output(output, prepared["semantic_input"])
        config = read(entity.company_config)
        outputs = a.unified_report.write_report(
            output=output, prepared=prepared, destination_paths=SimpleNamespace(run_dir=root / "runs" / day),
            run_config=SimpleNamespace(company_name=entity.company_name, ticker=config["ticker"],
                                       selected_date_iso=f"{day[:4]}-{day[4:6]}-{day[6:]}"),
            model=ANALYSIS_MODEL, role=entity.role)
        save(directory / "manifest.json", {"status": "success", "protocol": a.unified_report.PROTOCOL,
                                           "semantic_call_count": 1, "analysis_report_count": 1, "outputs": outputs,
                                           "usage": raw["usage"], "domain_metadata_normalizations": changes,
                                           "raw_response": str(raw_path), "source_artifacts": prepared["source_artifacts"]})

    # -- downstream stages, in process ------------------------------------------
    def downstream_commands(self) -> tuple[Any, Any, Any, Any, list[tuple[str, list[str]]]]:
        a, job = self.a, self.job
        day = self.company.selected_date
        flags = ["--llm-model", ANALYSIS_MODEL]
        if job.condition == "one_team":
            flags += ["--news-summary-model", SUMMARY_MODEL, "--decision-horizon-profile", "annual"]
        elif job.condition == "no_peer":
            flags.append("--no-competitor")
        elif job.condition == "no_subdata":
            flags.append("--primary-data-only")
        args = a.flow.build_parser().parse_args(["--company-name", job.company, "--selected-date", day, *flags])
        ablation = a.config_from_args(args)
        target = identity(self.company.target, a)
        peer = None if job.condition == "no_peer" else identity(self.company.peer, a)
        peer_key = a.build_run_key(peer.company_name, day) if peer else ""
        env_file = self.ctx.env_file
        paths, flow = self.paths, a.flow
        commands: list[tuple[str, list[str]]] = []
        if peer:
            commands += [
                ("peer_dataset", flow.build_peer_comparison_command(paths=paths, peer_run_key=peer_key, selected_date=day, target=target)),
                ("peer_analysis", flow.build_peer_analysis_command(paths=paths, peer_run_key=peer_key, target=target, peer=peer,
                                                                   args=args, ablation=ablation, env_file=env_file))]
        commands += [
            ("strategy", flow.build_strategy_command(paths=paths, selected_date=day, target=target, args=args,
                                                     ablation=ablation, env_file=env_file)),
            ("chart_catalog", flow.build_visualization_catalog_command(paths=paths, target=target, peer_run_key=peer_key)),
            ("writer", flow.build_writer_generation_command(paths=paths, args=args, ablation=ablation, env_file=env_file)),
            ("charts", flow.build_visualization_command(paths=paths, target=target, peer_run_key=peer_key)),
            ("render", flow.build_writer_render_command(paths=paths, args=args, ablation=ablation, env_file=env_file))]
        return args, ablation, target, peer, commands

    def run_cli(self, command: list[str]) -> None:
        """Run an agent CLI module's main() in this process instead of a subprocess."""
        if command[1] != "-m":
            raise ValueError(f"Unexpected command shape: {command[:3]}")
        module = importlib.import_module(command[2])
        code = module.main([str(value) for value in command[3:]])
        if code:
            raise RuntimeError(f"{self.job.key}: {command[2]} exited with {code}")

    # -- replay fidelity ----------------------------------------------------------
    def check_fidelity(self, name: str, sources: dict[str, int]) -> None:
        """Compare a fully replayed stage's output with the saved output of the replayed source."""
        kind = stage_kind(name)
        if kind not in FIDELITY_FILES or len(sources) != 1:
            return
        label = next(iter(sources))
        scope, files = FIDELITY_FILES[kind]
        day = self.company.selected_date
        reference_root = self.ctx.reference_report(label, self.job)
        if scope == "entity":
            role = name.split("_", 1)[0]
            entity = next((e for e in self.company.entities if e.role == role), None)
            if entity is None:
                return
            reference, current = entity_dir(reference_root, entity), self.entity_root(entity)
        else:
            role, reference, current = "final", reference_root, self.paths.company_dir
        if kind == "writer":
            self.writer_reference = label
        for template in files:
            relative = template.format(day=day)
            if not (reference / relative).is_file() or not (current / relative).is_file():
                continue
            old, new = self.normalized(reference / relative), self.normalized(current / relative)
            if kind == "news":
                old, new = {"output": old.get("output"), "usage": old.get("usage")}, {"output": new.get("output"), "usage": new.get("usage")}
            ignored = FIDELITY_IGNORED.get(Path(relative).name, frozenset())
            if ignored:
                old, new = ({key: value for key, value in item.items() if key not in ignored} for item in (old, new))
            self.log_fidelity(role, kind, relative, label, old, new)

    def check_report_html(self, label: str) -> None:
        """After a replayed Writer stage, compare the rendered HTML with the replayed source's HTML."""
        relative = f"Writer/{self.company.selected_date}/report.html"
        reference = self.ctx.reference_report(label, self.job) / relative
        current = self.paths.company_dir / relative
        if not reference.is_file() or not current.is_file():
            return
        old, new = self.normalized_text(reference), self.normalized_text(current)
        append_jsonl(self.ctx.layout.fidelity_log, {
            "recorded_at": now(), "replicate": self.job.replicate, "condition": self.job.condition,
            "target_company": self.job.company, "role": "final", "stage": "report_html", "file": relative,
            "reference": label, "identical": old == new, "key_order_identical": old == new,
            "differing_fields": [], "differences": [], "difference_count": 0 if old == new else 1})

    def log_fidelity(self, role: str, kind: str, relative: str, label: str, old: Any, new: Any) -> None:
        differences = json_differences(old, new)
        same_order = json.dumps(old, ensure_ascii=False) == json.dumps(new, ensure_ascii=False)
        append_jsonl(self.ctx.layout.fidelity_log, {
            "recorded_at": now(), "replicate": self.job.replicate, "condition": self.job.condition,
            "target_company": self.job.company, "role": role, "stage": kind, "file": relative, "reference": label,
            "identical": not differences and same_order, "key_order_identical": same_order,
            "differing_fields": sorted({item.split(".")[1].split("[")[0] for item in differences if "." in item}),
            "differences": differences[:20], "difference_count": len(differences)})

    def normalized_text(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        prefixes = {self.ctx.source.recorded_root, str(self.ctx.source.root), str(self.ctx.layout.out),
                    *(str(Path(root).resolve()) for root in self.ctx.replay_from)}
        for prefix in sorted(prefixes, key=len, reverse=True):
            text = text.replace(prefix, "<WORKSPACE>")
        return text

    def normalized(self, path: Path) -> Any:
        return json.loads(self.normalized_text(path))


def json_differences(old: Any, new: Any, path: str = "$") -> list[str]:
    """List JSON paths whose values differ (order-insensitive for objects)."""
    if isinstance(old, dict) and isinstance(new, dict):
        result = []
        for key in list(old) + [k for k in new if k not in old]:
            if key not in old or key not in new:
                result.append(f"{path}.{key}")
            else:
                result += json_differences(old[key], new[key], f"{path}.{key}")
        return result
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{path}[len {len(old)}->{len(new)}]"]
        return [item for index, (a, b) in enumerate(zip(old, new)) for item in json_differences(a, b, f"{path}[{index}]")]
    return [] if old == new else [path]


# ---------------------------------------------------------------------------
# Orchestration


def _execute_job(ctx: RunContext, job: Job, only: frozenset[str] | None = None) -> dict[str, Any]:
    run = ReportRun(ctx, job, only)
    try:
        run.execute()
        return {"key": job.key, "state": run.state.get("state")}
    except (DependencyMissing, Exception, FakeTransportStop) as exc:
        stopped = isinstance(exc, FakeTransportStop)
        run.update(state="stopped" if stopped else "failed", stopped_at=now(),
                   error={"type": type(exc).__name__, "message": str(exc)})
        if not stopped and not isinstance(exc, DependencyMissing):
            traceback.print_exc()
        print(now(), job.key, "STOPPED" if stopped else "FAILED", type(exc).__name__, str(exc)[:300], flush=True)
        return {"key": job.key, "state": run.state["state"], "error": str(exc)}


def _pool_job(arguments: tuple[RunContext, Job, frozenset[str] | None]) -> dict[str, Any]:
    return _execute_job(*arguments)


def execute_jobs(ctx: RunContext, jobs: list[Job], workers: int,
                 partial: dict[str, frozenset[str]] | None = None) -> list[dict[str, Any]]:
    """Run jobs level by level so Full precedes No-peer, One-team and later replicates."""
    partial = partial or {}
    results = []
    for level in sorted({job.level for job in jobs}):
        batch = [(ctx, job, partial.get(job.key)) for job in jobs if job.level == level]
        if workers <= 1 or len(batch) == 1:
            results += [_execute_job(*item) for item in batch]
            continue
        context = multiprocessing.get_context("fork")
        with context.Pool(processes=min(workers, len(batch)), maxtasksperchild=1) as pool:
            results += pool.map(_pool_job, batch, chunksize=1)
    return results


def report_dependencies(layout: Layout, selected: list[Job], jobs: list[Job], partial: dict[str, frozenset[str]],
                        add_dependencies: bool) -> list[dict[str, Any]]:
    """Print which dependency stages are already done in --out and which the run adds (transitively)."""
    rows = []
    chosen = {job.key for job in selected}
    for job in jobs:
        for dependency, stages in job.dependencies():
            if dependency.key in chosen:
                continue
            path = layout.status_file(dependency)
            state = read(path) if path.is_file() else {"stages": {}}
            missing = [name for name in stages if (state["stages"].get(name) or {}).get("outcome") != "done"]
            rows.append({"report": job.key, "needs": dependency.key, "stages": list(stages), "missing": missing,
                         "action": ("satisfied" if not missing else
                                    "added: runs only " + ", ".join(sorted(partial.get(dependency.key, ())))
                                    if add_dependencies else "missing: the report will stop")})
    for row in rows:
        print(f"dependency {row['report']} <- {row['needs']}: {row['action']}", flush=True)
    return rows


def make_context(args: argparse.Namespace, out: Path, mode: str) -> RunContext:
    source = Source(args.source)
    layout = Layout(out)
    env_file = Path(args.env_file).resolve() if args.env_file else layout.out / "status/empty.env"
    if not args.env_file:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.touch()
    replay_from = [Path(root).resolve() for root in getattr(args, "replay_from", None) or []]
    for root in replay_from:
        if root == layout.out or root == Path(args.out).resolve():
            raise SystemExit(f"--replay-from {root} is the output workspace itself")
    return RunContext(source=source, layout=layout, share_info=args.share_info, mode=mode, env_file=env_file,
                      companies=source.companies(), replay_from=replay_from)


def activate_layer(ctx: RunContext, mode: str, *, canned: bool = False) -> tuple[ReplayLayer, list[dict[str, Any]]]:
    """Index the original responses, then each --replay-from workspace in order."""
    layer = ReplayLayer(ReplayStore(), mode=mode, call_log=None)
    install_replay(layer)
    set_module_mode(False)
    build_replay_store(ctx.source, layer, canned=canned)
    sources = [{"label": ORIGINAL_LABEL, "indexed": layer.store.count()}]
    for root in ctx.replay_from:
        sources.append(build_previous_store(ctx.source, layer, root))
    layer.call_log = ctx.layout.call_log
    return layer, sources


def offline_guard() -> None:
    """Plan and fake runs: no real key in the process and no outbound sockets."""
    os.environ["OPENAI_API_KEY"] = "sk-offline-no-model-calls"
    os.environ.pop("OPENAI_BASE_URL", None)

    def blocked(*_: Any, **__: Any) -> None:
        raise RuntimeError("Offline mode: outbound network is blocked")

    socket.socket.connect = blocked  # type: ignore[assignment]
    socket.socket.connect_ex = blocked  # type: ignore[assignment]


def summarize_calls(rows: list[dict[str, Any]], jobs: list[Job]) -> dict[str, Any]:
    selected = {job.key for job in jobs}
    table: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int]] = {}
    for row in rows:
        if f"{row.get('replicate')}/{row.get('condition')}/{row.get('target_company')}" not in selected:
            continue
        group = step_group(row["step"])
        key = f"{group}|{row['condition']}|{row['replicate']}"
        bucket = table.setdefault(key, {"replayed": 0, "new": 0})
        bucket["replayed" if row.get("replayed") else "new"] += 1
        label = (row.get("source_label") or ORIGINAL_LABEL) if row.get("replayed") else "new"
        counts = by_source.setdefault(label, {})
        counts[group] = counts.get(group, 0) + 1
    totals: dict[str, dict[str, int]] = {}
    for key, bucket in table.items():
        group = key.split("|", 1)[0]
        total = totals.setdefault(group, {"replayed": 0, "new": 0})
        for name in ("replayed", "new"):
            total[name] += bucket[name]
    sub = {name: sum(totals.get(group, {}).get(name, 0) for group in SUB_ANALYSIS_GROUPS) for name in ("replayed", "new")}
    return {"by_step_condition_replicate": dict(sorted(table.items())), "by_step": totals,
            "by_source": {label: dict(sorted(counts.items())) for label, counts in sorted(by_source.items())},
            "sub_analyses_and_competitor": sub}


def print_plan(summary: dict[str, Any]) -> None:
    print(f"{'step':<16} {'condition':<12} {'rep':<4} {'replayed':>8} {'new':>5}")
    for key, bucket in summary["by_step_condition_replicate"].items():
        group, condition, replicate = key.split("|")
        print(f"{group:<16} {condition:<12} {replicate:<4} {bucket['replayed']:>8} {bucket['new']:>5}")
    print("totals by step:")
    for group, bucket in sorted(summary["by_step"].items()):
        print(f"  {group:<16} replayed {bucket['replayed']:>4}  new {bucket['new']:>4}")
    sub = summary["sub_analyses_and_competitor"]
    print(f"sub-analyses + competitor: replayed {sub['replayed']} / new {sub['new']}")
    print("calls by source (replayed from each workspace, or new):")
    for label, counts in summary.get("by_source", {}).items():
        detail = "  ".join(f"{group} {count}" for group, count in counts.items())
        print(f"  {label}: total {sum(counts.values())}  [{detail}]")


def command_plan(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    work = out / "plan" / f"work_{args.share_info}"
    if work.exists():
        shutil.rmtree(work)
    offline_guard()
    ctx = make_context(args, work, "plan")
    _configure_logging()
    layer, sources = activate_layer(ctx, "plan")
    selected = select_jobs(args.companies, args.conditions, args.replicates)
    jobs, partial = plan_jobs(selected, add_dependencies=not args.no_dependencies)
    dependencies = report_dependencies(ctx.layout, selected, jobs, partial, not args.no_dependencies)
    results = execute_jobs(ctx, jobs, args.workers, partial)
    calls = read_jsonl(ctx.layout.call_log)
    summary = summarize_calls(calls, selected)
    fidelity = read_jsonl(ctx.layout.fidelity_log)
    summary.update(
        share_information=args.share_info, created_at=now(), code=code_version(), reports=len(selected),
        replay_from=[str(Path(root).resolve()) for root in args.replay_from],
        dependencies=dependencies,
        dependency_calls=summarize_calls(calls, [job for job in jobs if job.key in partial])["by_step"],
        failures=[r for r in results if r["state"] not in {"planned", "success"}],
        replay_store={"entries": layer.store.count(), "entries_by_source": layer.store.count_by_source(),
                      "sources": sources, "notes": layer.store.notes},
        replay_fidelity=fidelity_summary(fidelity))
    save(out / "plan" / f"plan_{args.share_info}.json", summary)
    print_plan(summary)
    if partial:
        print("dependency stages (not counted above):", json.dumps(summary["dependency_calls"], ensure_ascii=False))
    for label, counts in summary["replay_fidelity"]["by_source"].items():
        print(f"replay fidelity vs {label}: {counts['identical']}/{counts['checked']} replayed stage outputs identical "
              f"to the saved outputs")
    if summary["failures"]:
        print("failures:", json.dumps(summary["failures"], ensure_ascii=False))
    if not args.keep_plan_work:
        shutil.rmtree(work)
    print(f"plan written to {out / 'plan' / f'plan_{args.share_info}.json'}; model calls: 0")
    return 1 if summary["failures"] else 0


def fidelity_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = by_source.setdefault(row.get("reference") or ORIGINAL_LABEL, {"checked": 0, "identical": 0, "by_stage": {}})
        stage = bucket["by_stage"].setdefault(row["stage"], {"checked": 0, "identical": 0})
        for target in (bucket, stage):
            target["checked"] += 1
            target["identical"] += bool(row["identical"])
    return {"checked": len(rows), "identical": sum(bool(r["identical"]) for r in rows), "by_source": by_source,
            "differing": [r for r in rows if not r["identical"]][:50]}


def check_settings(layout: Layout, settings: dict[str, Any]) -> None:
    if layout.settings.is_file():
        previous = read(layout.settings)
        keys = ("source", "share_information", "transport")
        if any(previous.get(key) != settings.get(key) for key in keys):
            raise SystemExit(f"{layout.out} was started with {[previous.get(k) for k in keys]}; "
                             "use a new --out for different settings")
    else:
        save(layout.settings, {**settings, "created_at": now()})


def command_run(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    mode = {"none": "real", "stop": "fake-stop", "canned": "fake-canned"}[args.fake_transport]
    if mode != "real":
        offline_guard()
    ctx = make_context(args, out, mode)
    check_settings(ctx.layout, {"source": str(ctx.source.root), "share_information": args.share_info, "transport": mode,
                                "replay_from": [str(root) for root in ctx.replay_from]})
    if mode == "real":
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise SystemExit("OPENAI_API_KEY is not set; new calls need a configured transport")
    _configure_logging()
    _, sources = activate_layer(ctx, mode, canned=mode == "fake-canned")
    for row in sources:
        print("replay source:", json.dumps(row, ensure_ascii=False), flush=True)
    selected = select_jobs(args.companies, args.conditions, args.replicates)
    jobs, partial = plan_jobs(selected, add_dependencies=not args.no_dependencies)
    report_dependencies(ctx.layout, selected, jobs, partial, not args.no_dependencies)
    started = now()
    results = execute_jobs(ctx, jobs, args.workers, partial)
    manifest = collect(ctx, started_at=started)
    print(json.dumps({"reports": len(selected), "dependency_reports": len(partial),
                      "states": _count(r["state"] for r in results), "calls": manifest["calls"]["totals"],
                      "calls_by_source": {label: sum(counts.values())
                                          for label, counts in manifest["calls"]["by_source"].items()}},
                     ensure_ascii=False))
    expected = {job.key: "partial" if job.key in partial else "success" for job in jobs}
    return 0 if all(r["state"] in {expected[r["key"]], "success"} for r in results) else 1


def _count(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def code_version() -> dict[str, Any]:
    def git(*command: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(ROOT), *command], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return ""
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def collect(ctx: RunContext, *, started_at: str | None = None) -> dict[str, Any]:
    """Copy finished reports and decisions to stable paths and write manifest.json."""
    layout = ctx.layout
    reports = []
    for job in select_jobs(COMPANIES, CONDITIONS, REPLICATES):
        path = layout.status_file(job)
        if not path.is_file():
            continue
        state = read(path)
        row = {"key": job.key, "state": state.get("state")}
        if state.get("state") == "success" and state.get("report") and Path(state["report"]).is_file():
            html = layout.out / "final_reports" / job.replicate / job.condition / f"{job.company}.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(state["report"], html)
            row.update(report=str(html.relative_to(layout.out)), report_sha256=sha_file(html))
            if state.get("decision") and Path(state["decision"]).is_file():
                decision = layout.out / "strategy_decisions" / job.replicate / job.condition / f"{job.company}.json"
                decision.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(state["decision"], decision)
                row["strategy_decision"] = str(decision.relative_to(layout.out))
        reports.append(row)
    calls = read_jsonl(layout.call_log)
    summary = summarize_calls(calls, select_jobs(COMPANIES, CONDITIONS, REPLICATES))
    from orchestration.usage_summary import estimate_api_cost
    usage_rows = read_jsonl(layout.usage_manifest)
    fidelity = read_jsonl(layout.fidelity_log)
    settings = read(layout.settings) if layout.settings.is_file() else {}
    previous = read(layout.out / "manifest.json") if (layout.out / "manifest.json").is_file() else {}
    manifest = {
        "code": code_version(), "source_workspace": str(ctx.source.root),
        "replay_from": [str(root) for root in ctx.replay_from] or settings.get("replay_from", []),
        "share_information": settings.get("share_information", ctx.share_info),
        "transport": settings.get("transport", ctx.mode),
        "models": MODELS_BY_STAGE, "recorded_models": sorted({row.get("model", "") for row in calls}),
        "calls": {"totals": {"replayed": sum(bool(r.get("replayed")) for r in calls),
                             "new": sum(not r.get("replayed") for r in calls)}, **summary},
        "usage_manifest": str(layout.usage_manifest.relative_to(layout.out)),
        "transport_attempts": len(usage_rows), "estimated_api_cost": estimate_api_cost(usage_rows) if usage_rows else {},
        "replay_fidelity": {key: value for key, value in fidelity_summary(fidelity).items() if key != "differing"},
        "reports": reports, "reports_completed": sum(r["state"] == "success" for r in reports),
        "first_started_at": previous.get("first_started_at") or started_at or now(),
        "last_started_at": started_at or previous.get("last_started_at"), "collected_at": now(),
    }
    save(layout.out / "manifest.json", manifest)
    return manifest


def command_collect(args: argparse.Namespace) -> int:
    ctx = make_context(args, Path(args.out).resolve(), "collect")
    manifest = collect(ctx)
    print(json.dumps({"reports_completed": manifest["reports_completed"], "calls": manifest["calls"]["totals"]},
                     ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("plan", "run", "collect"))
    parser.add_argument("--source", type=Path, required=True, help="Frozen original workspace (collected_data/, reports/, status/)")
    parser.add_argument("--out", type=Path, required=True, help="New workspace for the re-run")
    parser.add_argument("--companies", nargs="+", default=list(COMPANIES), choices=COMPANIES)
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=CONDITIONS)
    parser.add_argument("--replicates", nargs="+", default=list(REPLICATES), choices=REPLICATES)
    parser.add_argument("--share-info", choices=("recompute", "frozen"), default="recompute",
                        help="recompute: re-extract share information from cached DART XML; frozen: keep collected values")
    parser.add_argument("--workers", type=int, default=1, help="Parallel reports per dependency level (forked processes)")
    parser.add_argument("--env-file", default=None, help="Optional key file; exported variables always take precedence")
    parser.add_argument("--keep-plan-work", action="store_true", help="plan: keep the scratch report tree under OUT/plan/")
    parser.add_argument("--replay-from", type=Path, action="append", default=[], metavar="DIR",
                        help="Earlier re-run workspace (repeatable): its responses replay under the same keys and "
                             "request hashes when the original workspace has none")
    parser.add_argument("--no-dependencies", action="store_true",
                        help="Do not add unselected reports the selection depends on (they must be done in --out)")
    parser.add_argument("--fake-transport", choices=("none", "stop", "canned"), default="none",
                        help="run: offline smoke test. stop: halt at the first non-replayed call; canned: answer it "
                             "with the original response of the same step. Never use for real results.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers < 1:
        raise SystemExit("--workers must be positive")
    os.environ.setdefault("MPLBACKEND", "Agg")
    if args.action == "plan":
        return command_plan(args)
    if args.action == "run":
        return command_run(args)
    return command_collect(args)


if __name__ == "__main__":
    raise SystemExit(main())
