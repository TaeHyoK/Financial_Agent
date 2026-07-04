"""Chunk generation and scoring."""

from __future__ import annotations

import math
import re
import uuid
from typing import Iterable

from ..io.normalization import split_sentences, cosine_similarity
from .schemas import ContextChunk, DartSection, SectionType

SECTION_TYPE_MAP = {
    "사업의 개요": "overview",
    "주요 제품 및 서비스": "products",
    "원재료 및 생산설비": "materials",
    "주요계약 및 연구개발활동": "contracts",
}

SECTION_KEYWORDS = {
    "overview": ["사업", "전략", "경쟁", "시장", "리스크", "규제", "환율", "금리"],
    "products": ["제품", "서비스", "매출", "비중", "라인업", "모델", "솔루션"],
    "materials": ["원재료", "원가", "가격", "조달", "수급", "공급", "계약"],
    "facilities": ["설비", "생산", "CAPA", "증설", "라인", "가동", "공장"],
    "contracts": ["계약", "수주", "공급계약", "장기", "단가"],
    "rnd": ["연구개발", "R&D", "기술", "특허", "개발", "투자"],
}

INFO_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(%|억|조|만원|원|nm|GB|TB|MW|GWh|kWh|달러|USD|KRW|조원|억원)")
UPPER_TOKEN_PATTERN = re.compile(r"\b[A-Z]{2,}\b")
ABBR_PATTERN = re.compile(r"\(([^)]+)\)")
BOILERPLATE_PATTERNS = [
    re.compile(r"참고하시기 바랍니다"),
    re.compile(r"자세한 사항은 .*? 참고"),
    re.compile(r"추후 .*? 안내"),
]


