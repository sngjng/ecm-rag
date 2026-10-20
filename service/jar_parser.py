"""JAR 바이트코드를 CFR로 디컴파일하고 기존 소스코드 청크로 변환한다.

JAR 안의 클래스를 실행하지 않으며, 외부 디컴파일러도 ``shell=True`` 없이 호출한다.
운영망에는 CFR JAR를 별도로 반입한 뒤 ``RAG_CFR_JAR_PATH``로 위치만 주입한다.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from service.parsers import Item, parse_code


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str, int | None, int | None], None]


def _inspect_archive(path: Path, settings: Any) -> tuple[list[str], int]:
    """디컴파일 전에 ZIP 경로 이탈·암호화·압축 해제 크기 폭증을 차단한다."""
    try:
        with zipfile.ZipFile(path) as archive:
            class_names: list[str] = []
            total_uncompressed = 0
            for entry in archive.infolist():
                member = PurePosixPath(entry.filename.replace("\\", "/"))
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"안전하지 않은 JAR 항목 경로: {entry.filename}")
                if entry.flag_bits & 0x1:
                    raise ValueError("암호화된 JAR 항목은 처리할 수 없습니다")
                total_uncompressed += entry.file_size
                if total_uncompressed > settings.max_uncompressed_mb * 1024 * 1024:
                    raise ValueError(
                        f"JAR 압축 해제 크기가 {settings.max_uncompressed_mb} MiB 제한을 초과합니다"
                    )
                if entry.filename.endswith(".class") and not entry.is_dir():
                    class_names.append(entry.filename)
            if not class_names:
                raise ValueError("JAR에서 .class 파일을 찾지 못했습니다")
            if len(class_names) > settings.max_class_files:
                raise ValueError(
                    f"JAR 클래스 수가 {settings.max_class_files}개 제한을 초과합니다"
                )
            return class_names, total_uncompressed
    except zipfile.BadZipFile as exc:
        raise ValueError("올바른 JAR/ZIP 형식이 아닙니다") from exc


def _fallback_java_chunks(
    path: Path, repository: str, branch: str, logical_path: str
) -> list[Item]:
    """일부 디컴파일 결과가 AST로 해석되지 않아도 원문 검색은 유지한다."""
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = source.splitlines()
    package = None
    for line in lines[:100]:
        stripped = line.strip()
        if stripped.startswith("package ") and stripped.endswith(";"):
            package = stripped[8:-1].strip()
            break
    items: list[Item] = []
    for offset in range(0, len(lines), 120):
        end = min(offset + 120, len(lines))
        code = "\n".join(lines[offset:end])
        context = (
            f"Repository: {repository}\nBranch: {branch}\nPath: {logical_path}\n"
            f"Package: {package or ''}\nDecompiled Java source\n"
        )
        items.append(
            Item(
                kind="decompiled_code",
                text=code,
                embedding_text=context + code,
                line_start=offset + 1,
                line_end=end,
                identifier=path.stem,
                metadata={
                    "repository": repository,
                    "branch": branch,
                    "file_path": logical_path,
                    "language": "java",
                    "package_name": package,
                    "decompiled": True,
                    "ast_fallback": True,
                },
            )
        )
    return items


def parse_jar(
    path: Path,
    out: Path,
    repository: str,
    branch: str,
    logical_path: str,
    settings: Any,
    progress: ProgressCallback | None = None,
) -> tuple[list[Item], dict]:
    """CFR 출력 Java 파일을 클래스·메서드 단위로 파싱해 검색 청크를 만든다."""
    if progress:
        progress("inspecting_jar", "JAR 구조와 안전 제한을 확인하고 있습니다.", 0, None)
    class_names, uncompressed_bytes = _inspect_archive(path, settings)
    logger.info(
        "jar inspected file=%s classes=%d uncompressed_bytes=%d",
        path.name,
        len(class_names),
        uncompressed_bytes,
    )
    cfr_path = Path(settings.cfr_jar_path).expanduser()
    if not settings.cfr_jar_path or not cfr_path.is_file():
        raise RuntimeError(
            "JAR 디컴파일에는 CFR가 필요합니다. RAG_CFR_JAR_PATH에 cfr-*.jar 경로를 설정하세요"
        )

    decompiled_root = out / "decompiled"
    decompiled_root.mkdir(parents=True, exist_ok=True)
    command = [
        settings.java_command,
        "-jar",
        str(cfr_path.resolve()),
        str(path.resolve()),
        "--outputdir",
        str(decompiled_root.resolve()),
        "--silent",
        "true",
    ]
    if progress:
        progress(
            "decompiling_jar",
            f"CFR가 {len(class_names)}개 클래스의 Java 소스를 복원하고 있습니다.",
            0,
            len(class_names),
        )
    started_at = time.monotonic()
    logger.info("jar decompile started file=%s classes=%d", path.name, len(class_names))
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=settings.timeout_seconds,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Java 실행 파일을 찾지 못했습니다: {settings.java_command}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"JAR 디컴파일이 {settings.timeout_seconds}초 제한을 초과했습니다"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stdout or "").strip()[-2000:]
        raise RuntimeError(f"CFR 디컴파일 실패(exit={completed.returncode}): {detail}")

    java_files = sorted(decompiled_root.rglob("*.java"))
    if not java_files:
        raise RuntimeError("CFR가 Java 소스 파일을 생성하지 못했습니다")
    logger.info(
        "jar decompile completed file=%s java_files=%d elapsed_seconds=%.1f",
        path.name,
        len(java_files),
        time.monotonic() - started_at,
    )
    if progress:
        progress(
            "parsing_jar",
            f"디컴파일된 Java 파일 {len(java_files)}개의 클래스와 메서드를 분석하고 있습니다.",
            0,
            len(java_files),
        )

    items: list[Item] = []
    parsed_symbols = 0
    ast_fallback_files = 0
    skipped_large_files = 0
    # 최소 1%, 최대 파일마다 진행상황을 남겨 특정 Java 파일에서 정체될 때 식별한다.
    progress_interval = max(1, len(java_files) // 100)
    for file_index, java_path in enumerate(java_files, 1):
        # CFR가 출력 경로 밖에 파일을 만들었거나 심볼릭 링크가 연결된 경우 읽지 않는다.
        resolved = java_path.resolve()
        if not resolved.is_relative_to(decompiled_root.resolve()) or not resolved.is_file():
            continue
        if resolved.stat().st_size > settings.max_java_file_kb * 1024:
            skipped_large_files += 1
            continue
        relative = resolved.relative_to(decompiled_root.resolve()).as_posix()
        member_path = f"{logical_path}!/{relative}"
        if file_index == 1 or file_index % progress_interval == 0:
            logger.info(
                "jar java analysis file=%s progress=%d/%d source=%s",
                path.name,
                file_index,
                len(java_files),
                relative,
            )
        try:
            # 디컴파일 소스는 문법이 완전하지 않을 수 있고 파일 수도 많다. 일부
            # tree-sitter/grammar wheel 조합은 이런 입력에서 Python 예외가 아니라
            # segmentation fault를 일으키므로, JAR 경로에서는 순수 Python 분석기를
            # 강제해 Worker 프로세스가 통째로 종료되지 않게 한다.
            parsed, report = parse_code(
                resolved,
                repository,
                branch,
                member_path,
                safe_java_parser=True,
            )
            parsed_symbols += report["symbols"]
            for item in parsed:
                item.kind = "decompiled_code_symbol" if item.symbol else "decompiled_code"
                item.metadata["decompiled"] = True
                item.metadata["jar_path"] = logical_path
            items.extend(parsed)
        except (SyntaxError, UnicodeError, ValueError):
            ast_fallback_files += 1
            items.extend(
                _fallback_java_chunks(resolved, repository, branch, member_path)
            )
        if progress and (file_index % progress_interval == 0 or file_index == len(java_files)):
            progress(
                "parsing_jar",
                f"Java 파일을 분석하고 있습니다: {file_index}/{len(java_files)}",
                file_index,
                len(java_files),
            )

    if not items:
        raise ValueError("디컴파일 결과에서 검색 가능한 Java 내용을 추출하지 못했습니다")

    report = {
        "format": "jar",
        "decompiler": "cfr",
        "jar_class_files": len(class_names),
        "decompiled_java_files": len(java_files),
        "symbols": parsed_symbols,
        "ast_fallback_files": ast_fallback_files,
        "skipped_large_files": skipped_large_files,
    }
    logger.info(
        "jar parsing completed file=%s chunks=%d symbols=%d fallback_files=%d skipped_large=%d",
        path.name,
        len(items),
        parsed_symbols,
        ast_fallback_files,
        skipped_large_files,
    )
    # 운영자가 원본 JAR를 다시 열지 않고도 처리 범위와 청크 좌표를 점검할 수 있게 한다.
    (out / "jar-index.json").write_text(
        json.dumps(
            {
                "report": report,
                "sources": [
                    path.resolve().relative_to(decompiled_root.resolve()).as_posix()
                    for path in java_files
                    if path.resolve().is_relative_to(decompiled_root.resolve())
                ],
                "chunks": [
                    {
                        "kind": item.kind,
                        "identifier": item.identifier,
                        "file_path": item.metadata.get("file_path"),
                        "line_start": item.line_start,
                        "line_end": item.line_end,
                    }
                    for item in items
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return items, report
