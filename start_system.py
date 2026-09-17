import os
import sys
import socket
import webbrowser
import time
import threading
from pathlib import Path

# 确保控制台支持 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 将 backend 添加到 sys.path
backend_dir = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(backend_dir))

def check_port_available(port: int, host: str = "0.0.0.0") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        s.listen(1)
        s.close()
        return True
    except Exception:
        return False

def run_reverse_proxy(proxy_port: int, target_url: str = "http://127.0.0.1:8001"):
    import uvicorn
    from app.services.lan_proxy import create_reverse_proxy_app
    proxy_app = create_reverse_proxy_app(target_url=target_url)
    config = uvicorn.Config(
        app=proxy_app,
        host="0.0.0.0",
        port=proxy_port,
        log_level="warning",
        access_log=False
    )
    server = uvicorn.Server(config)
    server.run()

def main():
    import uvicorn
    from app.services.lan_proxy import get_network_info

    net_info = get_network_info()
    primary_ip = net_info.get("primary_ip", "127.0.0.1")

    # 优先使用 80 端口提供局域网反代服务（免加端口号访问），若被占用则尝试 8000
    proxy_port = None
    if check_port_available(80):
        proxy_port = 80
    elif check_port_available(8000):
        proxy_port = 8000

    print("=" * 66)
    print("      🚀 Makro-Takealot 智能搬品系统 (局域网反向代理版)")
    print("=" * 66)
    print(f"  工作目录: {backend_dir}")
    print(f"  本机网络: {net_info.get('hostname')} (首选 IP: {primary_ip})")
    print("-" * 66)
    print("  💻 本机访问地址:       http://localhost:8001")
    if proxy_port == 80:
        print("  🌐 局域网访问 (免端口): http://" + primary_ip + "/")
    elif proxy_port:
        print(f"  🌐 局域网访问地址:     http://{primary_ip}:{proxy_port}/")
    print(f"  🔌 局域网直连端口:     http://{primary_ip}:8001/")
    print(f"  📖 API 接口文档:       http://localhost:8001/api/docs")
    print("-" * 66)
    print("  🧩 浏览器插件提示: 支持配置当前局域网 IP 或 localhost:8001")
    print("=" * 66)

    # 启动局域网反向代理服务
    if proxy_port:
        proxy_thread = threading.Thread(
            target=run_reverse_proxy,
            args=(proxy_port, "http://127.0.0.1:8001"),
            daemon=True,
            name="LanReverseProxyThread"
        )
        proxy_thread.start()
        print(f"  [反向代理] 局域网反向代理已在 0.0.0.0:{proxy_port} 启动")
    else:
        print("  [反向代理] 80 与 8000 端口均被占用，仅提供 8001 端口直连")

    # 在启动 1.5 秒后自动在默认浏览器打开控制台
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open("http://localhost:8001")
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()

    # 启动主后端 FastAPI 服务
    uvicorn.run("main:app", app_dir=str(backend_dir), host="0.0.0.0", port=8001, reload=False)

if __name__ == "__main__":
    main()
