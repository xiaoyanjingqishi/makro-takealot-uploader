import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from sqlalchemy import text
from backend.app.database import engine

def clean_non_active():
    t0 = time.time()
    with engine.begin() as conn:
        res = conn.execute(text("""
            SELECT spr.id 
            FROM store_product_reprices spr
            LEFT JOIN makro_listings ml ON spr.listing_id = ml.id
            WHERE ml.id IS NULL OR ml.internal_state != 'ACTIVE'
        """)).fetchall()
        del_ids = [r[0] for r in res]
        print(f"待清理非在售商品数: {len(del_ids)}", flush=True)

        if del_ids:
            conn.execute(text("PRAGMA foreign_keys = OFF;"))
            conn.execute(text("""
                UPDATE makro_reprice_logs 
                SET reprice_id = NULL 
                WHERE reprice_id IN (
                    SELECT spr.id 
                    FROM store_product_reprices spr
                    LEFT JOIN makro_listings ml ON spr.listing_id = ml.id
                    WHERE ml.id IS NULL OR ml.internal_state != 'ACTIVE'
                )
            """))
            conn.execute(text("""
                DELETE FROM store_product_reprices 
                WHERE id IN (
                    SELECT spr.id 
                    FROM store_product_reprices spr
                    LEFT JOIN makro_listings ml ON spr.listing_id = ml.id
                    WHERE ml.id IS NULL OR ml.internal_state != 'ACTIVE'
                )
            """))
            conn.execute(text("PRAGMA foreign_keys = ON;"))

        remaining = conn.execute(text("SELECT count(*) FROM store_product_reprices")).scalar()
        print(f"清理完成！耗时: {time.time() - t0:.2f}s, 当前跟价监控表剩余纯在售商品数: {remaining}", flush=True)

if __name__ == "__main__":
    clean_non_active()
