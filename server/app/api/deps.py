"""依赖注入"""

from fastapi import Depends
from sqlalchemy.orm import Session
from app.database import get_db

def get_database() -> Session:
    """获取数据库会话"""
    db = get_db()
    return db
