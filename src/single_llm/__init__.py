"""Single-model, single-generation-call baseline for final report evaluation."""

from .config import SingleLLMConfig, load_single_llm_config
from .input_bundle import BundleBuildResult, build_input_bundle

__all__ = [
    "BundleBuildResult",
    "SingleLLMConfig",
    "build_input_bundle",
    "load_single_llm_config",
]
