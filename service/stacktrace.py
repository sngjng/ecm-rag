"""외부 연동 요청에 포함된 Java 스택트레이스를 안전하게 구조화한다.

이 모듈은 LLM에게 원문 전체를 맡겨 형식을 추측하게 하지 않는다. 정규식 기반으로
예외·오류 코드·호출 프레임을 먼저 추출하고, 그 결과를 PostgreSQL의 error_events,
stack_frames, code_symbols 검색에 적합한 짧은 질의로 변환한다.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

ERROR_CODE = re.compile(
    r"\b(?:ORA-\d{5}|SQLSTATE[=: ]+[0-9A-Z]{5}|SQLCODE[=: ]+-?\d+|"
    r"[A-Z]{2,12}[-_]?\d{3,8}[A-Z]?)\b"
)
EXCEPTION = re.compile(
    r"\b(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:Exception|Error|Throwable)\b"
)
JAVA_FRAME = re.compile(
    r"^\s*(?:at\s+)?(?P<class>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)"
    r"\.(?P<method>[\w$<>]+)\((?P<location>[^)]*)\)",
    re.MULTILINE,
)
CAUSE = re.compile(
    r"^\s*(?:Caused by:|Suppressed:)\s*(?P<class>"
    r"(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:Exception|Error|Throwable))"
    r"(?::\s*(?P<message>.*))?$",
    re.MULTILINE,
)
STANDARD_PACKAGE_PREFIXES = (
    "java.", "javax.", "jdk.", "sun.", "com.sun.",
    "org.springframework.", "org.apache.", "weblogic.",
    "oracle.", "org.postgresql.", "com.mysql.", "com.microsoft.sqlserver.",
    "com.ibm.db2.",
)


def _unique(values: list[str]) -> list[str]:
    """발견 순서를 보존하면서 중복 값을 제거한다."""
    return list(dict.fromkeys(value for value in values if value))


def _frame(match: re.Match[str]) -> dict[str, Any]:
    qualified_class = match.group("class")
    package_name, _, class_name = qualified_class.rpartition(".")
    location = match.group("location").strip()
    file_name: str | None = None
    line_number: int | None = None
    if location not in {"Native Method", "Unknown Source"}:
        file_part, separator, line_part = location.rpartition(":")
        if separator and line_part.isdigit():
            file_name = file_part
            line_number = int(line_part)
        else:
            file_name = location or None
    return {
        "class_name": qualified_class,
        "package_name": package_name or None,
        "simple_class_name": class_name or qualified_class,
        "method_name": match.group("method"),
        "file_name": file_name,
        "line_number": line_number,
        "is_application_code": not qualified_class.startswith(STANDARD_PACKAGE_PREFIXES),
    }


def is_stack_trace(text: str) -> bool:
    """예외명과 호출 프레임이 함께 있거나 여러 호출 프레임이면 trace로 판정한다."""
    frame_count = sum(1 for _ in JAVA_FRAME.finditer(text))
    return frame_count >= 2 or (frame_count >= 1 and EXCEPTION.search(text) is not None)


def analyze_stack_trace(text: str) -> dict[str, Any]:
    """Java 스택트레이스를 외부 시스템이 바로 사용할 수 있는 JSON 구조로 변환한다."""
    frames = [_frame(match) for match in JAVA_FRAME.finditer(text)]
    exceptions = _unique([match.group(0) for match in EXCEPTION.finditer(text)])
    causes = [
        {"exception_class": match.group("class"), "message": (match.group("message") or "").strip()}
        for match in CAUSE.finditer(text)
    ]
    error_codes = _unique([match.group(0) for match in ERROR_CODE.finditer(text)])
    application_frames = [frame for frame in frames if frame["is_application_code"]]

    # 시각·라인 번호는 실행마다 달라질 수 있으므로 fingerprint 계산에서는 정규화한다.
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?", "TIMESTAMP", text)
    normalized = re.sub(r":\d+\)", ":LINE)", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    primary_exception = exceptions[0] if exceptions else None
    root_cause = causes[-1] if causes else None
    return {
        "detected": is_stack_trace(text),
        "fingerprint": hashlib.sha256(normalized[:12000].encode("utf-8")).hexdigest(),
        "primary_exception": primary_exception,
        "root_cause": root_cause,
        "exception_classes": exceptions,
        "error_codes": error_codes,
        "frames": frames[:100],
        "application_frames": application_frames[:30],
        "frame_count": len(frames),
        "application_frame_count": len(application_frames),
        "truncated": len(frames) > 100,
    }


def build_stack_trace_query(
    trace: str,
    analysis: dict[str, Any],
    user_question: str | None = None,
) -> str:
    """구조화 결과를 exact 심볼 검색과 LLM 원인 설명에 적합한 질의로 만든다."""
    lines = [
        (user_question or "스택트레이스의 원인과 관련 소스 코드 및 조치 근거를 분석해 주세요.").strip(),
    ]
    if analysis["error_codes"]:
        lines.append("오류 코드: " + ", ".join(analysis["error_codes"]))
    if analysis["exception_classes"]:
        lines.append("예외 유형: " + ", ".join(analysis["exception_classes"]))
    if analysis["root_cause"]:
        root = analysis["root_cause"]
        lines.append(f"최하위 원인: {root['exception_class']}: {root['message']}".rstrip(": "))
    for frame in analysis["application_frames"][:8]:
        location = f"{frame['class_name']}.{frame['method_name']}"
        if frame["file_name"]:
            location += f"({frame['file_name']}:{frame['line_number'] or ''})"
        lines.append("애플리케이션 호출: " + location)
    # 예외 메시지는 코드·프레임 외 원인 표현을 보완하되 ChatRequest의 2,000자 상한을 지킨다.
    lines.append("스택트레이스 일부:\n" + trace[:700])
    return "\n".join(lines)[:2000]
