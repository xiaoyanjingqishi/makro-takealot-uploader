import os
import sys
import webbrowser
import time
from pathlib import Path

# 确保控制台支持 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 将 backend 添加到 sys.path
backend_dir = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(backend_dir))

def main():
    print("=" * 60)
    print("[START] Makro-Takealot 智能搬品系统")
    print("=" * 60)
    print(f"工作目录: {backend_dir}")
    print("正在启动 FastAPI 本地服务...")
    print("控制台地址: http://localhost:8001")
    print("API 文档:   http://localhost:8001/api/docs")
    print("=" * 60)

    # 尝试在启动 1.5 秒后自动在默认浏览器打开控制台
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open("http://localhost:8001")
        except Exception:
            pass

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run("main:app", app_dir=str(backend_dir), host="0.0.0.0", port=8001, reload=False)

if __name__ == "__main__":
    main()
