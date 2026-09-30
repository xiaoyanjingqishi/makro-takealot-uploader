import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal
from app.models.store import Store
from app.models.user import User
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.makro_reprice_log import MakroRepriceLog
from app.services.auto_reprice_service import AutoRepriceService
from app.utils.auth import create_access_token

class TestAutoReprice(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        self.store = self.db.query(Store).filter(Store.is_default == True).first()
        if not self.store:
            self.store = Store(
                name="TestStore",
                seller_id="cb80491bf0a34dc5",
                fk_csrf_token="test_token",
                cookie="test_cookie",
                default_brand="Generic",
                default_location_id="LOC_TEST",
                is_active=True,
                is_default=True
            )
            self.db.add(self.store)
            self.db.commit()
            self.db.refresh(self.store)

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

    def test_repricing_winning_hold(self):
        """测试自己已经是最低价持有者时，保持价格不恶性降价 (WINNING_HOLD)"""
        item = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="TEST_FSN_001",
            title="Winning Item",
            seller_sku="SKU-WIN-001",
            target_price=150.0,
            original_price=150.0,
            min_price_floor=100.0,
            auto_reprice=True,
            price_strategy="MINUS_1",
            status="ACTIVE"
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)

        with patch("app.services.makro_scraper_service.MakroScraperService.scrape_buyer_frontend") as mock_scrape:
            mock_scrape.return_value = {
                "price": 150.0,
                "mrp": 200.0,
                "seller_name": self.store.name,
                "seller_count": 2
            }
            res = AutoRepriceService.reprice_single_item(item, self.db)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["action"], "WINNING_HOLD")
            self.assertEqual(res["new_price"], 150.0)

            # 验证日志写入
            log = self.db.query(MakroRepriceLog).filter(MakroRepriceLog.piggyback_id == item.id).order_by(MakroRepriceLog.id.desc()).first()
            self.assertIsNotNone(log)
            self.assertEqual(log.action, "WINNING_HOLD")

    def test_repricing_floor_protection(self):
        """测试当降价超过最低防亏底价时，限制在最低价 (REACHED_FLOOR)"""
        item = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="TEST_FSN_002",
            title="Floor Protection Item",
            seller_sku="SKU-FLOOR-002",
            target_price=120.0,
            original_price=120.0,
            min_price_floor=100.0,
            auto_reprice=True,
            price_strategy="MINUS_1",
            status="ACTIVE"
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)

        with patch("app.services.makro_scraper_service.MakroScraperService.scrape_buyer_frontend") as mock_scrape, \
             patch("app.services.auto_reprice_service.AutoRepriceService._push_price_to_makro") as mock_push:
            mock_scrape.return_value = {
                "price": 95.0,  # 对手打出了 95 元，而底价为 100
                "mrp": 150.0,
                "seller_name": "CompetitorX",
                "seller_count": 3
            }
            mock_push.return_value = (True, "Price updated successfully")

            res = AutoRepriceService.reprice_single_item(item, self.db)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["action"], "REACHED_FLOOR")
            self.assertEqual(res["new_price"], 100.0) # 守住底线

    def test_repricing_undercut(self):
        """测试正常压价跟价 降 1 兰特 (UNDER_CUT)"""
        item = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="TEST_FSN_003",
            title="Undercut Item",
            seller_sku="SKU-CUT-003",
            target_price=200.0,
            original_price=180.0,
            min_price_floor=120.0,
            auto_reprice=True,
            price_strategy="MINUS_1",
            status="ACTIVE"
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)

        with patch("app.services.makro_scraper_service.MakroScraperService.scrape_buyer_frontend") as mock_scrape, \
             patch("app.services.auto_reprice_service.AutoRepriceService._push_price_to_makro") as mock_push:
            mock_scrape.return_value = {
                "price": 180.0,
                "mrp": 250.0,
                "seller_name": "CompetitorY",
                "seller_count": 2
            }
            mock_push.return_value = (True, "Price updated successfully")

            res = AutoRepriceService.reprice_single_item(item, self.db)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["action"], "UNDER_CUT")
            self.assertEqual(res["new_price"], 179.0) # 180 - 1 = 179

    @patch("app.services.makro_scraper_service.MakroScraperService.resolve_piggyback_product")
    def test_batch_collect_with_rich_items(self, mock_resolve):
        """测试从搜索页或变体矩阵批量采集富文本数据"""
        mock_resolve.return_value = {
            "makro_product_id": "FSN_RICH_001",
            "item_id": "itm_rich_001",
            "makro_url": "https://www.makro.co.za/-/p/itm_rich_001?pid=FSN_RICH_001",
            "title": "Rich Search Result Item",
            "title_zh": "Rich 搜索结果商品",
            "brand": "Generic",
            "vertical": "general",
            "image_url": "https://www.makro.co.za/img/1.jpg",
            "model_number": "",
            "barcode": "",
            "original_price": 299.0,
            "original_mrp": 399.0,
            "original_seller": "OtherSeller",
            "seller_count": 1
        }
        payload = {
            "store_id": self.store.id,
            "auto_comply": False,
            "rich_items": [
                {
                    "fsn": "FSN_RICH_001",
                    "item_id": "itm_rich_001",
                    "title": "Rich Search Result Item",
                    "variant_name": "Black / 10-Pack",
                    "variant_attributes": {"color": "Black", "pack": "10-Pack"}
                }
            ]
        }
        res = self.client.post("/api/piggyback/batch-collect", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total_requested"], 1)
        self.assertEqual(data["success_count"], 1)

        # 检查数据库保存的记录
        item = self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.makro_product_id == "FSN_RICH_001").first()
        self.assertIsNotNone(item)
        self.assertEqual(item.variant_name, "Black / 10-Pack")
        self.assertIn("color", item.variant_attributes)
        self.assertEqual(item.target_price, 298.0) # 默认压价 1 兰特

    def test_reprice_logs_api(self):
        """测试调价日志列表接口"""
        res = self.client.get("/api/reprice/logs?page=1&page_size=10", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("items", data)
        self.assertIn("total", data)

if __name__ == "__main__":
    unittest.main()
