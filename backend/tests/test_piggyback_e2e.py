import unittest
import json
from unittest.mock import patch, MagicMock
from app.services.makro_scraper_service import MakroScraperService
from app.services.makro_piggyback_service import MakroPiggybackService
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.store import Store
from app.database import SessionLocal, engine, Base

class TestMakroPiggyback(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_extract_fsn(self):
        # 1. 纯 FSN
        self.assertEqual(MakroScraperService.extract_fsn("GSPHPVTNMFHDAWV4"), "GSPHPVTNMFHDAWV4")
        self.assertEqual(MakroScraperService.extract_fsn("pmphaf9gafsfwmhm"), "PMPHAF9GAFSFWMHM")
        # 2. 前台 URL
        url1 = "https://www.makro.co.za/garden-pool/garden-care/garden-sprayers/max-360-degree-rotating-micro-sprinkler-nozzles-with-g-original-imahpvtmvyufxtk7/p/GSPHPVTNMFHDAWV4"
        self.assertEqual(MakroScraperService.extract_fsn(url1), "GSPHPVTNMFHDAWV4")
        # 3. 带参数 URL
        url2 = "https://www.makro.co.za/catalog/product?pid=BORHNWUPAVGHD4EV&ref=search"
        self.assertEqual(MakroScraperService.extract_fsn(url2), "BORHNWUPAVGHD4EV")
        # 4. 无效格式
        self.assertIsNone(MakroScraperService.extract_fsn("invalid_short_str"))

    def test_pricing_strategy(self):
        # MINUS_1: 499 - 1 = 498
        p, m = MakroPiggybackService.calculate_price(499.0, strategy="MINUS_1", min_floor=0.0, original_mrp=799.0)
        self.assertEqual(p, 498.0)
        self.assertEqual(m, 799.0)

        # PERCENT_2: 100 * 0.98 = 98
        p, m = MakroPiggybackService.calculate_price(100.0, strategy="PERCENT_2", min_floor=0.0)
        self.assertEqual(p, 98.0)

        # Floor protection: 100 with floor 99 -> 99
        p, m = MakroPiggybackService.calculate_price(100.0, strategy="PERCENT_2", min_floor=99.0)
        self.assertEqual(p, 99.0)

        # MANUAL
        p, m = MakroPiggybackService.calculate_price(250.0, strategy="MANUAL")
        self.assertEqual(p, 250.0)

    def test_compliance_blocking_gate(self):
        # 验证红线侵权禁售品绝对禁止跟品
        prohibited_item = MakroPiggybackItem(
            makro_product_id="TESTFSN123456789",
            title="Fake Chanel Luxury Diamond Bag",
            seller_sku="GPTEST001",
            compliance_status="PROHIBITED",
            target_price=299.0,
            target_mrp=599.0
        )
        fake_store = MagicMock()
        fake_store.name = "测试店铺"

        with self.assertRaises(ValueError) as ctx:
            MakroPiggybackService.publish_piggyback_listing(prohibited_item, fake_store, self.db)
        
        self.assertIn("红线拦截", str(ctx.exception))
        print("[OK] Compliance safety gate successfully blocked PROHIBITED item from publishing!")

    @patch("requests.get")
    def test_search_product_with_har_fixture(self, mock_get):
        # 模拟 HAR 中 searchProduct 的响应
        har_resp_mock = MagicMock()
        har_resp_mock.status_code = 200
        har_resp_mock.json.return_value = {
            "result": {
                "productList": [
                    {
                        "entityId": "GSPHPVTNMFHDAWV4",
                        "detail": {
                            "Brand": "Max",
                            "Model Number": "360 Degree Rotating Micro Sprinkler",
                            "Packaging Type": "Pack"
                        },
                        "title": "Max 360 Degree Rotating Micro Sprinkler Nozzles with G Connector",
                        "vertical": "garden_sprayer",
                        "imagePaths": {
                            "275x275": "https://www.makro.co.za/asset/cms/garden-sprayer/8/1/b/1-pack-original-imahpvtmvyufxtk7.jpeg"
                        },
                        "alreadySelling": False
                    }
                ]
            }
        }
        mock_get.return_value = har_resp_mock

        fake_store = MagicMock()
        fake_store.seller_id = "cb80491bf0a34dc5"
        fake_store.fk_csrf_token = "token123"
        fake_store.cookie = "cookie123"
        fake_store.default_location_id = "LOC123"
        fake_store.default_brand = "Beishi"

        prod_data = MakroScraperService.fetch_product_by_fsn_from_seller_api("GSPHPVTNMFHDAWV4", fake_store)
        self.assertIsNotNone(prod_data)
        self.assertEqual(prod_data["fsn"], "GSPHPVTNMFHDAWV4")
        self.assertEqual(prod_data["brand"], "Max")
        self.assertEqual(prod_data["vertical"], "garden_sprayer")
        self.assertIn("original", prod_data["image_url"])
        print("[OK] HAR-based searchProduct test passed with official metadata:", prod_data["title"])

if __name__ == "__main__":
    unittest.main()
