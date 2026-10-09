# -*- coding: utf-8 -*-
import os
import json
import logging
from pathlib import Path

import app.utils.asyncio_patch  # Windows asyncio 补丁

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, Response, FileResponse

from app.config import settings
from app.api import api_router
from app.core.lifespan import lifespan
import app.models  # 确保所有 ORM 模型完成注册

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

# 创建极简、标准、无顶层副作用的 FastAPI 实例
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.SYSTEM_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan
)

# 挂载 GZip 响应压缩 (对大于 1KB 的 API 响应与前端 HTML 自动压缩)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 配置 CORS 跨域 (允许浏览器插件和前端控制台无阻通信)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载核心 API 路由
app.include_router(api_router, prefix=settings.API_V1_STR)

# 挂载静态文件目录 (CSS / JS / 扩展包)
STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATES_DIR = Path(__file__).resolve().parent / "app" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# 避免与 Vue 3 的双大括号 {{ }} 插值冲突，将 Jinja2 变量定界符定制为 {[ 和 ]}
templates.env.variable_start_string = "{[["
templates.env.variable_end_string = "]]}"
CALCULATOR_PATH = Path(__file__).resolve().parent.parent / "tools" / "makro_pricing_calculator.html"


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
def dashboard(request: Request):
    if (TEMPLATES_DIR / "index.html").exists():
        resp = templates.TemplateResponse(request=request, name="index.html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return HTMLResponse("<h1>Makro-Takealot System Backend Online</h1><p><a href='/api/docs'>API Docs</a></p>")


@app.get("/calculator", response_class=HTMLResponse, summary="打开 Makro 选品与全链路精准定价测算工具")
def get_calculator():
    if CALCULATOR_PATH.exists():
        with open(CALCULATOR_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Calculator page not found</h1>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
