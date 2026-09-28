import imaplib
import email
from email.header import decode_header
import time
import re
import html
import logging
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 为 Python imaplib 动态注册 ID 扩展指令 (网易 163/126 邮箱防拦截专用)
if "ID" not in imaplib.Commands:
    imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")

def resolve_imap_server(email_address: str) -> Tuple[str, int, bool]:
    """
    根据邮箱后缀智能推断 IMAP 服务器地址、端口及 SSL 模式
    """
    domain = email_address.strip().split("@")[-1].lower() if "@" in email_address else ""
    
    presets = {
        "gmail.com": ("imap.gmail.com", 993, True),
        "googlemail.com": ("imap.gmail.com", 993, True),
        "163.com": ("imap.163.com", 993, True),
        "vip.163.com": ("imap.vip.163.com", 993, True),
        "126.com": ("imap.126.com", 993, True),
        "yeah.net": ("imap.yeah.net", 993, True),
        "qq.com": ("imap.qq.com", 993, True),
        "vip.qq.com": ("imap.qq.com", 993, True),
        "exmail.qq.com": ("imap.exmail.qq.com", 993, True),
        "outlook.com": ("outlook.office365.com", 993, True),
        "hotmail.com": ("outlook.office365.com", 993, True),
        "live.com": ("outlook.office365.com", 993, True),
        "msn.com": ("outlook.office365.com", 993, True),
        "office365.com": ("outlook.office365.com", 993, True),
        "sina.com": ("imap.sina.com", 993, True),
        "vip.sina.com": ("imap.vip.sina.com", 993, True),
        "sohu.com": ("imap.sohu.com", 993, True),
    }

    if domain in presets:
        return presets[domain]
    
    # 默认兜底
    return ("imap.gmail.com", 993, True)


