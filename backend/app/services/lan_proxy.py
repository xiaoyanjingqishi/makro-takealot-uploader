import socket
import logging
from typing import List, Dict, Any, Tuple
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response

logger = logging.getLogger("lan_proxy")

def get_network_info() -> Dict[str, Any]:
    """
    智能探测宿主机网络接口与首选局域网 IP
    优先匹配 192.168.x.x (排除 192.168.137.x 热点虚拟网卡), 10.x.x.x 等真实物理局域网地址
    排除 127.x.x.x, 169.254.x.x, 172.18.x.x (xray/VPN tun 虚拟接口)
    """
    hostname = socket.gethostname()
    try:
        _, _, ips = socket.gethostbyname_ex(hostname)
    except Exception:
        ips = []

    valid_ips = [
        ip for ip in ips 
        if not ip.startswith("127.") and not ip.startswith("169.254.")
    ]

    def ip_priority(ip: str) -> int:
        if ip.startswith("192.168.") and not ip.startswith("192.168.137."):
            return 100
        if ip.startswith("10."):
            return 90
        if ip.startswith("172.") and not ip.startswith("172.18."):
            return 80
        return 10

    sorted_ips = sorted(valid_ips, key=ip_priority, reverse=True)
    primary_ip = sorted_ips[0] if sorted_ips else "127.0.0.1"

    return {
        "hostname": hostname,
        "primary_ip": primary_ip,
        "all_ips": sorted_ips,
        "proxy_port": 80,
        "backend_port": 8001
    }


def create_reverse_proxy_app(target_url: str = "http://127.0.0.1:8001") -> FastAPI:
    """
    构建流式 ASGI 反向代理应用
    """
    app = FastAPI(title="Makro LAN Reverse Proxy", docs_url=None, redoc_url=None)
    client = httpx.AsyncClient(
        base_url=target_url,
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=True
    )

    @app.on_event("shutdown")
    async def shutdown_client():
        await client.aclose()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
    async def proxy_handler(request: Request, path: str):
        # 组装目标 URL 路径与查询字符串
        query_bytes = request.url.query.encode("utf-8")
        url = httpx.URL(path=f"/{path}", query=query_bytes)

        # 过滤与重构请求头
        headers = dict(request.headers)
        headers.pop("host", None)

        client_host = request.client.host if request.client else "127.0.0.1"
        existing_forwarded = headers.get("x-forwarded-for")
        if existing_forwarded:
            headers["x-forwarded-for"] = f"{existing_forwarded}, {client_host}"
        else:
            headers["x-forwarded-for"] = client_host
        headers["x-forwarded-proto"] = request.url.scheme

        body = await request.body()

        try:
            rp_req = client.build_request(
                method=request.method,
                url=url,
                headers=headers,
                content=body
            )
            rp_resp = await client.send(rp_req, stream=True)

            excluded_headers = {
                "content-encoding", 
                "content-length", 
                "transfer-encoding", 
                "connection",
                "keep-alive",
                "proxy-authenticate",
                "proxy-authorization"
            }
            resp_headers = {
                k: v for k, v in rp_resp.headers.items() 
                if k.lower() not in excluded_headers
            }

            return StreamingResponse(
                rp_resp.aiter_raw(),
                status_code=rp_resp.status_code,
                headers=resp_headers,
                background=rp_resp.aclose
            )
        except httpx.ConnectError:
            return Response(
                content="<h2 style='font-family:sans-serif;text-align:center;margin-top:100px;color:#dc2626;'>"
                        "Makro 后端服务 (端口 8001) 暂未启动或连接中断，请检查控制台。</h2>",
                status_code=502,
                media_type="text/html"
            )
        except Exception as e:
            return Response(
                content=f"Proxy Error: {str(e)}",
                status_code=500,
                media_type="text/plain"
            )

    return app
