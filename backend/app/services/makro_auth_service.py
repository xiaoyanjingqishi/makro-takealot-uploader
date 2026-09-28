import re
import time
import uuid
import logging
import threading
import requests
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from datetime import datetime

from .email_otp_service import EmailOtpService
from ..models.store import Store
from ..services.audit_logger import record_audit_log

logger = logging.getLogger(__name__)

MAKRO_BASE_URL = "https://seller.makro.co.za"

# 临时内存会话池 (用于存储两步验证过程中的中间会话状态与 Cookies)
_PENDING_SESSIONS: Dict[str, Dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()
SESSION_TTL_SECONDS = 300  # 5 分钟超时

def _clean_expired_sessions():
    """清理已过期的内存会话"""
    now = time.time()
    with _SESSIONS_LOCK:
        expired_keys = [k for k, v in _PENDING_SESSIONS.items() if now - v.get("created_at", 0) > SESSION_TTL_SECONDS]
        for k in expired_keys:
            _PENDING_SESSIONS.pop(k, None)

def parse_set_cookie_header(header_value: str) -> Dict[str, str]:
    """
    鲁棒解析 Makro / Akamai 返回的紧凑型 Set-Cookie 字符串
    解决 ',connect.sid=' 紧凑排布导致 Python 标准库解析丢失的问题
    """
    if not header_value:
        return {}

    cookie_dict = {}
    # 按照逗号且后接合法 cookie 变量名进行切分
    parts = re.split(r',(?=(?:[a-zA-Z0-9_\-\.]+)=)', header_value)
    excluded_keys = {'path', 'domain', 'expires', 'max-age', 'samesite', 'httponly', 'secure', 'priority'}

    for part in parts:
        item = part.strip().split(';')[0]
        if '=' in item:
            k, v = item.split('=', 1)
            k_clean = k.strip()
            if k_clean.lower() not in excluded_keys and k_clean:
                cookie_dict[k_clean] = v.strip()

    return cookie_dict

def format_cookies(cookies: Dict[str, str]) -> str:
    """将 Cookie 字典格式化为 HTTP 请求头字符串"""
    return "; ".join([f"{k}={v}" for k, v in cookies.items() if k and v])


class MakroAuthService:
    """
    Makro 卖家中心全自动认证与凭据同步服务
    """

    @classmethod
    def _build_session(cls) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": MAKRO_BASE_URL,
            "Referer": f"{MAKRO_BASE_URL}/?referral_url=%2Findex.html%3F%23dashboard%2Flistings-management%3FlistingState%3DACTIVE",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json"
        })
        return s

    @classmethod
    def send_login_request(
        cls,
        username: str,
        password: str,
        store_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        步骤 1: 提交账号密码发起登录，触发 Makro 发送邮箱验证码
        返回: { "success": bool, "session_id": str, "masked_email": str, "message": str }
        """
        _clean_expired_sessions()
        session = cls._build_session()

        payload = {
            "authName": "flipkart",
            "username": username.strip(),
            "password": password.strip(),
            "userNameType": "email"
        }

        url = f"{MAKRO_BASE_URL}/login"
        try:
            resp = session.post(url, json=payload, timeout=30)
        except Exception as e:
            return {"success": False, "message": f"连接 Makro 网关失败: {str(e)}"}

        if resp.status_code != 200:
            err_msg = resp.text[:200]
            try:
                err_json = resp.json()
                err_msg = err_json.get("message") or err_json.get("error") or err_msg
            except Exception:
                pass
            return {"success": False, "message": f"Makro 登录请求失败 (HTTP {resp.status_code}): {err_msg}"}

        # 提取全套 Cookies
        cookies_map = session.cookies.get_dict()
        set_cookie_raw = resp.headers.get("Set-Cookie", "")
        if set_cookie_raw:
            parsed = parse_set_cookie_header(set_cookie_raw)
            cookies_map.update(parsed)

        data = resp.json()
        mfa_data = data.get("mfa", {}).get("otp", {}).get("email", {})
        masked_user = mfa_data.get("user_id", username)
        remaining_attempts = mfa_data.get("remaining_attempts", 999)
        expiry_date = mfa_data.get("expiry_date", "")

        # 缓存当前认证会话
        session_id = str(uuid.uuid4())
        with _SESSIONS_LOCK:
            _PENDING_SESSIONS[session_id] = {
                "created_at": time.time(),
                "username": username,
                "password": password,
                "store_id": store_id,
                "cookies": cookies_map,
                "masked_email": masked_user,
                "mfa_info": mfa_data,
                "live_session": session  # 保留活跃 requests.Session 实例，维护完整 TCP/TLS 链路与 CookieJar
            }

        logger.info(f"Makro 账号 {username} 发起登录成功，已触发验证码至: {masked_user} (Session ID: {session_id[:8]}...)")
        return {
            "success": True,
            "session_id": session_id,
            "masked_email": masked_user,
            "remaining_attempts": remaining_attempts,
            "expiry_date": expiry_date,
            "message": f"验证码已发送至邮箱 {masked_user}，请查收"
        }

    @classmethod
    def verify_otp(
        cls,
        session_id: str,
        otp: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        步骤 2: 提交 6 位邮箱验证码，并自动调用 getFeaturesForSeller 提取 csrfToken 与完整凭据
        返回: (is_success, credential_details, message)
        """
        _clean_expired_sessions()
        with _SESSIONS_LOCK:
            sess_info = _PENDING_SESSIONS.get(session_id)

        if not sess_info:
            return False, {}, "登录会话不存在或已超时 (超过 5 分钟)，请重新发起登录。"

        # 优先复用发信时的原生 requests.Session 实例，保留完整的多重 CookieJar (如多 T cookie) 与 TCP/TLS 链路
        session = sess_info.get("live_session")
        if not session:
            session = cls._build_session()
            cookies = sess_info.get("cookies", {})
            session.cookies.update(cookies)
        else:
            # 确保最新 Cookie 也同步
            cookies = sess_info.get("cookies", {})
            session.cookies.update(cookies)

        # 1. 提交验证码
        url_verify = f"{MAKRO_BASE_URL}/verifyOtp"
        logger.info(f"正在向 Makro 提交验证码: {otp} (Session ID: {session_id[:8]}...)")
        try:
            resp_verify = session.post(url_verify, json={"otp": otp.strip()}, timeout=30)
            logger.info(f"Makro /verifyOtp 响应 HTTP {resp_verify.status_code}: {resp_verify.text[:300]}")
        except Exception as e:
            logger.error(f"请求验证码核验接口失败: {e}")
            return False, {}, f"请求验证码核验接口失败: {str(e)}"

        if resp_verify.status_code != 200:
            err_msg = resp_verify.text[:200]
            try:
                err_json = resp_verify.json()
                err_msg = err_json.get("message") or err_json.get("error") or err_msg
            except Exception:
                pass
            return False, {}, f"验证码核验失败 (HTTP {resp_verify.status_code}): {err_msg}"

        verify_data = resp_verify.json()
        if verify_data.get("code") != 1000 and "authenticated successfully" not in str(verify_data.get("message", "")).lower():
            return False, {}, f"验证码校验不通过: {verify_data.get('message', '未知错误')}"

        # 提取更新后的 Cookies (如 sellerId, is_login=true)
        set_cookie_v = resp_verify.headers.get("Set-Cookie", "")
        if set_cookie_v:
            cookies.update(parse_set_cookie_header(set_cookie_v))
        cookies.update(session.cookies.get_dict())
        cookies["is_login"] = "true"

        seller_data = verify_data.get("data", {})
        seller_id = seller_data.get("sellerId") or cookies.get("sellerId")

        # 2. 调用 getFeaturesForSeller 提取 csrfToken 与店铺全局详情
        url_features = f"{MAKRO_BASE_URL}/getFeaturesForSeller"
        session.headers["Referer"] = f"{MAKRO_BASE_URL}/sw.js"
        session.cookies.update(cookies)

        csrf_token = None
        feature_data = {}
        try:
            resp_feat = session.get(url_features, timeout=30)
            if resp_feat.status_code == 200:
                feature_data = resp_feat.json()
                csrf_token = feature_data.get("csrfToken")
                if not seller_id:
                    seller_id = feature_data.get("sellerId")
                # 再次合并新设置的 cookie
                set_cookie_f = resp_feat.headers.get("Set-Cookie", "")
                if set_cookie_f:
                    cookies.update(parse_set_cookie_header(set_cookie_f))
                cookies.update(session.cookies.get_dict())
        except Exception as e:
            logger.warning(f"获取 getFeaturesForSeller 失败: {e}")

        # 整理标准 Cookie 字符串
        cookie_str = format_cookies(cookies)

        # 清理已完成的会话
        with _SESSIONS_LOCK:
            _PENDING_SESSIONS.pop(session_id, None)

        seller_details = feature_data.get("sellerDetails", {})
        display_name = seller_details.get("displayName") or seller_data.get("displayName") or seller_data.get("name") or "Makro 店铺"
        business_name = seller_data.get("businessName") or ""

        result = {
            "seller_id": seller_id,
            "fk_csrf_token": csrf_token,
            "cookie": cookie_str,
            "display_name": display_name,
            "business_name": business_name,
            "store_id": sess_info.get("store_id"),
            "email": sess_info.get("username"),
            "seller_details": seller_details
        }

        logger.info(f"Makro 验证码核验成功！已获取 SellerID: {seller_id}, CSRF: {csrf_token[:10]}...")
        return True, result, "登录成功并已取得完整 Makro 凭据"

    @classmethod
    def sync_to_store_db(
        cls,
        store_id: int,
        seller_id: str,
        csrf_token: Optional[str],
        cookie: str,
        db: Session,
        login_email: Optional[str] = None,
        login_password: Optional[str] = None,
        imap_user: Optional[str] = None,
        imap_password: Optional[str] = None,
        imap_server: Optional[str] = None,
        imap_port: Optional[int] = None,
        notes_extra: Optional[str] = None
    ) -> Store:
        """将提取到的凭据直接持久化到指定店铺"""
        store = db.query(Store).filter(Store.id == store_id).first()
        if not store:
            raise Exception(f"未找到店铺 ID #{store_id}")

        store.seller_id = seller_id.strip() if seller_id else store.seller_id
        if csrf_token:
            store.fk_csrf_token = csrf_token.strip()
        store.cookie = cookie.strip()
        store.updated_at = datetime.now()

        if login_email:
            store.login_email = login_email.strip()
        if login_password:
            store.login_password = login_password.strip()
        if imap_user:
            store.imap_user = imap_user.strip()
        if imap_password:
            store.imap_password = imap_password.strip()
        if imap_server:
            store.imap_server = imap_server.strip()
        if imap_port:
            store.imap_port = int(imap_port)

        db.commit()
        db.refresh(store)

        record_audit_log(
            task_type="STORE_LOGIN_SYNC",
            status="SUCCESS",
            message=f"店铺【{store.name}】自动登录成功，已自动刷新并持久化凭据与邮箱配置",
            detail_logs={
                "store_id": store.id,
                "store_name": store.name,
                "seller_id": store.seller_id,
                "csrf_token": store.fk_csrf_token[:10] if store.fk_csrf_token else None
            },
            db=db
        )
        return store

    @classmethod
    def run_full_auto_login(
        cls,
        store_id: int,
        db: Session,
        override_username: Optional[str] = None,
        override_password: Optional[str] = None,
        override_imap_password: Optional[str] = None,
        override_imap_server: Optional[str] = None,
        override_imap_port: Optional[int] = None,
        override_imap_user: Optional[str] = None,
        max_wait_seconds: int = 60
    ) -> Dict[str, Any]:
        """
        全自动登录流水线：
        0. 预检邮箱收件箱当前最大 UID (阻断历史旧邮件误判)；
        1. 发送 Makro 登录请求；
        2. 自动连接指定邮箱 IMAP 轮询提取本次登录生成的新验证码；
        3. 原生 Session 提交验证码核验；
        4. 自动回写更新数据库店铺凭据与配置；
        5. 调用类目定义接口验证连通性。
        """
        store = db.query(Store).filter(Store.id == store_id).first()
        if not store:
            return {"success": False, "message": f"店铺 ID #{store_id} 不存在"}

        login_email = override_username or store.login_email
        login_password = override_password or store.login_password

        if not login_email or not login_password:
            return {
                "success": False,
                "need_manual_login": True,
                "message": "店铺未配置 Makro 登录邮箱或登录密码，无法执行全自动登录。请在店铺编辑中配置，或使用手动登录弹窗。"
            }

        # 邮箱配置解析
        imap_user = override_imap_user or store.imap_user or login_email
        imap_password = override_imap_password or store.imap_password
        imap_server = override_imap_server or store.imap_server
        imap_port = override_imap_port or store.imap_port or 993

        if not imap_password:
            return {
                "success": False,
                "need_manual_otp": True,
                "message": f"店铺未配置邮箱应用专用密码/授权码（账号: {imap_user}），无法自动收信。请在店铺编辑中配置，或在弹窗中手动输入验证码。"
            }

        # 阶段 0: 发信前打点，获取当前收件箱最新 UID (彻底杜绝误抓历史旧邮件)
        logger.info(f"正在预检邮箱 {imap_user} 收件箱最新邮件状态...")
        min_uid = EmailOtpService.get_latest_uid(
            email_address=imap_user,
            password=imap_password,
            server=imap_server,
            port=imap_port
        )
        logger.info(f"预检完成: 收件箱发信前最高 UID 为 {min_uid}，后续将严格只等待新邮件到达")

        # 记录发信起始时间戳 (容许轻微漂移)
        send_time = time.time() - 5

        # 阶段 1: 发起登录
        logger.info(f"正在为店铺【{store.name}】发起 Makro 登录请求...")
        login_res = cls.send_login_request(login_email, login_password, store_id=store.id)
        if not login_res.get("success"):
            return {
                "success": False,
                "message": f"发起 Makro 登录失败: {login_res.get('message')}"
            }

        session_id = login_res["session_id"]
        masked_email = login_res.get("masked_email", login_email)

        # 阶段 2: 轮询邮箱获取验证码 (仅检查 UID > min_uid 的新邮件)
        logger.info(f"正在从邮箱 {imap_user} 轮询提取 Makro 验证码 (仅匹配 UID > {min_uid}，最长等待 {max_wait_seconds}s)...")
        otp_ok, otp_code, otp_msg = EmailOtpService.poll_otp(
            email_address=imap_user,
            password=imap_password,
            server=imap_server,
            port=imap_port,
            max_wait_seconds=max_wait_seconds,
            since_timestamp=send_time,
            min_uid=min_uid
        )

        if not otp_ok or not otp_code:
            logger.warning(f"未能自动提取到验证码: {otp_msg}")
            return {
                "success": False,
                "session_id": session_id,
                "masked_email": masked_email,
                "need_manual_otp": True,
                "message": f"自动抓取验证码超时: {otp_msg}。已保留当前登录会话，您可以手动输入邮箱收到的 6 位验证码。"
            }

        # 阶段 3: 提交验证码并提取完整凭据
        logger.info(f"成功提取验证码【{otp_code}】，正在提交 Makro 校验...")
        verify_ok, creds, verify_msg = cls.verify_otp(session_id, otp_code)
        if not verify_ok:
            return {
                "success": False,
                "message": f"提交验证码失败: {verify_msg}"
            }

        # 阶段 4: 同步至数据库 (同时保存登录账密与邮箱配置，以便后续全自动续期与免密登录)
        seller_id = creds.get("seller_id") or store.seller_id
        csrf_token = creds.get("fk_csrf_token")
        cookie_str = creds.get("cookie")

        cls.sync_to_store_db(
            store_id=store.id,
            seller_id=seller_id,
            csrf_token=csrf_token,
            cookie=cookie_str,
            db=db,
            login_email=login_email,
            login_password=login_password,
            imap_user=imap_user,
            imap_password=imap_password,
            imap_server=imap_server,
            imap_port=imap_port
        )

        # 阶段 5: 轻量级接口验证
        check_msg = "连通性测试通过"
        try:
            from .makro_client import MakroClient
            client = MakroClient.from_store(store)
            client.get_vertical_definition("bath_towel")
        except Exception as e:
            check_msg = f"已保存凭据，但接口验证提示: {str(e)[:60]}"

        return {
            "success": True,
            "message": f"🎉 店铺【{store.name}】全自动登录成功！凭据与邮箱配置已持久化，{check_msg}。",
            "seller_id": seller_id,
            "fk_csrf_token": csrf_token,
            "display_name": creds.get("display_name"),
            "business_name": creds.get("business_name")
        }
