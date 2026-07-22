"""一次性迁移：yard_01 -> A01"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "data" / "ground_truth.db"
OLD, NEW = "yard_01", "A01"

TABLES_WITH_YARD_ID = (
    "yards",
    "survey_points",
    "calibration_data",
    "vehicle_bay_transforms",
)


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"数据库不存在: {DB}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        existing = conn.execute(
            "SELECT yard_id FROM yards WHERE yard_id = ?", (NEW,)
        ).fetchone()
        if existing:
            raise SystemExit(f"目标 yard_id {NEW} 已存在，中止")

        old = conn.execute(
            "SELECT yard_id, name FROM yards WHERE yard_id = ?", (OLD,)
        ).fetchone()
        if not old:
            print(f"yards 中无 {OLD}，当前堆场:")
            for row in conn.execute("SELECT yard_id, name FROM yards"):
                print(f"  {row['yard_id']} | {row['name']}")
            return

        print(f"迁移 {OLD} -> {NEW}")
        for table in TABLES_WITH_YARD_ID:
            n = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE yard_id = ?", (OLD,)
            ).fetchone()["c"]
            if n:
                conn.execute(
                    f"UPDATE {table} SET yard_id = ? WHERE yard_id = ?",
                    (NEW, OLD),
                )
                print(f"  {table}: {n} 行已更新")

        conn.commit()
        print("完成。当前 yards:")
        for row in conn.execute("SELECT yard_id, name FROM yards"):
            print(f"  {row['yard_id']} | {row['name']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
