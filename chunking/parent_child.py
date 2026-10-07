"""검색 hit 주변의 parent/previous/next 문맥을 재조립하는 helper."""

from __future__ import annotations

from collections import defaultdict

from models.schemas import Chunk


def expand_context(
    hit: Chunk,
    by_id: dict[str, Chunk],
    window: int = 1,
) -> list[Chunk]:
    """검색된 chunk의 앞/뒤 이웃 chunk를 `window`만큼 확장한다.

    복잡한 표가 여러 chunk로 잘린 경우 검색은 child chunk 하나를 맞힐 수 있지만,
    최종 LLM에는 앞뒤 조건이 필요할 수 있다. 이 함수가 연결 ID를 따라 주변 문맥을
    복원한다.
    """
    selected = [hit]
    prev_id = hit.previous_chunk_id
    next_id = hit.next_chunk_id

    for _ in range(window):
        if prev_id and prev_id in by_id:
            prev = by_id[prev_id]
            selected.insert(0, prev)
            prev_id = prev.previous_chunk_id

        if next_id and next_id in by_id:
            nxt = by_id[next_id]
            selected.append(nxt)
            next_id = nxt.next_chunk_id

    return selected


def group_by_parent(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    """동일 parent table/section에 속하는 child chunks를 그룹화한다."""
    out: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        # parent_id가 없는 일반 chunk는 자기 자신을 parent처럼 취급한다.
        out[chunk.parent_id or chunk.chunk_id].append(chunk)
    return dict(out)
