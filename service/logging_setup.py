"""API와 Worker가 동일한 타임스탬프 로그 형식을 사용하게 한다."""
from __future__ import annotations

import logging.config
from pathlib import Path

import yaml

from service.config import ROOT


def configure_logging(path: Path | None = None) -> None:
    """공통 YAML logging 설정을 적용한다."""
    config_path = path or ROOT / "config" / "logging.yaml"
    with config_path.open(encoding="utf-8") as handle:
        logging.config.dictConfig(yaml.safe_load(handle))
