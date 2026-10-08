"""
数据库本地恢复与同步工具 (Database Restore & Sync Tool)

功能：
1. 自动对本地现有数据库执行时间戳备份保护；
2. 支持从 backup_makro_app.db 或 backup_makro_app.db.gz 解压恢复至 backend/makro_app.db；
3. 校验数据库完整性 (PRAGMA integrity_check) 与核心业务表数据量。
"""

import os
import sys
import time
import shutil
import gzip
import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
TARGET_DB = BACKEND_DIR / "makro_app.db"
DEFAULT_BACKUP_RAW = ROOT_DIR / "backup_makro_app.db"
DEFAULT_BACKUP_GZ = ROOT_DIR / "backup_makro_app.db.gz"

def backup_current_db():
    if TARGET_DB.exists():
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dest = BACKEND_DIR / f"makro_app.db.local_bak_{timestamp}"
        shutil.copy2(TARGET_DB, backup_dest)
        print(f"[OK] 已安全备份当前本地旧库 -> {backup_dest.name}")
        return backup_dest
    return None

def restore_from_source(source_path: Path):
    if not source_path.exists():
        raise FileNotFoundError(f"备份文件不存在: {source_path}")

    # 1. 备份本地旧库
    backup_current_db()

    # 2. 如果是 .gz 压缩文件，先解压导入
    if source_path.suffix == ".gz":
        print(f"[*] 正在从 Gzip 压缩包解压导入: {source_path.name} ...")
        with gzip.open(source_path, "rb") as f_in, open(TARGET_DB, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        print(f"[*] 正在复制数据库文件: {source_path.name} -> {TARGET_DB.name} ...")
        shutil.copy2(source_path, TARGET_DB)

    # 3. 校验恢复后的完整性
    print("[*] 正在执行 SQLite 数据库完整性校验...")
    conn = sqlite3.connect(TARGET_DB)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    check_result = cur.fetchone()[0]
    if check_result != "ok":
        conn.close()
        raise RuntimeError(f"数据库完整性校验异常: {check_result}")

    # 4. 统计核心数据
    stats = {}
    for table in ["products", "makro_listings", "stores", "makro_orders"]:
        try:
            cur.execute(f"SELECT count(*) FROM {table}")
            stats[table] = cur.fetchone()[0]
        except Exception:
            pass
    conn.close()

    print("\n==========================================")
    print("       本地数据库恢复成功 (SUCCESS)        ")
    print("==========================================")
    print(f"目标数据库: {TARGET_DB}")
    print(f"完整性状态: {check_result}")
    for tbl, count in stats.items():
        print(f" - 表 {tbl:<18}: {count} 条记录")
    print("==========================================\n")

if __name__ == "__main__":
    src = None
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    elif DEFAULT_BACKUP_RAW.exists():
        src = DEFAULT_BACKUP_RAW
    elif DEFAULT_BACKUP_GZ.exists():
        src = DEFAULT_BACKUP_GZ
    else:
        print("[-] 未在工作区根目录下找到 backup_makro_app.db 或 backup_makro_app.db.gz")
        sys.exit(1)

    restore_from_source(src)
