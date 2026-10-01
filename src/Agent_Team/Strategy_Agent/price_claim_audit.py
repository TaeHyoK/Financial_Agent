"""Review flags for unsupported inferences from historical price observations.

This is a diagnostic, not a semantic validity gate. It deliberately reports
candidate sentences for human review instead of changing a recommendation.
"""

from __future__ import annotations

import re
from typing import Any


PRICE_CLAIM_AUDIT_VERSION = "lexical_v3"


_PRICE_BELIEF_PATTERNS = {
    "priced_in": re.compile(
        r"(?:이미|아직|충분히|상당.{0,3}부분|회복|성장|기대|우려).{0,25}(?:반영|선반영|녹아)"
        r"|(?:주가|가격).{0,30}반영"
        r"|선반영|재평가.{0,16}(?:끝|완결|진행)|할인.{0,6}(?:폭|확대)"
    ),
    "market_belief": re.compile(
        r"(?:시장|투자자).{0,25}(?:신뢰|기대|관심|심리|보수적으로|낙관)"
        r"|(?:관심|신뢰|기대).{0,12}(?:회복|강해|약해|높아|낮아|커졌|형성)"
    ),
}
_NEGATION = re.compile(
    r"(?:단정|판정|추정|의미).{0,20}(?:없|어렵|못|아니|부족)"
    r"|근거.{0,8}(?:없|아니|부족)"
    r"|(?:하지 못|하지 않).{0,12}단정"
)
_REVIEW_FIELDS = ("price_assessment", "decision_rationale", "thesis", "outlook")
_PRICE_CONTEXT = re.compile(r"주가|가격|수익률|시장 대비|시장 신뢰|투자자|매수세|재평가|밸류에이션|배수|저평가|고평가")


def audit_strategy_price_claims(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag market-history-to-belief claims; never silently reject prose.

    A match is reported only when the field cites a ``market.*`` card. Local
    negation following a phrase suppresses the flag, but the lexical audit is
    intentionally conservative about semantic certainty.
    """

    brief = decision.get("strategy_brief") or {}
    findings: list[dict[str, Any]] = []
    seen_sentences: set[tuple[str, str]] = set()
    for field in _REVIEW_FIELDS:
        linked = brief.get(field) or {}
        keys = [str(key) for key in linked.get("card_keys") or []]
        if not any(key.startswith("market.") for key in keys):
            continue
        prose = str(linked.get("text") or "")
        for sentence in re.split(r"(?<=[.!?])\s+", prose):
            for rule, pattern in _PRICE_BELIEF_PATTERNS.items():
                for match in pattern.finditer(sentence):
                    sentence_key = (field, sentence.strip())
                    if sentence_key in seen_sentences:
                        continue
                    if rule == "priced_in" and re.search(r"(?:실적|재무제표|매출|이익)에 반영|반영해야", match.group(0)):
                        continue
                    if field != "price_assessment" and rule == "priced_in" and not _PRICE_CONTEXT.search(sentence):
                        continue
                    # Only a negation of this local claim counts. A later
                    # caveat about valuation must not erase an earlier belief
                    # claim in the same sentence.
                    tail = sentence[match.end():match.end() + 32]
                    if _NEGATION.search(tail):
                        continue
                    findings.append({
                        "field": field,
                        "rule": rule,
                        "matched_text": match.group(0),
                        "sentence": sentence.strip(),
                        "market_card_keys": [key for key in keys if key.startswith("market.")],
                        "status": "manual_review_required",
                    })
                    seen_sentences.add(sentence_key)
    return findings
