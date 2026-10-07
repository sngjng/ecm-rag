"""설정은 환경 변수로만 받는다. 비밀번호나 모델 경로를 코드/업로드 폼에 넣지 않는다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ROOT = Path(os.environ.get('RAG_UPLOAD_ROOT', str(ROOT / 'artifacts' / 'uploads'))).resolve()
ARTIFACT_ROOT = Path(os.environ.get('RAG_ARTIFACT_ROOT', str(ROOT / 'artifacts' / 'processed'))).resolve()
DB_DSN = os.environ.get('RAG_DB_DSN', '')
EMBED_URL = os.environ.get('RAG_EMBED_URL', 'http://127.0.0.1:8200/v1/embeddings')
EMBED_MODEL = os.environ.get('RAG_EMBED_MODEL', 'bge-m3')
MAX_UPLOAD_MB = int(os.environ.get('RAG_MAX_UPLOAD_MB', '100'))
API_KEY = os.environ.get('RAG_API_KEY', '')
DOCLING_ARTIFACTS = os.environ.get('RAG_DOCLING_ARTIFACTS', '')
DRM_COMMAND = os.environ.get('RAG_DRM_COMMAND', '')
DRM_TIMEOUT = int(os.environ.get('RAG_DRM_TIMEOUT', '120'))
RERANK_MODEL_PATH = os.environ.get('RAG_RERANK_MODEL_PATH', '')
