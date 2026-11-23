"""贝位点修正审计：落盘到 logs/survey_adjust/<yard_id>.json。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from app.config import settings

_SAFE_YID = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_RECORDS = 500


def _dir() -> Path:
    log_dir = Path(os.path.dirname(settings.LOG_FILE) or "logs")
    out = log_dir / "survey_adjust"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _path(yard_id: str) -> Path:
    safe = _SAFE_YID.sub("_", (yard_id or "").strip()) or "unknown"
    return _dir() / f"{safe}.json"


def read_records(yard_id: str) -> list[dict[str, Any]]:
    path = _path(yard_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def append_record(yard_id: str, record: dict[str, Any]) -> Path:
    path = _path(yard_id)
    records = [record, *read_records(yard_id)][:_MAX_RECORDS]
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def path_for(yard_id: str) -> str:
    return str(_path(yard_id)).replace("\\", "/")
