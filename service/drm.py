"""사용자가 구현할 Java DRM 연동 계약.

DRM_COMMAND가 비어 있으면 평문 파일을 그대로 사용한다. 설정돼 있으면 실행 파일은
<input_path> <output_path> 두 인자를 받고, 종료 코드 0일 때만 복호화 성공으로 본다.
파일 경로는 subprocess 인자 배열로 전달해 쉘 확장을 막는다. 반환 임시 파일은 호출자가 삭제한다.
원본/복호화 파일은 웹 서버가 지정한 격리 디렉터리에서만 다룬다.
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from service.config import get_settings


def decrypt(input_path: Path, output_path: Path) -> Path:
    settings = get_settings().solutions.drm
    if not settings.enabled:
        return input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([settings.command, str(input_path), str(output_path)],
                   check=True, timeout=settings.timeout_seconds, capture_output=True)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError('DRM adapter가 성공 반환했지만 출력 파일이 비어 있습니다')
    return output_path
