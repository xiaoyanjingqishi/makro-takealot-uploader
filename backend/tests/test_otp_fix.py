import unittest
from app.services.email_otp_service import EmailOtpService

class TestEmailOtpService(unittest.TestCase):
    def test_clean_html_to_plain_text(self):
        raw_html = """
        <html>
        <head>
            <style>
                .otp-box { font-size: 24px; color: #333333; margin: 10px; }
                body { background-color: #ffffff; }
            </style>
        </head>
        <body>
            <p>Dear Partner,</p>
            <p>Your One Time Password (OTP) for Makro Seller Portal login is:</p>
            <div class="otp-box" style="font-size: 32px; font-weight: bold; letter-spacing: 5px;">
                591823
            </div>
            <p>This code is valid for 10 minutes.&nbsp;Do not share it.</p>
        </body>
        </html>
        """
        cleaned = EmailOtpService._clean_html_to_plain_text(raw_html)
        self.assertNotIn("<style>", cleaned)
        self.assertNotIn("font-size", cleaned)
        self.assertNotIn("#333333", cleaned)
        self.assertIn("591823", cleaned)
        self.assertIn("Dear Partner", cleaned)

    def test_extract_otp_from_html_body(self):
        raw_html = """
        <html>
        <head>
            <style>.header { color: #1e40af; font-size: 14px; }</style>
        </head>
        <body>
            <div>Hello,</div>
            <p>Your One Time Password (OTP) to verify your Makro Seller account is:</p>
            <div style="font-size: 28px; font-weight: 700; color: #000000; padding: 16px;">
                724915
            </div>
            <p>Please enter this code on the verification screen.</p>
        </body>
        </html>
        """
        otp = EmailOtpService._extract_otp_from_body(raw_html, subject="Makro Seller Portal Login")
        self.assertEqual(otp, "724915")

    def test_extract_otp_from_subject(self):
        subject = "Makro Verification Code: 830192"
        body = "Please use this code to log in."
        otp = EmailOtpService._extract_otp_from_body(body, subject=subject)
        self.assertEqual(otp, "830192")

    def test_extract_otp_flipkart_style(self):
        raw_text = "Dear Seller, 619284 is your OTP for logging into Flipkart Kevlar Seller Portal. Valid for 10 mins."
        otp = EmailOtpService._extract_otp_from_body(raw_text)
        self.assertEqual(otp, "619284")

    def test_ignore_year_and_hex(self):
        raw_text = "Copyright 2026 Makro South Africa. Color theme #202601. Your verification code is 382049."
        otp = EmailOtpService._extract_otp_from_body(raw_text)
        self.assertEqual(otp, "382049")

if __name__ == "__main__":
    unittest.main()
