import sqlite3
import json
import urllib.request

db = "data/ground_truth.db"
c = sqlite3.connect(db)
print("yards:", c.execute("select yard_id, mapped from yards").fetchall())
print("survey:", c.execute(
    "select yard_id, point_type, bay_no from survey_points"
).fetchall())
c.close()

for url in [
    "http://127.0.0.1:8000/api/yard/map?yard_id=yard_01",
    "http://127.0.0.1:8000/api/survey/history?yard_id=yard_01&point_type=mapping",
]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body = json.loads(r.read().decode())
            print(url, "->", json.dumps(body, ensure_ascii=False)[:500])
    except Exception as e:
        print(url, "-> ERROR", e)
