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
            if "fulfillment_status" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN fulfillment_status VARCHAR(50) DEFAULT 'NEW'"))
            if "fulfillment_updated_at" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN fulfillment_updated_at DATETIME"))
            if "po_id" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN po_id INTEGER"))
            if "purchase_cost" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN purchase_cost FLOAT DEFAULT 0.0"))
            if "domestic_logistics_fee" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN domestic_logistics_fee FLOAT DEFAULT 0.0"))
            if "independent_head_haul" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN independent_head_haul FLOAT DEFAULT 0.0"))
            if "purchase_order_sn" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN purchase_order_sn VARCHAR(100)"))
            if "purchase_notes" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN purchase_notes VARCHAR(255)"))
            if "domestic_tracking_no" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN domestic_tracking_no VARCHAR(100)"))
            if "total_weight_g" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN total_weight_g INTEGER DEFAULT 0"))
            if "air_freight_rate" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN air_freight_rate FLOAT DEFAULT 0.0"))
            if "label_printed" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN label_printed BOOLEAN DEFAULT 0"))
            if "label_printed_at" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN label_printed_at DATETIME"))
            if "is_weight_measured" not in order_cols:
                conn.execute(text("ALTER TABLE makro_orders ADD COLUMN is_weight_measured BOOLEAN DEFAULT 0"))
                conn.execute(text("UPDATE makro_orders SET is_weight_measured = 1 WHERE total_weight_g > 0"))

            # 确保 makro_order_status_logs 表与索引存在
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS makro_order_status_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    order_sn VARCHAR(100) NOT NULL,
                    change_type VARCHAR(50) DEFAULT 'FULFILLMENT',
                    from_status VARCHAR(100) NULL,
                    to_status VARCHAR(100) NOT NULL,
                    operator VARCHAR(100) DEFAULT 'SYSTEM',
                    note VARCHAR(255) NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_order_status_logs_order_id ON makro_order_status_logs (order_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_order_status_logs_order_sn ON makro_order_status_logs (order_sn)"))

            # 确保 makro_purchase_orders 表与索引存在
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS makro_purchase_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    po_number VARCHAR(100) NOT NULL UNIQUE,
                    store_id INTEGER NULL,
                    name VARCHAR(200) NULL,
                    status VARCHAR(50) DEFAULT 'PLANNING',
                    forwarder_name VARCHAR(100) NULL,
                    air_waybill_no VARCHAR(100) NULL,
                    domestic_logistics_fee FLOAT DEFAULT 0.0,
                    head_haul_fee FLOAT DEFAULT 0.0,
                    logistics_loss_fee FLOAT DEFAULT 0.0,
                    box_length_cm FLOAT DEFAULT 0.0,
                    box_width_cm FLOAT DEFAULT 0.0,
                    box_height_cm FLOAT DEFAULT 0.0,
                    package_weight_kg FLOAT DEFAULT 0.0,
                    notes TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            # 兼容已有 makro_purchase_orders 表增加新字段
            po_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(makro_purchase_orders)")).fetchall()]
            if "domestic_logistics_fee" not in po_cols:
                conn.execute(text("ALTER TABLE makro_purchase_orders ADD COLUMN domestic_logistics_fee FLOAT DEFAULT 0.0"))
            if "box_length_cm" not in po_cols:
                conn.execute(text("ALTER TABLE makro_purchase_orders ADD COLUMN box_length_cm FLOAT DEFAULT 0.0"))
            if "box_width_cm" not in po_cols:
                conn.execute(text("ALTER TABLE makro_purchase_orders ADD COLUMN box_width_cm FLOAT DEFAULT 0.0"))
            if "box_height_cm" not in po_cols:
                conn.execute(text("ALTER TABLE makro_purchase_orders ADD COLUMN box_height_cm FLOAT DEFAULT 0.0"))
            if "package_weight_kg" not in po_cols:
                conn.execute(text("ALTER TABLE makro_purchase_orders ADD COLUMN package_weight_kg FLOAT DEFAULT 0.0"))

            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_po_number ON makro_purchase_orders(po_number)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_po_store_id ON makro_purchase_orders(store_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_orders_po_id ON makro_orders(po_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_orders_fulfillment_st ON makro_orders(fulfillment_status)"))


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

            # 创建 makro_reprice_logs 表 (piggyback_id 与 reprice_id 均允许为 NULL)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS makro_reprice_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    piggyback_id INTEGER NULL,
                    reprice_id INTEGER NULL,
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

            # 检查 makro_reprice_logs 是否缺少 reprice_id 字段
            reprice_log_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(makro_reprice_logs)")).fetchall()]
            if "reprice_id" not in reprice_log_cols:
                conn.execute(text("ALTER TABLE makro_reprice_logs ADD COLUMN reprice_id INTEGER"))

            # 检查 makro_reprice_logs 表中的 piggyback_id 是否具有历史 NOT NULL 限制，若有则平滑迁移为 NULLABLE
            reprice_log_info = conn.execute(text("PRAGMA table_info(makro_reprice_logs)")).fetchall()
            piggyback_col = next((c for c in reprice_log_info if c[1] == "piggyback_id"), None)
            if piggyback_col and piggyback_col[3] == 1:  # notnull == 1
                logger.info("[INIT] 检测到 makro_reprice_logs.piggyback_id 存在历史 NOT NULL 约束，正在无损迁移为 NULLABLE...")
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(text("""
                    CREATE TABLE makro_reprice_logs_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        piggyback_id INTEGER NULL,
                        reprice_id INTEGER NULL,
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
                conn.execute(text("""
                    INSERT INTO makro_reprice_logs_new (id, piggyback_id, reprice_id, store_id, seller_sku, makro_product_id, competitor_seller, competitor_price, old_price, new_price, action, reason, created_at)
                    SELECT id, piggyback_id, reprice_id, store_id, seller_sku, makro_product_id, competitor_seller, competitor_price, old_price, new_price, action, reason, created_at
                    FROM makro_reprice_logs
                """))
                conn.execute(text("DROP TABLE makro_reprice_logs"))
                conn.execute(text("ALTER TABLE makro_reprice_logs_new RENAME TO makro_reprice_logs"))
                conn.execute(text("PRAGMA foreign_keys=ON"))
                logger.info("[INIT] makro_reprice_logs 表结构平滑迁移完成，piggyback_id 已支持为 NULL！")

            # 复合索引构建
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reprice_logs_sku ON makro_reprice_logs (seller_sku)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reprice_logs_created ON makro_reprice_logs (created_at DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_reprice_logs_reprice_id ON makro_reprice_logs (reprice_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_reprice_logs_piggyback_id ON makro_reprice_logs (piggyback_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_variants_product_id ON product_variants (product_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_status_id ON products (status, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_compliance_status_id ON products (compliance_status, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_logs_product_id ON task_logs (product_id, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_compliance_arbitration_logs_product_id ON compliance_arbitration_logs (product_id, id DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_piggyback_buybox_status ON makro_piggyback_items (buybox_status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_piggyback_is_abandoned ON makro_piggyback_items (is_abandoned)"))

            # 自动迁移弃用的历史 Qwen 视觉模型标识 (qwen-vl-max / qwen-vl-plus -> qwen3-vl-plus)
            legacy_vision_row = conn.execute(text("SELECT value FROM system_settings WHERE key='qwen_vision_model'")).first()
            if legacy_vision_row and legacy_vision_row[0] in ["qwen-vl-max", "qwen-vl-plus", "qwen-vl", "qwen2-vl-72b-instruct"]:
                logger.info(f"[INIT] 自动将百炼已弃用千问视觉模型 [{legacy_vision_row[0]}] 升级为现网官方推荐模型 [qwen3-vl-plus]...")
                conn.execute(text("UPDATE system_settings SET value='qwen3-vl-plus' WHERE key='qwen_vision_model'"))

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

            # 4.1 自动纠偏多品合单 (如 OD438926986036770100) 历史保存的单品价格为全单真实总金额
            import json
            from app.models.makro_order import MakroOrder
            from app.utils.date_utils import parse_to_beijing_time
            all_orders = db.query(MakroOrder).filter(MakroOrder.raw_items_json != None).all()
            fixed_order_amounts = 0
            fixed_dates_count = 0
            for o in all_orders:
                changed = False
                try:
                    raw_items = json.loads(o.raw_items_json) if o.raw_items_json else []
                    if isinstance(raw_items, list) and len(raw_items) > 1:
                        real_total = sum(float((it.get("pricing") or {}).get("total_price") or it.get("price") or 0.0) for it in raw_items)
                        if real_total > 0 and abs(real_total - float(o.total_amount or 0.0)) > 0.01:
                            o.total_amount = round(real_total, 2)
                            changed = True
                            fixed_order_amounts += 1
                except Exception:
                    pass

                # 4.2 自动规整存量订单历史时间为标准北京时间 (将原始抓取数据中的 IST +05:30 转换为 CST +08:00)
                try:
                    if o.raw_items_json:
                        raw_items = json.loads(o.raw_items_json)
                        if raw_items and raw_items[0].get("order_date"):
                            bj_order_dt = parse_to_beijing_time(raw_items[0]["order_date"])
                            if bj_order_dt and o.order_date != bj_order_dt:
                                o.order_date = bj_order_dt
                                changed = True
                    if o.raw_payload_json:
                        payload = json.loads(o.raw_payload_json)
                        if payload.get("dispatch_by_date"):
                            bj_dispatch_dt = parse_to_beijing_time(payload["dispatch_by_date"])
                            if bj_dispatch_dt and o.dispatch_by_date != bj_dispatch_dt:
                                o.dispatch_by_date = bj_dispatch_dt
                                changed = True
                        if payload.get("delivered_date"):
                            bj_delivered_dt = parse_to_beijing_time(payload["delivered_date"])
                            if bj_delivered_dt and o.delivered_date != bj_delivered_dt:
                                o.delivered_date = bj_delivered_dt
                                changed = True
                        if payload.get("is_label_printed") and not o.label_printed:
                            o.label_printed = True
                            changed = True
                except Exception:
                    pass

                if changed:
                    fixed_dates_count += 1

            if fixed_order_amounts > 0 or fixed_dates_count > 0:
                db.commit()
                if fixed_order_amounts > 0:
                    logger.info(f"[INIT] 自动纠偏 {fixed_order_amounts} 笔多商品合单的历史总金额与回款基数")
                if fixed_dates_count > 0:
                    logger.info(f"[INIT] 自动规整 {fixed_dates_count} 笔存量订单时间为标准北京时间")
    except Exception as p_err:
        logger.warning(f"[INIT] 订单数据自愈巡检跳过: {p_err}")

    # 5. 初始化多用户 RBAC 权限
    try:
        from app.init_db import init_and_migrate_db
        init_and_migrate_db()
    except Exception as m_err:
        logger.warning(f"[INIT] 数据库与权限迁移异常: {m_err}")

    logger.info(">>> 数据库表结构与权限自检迁移完成！")
