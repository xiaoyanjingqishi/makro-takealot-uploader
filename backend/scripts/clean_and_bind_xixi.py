# -*- coding: utf-8 -*-
"""
脚本：清除 xixi (Store ID: 4) 下历史错误同步的 hzh 镜像数据，
更新真实账密 (chenyouyou0407@gmail.com / 18350589058@163.com)，
并触发全自动登录认证换发真实 Seller ID 与凭据。
"""

import sys
import os
import sqlite3
import logging
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clean_and_bind_xixi")

from app.database import SessionLocal
from app.models.store import Store
from app.services.makro_auth_service import MakroAuthService
from app.services.makro_portal_service import MakroPortalService


def main():
    logger.info("=== 阶段 1: 清理 Store 4 (xixi) 的镜像脏数据 ===")
    
    db_path = os.path.join(backend_dir, "makro_app.db")
    raw_conn = sqlite3.connect(db_path)
    try:
        c = raw_conn.cursor()

        c.execute("SELECT COUNT(*) FROM makro_orders WHERE store_id = 4")
        cnt_o4 = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM makro_orders WHERE store_id = 3")
        cnt_o3 = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM store_product_reprices WHERE store_id = 4")
        cnt_r4 = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM store_product_reprices WHERE store_id = 3")
        cnt_r3 = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM makro_listings WHERE store_id = 4")
        cnt_l4 = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM makro_listings WHERE store_id = 3")
        cnt_l3 = c.fetchone()[0]

        logger.info(f"清洗前统计:")
        logger.info(f"  Store 3 (hzh)  - 订单: {cnt_o3}, 跟价: {cnt_r3}, 在售品: {cnt_l3}")
        logger.info(f"  Store 4 (xixi) - 订单: {cnt_o4}, 跟价: {cnt_r4}, 在售品: {cnt_l4}")

        # 2. 执行精准物理删除
        c.execute("DELETE FROM makro_orders WHERE store_id = 4")
        del_orders = c.rowcount
        c.execute("DELETE FROM store_product_reprices WHERE store_id = 4")
        del_reprices = c.rowcount
        c.execute("DELETE FROM makro_listings WHERE store_id = 4")
        del_listings = c.rowcount

        logger.info(f"已清除 Store 4 脏数据: 订单 {del_orders} 条, 跟价 {del_reprices} 条, 在售品 {del_listings} 条")

        # 3. 修正 Store 4 凭据配置
        c.execute("""
            UPDATE stores 
            SET login_email = 'chenyouyou0407@gmail.com',
                login_password = 'Whd000829.',
                imap_user = '18350589058@163.com',
                imap_server = 'imap.163.com',
                imap_port = 993,
                imap_password = 'HGmmNsD5M7D7dHAL',
                seller_id = 'xixi_pending',
                cookie = NULL,
                fk_csrf_token = NULL,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 4
        """)

        raw_conn.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        logger.info("Store 4 凭据与邮箱配置已更新并落盘。")

        # 4. 验证清洗后 Store 3 完好，Store 4 归零
        c.execute("SELECT COUNT(*) FROM makro_orders WHERE store_id = 4")
        assert c.fetchone()[0] == 0, "Store 4 订单未清零！"
        c.execute("SELECT COUNT(*) FROM store_product_reprices WHERE store_id = 4")
        assert c.fetchone()[0] == 0, "Store 4 跟价未清零！"
        c.execute("SELECT COUNT(*) FROM makro_listings WHERE store_id = 4")
        assert c.fetchone()[0] == 0, "Store 4 在售品未清零！"

        c.execute("SELECT COUNT(*) FROM makro_orders WHERE store_id = 3")
        assert c.fetchone()[0] == cnt_o3, "Store 3 订单数量受损！"
        c.execute("SELECT COUNT(*) FROM store_product_reprices WHERE store_id = 3")
        assert c.fetchone()[0] == cnt_r3, "Store 3 跟价数量受损！"
        c.execute("SELECT COUNT(*) FROM makro_listings WHERE store_id = 3")
        assert c.fetchone()[0] == cnt_l3, "Store 3 在售品数量受损！"

        logger.info("✓ 验证通过：Store 3 数据 100% 完好无损，Store 4 彻底归零！")
    finally:
        raw_conn.close()

    logger.info("\n=== 阶段 2: 触发 xixi 全自动登录换发真实凭据 ===")
    with SessionLocal() as db:
        res = MakroAuthService.run_full_auto_login(
            store_id=4,
            db=db,
            max_wait_seconds=75
        )
        logger.info(f"自动登录执行结果: {res.get('message')}")
        if res.get("success"):
            logger.info("✓ 全自动登录认证成功！真实凭据与真实 Seller ID 已自动写入数据库。")
            store4_fresh = db.query(Store).filter(Store.id == 4).first()
            logger.info(f"  店铺名: {store4_fresh.name}")
            logger.info(f"  真实 Seller ID: {store4_fresh.seller_id}")
            logger.info(f"  Cookie 预览: {store4_fresh.cookie[:30] if store4_fresh.cookie else 'None'}...")

            logger.info("\n=== 阶段 3: 执行 xixi 真实数据初次同步 ===")
            try:
                o_res = MakroPortalService.sync_store_orders(store4_fresh, db)
                logger.info(f"  订单同步结果: {o_res.get('message', o_res)}")
            except Exception as e:
                logger.warning(f"  订单同步跳过/提示: {e}")

            try:
                l_res = MakroPortalService.sync_store_listings(store4_fresh, db)
                logger.info(f"  商品同步结果: {l_res.get('total_synced', 0)} 件商品")
            except Exception as e:
                logger.warning(f"  商品同步跳过/提示: {e}")
        else:
            logger.warning(f"自动登录提示: {res}")


if __name__ == "__main__":
    main()