def _split_long_sentence(sentence: str, hard_max: int) -> list[str]:
    if len(sentence) <= hard_max:
        return [sentence]
    parts: list[str] = []
    start = 0
    min_window = max(20, hard_max // 3)
    while start < len(sentence):
        end = min(start + hard_max, len(sentence))
        window = sentence[start:end]
        split_at = None

        # Prefer punctuation boundaries within the window.
        for idx in range(len(window) - 1, min_window - 1, -1):
            if window[idx] in ".?!…":
                split_at = start + idx + 1
                break

        # Fallback to last whitespace within the window.
        if split_at is None:
            for idx in range(len(window) - 1, min_window - 1, -1):
                if window[idx].isspace():
                    split_at = start + idx + 1
                    break

        if split_at is None or split_at <= start:
            split_at = end

        chunk = sentence[start:split_at].strip()
        if chunk:
            parts.append(chunk)
        start = split_at
    return parts


def _is_table_block(text: str) -> bool:
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    # 모든 줄이 '|'로 시작하는지 확인 (공백 제거 후)
    return all(line.startswith("|") for line in lines)


def _split_markdown_table(table_text: str, hard_max: int) -> list[str]:
    lines = [line.strip() for line in table_text.splitlines() if line.strip()]
    if not lines:
        return []
    
    # 전체가 hard_max보다 작으면 통째로 반환
    if len(table_text) <= hard_max:
        return [table_text.strip()]

    # 헤더 감지 로직 (구분선 |---| 유무 확인)
    header_lines = []
    body_lines = lines
    
    # 2번째 줄이 구분선(---) 형태인지 정규식으로 확인
    sep_pattern = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$")
    
    if len(lines) >= 2 and sep_pattern.match(lines[1]):
        header_lines = lines[:2] # 제목행 + 구분선행
        body_lines = lines[2:]
    else:
        # 구분선이 없으면 첫 줄을 헤더로 가정 (데이터 테이블 특성상 첫줄이 중요)
        header_lines = lines[:1]
        body_lines = lines[1:]

    chunks: list[str] = []
    
    # 현재 청크 버퍼 초기화 (헤더를 미리 넣어둠)
    current = header_lines.copy()
    current_len = sum(len(l) for l in current) + len(current) # +len(current)는 줄바꿈 문자 고려

    for row in body_lines:
        row_len = len(row) + 1 # +1 for newline
        
        # (헤더 포함) 현재 버퍼 + 새 행이 제한을 넘지 않는지 확인
        # 단, current가 헤더만 있는 상태(len(current) == len(header_lines))라면 무조건 추가 (최소 1행 보장)
        if current_len + row_len <= hard_max or len(current) == len(header_lines):
            current.append(row)
            current_len += row_len
        else:
            # 제한 초과 시 현재까지를 청크로 저장
            chunks.append("\n".join(current).strip())
            
            # **중요: 다음 청크를 위해 헤더를 다시 복사해서 시작**
            current = header_lines.copy()
            current.append(row)
            current_len = sum(len(l) for l in current) + len(current)

    # 남은 내용 처리
    if current:
        chunks.append("\n".join(current).strip())

    return chunks


def _build_chunks(sentences: list[str], target_min: int, target_max: int, hard_max: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if _is_table_block(sentence):
            if current:
                chunks.append(" ".join(current).strip())
                current = []
                current_len = 0
            chunks.append(sentence.strip())
            continue

        for part in _split_long_sentence(sentence, hard_max):
            part_len = len(part)
            if current_len + part_len + (1 if current else 0) <= target_max:
                current.append(part)
                current_len += part_len + (1 if current_len > 0 else 0)
            else:
                if current:
                    chunks.append(" ".join(current).strip())
                current = [part]
                current_len = part_len

    if current:
        chunks.append(" ".join(current).strip())

    return chunks


def _score_info(text: str) -> float:
    score = 0.0
    score += len(INFO_PATTERN.findall(text)) * 1.0
    score += len(UPPER_TOKEN_PATTERN.findall(text)) * 0.5
    for match in ABBR_PATTERN.findall(text):
        if any(ch.isalpha() for ch in match):
            score += 0.5
    return score


def _score_section(text: str, section_type: SectionType) -> float:
    keywords = SECTION_KEYWORDS.get(section_type, [])
    score = 0.0
    for keyword in keywords:
        score += text.count(keyword) * 0.3
    return score


def _length_penalty(char_len: int, target_min: int, target_max: int) -> float:
    if char_len < target_min:
        return (target_min - char_len) / max(target_min, 1)
    if char_len > target_max:
        return (char_len - target_max) / max(target_max, 1)
    return 0.0


def _text_hash_vector(text: str, dim: int = 256) -> list[float]:
    vec = [0.0] * dim
    src = re.sub(r"\s+", " ", text.strip().lower())
    if not src:
        return vec
    for i in range(max(len(src) - 1, 1)):
        bigram = src[i : i + 2]
        idx = hash(bigram) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0:
        return vec
    return [x / norm for x in vec]


def _apply_chunk_selection(chunks: list[ContextChunk], chunk_config: dict) -> list[ContextChunk]:
    if not chunks:
        return []

    topk_per_section_cfg = chunk_config.get("topk_per_section", {}) or {}
    diversity_cfg = chunk_config.get("diversity", {}) or {}
    diversity_enabled = bool(diversity_cfg.get("enabled", False))
    cosine_skip_threshold = float(diversity_cfg.get("cosine_skip_threshold", 0.80))
    vector_cache: dict[str, list[float]] = {}

    def _vector(text: str) -> list[float]:
        if text in vector_cache:
            return vector_cache[text]
        vec = _text_hash_vector(text)
        vector_cache[text] = vec
        return vec

    selected: list[ContextChunk] = []
    by_section: dict[str, list[ContextChunk]] = {}
    for chunk in chunks:
        by_section.setdefault(chunk.section_type, []).append(chunk)

    for section_type, section_chunks in by_section.items():
        ranked = sorted(section_chunks, key=lambda c: c.score_total, reverse=True)
        topk_raw = topk_per_section_cfg.get(section_type)
        topk = int(topk_raw) if topk_raw is not None else len(ranked)
        if topk <= 0:
            continue

        kept: list[ContextChunk] = []
        for chunk in ranked:
            if len(kept) >= topk:
                break
            if diversity_enabled and kept:
                chunk_vec = _vector(chunk.text)
                too_similar = False
                for prev in kept:
                    if cosine_similarity(chunk_vec, _vector(prev.text)) >= cosine_skip_threshold:
                        too_similar = True
                        break
                if too_similar:
                    continue
            kept.append(chunk)
        selected.extend(kept)

    return selected


def build_context_chunks(
    *,
    sections: Iterable[DartSection],
    company_id: str,
    company_name: str,
    report_key: str,
    report_date: str,
    chunk_config: dict,
) -> list[ContextChunk]:
    target_min = int(chunk_config.get("target_char_min", 40))
    target_max = int(chunk_config.get("target_char_max", 140))
    hard_max = int(chunk_config.get("hard_char_max", 220)) # 표 때문에 조금 넉넉하게 잡는 것을 추천 (예: 500~1000)
    min_drop = int(chunk_config.get("min_char_drop", 25))

    all_chunks: list[ContextChunk] = []

    for section in sections:
        base_section_type = SECTION_TYPE_MAP.get(section.section_name, "unknown")
        units: list[str] = []
        
        # ----------------------------------------------------
        # [수정됨] 텍스트/테이블 혼합 처리 버퍼 로직
        # ----------------------------------------------------
        table_buffer: list[str] = []
        text_buffer: list[str] = []

        for line in section.raw_text.splitlines():
            stripped_line = line.strip()
            
            # 빈 줄 처리 (테이블 중간에 빈 줄이 없다고 가정)
            if not stripped_line:
                # 텍스트 버퍼나 테이블 버퍼가 있으면 닫지 않고 유지할 수도 있으나,
                # 안전하게 끊어주는 것이 좋습니다.
                continue

            if stripped_line.startswith("|"):
                # 텍스트 버퍼에 내용이 있으면 먼저 처리
                if text_buffer:
                    units.extend(split_sentences("\n".join(text_buffer)))
                    text_buffer = []
                # 테이블 버퍼에 추가
                table_buffer.append(stripped_line)
            else:
                # 테이블 버퍼에 내용이 있으면 처리 (테이블 끝남)
                if table_buffer:
                    table_text = "\n".join(table_buffer)
                    # 테이블 분할 함수 호출
                    units.extend(_split_markdown_table(table_text, hard_max))
                    table_buffer = []
                # 텍스트 버퍼에 추가
                text_buffer.append(stripped_line)

        # 루프 종료 후 남은 버퍼 처리
        if table_buffer:
            units.extend(_split_markdown_table("\n".join(table_buffer), hard_max))
        if text_buffer:
            units.extend(split_sentences("\n".join(text_buffer)))
        # ----------------------------------------------------

        raw_chunks = _build_chunks(units, target_min, target_max, hard_max)
        
        for raw in raw_chunks:
            raw = raw.strip()
            if not raw:
                continue
            char_len = len(raw)
            if char_len < min_drop:
                continue
            score_info = _score_info(raw)
            if section.section_name == "원재료 및 생산설비":
                score_materials = _score_section(raw, "materials")
                score_facilities = _score_section(raw, "facilities")
                if score_facilities > score_materials:
                    section_type: SectionType = "facilities"
                    score_section = score_facilities
                else:
                    section_type = "materials"
                    score_section = score_materials
            else:
                section_type = base_section_type
                score_section = _score_section(raw, section_type)
            score_total = score_info + score_section - _length_penalty(char_len, target_min, target_max)
            if not INFO_PATTERN.search(raw):
                score_total *= 0.1
            if any(pat.search(raw) for pat in BOILERPLATE_PATTERNS):
                score_total -= 2.0
            all_chunks.append(
                ContextChunk(
                    company_id=company_id,
                    company_name=company_name,
                    report_key=report_key,
                    report_date=report_date,
                    section_type=section_type,
                    chunk_id=str(uuid.uuid4()),
                    text=raw,
                    char_len=char_len,
                    score_info=score_info,
                    score_section=score_section,
                    score_total=score_total,
                    provenance={
                        "section_name": section.section_name,
                        "line_start": section.provenance.get("line_start"),
                        "line_end": section.provenance.get("line_end"),
                    },
                )
            )

    return _apply_chunk_selection(all_chunks, chunk_config)
