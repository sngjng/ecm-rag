"""이미 운영 중인 BGE-M3 OpenAI 호환 embedding API에만 연결한다."""
from __future__ import annotations
import math
import httpx
from service import config


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    # 긴 코드/표는 API 길이 제한과 메모리 사용량을 위해 요청 단위로 절단한다.
    results: list[list[float]] = []
    with httpx.Client(timeout=120.0) as client:
        for start in range(0, len(texts), 8):
            response = client.post(config.EMBED_URL, json={
                'model': config.EMBED_MODEL, 'input': [t[:24000] for t in texts[start:start + 8]]
            })
            response.raise_for_status()
            data = sorted(response.json()['data'], key=lambda item: item['index'])
            if len(data) != len(texts[start:start + 8]):
                raise ValueError('Embedding 응답 건수가 요청과 다릅니다')
            for item in data:
                vector = item['embedding']
                if len(vector) != 1024 or not all(math.isfinite(v) for v in vector):
                    raise ValueError('BGE-M3 응답은 유한한 1024차원 벡터여야 합니다')
                results.append(vector)
    return results
