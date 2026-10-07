"""BGE-M3 dense embedding adapter."""

from __future__ import annotations

from FlagEmbedding import BGEM3FlagModel


class BgeM3Embedder:
    """FlagEmbedding의 BGEM3FlagModel을 프로젝트 인터페이스로 감싼다.

    BGE-M3는 dense/sparse/multi-vector를 모두 지원하지만 v0.1.0에서는
    Chroma dense 검색부터 검증하기 위해 dense vector만 반환한다.
    향후 lexical/RRF 또는 BGE-M3 sparse를 함께 사용하는 hybrid 구조로 확장 가능하다.
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
