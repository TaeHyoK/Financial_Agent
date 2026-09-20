"""Static experiment protocol agreed for the six-company ablation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


ABLATION_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_COMPAT_ROOT = ABLATION_ROOT / "runtime_compat"
FINAL_ROOT = Path(os.getenv("FINANCIAL_AGENT_ROOT", ABLATION_ROOT)).resolve()
FINAL_SRC = FINAL_ROOT / "src"
DEFAULT_ENV_FILE = FINAL_ROOT / "configs" / ".env"
DEFAULT_SELECTED_DATE = "20251031"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_SEED = 20251031
DEFAULT_REPLICATES = 3
RANDOM_NEWS_COUNT = 20

CONDITIONS = (
    "full",
    "no_peer",
    "no_subdata",
    "unified_domain_team",
    "random_news",
)


@dataclass(frozen=True)
class CompanySpec:
    key: str
    name: str
    target_news_query: str = ""


COMPANY_SPECS = (
    CompanySpec("skbiopharm", "SK바이오팜"),
    CompanySpec("amorepacific", "아모레퍼시픽"),
    CompanySpec("coway", "코웨이"),
    CompanySpec("hyundai_mobis", "현대모비스"),
    CompanySpec("bgf_retail", "BGF리테일"),
    CompanySpec(
        "s_oil",
        "S-OIL",
        '("S-OIL" OR "에쓰오일" OR "에스오일")',
    ),
)

COMPANY_BY_KEY = {company.key: company for company in COMPANY_SPECS}
COMPANY_BY_NAME = {company.name: company for company in COMPANY_SPECS}

DEFAULT_SOURCE_ROOTS = (
    ABLATION_ROOT / "Output_total" / "full",
    FINAL_ROOT / "Output_total",
)
