"""이미 운영 중인 BGE-M3 OpenAI 호환 embedding API에만 연결한다."""
from __future__ import annotations
import hashlib
import math
import httpx
from service.config import get_settings


def embed(texts: list[str]) -> list[list[float]]:
    """문자열 목록을 설정된 차원의 embedding 벡터로 변환한다.

    test profile의 deterministic_stub은 같은 입력에 항상 같은 벡터를 만들어 DB/검색
    통합 테스트를 모델 서버 없이 수행하게 한다. 운영 profile은 OpenAI 호환 BGE-M3
    endpoint를 batch 단위로 호출하고 응답 개수·차원·유한값을 검증한다.
    """
    if not texts:
        return []
    settings = get_settings().models.embedding
    if settings.provider == 'deterministic_stub':
        # 네트워크 없는 단위/통합 테스트용이다. 운영 검색 품질 평가에는 사용하지 않는다.
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode('utf-8')).digest()
            vector = [((seed[index % len(seed)] / 255.0) * 2) - 1
                      for index in range(settings.dimension)]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors
    if settings.provider != 'openai_compatible':
        raise RuntimeError(f'지원하지 않는 embedding provider: {settings.provider}')
    # 긴 코드/표는 API 길이 제한과 메모리 사용량을 위해 요청 단위로 절단한다.
    results: list[list[float]] = []
    headers = {'Authorization': f'Bearer {settings.api_key}'} if settings.api_key else {}
    with httpx.Client(timeout=settings.timeout_seconds, headers=headers) as client:
        for start in range(0, len(texts), settings.batch_size):
            # 모델의 입력 제한과 메모리 급증을 방지하기 위해 batch와 문자열 길이를 모두
            # 설정값으로 제한한다.
            batch = texts[start:start + settings.batch_size]
            response = client.post(settings.base_url, json={
                'model': settings.model,
                'input': [text[:settings.max_input_characters] for text in batch],
            })
            response.raise_for_status()
            data = sorted(response.json()['data'], key=lambda item: item['index'])
            if len(data) != len(batch):
                raise ValueError('Embedding 응답 건수가 요청과 다릅니다')
            for item in data:
                vector = item['embedding']
                if len(vector) != settings.dimension or not all(math.isfinite(v) for v in vector):
                    raise ValueError(
                        f'{settings.model} 응답은 유한한 {settings.dimension}차원 벡터여야 합니다'
                    )
                results.append(vector)
    return results
