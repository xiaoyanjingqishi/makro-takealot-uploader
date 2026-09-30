import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal, engine, Base
from app.models.store import Store
from app.models.user import User
from app.models.makro_piggyback import MakroPiggybackItem
from app.utils.auth import create_access_token

class TestPiggybackAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        # 准备或获取默认测试店铺
        self.store = self.db.query(Store).filter(Store.is_default == True).first()
        if not self.store:
            self.store = Store(
                name="API测试店铺",
                seller_id="cb80491bf0a34dc5",
                fk_csrf_token="test_token",
                cookie="test_cookie",
                default_brand="Beishi",
                default_location_id="LOC_TEST",
                is_active=True,
                is_default=True
            )
            self.db.add(self.store)
            self.db.commit()
            self.db.refresh(self.store)

        # 准备管理员用户 Token
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

    @patch("app.services.makro_scraper_service.MakroScraperService.resolve_piggyback_product")
    @patch("app.services.makro_piggyback_service.MakroPiggybackService.check_compliance_for_item")
    def test_collect_and_list_lifecycle(self, mock_comp, mock_resolve):
        # 1. 模拟抓取解析
        mock_resolve.return_value = {
            "makro_product_id": "GSPHPVTNMFHDAWV4",
            "makro_url": "https://www.makro.co.za/p/GSPHPVTNMFHDAWV4",
            "title": "Max 360 Degree Rotating Micro Sprinkler Nozzles 10 Pack",
            "title_zh": "Max 360度微喷喷头10个装",
            "brand": "Max",
            "vertical": "garden_sprayer",
            "image_url": "https://www.makro.co.za/asset/img.jpg",
            "model_number": "MSN-10",
            "barcode": "6009123456789",
            "original_price": 499.0,
            "original_mrp": 799.0
        }
        mock_comp.return_value = {"status": "SAFE", "summary": "经检测无品牌侵权风险"}

        # 调用采集接口
        resp = self.client.post(
            "/api/piggyback/collect",
            headers=self.headers,
            json={
                "url_or_fsn": "GSPHPVTNMFHDAWV4",
                "store_id": self.store.id,
                "price_strategy": "MINUS_1",
                "min_price_floor": 200.0
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        item = data["item"]
        self.assertEqual(item["makro_product_id"], "GSPHPVTNMFHDAWV4")
        self.assertEqual(item["target_price"], 498.0) # 499 - 1
        item_id = item["id"]

        # 2. 查询列表
        list_resp = self.client.get(
            f"/api/piggyback/items?search=GSPHPVTNMFHDAWV4",
            headers=self.headers
        )
        self.assertEqual(list_resp.status_code, 200)
        list_data = list_resp.json()
        self.assertGreaterEqual(list_data["total"], 1)

        # 3. 修改单品参数
        update_resp = self.client.put(
            f"/api/piggyback/items/{item_id}",
            headers=self.headers,
            json={
                "target_price": 488.0,
                "inventory": 88
            }
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["item"]["target_price"], 488.0)
        self.assertEqual(update_resp.json()["item"]["inventory"], 88)

        # 4. 批量改价测试
        batch_price_resp = self.client.post(
            "/api/piggyback/batch-apply-pricing",
            headers=self.headers,
            json={
                "ids": [item_id],
                "price_strategy": "PERCENT_2"
            }
        )
        self.assertEqual(batch_price_resp.status_code, 200)
        self.assertEqual(batch_price_resp.json()["updated_count"], 1)

        # 5. 删除测试
        del_resp = self.client.delete(f"/api/piggyback/items/{item_id}", headers=self.headers)
        self.assertEqual(del_resp.status_code, 200)
        print("[OK] Piggyback API lifecycle test passed successfully!")

if __name__ == "__main__":
    unittest.main()
