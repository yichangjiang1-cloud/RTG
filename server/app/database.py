from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
import os

# 确保数据库文件的目录存在
# 处理不同的路径格式
db_url = settings.DATABASE_URL
if db_url.startswith("sqlite:///./"):
    db_path = db_url.replace("sqlite:///./", "")
elif db_url.startswith("sqlite:////"):
    db_path = db_url.replace("sqlite:////", "/")
elif db_url.startswith("sqlite:///"):
    db_path = db_url.replace("sqlite:///", "")
else:
    db_path = db_url

print(f"[DEBUG] 数据库路径: {db_path}")
print(f"[DEBUG] 数据库 URL: {db_url}")

db_dir = os.path.dirname(db_path)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)
    print(f"[DEBUG] 创建数据库目录: {db_dir}")

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    echo=settings.DEBUG
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _sqlite_migrate():
    """SQLite 轻量迁移：为已有库补齐模型新增列（create_all 不会 ALTER 旧表）"""
    if "sqlite" not in settings.DATABASE_URL or not os.path.exists(db_path):
        return
    import sqlite3

    alterations = [
        ("calibration_data", "source_vehicle_id", "VARCHAR(50)"),
        ("vehicle_configs", "l_arm_elec", "JSON"),
        ("vehicle_configs", "l_arm_diesel", "JSON"),
        ("vehicle_configs", "l_arm_calibration", "JSON"),
    ]
    conn = sqlite3.connect(db_path)
    try:
        for table, column, col_type in alterations:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                print(f"[DEBUG] 迁移: {table}.{column} 已添加")
        conn.commit()
    finally:
        conn.close()


def init_db():
    """初始化数据库表"""
    from app.models import Base
    print(f"\n[DEBUG] 开始创建数据库表...")
    print(f"[DEBUG] 引擎: {engine}")
    print(f"[DEBUG] Base 元数据表: {Base.metadata.tables.keys()}")

    try:
        Base.metadata.create_all(bind=engine)
        _sqlite_migrate()
        print("[DEBUG] 数据库表创建成功")

        # 验证表是否真的被创建了
        import sqlite3
        db_path = "data/ground_truth.db"
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            conn.close()
            print(f"[DEBUG] 数据库中的表: {tables}")
        else:
            print(f"[DEBUG] 数据库文件不存在: {db_path}")
            print(f"[DEBUG] 当前工作目录: {os.getcwd()}")
    except Exception as e:
        print(f"[DEBUG] 创建表失败: {e}")
        import traceback
        traceback.print_exc()
        raise
