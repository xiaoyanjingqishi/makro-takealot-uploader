import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.email_otp_service import EmailOtpService, resolve_imap_server, _detect_available_proxy

def test_proxy_detection():
    # 验证代理探测函数正常执行且不抛异常
    proxy = _detect_available_proxy()
    print(f"[OK] Proxy detected in current environment: {proxy}")

def test_email_resolution():
    cases = [
        ('test@163.com', 'imap.163.com'),
        ('test@gmail.com', 'imap.gmail.com'),
        ('user@qq.com', 'imap.qq.com'),
        ('seller@outlook.com', 'outlook.office365.com'),
        ('corp@exmail.qq.com', 'imap.exmail.qq.com'),
        ('user@126.com', 'imap.126.com'),
        ('user@yeah.net', 'imap.yeah.net')
    ]
    for email_addr, expected_server in cases:
        srv, port, ssl = resolve_imap_server(email_addr)
        assert srv == expected_server, f'{email_addr} got {srv} != {expected_server}'
        assert port == 993 and ssl is True
    print("[OK] IMAP server resolution test passed!")

def test_otp_extraction():
    samples = [
        ('Your Makro Verification Code is 779558. Valid for 10 minutes.', '779558'),
        ('Dear Seller, use OTP: 118973 to login to Flipkart Makro Portal.', '118973'),
        ('<p style="color:#333333">Your one-time password is <strong>482910</strong></p>', '482910'),
        ('Flipkart Seller Hub: 928371 is your verification code.', '928371'),
        ('验证码：551920，请在10分钟内输入。', '551920')
    ]
    for text, expected in samples:
        extracted = EmailOtpService._extract_otp_from_body(text)
        assert extracted == expected, f'Got {extracted} != {expected} for "{text}"'
    print("[OK] OTP extraction regex test passed!")

if __name__ == "__main__":
    test_proxy_detection()
    test_email_resolution()
    test_otp_extraction()
    print("ALL TESTS PASSED SUCCESSFULLY!")
