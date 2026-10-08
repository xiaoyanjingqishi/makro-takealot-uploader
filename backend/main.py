import logging
import app.utils.asyncio_patch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.api import api_router
import app.models  # 确保所有模型都被导入以便创建数据表

# 初始化数据库表结构
Base.metadata.create_all(bind=engine)

# 确保 products 表具备 previous_status 字段 (支持弃用与恢复)
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE products ADD COLUMN previous_status VARCHAR(50)"))
        _conn.commit()
except Exception:
    pass

# 确保 products 表具备 seo_keywords 字段 (支持搜索意图关键词持久化)
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE products ADD COLUMN seo_keywords TEXT"))
        _conn.commit()
except Exception:
    pass

# 确保 products 表具备 takealot_title_zh 与 makro_title_zh 中文对照字段
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(products)")).fetchall()]
        if "takealot_title_zh" not in cols:
            _conn.execute(text("ALTER TABLE products ADD COLUMN takealot_title_zh VARCHAR(500)"))
        if "makro_title_zh" not in cols:
            _conn.execute(text("ALTER TABLE products ADD COLUMN makro_title_zh VARCHAR(500)"))
        _conn.commit()
except Exception:
    pass

# 确保 makro_orders 表具备 delivered_date 字段 (支持买家签收时间展示)
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(makro_orders)")).fetchall()]
        if "delivered_date" not in cols:
            _conn.execute(text("ALTER TABLE makro_orders ADD COLUMN delivered_date DATETIME"))
            _conn.commit()
except Exception:
    pass

# 确保 stores 表具备 last_auto_login_at 与 last_auto_login_status 字段 (支持21小时保活检测与状态记录)
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(stores)"))]
        if "last_auto_login_at" not in cols:
            _conn.execute(text("ALTER TABLE stores ADD COLUMN last_auto_login_at DATETIME"))
        if "last_auto_login_status" not in cols:
            _conn.execute(text("ALTER TABLE stores ADD COLUMN last_auto_login_status VARCHAR(255)"))
        
        # 确保 task_logs 表具备 user_id 与 operator_name 字段 (支持员工工作量与人效审计追踪)
        task_cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(task_logs)"))]
        if "user_id" not in task_cols:
            _conn.execute(text("ALTER TABLE task_logs ADD COLUMN user_id INTEGER"))
        if "operator_name" not in task_cols:
            _conn.execute(text("ALTER TABLE task_logs ADD COLUMN operator_name VARCHAR(100)"))

        # 确保 product_store_listings 具备 user_id 字段 (支持按员工统计各店铺刊登产出)
        psl_cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(product_store_listings)"))]
        if "user_id" not in psl_cols:
            _conn.execute(text("ALTER TABLE product_store_listings ADD COLUMN user_id INTEGER"))

        _conn.commit()
except Exception as _mig_err:
    print(f"[INIT] 数据库字段自检迁移提示: {_mig_err}")



# 确保多店铺初始数据迁移 (如果 stores 为空，从现有系统配置无缝迁移首个默认店铺)
try:
    from app.database import SessionLocal
    from app.models.store import Store, ProductStoreListing
    from app.models.setting import SystemSetting
    from app.models.product import Product
    with SessionLocal() as _db:
        store_count = _db.query(Store).count()
        if store_count == 0:
            s_seller = _db.query(SystemSetting).filter(SystemSetting.key == "seller_id").first()
            s_csrf = _db.query(SystemSetting).filter(SystemSetting.key == "fk_csrf_token").first()
            s_cookie = _db.query(SystemSetting).filter(SystemSetting.key == "cookie").first()
            s_brand = _db.query(SystemSetting).filter(SystemSetting.key == "default_brand").first()

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
            _db.add(default_store)
            _db.commit()
            _db.refresh(default_store)

            # 将现有已提交的商品关联至默认店铺
            submitted_products = _db.query(Product).filter(Product.status == "SUBMITTED").all()
            for p in submitted_products:
                _db.add(ProductStoreListing(
                    product_id=p.id,
                    store_id=default_store.id,
                    status="SUBMITTED",
                    makro_sku_id=p.makro_sku_id,
                    makro_request_id=p.makro_request_id,
                    selling_price=p.makro_selling_price,
                    mrp=p.makro_mrp
                ))
            _db.commit()
except Exception as _e:
    print(f"[INIT] 店铺初始迁移跳过或异常: {_e}")

