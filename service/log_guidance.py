"""대량 장애 로그를 검색·LLM 입력에 적합한 핵심 증거로 축약한다."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from service.stacktrace import ERROR_CODE, EXCEPTION, analyze_stack_trace

LOG_LEVEL = re.compile(
    r"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|SEVERE)\b",
    re.IGNORECASE,
)
ERROR_MARKER = re.compile(
    r"(?:\bERROR\b|\bFATAL\b|\bCRITICAL\b|\bSEVERE\b|\bWARN(?:ING)?\b|"
    r"Traceback|Caused by:|Exception|Throwable|ORA-\d{5}|SQLSTATE|SQLCODE|오류|실패)",
    re.IGNORECASE,
)
TIMESTAMP = re.compile(
    r"\b(?:"
    r"\d{4}[-/.]\d{2}[-/.]\d{2}[ T]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\d{8}\s+\d{6}(?:[.,]\d{1,9})?"
    r")\b"
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _select_error_excerpt(
    lines: list[str],
    max_context_characters: int,
) -> tuple[str, list[str], int]:
    """오류 의심 행의 앞 1행·뒤 5행을 환경별 문자 예산 이내 증거로 만든다."""
    marker_indices = [index for index, line in enumerate(lines) if ERROR_MARKER.search(line)]
    # 오류가 수천 건인 대용량 로그에서도 앞부분만 선택하지 않는다. 입력 전체 구간에서
    # 오류 위치를 균등 표본화하여 후반부의 다른 장애 유형도 LLM 근거에 포함한다.
    max_windows = min(200, max(1, max_context_characters // 1200))
    if len(marker_indices) > max_windows:
        last = len(marker_indices) - 1
        marker_indices_for_excerpt = _unique([
            str(marker_indices[round(position * last / (max_windows - 1))])
            for position in range(max_windows)
        ]) if max_windows > 1 else [str(marker_indices[0])]
        excerpt_markers = [int(value) for value in marker_indices_for_excerpt]
    else:
        excerpt_markers = marker_indices
    selected_indices: set[int] = set()
    for index in excerpt_markers:
        selected_indices.update(range(max(0, index - 1), min(len(lines), index + 6)))

    if not selected_indices:
        # 형식을 모르는 로그도 완전히 버리지 않고 시작과 끝부분을 분석 근거로 사용한다.
        selected_indices.update(range(min(len(lines), 80)))
        selected_indices.update(range(max(0, len(lines) - 20), len(lines)))

    excerpt_lines: list[str] = []
    used = 0
    # 선택된 모든 시간 구간이 포함되도록 한 줄이 전체 예산을 독점하지 못하게 한다.
    per_line_limit = max(200, max_context_characters // max(1, len(selected_indices)))
    for index in sorted(selected_indices):
        value = lines[index][:min(2000, per_line_limit)]
        remaining = max_context_characters - used
        if remaining <= 0:
            break
        value = value[:remaining]
        excerpt_lines.append(value)
        used += len(value) + 1

    key_lines = _unique(
        [lines[index].strip()[:500] for index in marker_indices if lines[index].strip()]
    )[:20]
    return "\n".join(excerpt_lines), key_lines, len(marker_indices)


def analyze_log_input(
    text: str,
    *,
    max_context_characters: int = 16000,
) -> tuple[dict[str, Any], str]:
    """로그 통계·오류 코드·예외·호출 프레임과 LLM용 핵심 구간을 반환한다."""
    lines = text.splitlines()
    excerpt, key_lines, error_line_count = _select_error_excerpt(
        lines,
        max_context_characters,
    )
    stack_analysis = analyze_stack_trace(text)
    levels = Counter(
        match.group(1).upper().replace("WARNING", "WARN")
        for line in lines
        for match in LOG_LEVEL.finditer(line)
    )
    error_codes = _unique([match.group(0) for match in ERROR_CODE.finditer(text)])[:30]
    exception_classes = _unique(
        [match.group(0) for match in EXCEPTION.finditer(text)]
    )[:30]
    timestamps = _unique([match.group(0) for match in TIMESTAMP.finditer(text)])[:100]
    error_occurrences: list[dict[str, str | None]] = []
    current_timestamp: str | None = None
    for line in lines:
        timestamp_match = TIMESTAMP.search(line)
        if timestamp_match:
            current_timestamp = timestamp_match.group(0)
        if ERROR_MARKER.search(line) and line.strip():
            error_occurrences.append({
                "timestamp": timestamp_match.group(0) if timestamp_match else current_timestamp,
                "message": line.strip()[:500],
            })
        if len(error_occurrences) >= 50:
            break
    analysis = {
        "total_lines": len(lines),
        "total_characters": len(text),
        "error_line_count": error_line_count,
        "levels": dict(levels),
        "error_codes": error_codes,
        "exception_classes": exception_classes,
        "timestamps": timestamps,
        "first_timestamp": timestamps[0] if timestamps else None,
        "last_timestamp": timestamps[-1] if timestamps else None,
        "error_occurrences": error_occurrences,
        "primary_exception": stack_analysis.get("primary_exception"),
        "root_cause": stack_analysis.get("root_cause"),
        "application_frames": stack_analysis.get("application_frames", [])[:10],
        "stack_trace_detected": stack_analysis.get("detected", False),
        "fingerprint": stack_analysis["fingerprint"],
        "selected_characters": len(excerpt),
        "input_truncated_for_llm": len(excerpt) < len(text),
        "key_lines": key_lines,
    }
    return analysis, excerpt


def build_log_search_query(question: str, analysis: dict[str, Any]) -> str:
    """pgvector·FTS·심볼 검색에 사용할 2,000자 이내의 핵심 질의를 만든다."""
    parts = [
        question.strip()
        or "장애 로그의 원인, 관련 오류 사례, 소스 코드와 해결 절차를 찾아주세요."
    ]
    if analysis["error_codes"]:
        parts.append("오류 코드: " + ", ".join(analysis["error_codes"][:10]))
    if analysis["exception_classes"]:
        parts.append("예외 유형: " + ", ".join(analysis["exception_classes"][:10]))
    root_cause = analysis.get("root_cause")
    if root_cause:
        parts.append(
            f"최하위 원인: {root_cause.get('exception_class', '')}: "
            f"{root_cause.get('message', '')}".rstrip(": ")
        )
    for frame in analysis.get("application_frames", [])[:6]:
        parts.append(
            "애플리케이션 호출: "
            f"{frame.get('class_name', '')}.{frame.get('method_name', '')}"
        )
    if analysis["key_lines"]:
        parts.append("핵심 로그:\n" + "\n".join(analysis["key_lines"][:6]))
    return "\n".join(parts)[:2000]
