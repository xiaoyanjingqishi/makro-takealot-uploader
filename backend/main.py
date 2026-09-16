from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.api import api_router
import app.models  # 确保所有模型都被导入以便创建数据表

# 初始化数据库表结构
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc"
)

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

# 挂载 API 路由
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    if TEMPLATE_PATH.exists():
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Makro-Takealot System Backend Online</h1><p><a href='/api/docs'>API Docs</a></p>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
