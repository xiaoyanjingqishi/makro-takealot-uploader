import sys
import unittest
from pathlib import Path

# 添加 backend 到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.store import Store
from app.utils.auth import hash_password, create_access_token
from app.services.auto_login_scheduler import auto_login_scheduler

class TestSchedulerAndRBAC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        db = SessionLocal()
        try:
            # 确保有一个管理员用户和一个普通用户
            admin = db.query(User).filter(User.username == "test_admin_rbac").first()
            if not admin:
                admin = User(
                    username="test_admin_rbac",
                    password_hash=hash_password("admin123"),
                    role="ADMIN",
                    nickname="Test Admin",
                    is_active=True
                )
                db.add(admin)

            operator = db.query(User).filter(User.username == "test_operator_rbac").first()
            if not operator:
                operator = User(
                    username="test_operator_rbac",
                    password_hash=hash_password("op123"),
                    role="OPERATOR",
                    nickname="Test Operator",
                    is_active=True
                )
                db.add(operator)

            db.commit()

            cls.admin_token = create_access_token({"user_id": admin.id, "sub": "test_admin_rbac"})
            cls.operator_token = create_access_token({"user_id": operator.id, "sub": "test_operator_rbac"})
        finally:
            db.close()

    def test_01_operator_cannot_save_settings(self):
        """测试普通用户 (OPERATOR) 无法修改系统设置，返回 403 Forbidden"""
        headers = {"Authorization": f"Bearer {self.operator_token}"}
        payload = {
            "markup_ratio": 1.5,
            "auto_login_check_enabled": True,
            "auto_login_check_interval_hours": 21.0
        }
        resp = self.client.post("/api/settings", json=payload, headers=headers)
        self.assertEqual(resp.status_code, 403, "普通操作员应该被拒绝修改系统设置")

    def test_02_operator_gets_masked_settings(self):
        """测试普通用户查看系统设置时，敏感 API Key 和 Cookie 被脱敏"""
        headers = {"Authorization": f"Bearer {self.operator_token}"}
        resp = self.client.get("/api/settings", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        if data.get("qwen_api_key"):
            self.assertEqual(data.get("qwen_api_key"), "******")
        if data.get("cookie"):
            self.assertEqual(data.get("cookie"), "******")

    def test_03_admin_can_save_settings(self):
        """测试管理员 (ADMIN) 可以修改系统设置并热重载调度器间隔"""
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        # 先读取原设置
        resp = self.client.get("/api/settings", headers=headers)
        self.assertEqual(resp.status_code, 200)
        curr = resp.json()

        # 修改巡检间隔为 18.0 小时
        curr["auto_login_check_interval_hours"] = 18.0
        curr["auto_login_check_enabled"] = True

        save_resp = self.client.post("/api/settings", json=curr, headers=headers)
        self.assertEqual(save_resp.status_code, 200)
        self.assertEqual(save_resp.json().get("message"), "配置更新成功")

        # 读取保存后的配置验证
        verify_resp = self.client.get("/api/settings", headers=headers)
        self.assertEqual(verify_resp.status_code, 200)
        saved = verify_resp.json()
        self.assertEqual(saved.get("auto_login_check_interval_hours"), 18.0)

        # 验证调度器实时更新
        self.assertEqual(auto_login_scheduler.interval_hours, 18.0)

    def test_04_store_credential_caching(self):
        """测试店铺的登录账号、登录密码和邮箱授权码能够成功保存并返回"""
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        store_payload = {
            "name": "RBAC测试店铺",
            "seller_id": "TEST_SELLER_999",
            "login_email": "tester@example.com",
            "login_password": "MySecretMakroPassword123!",
            "imap_provider": "163",
            "imap_server": "imap.163.com",
            "imap_port": 993,
            "imap_user": "tester@example.com",
            "imap_password": "MySecretImapPassword456!",
            "is_active": True
        }
        resp = self.client.post("/api/stores", json=store_payload, headers=headers)
        self.assertEqual(resp.status_code, 200)
        store_data = resp.json()
        store_id = store_data["id"]

        try:
            # 校验创建后返回的凭据
            self.assertEqual(store_data.get("login_password"), "MySecretMakroPassword123!")
            self.assertEqual(store_data.get("imap_password"), "MySecretImapPassword456!")
            self.assertTrue(store_data.get("has_login_password"))
            self.assertTrue(store_data.get("has_imap_password"))

            # 校验列表查询也包含凭据
            list_resp = self.client.get("/api/stores", headers=headers)
            self.assertEqual(list_resp.status_code, 200)
            stores = list_resp.json()
            matched = next((s for s in stores if s["id"] == store_id), None)
            self.assertIsNotNone(matched)
            self.assertEqual(matched.get("login_password"), "MySecretMakroPassword123!")
            self.assertEqual(matched.get("imap_password"), "MySecretImapPassword456!")
        finally:
            # 清理测试店铺
            self.client.delete(f"/api/stores/{store_id}", headers=headers)

if __name__ == "__main__":
    unittest.main()
