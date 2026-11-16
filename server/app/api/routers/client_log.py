"""接收浏览器日志，写入 logs/frontend.log。"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.utils.logger import get_frontend_file_logger

router = APIRouter(prefix="/api", tags=["client-log"])

_LEVELS = {
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class ClientLogEntry(BaseModel):
    level: str = "info"
    module: str = ""
    message: str = ""
    detail: str = ""
    page: str = ""
    time: str = ""


class ClientLogBatch(BaseModel):
    entries: list[ClientLogEntry] = Field(default_factory=list)


def _clip(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


@router.post("/client-log")
def ingest_client_log(batch: ClientLogBatch):
    file_log = get_frontend_file_logger()
    kept = 0
    for item in batch.entries[:40]:
        message = _clip(item.message, 1000)
        if not message:
            continue
        file_log.log(
            _LEVELS.get(item.level, logging.INFO),
            "page=%s module=%s client_time=%s %s %s",
            _clip(item.page, 200),
            _clip(item.module, 40),
            _clip(item.time, 20),
            message,
            _clip(item.detail, 800),
        )
        kept += 1
    return {"code": 200, "data": {"kept": kept}}
