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
    DEFAULT_API_URL = ""

    _lock = threading.Lock()
    _pool: List[str] = []
    _last_fetch_time: float = 0.0
    _is_fetching: bool = False
    _last_error: str = ""

    @classmethod
    def is_crawler_proxy_enabled(cls) -> bool:
        """检查系统设置中是否明确启用了买家前台爬虫代理 (默认推荐关闭直连，更稳定迅速)"""
        db = SessionLocal()
        try:
            rec = db.query(SystemSetting).filter(SystemSetting.key == "crawler_proxy_enabled").first()
            if rec and rec.value is not None:
                return str(rec.value).strip().lower() in ("true", "1", "yes")
            # 若未配置开关，检查是否填报了自定义的有效代理提取链接
            api_rec = db.query(SystemSetting).filter(SystemSetting.key == "xiequ_proxy_api").first()
            if api_rec and api_rec.value and api_rec.value.strip():
                return True
            return False
        except Exception:
            return False
        finally:
            db.close()

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
        从代理 API 提取一批最新的代理 IP 并加载到可用池中
        """
        if not cls.is_crawler_proxy_enabled():
            with cls._lock:
                cls._pool = []
            return []

        api_url = cls.get_api_url()
        if not api_url:
            with cls._lock:
                cls._pool = []
            return []

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
    def get_proxy(cls, exclude: Optional[set] = None) -> Optional[str]:
        """
        从活跃代理池中提取一个可用代理 URL (如 http://117.89.88.137:5417)
        采用轮转机制支持高并发多线程复用；当池中代理不足 15 个时，自动后台异步补充
        """
        if not cls.is_crawler_proxy_enabled():
            return None

        exclude_set = exclude or set()
        with cls._lock:
            # 若池中代理偏少，且未在提取中，触发后台补充
            if (len(cls._pool) < 15 or (time.time() - cls._last_fetch_time > 240)) and not cls._is_fetching:
                threading.Thread(target=cls.refresh_pool, args=(False,), daemon=True, name="ProxyRefreshThread").start()

            if cls._pool:
                # 优先寻找不在 exclude_set 中的代理
                for idx, ip in enumerate(cls._pool):
                    p_url = f"http://{ip}"
                    if p_url not in exclude_set and ip not in exclude_set:
                        # 轮转: 移到末尾
                        chosen = cls._pool.pop(idx)
                        cls._pool.append(chosen)
                        return p_url

                # 如果都在 exclude 中，取出队首
                chosen = cls._pool.pop(0)
                cls._pool.append(chosen)
                return f"http://{chosen}"

        # 若池中完全为空，尝试同步拉取一次
        new_ips = cls.refresh_pool(force=True)
        if new_ips:
            for ip in new_ips:
                p_url = f"http://{ip}"
                if p_url not in exclude_set:
                    return p_url
            return f"http://{new_ips[0]}"

        return None

    @classmethod
    def get_candidate_proxies(cls, max_count: int = 5) -> List[str]:
        """
        获取一批可供按序故障转移的候选代理 URL 列表
        """
        with cls._lock:
            pool_len = len(cls._pool)

        # 若代理池为空，立即同步阻塞拉取一批代理
        if pool_len == 0:
            cls.refresh_pool(force=True)
        elif pool_len < 15 and not cls._is_fetching:
            threading.Thread(target=cls.refresh_pool, args=(False,), daemon=True, name="ProxyRefreshThread").start()

        with cls._lock:
            candidates = [f"http://{ip}" for ip in cls._pool[:max_count]]

        return candidates

    @classmethod
    def report_failure(cls, proxy_url: Optional[str] = None):
        """
        报告某个代理失效（超时/连接重置），立即从活跃池中淘汰，避免其他线程踩坑
        """
        if not proxy_url:
            return
        raw = proxy_url.replace("http://", "").replace("https://", "").strip().split("/")[0].strip()
        with cls._lock:
            if raw in cls._pool:
                cls._pool.remove(raw)
                logger.info(f"代理 {raw} 请求失败已立即淘汰，当前池内剩余: {len(cls._pool)}")
                if len(cls._pool) < 10 and not cls._is_fetching:
                    threading.Thread(target=cls.refresh_pool, args=(True,), daemon=True).start()

    @classmethod
    def resilient_request(
        cls,
        method: str,
        url: str,
        max_retries: int = 3,
        fallback_direct: bool = True,
        use_proxy: bool = True,
        **kwargs
    ) -> requests.Response:
        """
        高可用弹性网络请求执行器:
        1. 针对动态代理 502 Bad Gateway、连接超时、隧道中断等故障，自动多候选按序轮询切换
        2. 故障代理实时调用 report_failure 剔除，杜绝坏 IP 污染
        3. 代理候选全部耗尽后，自动无缝降级直连 (fallback_direct=True)，保障业务 100% 连通性
        """
        if "timeout" not in kwargs:
            kwargs["timeout"] = 30

        # Makro 商家后台（seller.makro.co.za）接口严格采用直连，不走携趣代理
        is_seller_api = "seller.makro.co.za" in url
        if not use_proxy or is_seller_api:
            direct_kwargs = dict(kwargs)
            direct_kwargs.pop("proxies", None)
            logger.debug(f"Makro 商家后台直接高速直连访问 (不走代理): {url[:80]}")
            return requests.request(method, url, **direct_kwargs)

        candidates = cls.get_candidate_proxies(max_count=max(max_retries, 3))
        last_exception = None

        for proxy_url in candidates:
            proxies = {"http": proxy_url, "https": proxy_url}
            req_kwargs = dict(kwargs)
            req_kwargs["proxies"] = proxies
            try:
                resp = requests.request(method, url, **req_kwargs)
                # 检查响应是否是代理服务器本身的 502/503/504 错误
                if resp.status_code in (502, 503, 504) and any(
                    err_kw in resp.text.lower()
                    for err_kw in ["bad gateway", "proxy error", "tunnel connection failed", "squid", "cannot connect to server"]
                ):
                    cls.report_failure(proxy_url)
                    logger.warning(f"代理 {proxy_url} 返回网关/隧道错误 HTTP {resp.status_code}，已剔除并尝试下一候选...")
                    last_exception = Exception(f"Proxy returned {resp.status_code}: {resp.text[:120]}")
                    continue
                return resp
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
                cls.report_failure(proxy_url)
                logger.warning(f"代理 {proxy_url} 发生网络/隧道异常 ({type(e).__name__}: {e})，已剔除并尝试下一候选...")
                last_exception = e
                continue
            except requests.exceptions.Timeout as e:
                cls.report_failure(proxy_url)
                logger.warning(f"代理 {proxy_url} 请求超时，已剔除并尝试下一候选...")
                last_exception = e
                continue

        # 如果代理池候选用完或池为空
        if fallback_direct:
            logger.info(f"所有动态代理候选均不可用或池为空，自动切换直连降级通道请求: {url}")
            direct_kwargs = dict(kwargs)
            direct_kwargs.pop("proxies", None)
            try:
                return requests.request(method, url, **direct_kwargs)
            except Exception as direct_e:
                logger.error(f"直连降级通道请求依然失败: {direct_e}")
                raise direct_e

        if last_exception:
            raise last_exception
        raise Exception(f"无可用代理且直连降级未启用: {url}")

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
