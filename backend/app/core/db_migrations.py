# -*- coding: utf-8 -*-
"""
数据库受控迁移与存量数据自检巡检器
"""

import logging
from sqlalchemy import text
from app.config import settings
from app.database import Base

logger = logging.getLogger(__name__)


def run_system_migrations(engine, SessionLocal):
    """
    系统启动时受控执行的数据库表结构同步、字段补充、高频索引构建与存量数据自愈巡检
    """
    logger.info(">>> 开始执行系统数据库表结构自检与迁移...")

    # 1. 初始化所有 Base 继承模型的基本表
    Base.metadata.create_all(bind=engine)

    # 2. 字段动态补充 (向下兼容历史 SQLite 数据库)
    try:
        with engine.connect() as conn:
            # 检查 products 表
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(products)")).fetchall()]
            if "previous_status" not in cols:
                conn.execute(text("ALTER TABLE products ADD COLUMN previous_status VARCHAR(50)"))
            if "seo_keywords" not in cols:
                conn.execute(text("ALTER TABLE products ADD COLUMN seo_keywords TEXT"))
            if "takealot_title_zh" not in cols:
                conn.execute(text("ALTER TABLE products ADD COLUMN takealot_title_zh VARCHAR(500)"))
            if "makro_title_zh" not in cols:
                conn.execute(text("ALTER TABLE products ADD COLUMN makro_title_zh VARCHAR(500)"))
            if "clean_mode" not in cols:
                conn.execute(text("ALTER TABLE products ADD COLUMN clean_mode VARCHAR(20) DEFAULT 'text'"))

            # 检查 makro_orders 表
            order_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(makro_orders)")).fetchall()]
            if "delivered_date" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN delivered_date DATETIME"))

            # 检查 stores 表
            store_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(stores)")).fetchall()]
            if "last_auto_login_at" not in store_cols:
                conn.execute(text("ALTER TABLE stores ADD COLUMN last_auto_login_at DATETIME"))
            if "last_auto_login_status" not in store_cols:
                conn.execute(text("ALTER TABLE stores ADD COLUMN last_auto_login_status VARCHAR(255)"))

            # 检查 task_logs 表
            task_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(task_logs)")).fetchall()]
            if "user_id" not in task_cols:
                conn.execute(text("ALTER TABLE task_logs ADD COLUMN user_id INTEGER"))
            if "operator_name" not in task_cols:
                conn.execute(text("ALTER TABLE task_logs ADD COLUMN operator_name VARCHAR(100)"))

            # 检查 product_store_listings 表
            psl_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(product_store_listings)")).fetchall()]
            if "user_id" not in psl_cols:
                conn.execute(text("ALTER TABLE product_store_listings ADD COLUMN user_id INTEGER"))

            # 检查 makro_piggyback_items 表字段
            pb_tables = [row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='makro_piggyback_items'")).fetchall()]
            if pb_tables:
                pb_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(makro_piggyback_items)")).fetchall()]
                if "item_id" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN item_id VARCHAR(100)"))
                if "seller_count" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN seller_count INTEGER DEFAULT 1"))
                if "variant_attributes" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN variant_attributes TEXT"))
                if "variant_name" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN variant_name VARCHAR(200)"))
                if "auto_reprice" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN auto_reprice BOOLEAN DEFAULT 1"))
                if "max_price_ceiling" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN max_price_ceiling FLOAT DEFAULT 0.0"))
                if "last_reprice_at" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN last_reprice_at DATETIME"))
                if "last_reprice_result" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN last_reprice_result VARCHAR(200)"))
                if "buybox_status" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN buybox_status VARCHAR(50) DEFAULT 'UNKNOWN'"))
                if "last_competitor_price" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN last_competitor_price FLOAT"))
                if "is_abandoned" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN is_abandoned BOOLEAN DEFAULT 0"))
                if "abandoned_reason" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN abandoned_reason VARCHAR(200)"))
                if "abandoned_at" not in pb_cols:
                    conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN abandoned_at DATETIME"))

            # 创建 makro_reprice_logs 表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS makro_reprice_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    piggyback_id INTEGER NOT NULL,
                    store_id INTEGER NOT NULL,
                    seller_sku VARCHAR(100) NOT NULL,
                    makro_product_id VARCHAR(100) NOT NULL,
                    competitor_seller VARCHAR(100),
                    competitor_price FLOAT DEFAULT 0.0,
                    old_price FLOAT DEFAULT 0.0,
                    new_price FLOAT DEFAULT 0.0,
                    action VARCHAR(50) NOT NULL,
                    reason TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # 复合索引构建
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reprice_logs_sku ON makro_reprice_logs (seller_sku)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reprice_logs_created ON makro_reprice_logs (created_at DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_variants_product_id ON product_variants (product_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_status_id ON products (status, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_compliance_status_id ON products (compliance_status, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_logs_product_id ON task_logs (product_id, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_compliance_arbitration_logs_product_id ON compliance_arbitration_logs (product_id, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_piggyback_buybox_status ON makro_piggyback_items (buybox_status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_piggyback_is_abandoned ON makro_piggyback_items (is_abandoned)"))
            conn.commit()
    except Exception as mig_err:
        logger.warning(f"[INIT] 数据库字段/索引自检迁移异常: {mig_err}")

    # 3. 初始店铺迁移 (若为空)
    try:
        from app.models.store import Store, ProductStoreListing
        from app.models.setting import SystemSetting
        from app.models.product import Product
        with SessionLocal() as db:
            store_count = db.query(Store).count()
            if store_count == 0:
                s_seller = db.query(SystemSetting).filter(SystemSetting.key == "seller_id").first()
                s_csrf = db.query(SystemSetting).filter(SystemSetting.key == "fk_csrf_token").first()
                s_cookie = db.query(SystemSetting).filter(SystemSetting.key == "cookie").first()
                s_brand = db.query(SystemSetting).filter(SystemSetting.key == "default_brand").first()

                seller_id = s_seller.value if s_seller and s_seller.value else settings.DEFAULT_SELLER_ID
                csrf_token = s_csrf.value if s_csrf and s_csrf.value else settings.DEFAULT_FK_CSRF_TOKEN
                cookie = s_cookie.value if s_cookie and s_cookie.value else ""
                default_brand = s_brand.value if s_brand and s_brand.value else settings.DEFAULT_BRAND

                default_store = Store(
                    name="Makro 旗舰主力店",
                    seller_id=seller_id,
                    fk_csrf_token=csrf_token,
                    cookie=cookie,
                    default_brand=default_brand,
                    is_active=True,
                    is_default=True,
                    notes="系统初始默认店铺"
                )
                db.add(default_store)
                db.commit()
                db.refresh(default_store)

                submitted_products = db.query(Product).filter(Product.status == "SUBMITTED").all()
                for p in submitted_products:
                    db.add(ProductStoreListing(
                        product_id=p.id,
                        store_id=default_store.id,
                        status="SUBMITTED",
                        makro_sku_id=p.makro_sku_id,
                        makro_request_id=p.makro_request_id,
                        selling_price=p.makro_selling_price,
                        mrp=p.makro_mrp
                    ))
                db.commit()
    except Exception as e:
        logger.warning(f"[INIT] 店铺初始迁移跳过或异常: {e}")

    # 4. 存量商品类目与异常数据巡检纠偏
    try:
        with SessionLocal() as db:
            from app.models.makro_piggyback import MakroPiggybackItem
            from app.services.makro_piggyback_service import MakroPiggybackService
            anomalies = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.original_price >= 5000).all()
            for it in anomalies:
                if it.original_price and (it.original_price % 100 == 0):
                    it.original_price = round(it.original_price / 100.0, 2)
                if it.original_mrp and (it.original_mrp % 100 == 0):
                    it.original_mrp = round(it.original_mrp / 100.0, 2)
                t_price, t_mrp = MakroPiggybackService.calculate_price(
                    original_price=it.original_price,
                    strategy=it.price_strategy or "MINUS_1",
                    min_floor=it.min_price_floor or 0.0,
                    original_mrp=it.original_mrp
                )
                it.target_price = t_price
                it.target_mrp = t_mrp
            if anomalies:
                db.commit()
                logger.info(f"[INIT] 自动修复 {len(anomalies)} 件历史异常价格跟品商品")
    except Exception as p_err:
        logger.warning(f"[INIT] 跟品价格纠偏跳过: {p_err}")

    # 5. 初始化多用户 RBAC 权限
    try:
        from app.init_db import init_and_migrate_db
        init_and_migrate_db()
    except Exception as m_err:
        logger.warning(f"[INIT] 数据库与权限迁移异常: {m_err}")

    logger.info(">>> 数据库表结构与权限自检迁移完成！")
