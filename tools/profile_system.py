import os
import sys
import sqlite3
import time

def inspect_db():
    db_path = "backend/makro_app.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    print("=== SQLite Pragmas ===")
    for p in ['journal_mode', 'synchronous', 'cache_size', 'mmap_size', 'page_size', 'page_count', 'auto_vacuum', 'freelist_count']:
        c.execute(f"PRAGMA {p}")
        print(f"  {p}: {c.fetchone()[0]}")

    print("\n=== Table Row Counts ===")
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in c.fetchall()]
    table_counts = {}
    for t in tables:
        if t.startswith('sqlite_'): continue
        c.execute(f"SELECT count(*) FROM [{t}]")
        cnt = c.fetchone()[0]
        table_counts[t] = cnt
        print(f"  {t}: {cnt:,} rows")

    print("\n=== Indexes Count ===")
    c.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' ORDER BY tbl_name")
    indexes = c.fetchall()
    print(f"Total Indexes: {len(indexes)}")
    for name, tbl in indexes:
        print(f"  Index: {name} on {tbl}")

    conn.close()

if __name__ == "__main__":
    inspect_db()