class EmailOtpService:
    """
    多邮箱服务商通用验证码提取引擎 (支持 Gmail, 163, 126, QQ, Outlook 及自定义 IMAP)
    """

    @classmethod
    def _create_imap_client(
        cls,
        server: str,
        port: int = 993,
        use_ssl: bool = True
    ) -> imaplib.IMAP4:
        """创建 IMAP 连接"""
        timeout = 20
        if use_ssl:
            client = imaplib.IMAP4_SSL(server, port, timeout=timeout)
        else:
            client = imaplib.IMAP4(server, port, timeout=timeout)
        return client

    @classmethod
    def _handshake_id_if_needed(cls, client: imaplib.IMAP4, server: str):
        """针对网易系邮箱 (163/126) 自动执行 IMAP ID 握手指令，消除 'NO Select Unsafe Login' 阻断"""
        if "163.com" in server.lower() or "126.com" in server.lower() or "yeah.net" in server.lower():
            try:
                client._simple_command("ID", '("name" "python-makro-uploader" "version" "2.1.0")')
            except Exception as e:
                logger.debug(f"IMAP ID 指令执行提示: {e}")

    @classmethod
    def test_connection(
        cls,
        email_address: str,
        password: str,
        server: Optional[str] = None,
        port: Optional[int] = 993,
        use_ssl: bool = True
    ) -> Dict[str, Any]:
        """
        即时测试邮箱连通性与账号密码有效性
        """
        if not email_address or not password:
            return {"success": False, "message": "邮箱地址或密码/授权码不能为空"}

        resolved_server, resolved_port, resolved_ssl = resolve_imap_server(email_address)
        actual_server = server.strip() if server and server.strip() else resolved_server
        actual_port = int(port) if port else resolved_port

        client = None
        try:
            client = cls._create_imap_client(actual_server, actual_port, use_ssl=resolved_ssl)
            cls._handshake_id_if_needed(client, actual_server)
            client.login(email_address.strip(), password.strip())

            # 再次握手确保选择收件箱权限正常
            cls._handshake_id_if_needed(client, actual_server)
            status, data = client.select("INBOX", readonly=True)
            if status != "OK":
                return {"success": False, "message": f"连接成功但无法打开 INBOX 收件箱: {data}"}

            total_messages = 0
            if data and data[0]:
                try:
                    total_messages = int(data[0].decode())
                except Exception:
                    pass

            return {
                "success": True,
                "message": f"邮箱连接与鉴权成功！收件箱共 {total_messages} 封邮件。",
                "server": actual_server,
                "port": actual_port,
                "total_messages": total_messages
            }
        except imaplib.IMAP4.error as e:
            err_msg = str(e)
            if "authentication failed" in err_msg.lower() or "login failed" in err_msg.lower():
                hint = "鉴权失败：密码错误或未开启 IMAP。如果是 163/QQ 邮箱请使用专用【授权码】，Gmail 请使用【应用专用密码】。"
                return {"success": False, "message": f"{hint} (错误详情: {err_msg})"}
            return {"success": False, "message": f"IMAP 服务端返回错误: {err_msg}"}
        except Exception as e:
            return {"success": False, "message": f"连接邮箱服务器失败 ({actual_server}:{actual_port}): {str(e)}"}
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

    @classmethod
    def get_latest_uid(
        cls,
        email_address: str,
        password: str,
        server: Optional[str] = None,
        port: Optional[int] = 993,
        use_ssl: bool = True
    ) -> int:
        """
        获取当前收件箱的最大 UID (用于登录发信前打点，阻断匹配历史旧邮件)
        """
        if not email_address or not password:
            return 0

        resolved_server, resolved_port, resolved_ssl = resolve_imap_server(email_address)
        actual_server = server.strip() if server and server.strip() else resolved_server
        actual_port = int(port) if port else resolved_port

        client = None
        try:
            client = cls._create_imap_client(actual_server, actual_port, use_ssl=resolved_ssl)
            cls._handshake_id_if_needed(client, actual_server)
            client.login(email_address.strip(), password.strip())
            cls._handshake_id_if_needed(client, actual_server)
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                return 0

            status, data = client.uid("SEARCH", None, "ALL")
            if status == "OK" and data and data[0]:
                uids = [int(x) for x in data[0].split() if x.isdigit()]
                if uids:
                    max_uid = max(uids)
                    logger.info(f"[{email_address}] 登录前预检收件箱最新 UID: {max_uid} (收件箱总计 {len(uids)} 封邮件)")
                    return max_uid
            return 0
        except Exception as e:
            logger.warning(f"获取收件箱最新 UID 失败 (将降级为时间戳容差比对): {e}")
            return 0
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

    @classmethod
    def _decode_mime_str(cls, s: Optional[str]) -> str:
        """解码邮件标题或发件人 MIME 编码字符串"""
        if not s:
            return ""
        decoded_fragments = decode_header(s)
        res = []
        for content, encoding in decoded_fragments:
            if isinstance(content, bytes):
                try:
                    res.append(content.decode(encoding or "utf-8", errors="ignore"))
                except Exception:
                    res.append(content.decode("gbk", errors="ignore"))
            else:
                res.append(str(content))
        return "".join(res)

    @classmethod
    def _clean_html_to_plain_text(cls, content: str) -> str:
        """
        将 HTML 富文本深度清洗为结构清晰的纯文本
        彻底清除 CSS 样式、JavaScript、HTML 标签及实体字符对正则匹配的干扰
        """
        if not content:
            return ""

        # 1. 移除 style、script、head 块及其内部所有属性
        text = re.sub(r'(?is)<style[^>]*>.*?</style>', ' ', content)
        text = re.sub(r'(?is)<script[^>]*>.*?</script>', ' ', text)
        text = re.sub(r'(?is)<head[^>]*>.*?</head>', ' ', text)

        # 2. 将换行或块级 HTML 标签转换为换行
        text = re.sub(r'(?i)<(br|p|div|tr|td|li|h[1-6])[^>]*>', '\n', text)
        text = re.sub(r'(?i)</(p|div|tr|li|h[1-6])>', '\n', text)

        # 3. 剥离所有剩余的 HTML 标签
        text = re.sub(r'<[^>]+>', ' ', text)

        # 4. 反转义 HTML 实体 (如 &nbsp;, &#39;, &amp;)
        text = html.unescape(text)

        # 5. 清理十六进制颜色值 (如 #ffffff, #123456)
        text = re.sub(r'#[0-9a-fA-F]{6}\b', ' ', text)
        text = re.sub(r'#[0-9a-fA-F]{3}\b', ' ', text)

        # 6. 规范化空白字符与多余换行
        text = re.sub(r'[ \t\r\f]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n', text)
        return text.strip()

    @classmethod
    def _extract_otp_from_body(cls, text: str, subject: str = "") -> Optional[str]:
        """
        从邮件正文内容与标题中精准提取 6 位数字 OTP
        """
        if not text and not subject:
            return None

        # 1. 优先检查标题中是否直接含有 6 位验证码 (如 "Makro Seller Portal OTP: 123456")
        if subject:
            subj_clean = re.sub(r'#[0-9a-fA-F]{6}\b', ' ', subject)
            m_sub = re.search(r'(?:verification\s*code|otp|verify|code|验证码)[^\d]{0,25}(\b\d{6}\b)', subj_clean, re.IGNORECASE)
            if m_sub:
                otp = m_sub.group(1)
                logger.info(f"[OTP提取] 从邮件标题直接匹配到验证码: {otp}")
                return otp

        # 2. 深度清洗 HTML 正文为规范纯文本
        clean_text = cls._clean_html_to_plain_text(text)

        # 优先级 1: 紧随强关键字引导的 6 位数字
        p1 = [
            r'(?:verification\s*code|verify\s*code|one\s*time\s*password|otp|code|验证码)\s*(?:is|为|:|：|\-)?\s*(\b\d{6}\b)',
            r'(\b\d{6}\b)\s*(?:is\s*your\s*(?:verification|otp|code|one\s*time)|为您的验证码|是您的验证码)',
            r'(?:verification\s*code|verify\s*code|one\s*time\s*password|otp|验证码)[^\d\n]{0,50}(\b\d{6}\b)',
            r'(\b\d{6}\b)[^\d\n]{0,50}(?:verification\s*code|verify\s*code|one\s*time\s*password|otp|验证码)',
        ]
        for pattern in p1:
            m = re.search(pattern, clean_text, re.IGNORECASE)
            if m:
                otp = m.group(1)
                snippet_start = max(0, m.start() - 25)
                snippet_end = min(len(clean_text), m.end() + 25)
                snippet = clean_text[snippet_start:snippet_end].replace('\n', ' ')
                logger.info(f"[OTP提取] 关键字精准匹配到验证码: {otp} (上下文: ...{snippet}...)")
                return otp

        # 优先级 2: 独立行出现的 6 位数字 (常见于大号居中验证码文本)
        m_standalone = re.search(r'(?:^|\n)\s*(\b\d{6}\b)\s*(?:\n|$)', clean_text)
        if m_standalone:
            otp = m_standalone.group(1)
            logger.info(f"[OTP提取] 独立段落匹配到验证码: {otp}")
            return otp

        # 优先级 3: 提取所有 6 位数字并排除年份与干扰项
        matches = re.findall(r'\b\d{6}\b', clean_text)
        invalid_prefixes = ["2024", "2025", "2026", "2027", "1111", "0000", "1234"]
        filtered = [c for c in matches if not any(c.startswith(p) for p in invalid_prefixes)]
        if filtered:
            otp = filtered[0]
            logger.info(f"[OTP提取] 纯文本候选集匹配到验证码: {otp} (候选集: {filtered})")
            return otp

        return None

    @classmethod
    def _get_email_body(cls, msg: email.message.Message) -> str:
        """提取邮件的正文文本 (包含 text/plain 与 text/html)"""
        body_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" in content_disposition:
                    continue
                if content_type in ["text/plain", "text/html"]:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            body_parts.append(payload.decode(charset, errors="ignore"))
                        except Exception:
                            body_parts.append(payload.decode("gbk", errors="ignore"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    body_parts.append(payload.decode(charset, errors="ignore"))
                except Exception:
                    body_parts.append(payload.decode("gbk", errors="ignore"))

        return "\n".join(body_parts)

    @classmethod
    def poll_otp(
        cls,
        email_address: str,
        password: str,
        server: Optional[str] = None,
        port: Optional[int] = 993,
        use_ssl: bool = True,
        max_wait_seconds: int = 60,
        poll_interval: int = 3,
        since_timestamp: Optional[float] = None,
        min_uid: Optional[int] = None
    ) -> Tuple[bool, Optional[str], str]:
        """
        持续轮询邮箱收件箱，捕获刚刚送达的 Makro 验证码
        支持根据发信前的 min_uid 进行严格高水位过滤，彻底杜绝误抓历史旧邮件
        返回: (is_success, otp_code, message)
        """
        start_time = since_timestamp or (time.time() - 90)
        deadline = time.time() + max_wait_seconds

        resolved_server, resolved_port, resolved_ssl = resolve_imap_server(email_address)
        actual_server = server.strip() if server and server.strip() else resolved_server
        actual_port = int(port) if port else resolved_port

        filter_desc = f"UID > {min_uid}" if (min_uid and min_uid > 0) else f"时间在 {int(start_time)} 之后"
        logger.info(f"开始轮询邮箱 {email_address} ({actual_server}:{actual_port}) 获取 Makro 验证码 (过滤策略: {filter_desc})...")

        client = None
        try:
            client = cls._create_imap_client(actual_server, actual_port, use_ssl=resolved_ssl)
            cls._handshake_id_if_needed(client, actual_server)
            client.login(email_address.strip(), password.strip())
            cls._handshake_id_if_needed(client, actual_server)

            while time.time() < deadline:
                # 重新 select 刷新邮箱最新状态
                status, _ = client.select("INBOX", readonly=True)
                if status != "OK":
                    time.sleep(poll_interval)
                    continue

                target_uids = []
                if min_uid and min_uid > 0:
                    # 优先根据 UID 范围精准查询
                    status, uid_data = client.uid("SEARCH", None, f"UID {min_uid + 1}:*")
                    if status == "OK" and uid_data and uid_data[0]:
                        raw_uids = [int(x) for x in uid_data[0].split() if x.isdigit()]
                        # 严格过滤: 必须严格大于发信前记录的最新 UID
                        target_uids = [u for u in raw_uids if u > min_uid]
                else:
                    # 回退兼容策略: 检索收件箱最新 10 封
                    status, search_data = client.uid("SEARCH", None, "ALL")
                    if status == "OK" and search_data and search_data[0]:
                        raw_uids = [int(x) for x in search_data[0].split() if x.isdigit()]
                        target_uids = raw_uids[-10:] if len(raw_uids) > 10 else raw_uids

                if target_uids:
                    logger.info(f"收件箱检测到候选新邮件 UID 列表: {target_uids}")
                    # 从最新到旧倒序检查
                    for u_val in sorted(target_uids, reverse=True):
                        status, msg_data = client.uid("FETCH", str(u_val), "(RFC822)")
                        if status != "OK" or not msg_data:
                            continue

                        raw_email = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple) and len(response_part) > 1:
                                raw_email = response_part[1]
                                break

                        if not raw_email:
                            continue

                        msg = email.message_from_bytes(raw_email)
                        subject = cls._decode_mime_str(msg.get("Subject", ""))
                        sender = cls._decode_mime_str(msg.get("From", ""))
                        date_str = msg.get("Date")

                        # 若未启用 min_uid，则执行时间戳比对
                        if not (min_uid and min_uid > 0) and date_str:
                            try:
                                msg_date = email.utils.parsedate_to_datetime(date_str)
                                msg_timestamp = msg_date.timestamp()
                                if msg_timestamp < (start_time - 15):
                                    continue
                            except Exception:
                                pass

                        combined_header = f"{subject} {sender}".lower()
                        # 检查是否为 Makro / Flipkart 或验证码相关邮件
                        keywords = ["makro", "flipkart", "otp", "verification", "verify", "code", "security", "login", "auth"]
                        if any(kw in combined_header for kw in keywords):
                            body = cls._get_email_body(msg)
                            otp = cls._extract_otp_from_body(body, subject=subject)
                            if otp:
                                logger.info(f"🎉 成功提取到本次登录 Makro 验证码: {otp} (UID: {u_val}, 主题: 【{subject}】)")
                                return True, otp, f"成功从主题为【{subject}】的邮件中提取到验证码"

                remaining = int(deadline - time.time())
                logger.info(f"等待新验证码邮件送达... (剩余等待时间 {remaining}s)")
                time.sleep(poll_interval)

            return False, None, f"在 {max_wait_seconds} 秒内未在收件箱中检测到新到达的 Makro 验证码邮件，请确认 Makro 发送状态或稍后重试。"

        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP 协议异常: {e}")
            return False, None, f"IMAP 操作异常: {str(e)}"
        except Exception as e:
            logger.error(f"邮箱轮询异常: {e}")
            return False, None, f"邮箱轮询异常: {str(e)}"
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass
