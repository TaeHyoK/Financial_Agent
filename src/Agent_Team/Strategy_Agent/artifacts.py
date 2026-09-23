"""Filenames the Strategy Agent writes into a run directory.

The Writer Agent and the pipeline read the same files, so the names live here
instead of being repeated as literals in every producer and consumer.
"""

from __future__ import annotations


COMPACT_PACKET_FILENAME = "strategy_compact_packet.json"
PACKET_PROVENANCE_FILENAME = "strategy_packet_provenance.json"
CONTEXT_PACKAGE_FILENAME = "strategy_context_package.json"
GENERATION_CONTEXT_FILENAME = "strategy_generation_context.json"
CONTEXT_TELEMETRY_FILENAME = "strategy_context_telemetry.json"
DECISION_OUTPUT_FILENAME = "strategy_decision_output.json"
DECISION_CACHE_FILENAME = "strategy_decision_cache.json"
DECISION_PROFILE_FILENAME = "strategy_decision_profile.json"
FAILURE_REPORT_FILENAME = "strategy_failure_report.json"


__all__ = [
    "COMPACT_PACKET_FILENAME",
    "CONTEXT_PACKAGE_FILENAME",
    "CONTEXT_TELEMETRY_FILENAME",
    "DECISION_CACHE_FILENAME",
    "DECISION_OUTPUT_FILENAME",
    "DECISION_PROFILE_FILENAME",
    "FAILURE_REPORT_FILENAME",
    "GENERATION_CONTEXT_FILENAME",
    "PACKET_PROVENANCE_FILENAME",
]