# 确保核心高频复合索引与新增字段存在，彻底消除全表扫描慢查询
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        # 检查并补充 products.clean_mode 字段
        cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(products)")).fetchall()]
        if "clean_mode" not in cols:
            _conn.execute(text("ALTER TABLE products ADD COLUMN clean_mode VARCHAR(20) DEFAULT 'text'"))
            _conn.commit()

        # 检查并补充 makro_piggyback_items.item_id, seller_count, variant, 与 reprice 字段
        pb_tables = [row[0] for row in _conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='makro_piggyback_items'")).fetchall()]
        if pb_tables:
            pb_cols = [row[1] for row in _conn.execute(text("PRAGMA table_info(makro_piggyback_items)")).fetchall()]
            if "item_id" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN item_id VARCHAR(100)"))
            if "seller_count" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN seller_count INTEGER DEFAULT 1"))
            if "variant_attributes" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN variant_attributes TEXT"))
            if "variant_name" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN variant_name VARCHAR(200)"))
            if "auto_reprice" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN auto_reprice BOOLEAN DEFAULT 1"))
            if "max_price_ceiling" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN max_price_ceiling FLOAT DEFAULT 0.0"))
            if "last_reprice_at" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN last_reprice_at DATETIME"))
            if "last_reprice_result" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN last_reprice_result VARCHAR(200)"))
            if "buybox_status" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN buybox_status VARCHAR(50) DEFAULT 'UNKNOWN'"))
            if "last_competitor_price" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN last_competitor_price FLOAT"))
            if "is_abandoned" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN is_abandoned BOOLEAN DEFAULT 0"))
            if "abandoned_reason" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN abandoned_reason VARCHAR(200)"))
            if "abandoned_at" not in pb_cols:
                _conn.execute(text("ALTER TABLE makro_piggyback_items ADD COLUMN abandoned_at DATETIME"))
            _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_piggyback_buybox_status ON makro_piggyback_items (buybox_status)"))
            _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_makro_piggyback_is_abandoned ON makro_piggyback_items (is_abandoned)"))
            _conn.commit()

        # 检查并创建 makro_reprice_logs 表
        _conn.execute(text("""
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
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reprice_logs_sku ON makro_reprice_logs (seller_sku)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reprice_logs_created ON makro_reprice_logs (created_at DESC)"))
        _conn.commit()

        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_variants_product_id ON product_variants (product_id)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_status_id ON products (status, id DESC)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_compliance_status_id ON products (compliance_status, id DESC)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_logs_product_id ON task_logs (product_id, id DESC)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_compliance_arbitration_logs_product_id ON compliance_arbitration_logs (product_id, id DESC)"))
        _conn.commit()
except Exception as _ie:
    print(f"[INIT] 字段与复合索引初始化跳过或异常: {_ie}")

# 自动纠偏跟品池历史分/兰特单位异常 (如 49900.0 自动纠偏为 499.0 并重算跟品价)
try:
    with SessionLocal() as _db:
        from app.models.makro_piggyback import MakroPiggybackItem
        from app.services.makro_piggyback_service import MakroPiggybackService
        anomalies = _db.query(MakroPiggybackItem).filter(MakroPiggybackItem.original_price >= 5000).all()
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
            _db.commit()
            print(f"[INIT] 自动修复 {len(anomalies)} 件历史异常价格跟品商品")
except Exception as _p_err:
    print(f"[INIT] 跟品价格纠偏跳过: {_p_err}")

# 确保全量存量商品类目健康合规 (自动纠偏历史脏类目)
try:
    with SessionLocal() as _db:
        from app.services.vertical_service import VerticalService
        _dirty = _db.query(Product).filter(Product.status.in_(["PENDING_CLEAN", "CLEANED", "FAILED"])).all()
        _repaired = 0
        for _p in _dirty:
            _target, _ = VerticalService.resolve_vertical(_p.makro_vertical)
            if _target != _p.makro_vertical:
                _p.makro_vertical = _target
                _repaired += 1
        if _repaired > 0:
            _db.commit()
            print(f"[INIT] 启动自动巡检：纠偏 {_repaired} 件商品的失效/历史类目")
except Exception as _ve:
    print(f"[INIT] 商品类目巡检跳过: {_ve}")

# 自动纠偏跟品池历史虚假占位赢车状态 (将原卖家为外部竞对但被误标为 WINNING 的历史数据纠偏为 LOSING 或 FLOOR_HIT)
try:
    with SessionLocal() as _db:
        from app.models.makro_piggyback import MakroPiggybackItem
        from app.models.store import Store
        active_stores = _db.query(Store).filter(Store.is_active == True).all()
        all_store_idents = set()
        for s in active_stores:
            if s.name:
                all_store_idents.add(s.name.strip().lower())
            if s.default_brand:
                all_store_idents.add(s.default_brand.strip().lower())
            if s.seller_id:
                all_store_idents.add(s.seller_id.strip().lower())

        false_winners = _db.query(MakroPiggybackItem).filter(MakroPiggybackItem.buybox_status == "WINNING").all()
        corrected_count = 0
        for it in false_winners:
            seller = (it.original_seller or "").strip().lower()
            if seller and not any(ident in seller or seller in ident for ident in all_store_idents if ident):
                min_floor = float(it.min_price_floor or 0.0)
                target_p = float(it.target_price or 0.0)
                if min_floor > 0 and target_p <= min_floor:
                    it.buybox_status = "FLOOR_HIT"
                else:
                    it.buybox_status = "LOSING"
                corrected_count += 1
        if corrected_count > 0:
            _db.commit()
            print(f"[INIT] 自动纠偏 {corrected_count} 件虚假占位赢车的历史跟品商品状态为真实丢车/底价状态")
except Exception as _bb_err:
    print(f"[INIT] 历史 Buybox 状态纠偏跳过: {_bb_err}")

# 初始化多用户 RBAC 权限与店铺在线表
try:
    from app.init_db import init_and_migrate_db
    init_and_migrate_db()
except Exception as _m_err:
    print(f"[INIT] 数据库与权限迁移异常: {_m_err}")

# 启动订单与商品 30 分钟静默后台自动同步引擎
try:
    from app.services.order_sync_scheduler import order_sync_scheduler
    order_sync_scheduler.start()
except Exception as _sched_err:
    print(f"[INIT] 启动后台定时同步失败: {_sched_err}")

# 启动店铺全自动登录与凭据保活定时调度引擎 (默认每 21 小时，可由管理员在系统配置中自定义)
try:
    from app.services.auto_login_scheduler import auto_login_scheduler
    auto_login_scheduler.start()
except Exception as _als_err:
    print(f"[INIT] 启动自动登录保活调度器失败: {_als_err}")

# 启动智能自动跟价定时巡检调度引擎 (默认每 60 分钟巡检一次在售跟品)
try:
    from app.services.auto_reprice_scheduler import auto_reprice_scheduler
    auto_reprice_scheduler.start()
except Exception as _reprice_err:
    print(f"[INIT] 启动自动跟价调度器失败: {_reprice_err}")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.SYSTEM_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc"
)

# 挂载 GZip 响应压缩 (对大于 1KB 的 API 响应与前端 HTML 自动压缩 80%~85% 网络传输体积)
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 配置 CORS 跨域 (允许浏览器插件和前端控制台无阻通信)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",  # 允许所有域名与 chrome-extension:// 协议
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import HTMLResponse, Response, FileResponse
import os
import json
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent / "app" / "templates" / "index.html"
_template_cache = {"content": "", "mtime": 0}

# 挂载 API 路由
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/api/download/extension", summary="下载 Chrome 搬品浏览器插件安装包")
@app.get("/download/extension.zip", include_in_schema=False)
def download_extension():
    zip_path = Path(__file__).resolve().parent / "app" / "static" / "makro-extension.zip"
    ext_dir = Path(__file__).resolve().parent.parent / "extension"
    if not ext_dir.exists():
        ext_dir = Path(__file__).resolve().parent / "extension"

    ext_ver = settings.EXTENSION_VERSION
    manifest_path = ext_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as _mf:
                _m_data = json.load(_mf)
                if _m_data.get("version"):
                    ext_ver = str(_m_data["version"]).strip()
        except Exception:
            pass

    if not zip_path.exists():
        import zipfile
        if ext_dir.exists():
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(ext_dir):
                    for file in files:
                        full_path = Path(root) / file
                        arc_name = full_path.relative_to(ext_dir)
                        z.write(full_path, arc_name)
    if not zip_path.exists():
        return Response(content="Extension package not found", status_code=404)
    return FileResponse(
        path=str(zip_path),
        filename=f"makro-extension-v{ext_ver}.zip",
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=makro-extension-v{ext_ver}.zip",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_content = ""
    if TEMPLATE_PATH.exists():
        try:
            cur_mtime = TEMPLATE_PATH.stat().st_mtime
            if cur_mtime != _template_cache["mtime"] or not _template_cache["content"]:
                with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                    _template_cache["content"] = f.read()
                _template_cache["mtime"] = cur_mtime
            html_content = _template_cache["content"]
        except Exception:
            with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                html_content = f.read()
    else:
        html_content = "<h1>Makro-Takealot System Backend Online</h1><p><a href='/api/docs'>API Docs</a></p>"
    
    resp = HTMLResponse(content=html_content)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

CALCULATOR_PATH = Path(__file__).resolve().parent.parent / "tools" / "makro_pricing_calculator.html"

@app.get("/calculator", response_class=HTMLResponse, summary="打开 Makro 选品与全链路精准定价测算工具")
def get_calculator():
    if CALCULATOR_PATH.exists():
        with open(CALCULATOR_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Calculator page not found</h1>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
