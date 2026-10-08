import time
import requests
import logging
import threading
from typing import Optional, List, Dict, Any
from ..database import SessionLocal
from ..models.setting import SystemSetting

logger = logging.getLogger("proxy_service")

class ProxyPoolService:
    """
    统一国内动态代理池管理器 (支持携趣代理 API 动态提取与故障自动转移)
    为跟品批量高并发采集、全店定时巡航改价提供 100% 防封与自动换 IP 支持
    """
    DEFAULT_API_URL = "http://api.xiequ.cn/VAD/GetIp.aspx?act=getturn51&uid=53898&vkey=78DB146CC637E8735CD85174996AE873&num=10&time=6&plat=1&re=0&type=7&so=1&group=51&ow=1&spl=1&addr=&db=1"

    _lock = threading.Lock()
    _pool: List[str] = []
    _last_fetch_time: float = 0.0
    _is_fetching: bool = False
    _last_error: str = ""

    @classmethod
    def get_api_url(cls) -> str:
        db = SessionLocal()
        try:
            rec = db.query(SystemSetting).filter(SystemSetting.key == "xiequ_proxy_api").first()
            return rec.value.strip() if rec and rec.value else cls.DEFAULT_API_URL
        except Exception:
            return cls.DEFAULT_API_URL
        finally:
            db.close()

    @classmethod
    def refresh_pool(cls, force: bool = False) -> List[str]:
        """
        从携趣 API 提取一批最新的国内代理 IP 并加载到可用池中
        """
        with cls._lock:
            # 节流限制：同一时刻只有一个线程去调提取接口，且提取间隔不少于 5 秒
            now = time.time()
            if cls._is_fetching:
                return cls._pool
            if not force and (now - cls._last_fetch_time < 5.0) and len(cls._pool) > 0:
                return cls._pool
            cls._is_fetching = True

        api_url = cls.get_api_url()
        new_ips = []
        try:
            logger.info("正在从携趣代理 API 提取最新国内动态 IP 池...")
            resp = requests.get(api_url, timeout=10)
            text = resp.text.strip()
            
            if "请先添加白名单" in text:
                err_msg = f"携趣代理提取失败: {text}"
                logger.warning(err_msg)
                cls._last_error = text
                return []
            
            # 解析 IP:PORT
            for line in text.split("\n"):
                clean = line.strip()
                if ":" in clean and not clean.startswith("<") and not clean.startswith("{"):
                    new_ips.append(clean)
            
            with cls._lock:
                if new_ips:
                    # 合并去重并保留新提取的代理
                    existing_set = set(cls._pool)
                    for ip in new_ips:
                        if ip not in existing_set:
                            cls._pool.append(ip)
                    cls._last_fetch_time = time.time()
                    cls._last_error = ""
                    logger.info(f"携趣动态代理池已成功补充 {len(new_ips)} 个国内 IP，当前池内活跃总量: {len(cls._pool)}")
        except Exception as ex:
            logger.error(f"提取携趣代理 API 异常: {ex}")
            cls._last_error = str(ex)
        finally:
            with cls._lock:
                cls._is_fetching = False

        return new_ips

    @classmethod
    def get_proxy(cls) -> Optional[str]:
        """
        从活跃代理池中提取一个可用代理 URL (如 http://117.89.88.137:5417)
        采用轮转机制支持高并发多线程复用；当池中代理不足 3 个时，自动后台异步补充
        """
        with cls._lock:
            # 若池中代理偏少，且未在提取中，触发后台补充
            if (len(cls._pool) < 3 or (time.time() - cls._last_fetch_time > 240)) and not cls._is_fetching:
                threading.Thread(target=cls.refresh_pool, args=(False,), daemon=True, name="ProxyRefreshThread").start()

            if cls._pool:
                # 轮换获取 (Pop from front, push to back)
                chosen = cls._pool.pop(0)
                cls._pool.append(chosen)
                return f"http://{chosen}"

        # 若池中完全为空，尝试同步拉取一次
        new_ips = cls.refresh_pool(force=True)
        if new_ips:
            return f"http://{new_ips[0]}"

        return None

    @classmethod
    def report_failure(cls, proxy_url: Optional[str] = None):
        """
        报告某个代理失效（超时/连接重置），立即从活跃池中淘汰，避免其他线程踩坑
        """
        if not proxy_url:
            return
        raw = proxy_url.replace("http://", "").replace("https://", "").strip()
        with cls._lock:
            if raw in cls._pool:
                cls._pool.remove(raw)
                logger.info(f"代理 {raw} 请求失败已立即淘汰，当前池内剩余: {len(cls._pool)}")
                if len(cls._pool) < 2 and not cls._is_fetching:
                    threading.Thread(target=cls.refresh_pool, args=(True,), daemon=True).start()

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """
        获取当前代理池运行监控状态
        """
        with cls._lock:
            pool_size = len(cls._pool)
            sample_proxies = cls._pool[:3]
        return {
            "pool_size": pool_size,
            "sample_proxies": sample_proxies,
            "last_fetch_time": cls._last_fetch_time,
            "last_error": cls._last_error,
            "is_ready": pool_size > 0
        }
