"""문서 청크에서 PostgreSQL 경량 GraphRAG 엔터티와 관계를 추출한다."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from service.config import get_settings
from service.http_client import RETRYABLE_STATUS_CODES, get_http_client, sleep_before_retry
from service.parsers import Item

logger = logging.getLogger(__name__)

GRAPH_EXTRACTION_PROMPT = """당신은 사내 문서에서 지식 그래프를 추출합니다.
문서에 명시된 시스템, 서버, 애플리케이션, API, 데이터베이스, 네트워크 영역,
업무 주체, 처리 단계를 엔터티로 추출하고 명시된 방향 관계만 연결하세요.
시스템 구성도·아키텍처 문맥에서는 실제 제품/서버/엔진/WAS/API/DB/단말만 엔터티로
추출하세요. '성능 테스트 수행', '검증', '모니터링', '처리 과정', 문서 제목이나 전체
서비스를 가리키는 포괄 명칭은 독립 구성요소로 만들지 마세요. 단, 실제 연결 대상인 경우는
예외입니다. 내부망, 외부망, DMZ, 인터넷망 같은 경계는 엔터티가 아니라 각 엔터티의
group으로 기록하세요. group은 문서에 명시된 단일 영역명만 사용하고 여러 영역을 합치거나
'등 영역' 같은 설명을 넣지 마세요.
추측하거나 일반 상식을 추가하지 마세요. 각 엔터티와 관계에는 근거 CHUNK 번호를 넣으세요.
관계 유형은 calls, sends_to, reads_from, writes_to, depends_on, contains, deployed_on,
connects_to, authenticates_with, routes_to, precedes, related_to 중 하나를 사용하세요.
동일 엔터티를 별칭으로 중복 생성하지 말고 핵심 구성요소와 직접 관계만 추출하세요.
description은 80자 이내로 간결하게 작성하세요.
JSON이나 Markdown을 사용하지 말고 아래 파이프 구분 형식만 한 줄씩 반환하세요.
E|고유키|표시명|유형|망 영역 또는 빈 값|근거 CHUNK 번호(쉼표 구분)|역할
R|출발 엔터티 키|도착 엔터티 키|관계 유형|근거 CHUNK 번호(쉼표 구분)|관계 설명
예시:
E|push_engine|Push Engine|application|외부망|3|푸시 요청 처리
E|webtob|WebtoB|server|DMZ|3|웹 게이트웨이
R|push_engine|webtob|sends_to|3|푸시 메시지 전송
관계가 명확하지 않으면 R 행을 만들지 마세요. 헤더·번호·설명 문장은 출력하지 마세요.
/no_think"""

ENTITY_TYPES = {
    "system", "server", "application", "api", "database", "network",
    "actor", "process", "component", "other",
}
RELATION_TYPES = {
    "calls", "sends_to", "reads_from", "writes_to", "depends_on", "contains",
    "deployed_on", "connects_to", "authenticates_with", "routes_to", "precedes",
    "related_to",
}


def _sample_item_indices(total: int, maximum: int) -> list[int]:
    if total <= maximum:
        return list(range(total))
    if maximum == 1:
        return [0]
    return list(dict.fromkeys(
        round(position * (total - 1) / (maximum - 1)) for position in range(maximum)
    ))


ARCHITECTURE_KEYWORDS = (
    "구성도", "아키텍처", "architecture", "시스템 구성", "연계 구성",
    "내부망", "외부망", "인터넷망", "dmz", "서버", "engine", "was",
    "webtob", "database", " db", "api", "중계서버",
)
ACTIVITY_KEYWORDS = (
    "성능 테스트", "테스트 수행", "검증 수행", "모니터링 수행", "시험 계획",
)


def _architecture_score(item: Item) -> int:
    """구성도·망 경계·컴포넌트가 포함된 청크를 LLM 표본 앞쪽으로 올린다."""
    text = f"{item.metadata.get('heading', '')}\n{item.text or ''}".casefold()
    score = sum(3 for keyword in ARCHITECTURE_KEYWORDS if keyword in text)
    if item.metadata.get("content_source") == "image_ocr":
        # 구성도가 그림으로 삽입된 PPTX/PDF에서는 OCR 청크가 유일한 컴포넌트 근거다.
        score += 8
    score -= sum(3 for keyword in ACTIVITY_KEYWORDS if keyword in text)
    return score


def _select_item_indices(items: list[Item], maximum: int) -> list[int]:
    """아키텍처 고신호 청크와 문서 전체의 균등 표본을 함께 선택한다."""
    if len(items) <= maximum:
        return list(range(len(items)))
    priority_limit = max(1, maximum // 2)
    scored = sorted(
        ((_architecture_score(item), index) for index, item in enumerate(items)),
        key=lambda value: (-value[0], value[1]),
    )
    priority = [index for score, index in scored if score > 0][:priority_limit]
    selected = list(priority)
    # 특정 슬라이드만 과대 대표되지 않도록 나머지는 기존 균등 표본으로 채운다.
    for index in _sample_item_indices(len(items), maximum):
        if index not in selected:
            selected.append(index)
        if len(selected) >= maximum:
            break
    if len(selected) < maximum:
        for _, index in scored:
            if index not in selected:
                selected.append(index)
            if len(selected) >= maximum:
                break
    return selected


def _build_extraction_text(
    items: list[Item], max_chunks: int, max_characters: int
) -> str:
    indices = _select_item_indices(items, max_chunks)
    per_chunk = max(300, max_characters // max(1, len(indices)))
    blocks: list[str] = []
    used = 0
    for index in indices:
        text = (items[index].text or "").strip()[:per_chunk]
        if not text:
            continue
        block = f"[CHUNK {index}]\n{text}"
        remaining = max_characters - used
        if remaining <= 0:
            break
        blocks.append(block[:remaining])
        used += len(block) + 2
    return "\n\n".join(blocks)


def _sequence_list(value: str) -> list[int]:
    return list(dict.fromkeys(
        int(token) for token in re.findall(r"\d+", value)
    ))[:20]


def _parse_graph_lines(value: str) -> dict[str, list[dict[str, Any]]]:
    """LLM의 단순 행 프로토콜을 파싱한다.

    JSON grammar를 사용하지 않으므로 llama.cpp PEG 오류와 따옴표·쉼표 오류가 없다.
    형식이 깨진 한 줄은 제외하되 다른 정상 행의 의미를 임의로 보정하지 않는다.
    """
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for raw_line in value.splitlines():
        line = raw_line.strip().strip("`").lstrip("- ")
        if not line or "|" not in line:
            continue
        fields = [field.strip() for field in line.split("|")]
        record_type = fields[0].upper()
        if record_type in {"E", "ENTITY"} and len(fields) >= 7:
            entities.append({
                "key": fields[1],
                "name": fields[2],
                "type": fields[3],
                "group": fields[4],
                "chunk_seqs": _sequence_list(fields[5]),
                "description": "|".join(fields[6:]).strip(),
            })
        elif record_type in {"R", "RELATION"} and len(fields) >= 6:
            relations.append({
                "source": fields[1],
                "target": fields[2],
                "type": fields[3],
                "chunk_seqs": _sequence_list(fields[4]),
                "description": "|".join(fields[5:]).strip(),
                "confidence": 0.9,
            })
    if not entities:
        raise ValueError("graph extraction returned no valid entity rows")
    return {"entities": entities, "relations": relations}


def _ollama_generate_url(base_url: str) -> str:
    parts = urlsplit(base_url.rstrip("/"))
    root_path = parts.path.split("/v1", 1)[0].rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"{root_path}/api/generate", "", ""))


def _generate_graph_lines(content: str, settings: Any) -> str:
    """일반 채팅과 분리된 그래프 추출 전용 LLM 호출을 수행한다."""
    llm = get_settings().models.llm
    client = get_http_client(
        "graph-extraction",
        llm.timeout_seconds,
        llm.api_key,
        llm.http_max_connections,
        llm.http_max_keepalive_connections,
    )
    prompt = (
        f"{GRAPH_EXTRACTION_PROMPT}\n\n"
        f"엔터티 최대 {min(settings.max_entities, 30)}개, 관계 최대 "
        f"{min(settings.max_relations, 40)}개입니다.\n\n{content}"
    )
    if settings.extraction_provider == "ollama_generate":
        url = _ollama_generate_url(llm.base_url)
        payload = {
            "model": llm.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "raw": False,
            "options": {
                "temperature": 0,
                "num_predict": settings.extraction_max_tokens,
            },
        }
        response_reader = lambda response: response.json()["response"]
    else:
        url = llm.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = {
            "model": llm.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": settings.extraction_max_tokens,
            "stream": False,
        }
        response_reader = lambda response: response.json()["choices"][0]["message"]["content"]

    for attempt in range(1, llm.max_attempts + 1):
        try:
            response = client.post(url, json=payload)
        except httpx.TransportError:
            if attempt >= llm.max_attempts:
                raise
            sleep_before_retry(
                attempt, llm.retry_backoff_seconds, llm.retry_max_backoff_seconds
            )
            continue
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < llm.max_attempts:
            sleep_before_retry(
                attempt,
                llm.retry_backoff_seconds,
                llm.retry_max_backoff_seconds,
                response,
            )
            continue
        response.raise_for_status()
        result = response_reader(response)
        if not isinstance(result, str) or not result.strip():
            raise ValueError("graph extraction returned empty output")
        logger.info(
            "knowledge graph extractor completed provider=%s output_chars=%d",
            settings.extraction_provider,
            len(result),
        )
        return result
    raise RuntimeError("graph extraction retry loop ended without response")


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _chunk_seqs(value: Any, item_count: int) -> list[int]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        sequence for item in value
        if isinstance(item, int) and 0 <= (sequence := item) < item_count
    ))[:20]


def normalize_graph(
    payload: dict[str, Any], item_count: int, max_entities: int, max_relations: int
) -> dict[str, list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    keys: set[str] = set()
    raw_entities = payload.get("entities")
    if isinstance(raw_entities, list):
        for index, item in enumerate(raw_entities[:max_entities]):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"), 200)
            key = _text(item.get("key"), 80) or f"entity-{index + 1}"
            if not name or key in keys:
                continue
            entity_type = _text(item.get("type"), 30).lower()
            if entity_type not in ENTITY_TYPES:
                entity_type = "other"
            keys.add(key)
            entities.append({
                "key": key,
                "name": name,
                "type": entity_type,
                "description": _text(item.get("description"), 500),
                "group": _text(item.get("group"), 100),
                "chunk_seqs": _chunk_seqs(item.get("chunk_seqs"), item_count),
            })

    entities_by_key = {entity["key"]: entity for entity in entities}
    relations: list[dict[str, Any]] = []
    seen_relations: set[tuple[str, str, str]] = set()
    raw_relations = payload.get("relations")
    if isinstance(raw_relations, list):
        for item in raw_relations[:max_relations]:
            if not isinstance(item, dict):
                continue
            source = _text(item.get("source"), 80)
            target = _text(item.get("target"), 80)
            relation_type = _text(item.get("type"), 40).lower()
            if relation_type not in RELATION_TYPES:
                relation_type = "related_to"
            relation_key = (source, target, relation_type)
            if source not in keys or target not in keys or source == target:
                continue
            if relation_key in seen_relations:
                continue
            seen_relations.add(relation_key)
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 1))))
            except (TypeError, ValueError):
                confidence = 1.0
            evidence_sequences = _chunk_seqs(item.get("chunk_seqs"), item_count)
            if not evidence_sequences:
                # 모델이 관계 근거만 생략한 경우 양 끝 엔터티의 원문 근거를 합쳐
                # 근거 없는 관계가 생성되지 않도록 한다.
                evidence_sequences = list(dict.fromkeys(
                    entities_by_key[source]["chunk_seqs"]
                    + entities_by_key[target]["chunk_seqs"]
                ))[:20]
            if not evidence_sequences:
                continue
            relations.append({
                "source": source,
                "target": target,
                "type": relation_type,
                "description": _text(item.get("description"), 500),
                "confidence": confidence,
                "chunk_seqs": evidence_sequences,
            })

            # 관계 근거가 있는 청크는 양 끝 엔터티의 mention 근거이기도 하다. 엔터티
            # 검색 lane에서 관계 엔터티를 찾을 수 있도록 동일 청크에 연결한다.
            for key in (source, target):
                entities_by_key[key]["chunk_seqs"] = list(dict.fromkeys(
                    entities_by_key[key]["chunk_seqs"] + evidence_sequences
                ))[:20]

    # 관계에 쓰이지 않는 엔터티도 검색어 매칭에는 유용하므로 보존한다.
    return {"entities": entities, "relations": relations}


def extract_knowledge_graph(items: list[Item], settings: Any) -> dict[str, Any]:
    """아키텍처 관련 청크를 전용 행 프로토콜로 추출한다."""
    content = _build_extraction_text(
        items,
        settings.extraction_max_chunks,
        settings.extraction_max_characters,
    )
    if not content:
        return {"entities": [], "relations": []}
    raw = _generate_graph_lines(content, settings)
    graph = normalize_graph(
        _parse_graph_lines(raw),
        len(items),
        settings.max_entities,
        settings.max_relations,
    )
    logger.info(
        "knowledge graph extracted entities=%d relations=%d sampled_chunks=%d",
        len(graph["entities"]),
        len(graph["relations"]),
        min(len(items), settings.extraction_max_chunks),
    )
    return graph
