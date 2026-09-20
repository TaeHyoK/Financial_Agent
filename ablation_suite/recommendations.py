"""Read the Writer's explicit rating without inferring an investment decision."""

from pathlib import Path

from bs4 import BeautifulSoup


def read_explicit_rating(path: str | Path) -> dict[str, object]:
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "lxml")
    fields = soup.find_all("meta", attrs={"name": "investment-recommendation"})
    values = [str(field.get("content") or "").strip().casefold() for field in fields]
    label = values[0] if len(values) == 1 and values[0] in {"buy", "hold", "sell"} else "unclear"
    return {
        "present": bool(fields), "label": label,
        "source": "html_investment_recommendation_meta" if fields else "missing",
        "matched_text": " | ".join(str(field) for field in fields),
    }


def explicit_rating_comparison(full_path: str | Path, ablation_path: str | Path) -> dict:
    full, ablation = read_explicit_rating(full_path), read_explicit_rating(ablation_path)
    if not full["present"] and not ablation["present"]:
        return {}
    comparable = full["label"] != "unclear" and ablation["label"] != "unclear"
    return {
        "full_label": full["label"], "ablation_label": ablation["label"],
        "flip": full["label"] != ablation["label"] if comparable else None,
        "full_order_consistent": None, "ablation_order_consistent": None,
        "extraction_method": "explicit_html_metadata_not_judge_inference",
    }
