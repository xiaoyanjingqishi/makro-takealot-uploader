import unittest
from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal
from app.models.user import User
from app.utils.auth import create_access_token

class TestPiggybackBatchEnhancements(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.user = self.db.query(User).filter(User.username == "admin").first()
        if not self.user:
            self.user = User(username="admin", role="ADMIN", is_active=True)
            self.user.set_password("admin123")
            self.db.add(self.user)
            self.db.commit()
            self.db.refresh(self.user)
        token = create_access_token({"sub": self.user.username, "user_id": self.user.id, "role": self.user.role})
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        self.db.close()

    def test_get_piggyback_ids(self):
        # 1. 测试 /api/piggyback/ids 端点
        resp = self.client.get("/api/piggyback/ids?stage=ALL", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("ids", data)
        self.assertIn("total", data)
        self.assertEqual(len(data["ids"]), data["total"])

    def test_piggyback_compliance_stats(self):
        # 2. 测试 /api/piggyback/items 中 stats 包含所有的风控字段
        resp = self.client.get("/api/piggyback/items?stage=ALL", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        stats = data.get("stats", {})
        self.assertIn("safe_count", stats)
        self.assertIn("risk_count", stats)
        self.assertIn("prohibited_count", stats)
        self.assertIn("pending_check_count", stats)

if __name__ == "__main__":
    unittest.main()
