"""BGE-M3 dense embedding adapter."""

from __future__ import annotations

from FlagEmbedding import BGEM3FlagModel


class BgeM3Embedder:
    """FlagEmbedding의 BGEM3FlagModel을 프로젝트 인터페이스로 감싼다.

    PostgreSQL pgvector에는 dense vector를 저장하고 exact/FTS 검색 결과와 RRF로
    결합한다. sparse/multi-vector는 별도 backend가 필요할 때 adapter로 확장한다.
    """

    def __init__(self, model_name_or_path: str, use_fp16: bool = True) -> None:
        # 폐쇄망에서는 model_name 대신 사전에 내려받은 로컬 디렉터리 경로를 넣을 수 있다.
        self.model = BGEM3FlagModel(model_name_or_path, use_fp16=use_fp16)

    def encode_dense(
        self,
        texts: list[str],
        batch_size: int = 8,
        max_length: int = 8192,
    ) -> list[list[float]]:
        """문자열 목록을 dense vector 목록으로 변환한다."""
        output = self.model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return output["dense_vecs"].tolist()
