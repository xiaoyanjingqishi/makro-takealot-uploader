import app.utils.asyncio_patch
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

# 确保核心高频复合索引存在，彻底消除全表扫描慢查询
try:
    from sqlalchemy import text
    with engine.connect() as _conn:
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_variants_product_id ON product_variants (product_id)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_status_id ON products (status, id DESC)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_compliance_status_id ON products (compliance_status, id DESC)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_logs_product_id ON task_logs (product_id, id DESC)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_logs_created_at ON task_logs (created_at DESC)"))
        _conn.commit()
except Exception as _ie:
    print(f"[INIT] 复合索引初始化跳过或异常: {_ie}")

app = FastAPI(
    title=settings.PROJECT_NAME,
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

from fastapi.responses import HTMLResponse, Response
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent / "app" / "templates" / "index.html"
_template_cache = {"content": "", "mtime": 0}

# 挂载 API 路由
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    if TEMPLATE_PATH.exists():
        try:
            cur_mtime = TEMPLATE_PATH.stat().st_mtime
            if cur_mtime != _template_cache["mtime"] or not _template_cache["content"]:
                with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                    _template_cache["content"] = f.read()
                _template_cache["mtime"] = cur_mtime
            return _template_cache["content"]
        except Exception:
            with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                return f.read()
    return "<h1>Makro-Takealot System Backend Online</h1><p><a href='/api/docs'>API Docs</a></p>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
