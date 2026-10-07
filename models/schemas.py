"""보험약관 RAG 파이프라인에서 공통으로 사용하는 데이터 모델 정의.

이 파일은 파이프라인 전체의 '계약(Contract)' 역할을 한다.
Docling 원본 JSON을 그대로 여러 모듈에서 직접 참조하지 않고,
보험약관 처리에 필요한 최소 구조를 Pydantic 모델로 정규화한다.

핵심 설계 원칙
----------------
1. 원본 위치(page/bbox)를 최대한 보존한다.
2. 표는 Markdown 문자열이 아니라 셀 좌표와 span 정보를 가진 구조로 저장한다.
3. 청크는 검색용 텍스트와 원본 문맥 연결 정보를 분리해 보관한다.
4. parent/previous/next ID를 통해 잘린 표도 다시 주변 문맥으로 확장할 수 있게 한다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    """정형화된 문서 블록의 유형.

    Docling 내부 label을 그대로 노출하지 않고 프로젝트 내부에서 안정적으로
    사용할 수 있는 공통 타입으로 한 번 추상화한다.
    """

    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    TITLE = "title"
    LIST = "list"
    FOOTNOTE = "footnote"


class SourceRef(BaseModel):
    """원본 PDF에서 특정 데이터가 나온 위치 정보."""

    # 사용자에게 근거 페이지를 제시하거나 문제 발생 페이지를 재검수할 때 사용한다.
    page_no: int
    # 좌표계는 Docling/PDF 파서가 제공하는 값을 그대로 담기 위한 선택 필드다.
    bbox: tuple[float, float, float, float] | None = None
    # 필요하면 원본 Docling 노드 reference를 역추적할 수 있게 남겨둔다.
    docling_ref: str | None = None


class TableCell(BaseModel):
    """표의 논리적 셀 하나.

    `row`, `col`은 좌상단 시작 위치이고 `row_span`, `col_span`은 병합 범위다.
    Markdown으로 변환하기 전에 이 구조를 보존해야 병합 셀 의미를 잃지 않는다.
    """

    row: int
    col: int
    text: str = ""
    row_span: int = 1
    col_span: int = 1
    is_row_header: bool = False
    is_col_header: bool = False
    source: SourceRef | None = None


class CanonicalTable(BaseModel):
    """보험약관에서 추출한 하나의 논리 표.

    이후 table normalizer/chunker는 Docling 원본 대신 이 모델을 기준으로 동작한다.
    """

    table_id: str
    title: str | None = None
    # 예: ["2-4 암진단Ⅱ ... 특별약관", "제3조 보험금의 지급사유"]
    article_path: list[str] = Field(default_factory=list)
    # 표가 여러 페이지에 걸칠 수 있으므로 단일 페이지가 아니라 목록으로 저장한다.
    source_pages: list[int] = Field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    cells: list[TableCell] = Field(default_factory=list)
    footnotes: list[str] = Field(default_factory=list)
    # 사람이 검수할 때 유용한 faithful representation. 검색 원본으로는 사용하지 않는다.
    html: str | None = None
    # 0~1 품질 점수. 낮으면 fallback parser/OCR 대상으로 넘길 수 있다.
    validation_score: float = 1.0
    warnings: list[str] = Field(default_factory=list)


class CanonicalBlock(BaseModel):
    """표 이외의 텍스트/제목/목록 등 일반 문서 블록."""

    block_id: str
    type: BlockType
    text: str = ""
    # 문서 계층을 청크에 반복 주입하기 위한 경로.
    heading_path: list[str] = Field(default_factory=list)
    source: SourceRef
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalDocument(BaseModel):
    """한 개 PDF를 정규화한 프로젝트 내부 표준 문서."""

    document_id: str
    filename: str
    page_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    blocks: list[CanonicalBlock] = Field(default_factory=list)
    tables: list[CanonicalTable] = Field(default_factory=list)


class Chunk(BaseModel):
    """벡터 DB/lexical index에 저장되는 검색 최소 단위.

    검색에는 `retrieval_text`를 사용하지만, 답변 생성 전에는 parent/prev/next
    메타데이터를 통해 더 넓은 문맥을 다시 불러올 수 있다.
    """

    chunk_id: str
    document_id: str
    kind: str
    retrieval_text: str

    # parent_id는 동일 논리 표/조항으로 다시 묶기 위한 키다.
    parent_id: str | None = None
    table_id: str | None = None

    # 하나의 parent가 여러 청크로 나뉘었을 때 순서를 재구성하기 위한 정보.
    chunk_index: int = 0
    chunk_count: int = 1
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None

    source_pages: list[int] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
