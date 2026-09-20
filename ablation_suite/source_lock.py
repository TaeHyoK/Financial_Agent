"""Content manifests for exactly the common files consumed by snapshot reuse."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .snapshots import FrozenSource, _source_entities
from .utils import load_json, sha256_file, write_json
from orchestration.config import load_run_config
from orchestration.paths import resolve_run_paths
from orchestration.end_to_end_loop import _reused_news_report_context_source


def source_content_manifest(source: FrozenSource) -> dict:
    files = {source.full_manifest, source.target_config, source.peer_config}
    for _, config_path, root in _source_entities(source):
        config = load_run_config(config_path)
        paths = resolve_run_paths(config, root)
        files.update({
            paths.run_config_copy, paths.run_status,
            paths.yfinance_dir / "market_full_dataset.json",
            paths.yfinance_dir / "market_full_dataset.csv",
            paths.market_summary_dated, paths.yfinance_dir / "manifest.json",
            paths.valuation_snapshot, paths.dart_master,
            _reused_news_report_context_source(paths, config),
        })
        if not paths.news_context_export_dir.is_dir():
            raise ValueError(f"Missing common news context directory: {paths.news_context_export_dir}")
        files.update(path for path in paths.news_context_export_dir.rglob("*") if path.is_file())
        # Require the shared selected-article packet, not a legacy summary request.
        files.add(paths.news_articles)
        files.add(paths.news_llm_period_summaries)
    hashes = {str(path.resolve()): sha256_file(path) for path in sorted(files)}
    content = {"version": "common_source_content_v1", "company_name": source.company_name,
               "selected_date": source.selected_date, "files": hashes}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    return {**content, "content_sha256": digest}


def lock_or_verify_source(source: FrozenSource, lock_path: Path) -> str:
    current = source_content_manifest(source)
    if lock_path.is_file():
        expected = load_json(lock_path)
        if current != expected:
            raise ValueError(f"Common source contents changed; restore the frozen data or use a new suite: {lock_path}")
    else:
        write_json(lock_path, current)
    return current["content_sha256"]
