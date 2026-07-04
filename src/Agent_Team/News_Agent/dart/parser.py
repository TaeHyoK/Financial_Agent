"""분기보고서 XML에서 '1. 사업의 개요' 섹션을 추출하는 유틸리티."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup


SECTION_PATTERN = re.compile(
    r"<SECTION-2\b[^>]*>\s*"
    r"(?P<header>.*?<TITLE[^>]*>.*?</TITLE>)"
    r"(?P<body>.*?)"
    r"</SECTION-2>",
    re.DOTALL | re.IGNORECASE,
)

SECTION1_PATTERN = re.compile(
    r"<SECTION-1\b[^>]*>\s*"
    r"(?P<header>.*?<TITLE[^>]*>.*?</TITLE>)"
    r"(?P<body>.*?)"
    r"</SECTION-1>",
    re.DOTALL | re.IGNORECASE,
)

TITLE_PATTERN = re.compile(r"<TITLE[^>]*>.*?</TITLE>", re.DOTALL | re.IGNORECASE)

WHITESPACE_PATTERN = re.compile(r"[ \t\f\v]+")
TABLE_HEADER_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def extract_section_html(content: str, title_keyword: str) -> str | None:
    for match in SECTION_PATTERN.finditer(content):
        header = match.group("header")
        if title_keyword in header:
            return match.group()
    return None


def extract_section_1_html(content: str, title_keyword: str) -> str | None:
    for match in SECTION1_PATTERN.finditer(content):
        header = match.group("header")
        if title_keyword in header:
            return match.group()
    return None


def extract_title_text(header_html: str) -> str:
    match = TITLE_PATTERN.search(header_html)
    if match:
        return html_to_text(match.group()).strip()
    return html_to_text(header_html).strip()


def extract_section_2_fragments(section_html: str) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    for match in SECTION_PATTERN.finditer(section_html):
        header = match.group("header")
        title_text = extract_title_text(header)
        fragments.append((title_text, match.group()))
    return fragments


def html_to_text(html_fragment: str) -> str:
    soup = BeautifulSoup(html_fragment, "html.parser")

    for table in soup.find_all("table"):
        markdown_table = _convert_table_to_markdown(table)
        if markdown_table:
            table.replace_with(f"\n{markdown_table}\n")

    text = soup.get_text(separator="\n")
    return _clean_text(text)


def _clean_text(text: str) -> str:
    unescaped = html.unescape(text)
    normalized = WHITESPACE_PATTERN.sub(" ", unescaped)
    lines = [line.strip() for line in normalized.splitlines()]
    return "\n".join(line for line in lines if line)


def _convert_table_to_markdown(table_tag) -> str:
    rows = table_tag.find_all("tr")
    table_data: list[list[str]] = []

    for row in rows:
        cols = row.find_all(["td", "th"])
        if not cols:
            continue
        cols_text = [col.get_text(strip=True).replace("|", "\\|") for col in cols]
        if any(cols_text):
            table_data.append(cols_text)

    if not table_data:
        return ""

    # 단위 표기용 테이블(한 줄, "(단위" 포함)은 텍스트로 변환
    if len(table_data) == 1 and any("(단위" in cell for cell in table_data[0]):
        text = " ".join(table_data[0]).replace("|", "").strip()
        return f"*{text}*"

    num_cols = max(len(row) for row in table_data)
    header = table_data[0]
    header = header + [""] * (num_cols - len(header))
    body_rows = [
        row + [""] * (num_cols - len(row)) for row in table_data[1:]
    ]

    header_line = "| " + " | ".join(header) + " |"
    sep_line = "| " + " | ".join(["---"] * num_cols) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in body_rows]

    return "\n".join([header_line, sep_line, *body_lines])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="분기보고서 XML에서 '1. 사업의 개요' 섹션을 추출합니다."
    )
    parser.add_argument(
        "--input",
        default="분기보고서.txt",
        help="파싱할 분기보고서 XML 파일 경로 (기본값: 분기보고서.txt).",
    )
    parser.add_argument(
        "--output",
        default="사업개요.txt",
        help="추출 결과 저장 파일 경로 (기본값: 사업개요.txt).",
    )
    parser.add_argument(
        "--title",
        default="1. 사업의 개요",
        help="추출할 섹션의 제목 키워드 (기본값: 1. 사업의 개요).",
    )
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help="Keep the raw HTML fragment instead of converting to plain text.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        parser.error(f"입력 파일을 찾을 수 없습니다: {input_path}")

    content = input_path.read_text(encoding="utf-8")
    fragment = extract_section_html(content, args.title)
    if fragment is None:
        raise SystemExit(f"'{args.title}' 제목을 포함한 섹션을 찾지 못했습니다.")

    if args.keep_html:
        output_text = fragment
    else:
        output_text = html_to_text(fragment)

    output_path = Path(args.output)
    output_path.write_text(output_text, encoding="utf-8")
    print(f"섹션을 추출하여 {output_path.resolve()} 에 저장했습니다.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
