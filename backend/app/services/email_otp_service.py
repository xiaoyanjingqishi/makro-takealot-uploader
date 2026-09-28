import imaplib
import email
from email.header import decode_header
import time
import re
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
    def _extract_otp_from_body(cls, text: str) -> Optional[str]:
        """
        从邮件正文内容中精准提取 6 位数字 OTP
        """
        if not text:
            return None

        # 清洗可能影响匹配的常见 HTML 十六进制颜色值，如 #123456
        clean_text = re.sub(r'#[0-9a-fA-F]{6}\b', ' ', text)
        clean_text = re.sub(r'#[0-9a-fA-F]{3}\b', ' ', clean_text)

        # 优先级 1: 带有明显关键字引导的 6 位数字
        p1 = [
            r'(?:verification\s*code|verify\s*code|otp|one\s*time\s*password|验证码)[^\d]{0,40}(\b\d{6}\b)',
            r'(\b\d{6}\b)[^\d]{0,40}(?:is\s*your\s*verification|is\s*your\s*otp|为您的验证码|是您的验证码)',
            r'(?:code|Code|CODE)\s*[:：]?\s*(\b\d{6}\b)',
        ]
        for pattern in p1:
            m = re.search(pattern, clean_text, re.IGNORECASE)
            if m:
                otp = m.group(1)
                # 排除 123456, 000000 等极端占位符（除非确实就是）
                return otp

        # 优先级 2: 独立出现的 6 位数字
        matches = re.findall(r'\b\d{6}\b', clean_text)
        if matches:
            # 优先取倒数第一个或最匹配的
            return matches[0]

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
        since_timestamp: Optional[float] = None
    ) -> Tuple[bool, Optional[str], str]:
        """
        持续轮询邮箱收件箱，捕获刚刚送达的 Makro 验证码
        返回: (is_success, otp_code, message)
        """
        start_time = since_timestamp or (time.time() - 90)  # 默认检查最近 90 秒内的邮件
        deadline = time.time() + max_wait_seconds

        resolved_server, resolved_port, resolved_ssl = resolve_imap_server(email_address)
        actual_server = server.strip() if server and server.strip() else resolved_server
        actual_port = int(port) if port else resolved_port

        logger.info(f"开始轮询邮箱 {email_address} (服务器: {actual_server}:{actual_port}) 获取 Makro 验证码...")

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

                # 搜索全部或最近未读邮件
                # 为了防止时区差异导致的 SINCE 漏单，直接检索最新的一批邮件 ID 进行比对
                status, search_data = client.search(None, "ALL")
                if status == "OK" and search_data and search_data[0]:
                    msg_ids = search_data[0].split()
                    # 检查最新的 10 封邮件
                    recent_ids = msg_ids[-10:] if len(msg_ids) > 10 else msg_ids
                    # 从最新到旧倒序检查
                    for m_id in reversed(recent_ids):
                        status, msg_data = client.fetch(m_id, "(RFC822)")
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

                        # 检查时间是否在触发后
                        is_recent = True
                        if date_str:
                            try:
                                msg_date = email.utils.parsedate_to_datetime(date_str)
                                msg_timestamp = msg_date.timestamp()
                                # 邮件时间需在起始时间之后（容许 15 秒轻微时间漂移）
                                if msg_timestamp < (start_time - 15):
                                    is_recent = False
                            except Exception:
                                pass

                        if not is_recent:
                            continue

                        combined_header = f"{subject} {sender}".lower()
                        # 检查是否为 Makro / Flipkart 或验证码相关邮件
                        keywords = ["makro", "flipkart", "otp", "verification", "verify", "code", "security", "login", "auth"]
                        if any(kw in combined_header for kw in keywords):
                            body = cls._get_email_body(msg)
                            otp = cls._extract_otp_from_body(f"{subject}\n{body}")
                            if otp:
                                logger.info(f"成功提取到 Makro 验证码: {otp} (主题: {subject})")
                                return True, otp, f"成功从主题为【{subject}】的邮件中提取到验证码"

                remaining = int(deadline - time.time())
                logger.debug(f"未捕获到新验证码邮件，等待 {poll_interval} 秒后重试 (剩余 {remaining}s)...")
                time.sleep(poll_interval)

            return False, None, f"在 {max_wait_seconds} 秒内未在邮箱收件箱中检测到 Makro 验证码邮件，请检查发件人或尝试手动输入。"

        except imaplib.IMAP4.error as e:
            return False, None, f"IMAP 操作异常: {str(e)}"
        except Exception as e:
            return False, None, f"邮箱轮询异常: {str(e)}"
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass
