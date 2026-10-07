"""다단 표 헤더를 열별 계층 경로로 변환한다."""

from __future__ import annotations


def build_header_paths(grid: list[list[str]], header_rows: int) -> list[str]:
    """상위 여러 헤더 행을 `A > B > C` 형태의 leaf header로 만든다.

    예를 들어 첫 번째 헤더 행이 '가입나이', 두 번째 행이 '남자'라면
    최종 key는 '가입나이 > 남자'가 된다. 이 형태는 벡터화 시 조건 의미를
    명시적으로 보존하고, JSON record key로도 사용할 수 있다.
    """
    if not grid:
        return []

    width = max(len(r) for r in grid)
    paths: list[str] = []

    for col in range(width):
        parts: list[str] = []
        for row in range(min(header_rows, len(grid))):
            if col < len(grid[row]):
                value = grid[row][col].strip()
                # colspan 확장으로 동일 값이 반복될 수 있어 중복은 제거한다.
                if value and value not in parts:
                    parts.append(value)

        # 헤더 인식 실패 시 key가 사라지지 않도록 안정적인 fallback 이름을 사용한다.
        paths.append(" > ".join(parts) if parts else f"column_{col + 1}")

    return paths
