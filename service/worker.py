"""별도 프로세스로 구동하는 큐 워커. 파일 저장과 DB INSERT가 완료된 건만 처리."""
from __future__ import annotations
import argparse
import logging
import os
import time
from pathlib import Path
from uuid import UUID
from service import config, store
from service.drm import decrypt
from service.embedding import embed
from service.parsers import parse_pdf, parse_text, parse_logs, parse_code

logger = logging.getLogger(__name__)


def process(asset_id: str):
    asset = store.get_asset(asset_id)
    if not asset or asset['status'] != 'queued': return
    # 한 업로드만 점유; 여러 워커가 동일 대상을 재처리하지 않도록 상태를 원자적으로 갱신한다.
    with store.connect() as conn:
        row = conn.execute('''UPDATE rag.assets SET status='processing'
            WHERE asset_id=%s AND status='queued' RETURNING asset_id''', (UUID(asset_id),)).fetchone()
    if not row: return
    raw = Path(asset['source_path'])
    # DB 경로를 신뢰하지 않고 업로드 루트 아래인지 재검증한다.
    if not raw.resolve().is_relative_to(config.UPLOAD_ROOT):
        store.set_status(asset_id, 'failed', '허용하지 않은 파일 경로'); return
    output = config.ARTIFACT_ROOT / asset_id
    clear = output / ('decrypted' + raw.suffix)
    try:
        output.mkdir(parents=True, exist_ok=True)
        source = decrypt(raw, clear)
        content_type = asset['content_type']
        if content_type == 'document' and source.suffix.lower() == '.docx':
            from service.parsers import parse_docx
            items, report = parse_docx(source)
        elif content_type == 'document' and source.suffix.lower() == '.pdf':
            if not config.DOCLING_ARTIFACTS:
                raise RuntimeError('폐쇄망 PDF 처리를 위해 RAG_DOCLING_ARTIFACTS가 필요합니다')
            items, report = parse_pdf(source, output)
        elif content_type in ('document', 'incident'):
            items, report = parse_text(source)
        elif content_type == 'error_trace':
            items, report = parse_logs(source)
        elif content_type == 'source_code':
            items, report = parse_code(source, asset['repository'] or '', asset['branch'] or '', asset['metadata'].get('logical_path') or asset['filename'])
        else: raise ValueError('유형과 확장자가 맞지 않습니다')
        if not items: raise ValueError('검색 가능한 내용을 추출하지 못했습니다')
        vectors = embed([item.text for item in items])
        store.save_items(asset_id, items, vectors, report)
        logger.info('indexed asset=%s chunks=%d', asset_id, len(items))
    except Exception as exc:
        logger.exception('ingestion failed asset=%s', asset_id)
        store.set_status(asset_id, 'failed', f'{type(exc).__name__}: {exc}'[:1000])
    finally:
        # DRM 복호화 산출물은 실패 여부와 관계없이 제거한다. Docling JSON에는 평문이 남을 수 있으므로
        # artifacts 디렉터리 권한과 보존정책을 별도로 설정해야 한다.
        if clear.exists(): clear.unlink()


def run(poll: float):
    config.UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    config.ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        with store.connect() as conn:
            rows = conn.execute("SELECT asset_id FROM rag.assets WHERE status='queued' ORDER BY created_at LIMIT 10").fetchall()
        for row in rows: process(str(row['asset_id']))
        time.sleep(poll)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('--poll', type=float, default=3)
    args = parser.parse_args()
    run(args.poll)
