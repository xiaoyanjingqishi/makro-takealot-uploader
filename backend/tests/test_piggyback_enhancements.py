import unittest
try:
    from app.services.makro_scraper_service import MakroScraperService
except ImportError:
    from backend.app.services.makro_scraper_service import MakroScraperService

class TestPiggybackEnhancements(unittest.TestCase):
    def test_user_provided_url_extraction(self):
        url = "https://www.makro.co.za/max-360-degree-rotating-micro-sprinkler-nozzles-g-connector-garden-watering-misting-10-pack-hanging-design-1-hand-held-sprayer/p/itmdda5c11c09523?pid=GSPHPVTNMFHDAWV4&lid=LSTGSPHPVTNMFHDAWV4ZJ6LV3&sattr[]=packaging_type&st=packaging_type"
        fsn, item_id = MakroScraperService.extract_identifiers(url)
        self.assertEqual(fsn, "GSPHPVTNMFHDAWV4")
        self.assertEqual(item_id, "itmdda5c11c09523")

        canonical = MakroScraperService.format_canonical_makro_url(fsn, item_id)
        self.assertEqual(canonical, "https://www.makro.co.za/-/p/itmdda5c11c09523?pid=GSPHPVTNMFHDAWV4")

    def test_fsn_only_extraction(self):
        raw = "GSPHPVTNMFHDAWV4"
        fsn, item_id = MakroScraperService.extract_identifiers(raw)
        self.assertEqual(fsn, "GSPHPVTNMFHDAWV4")
        self.assertIsNone(item_id)

        canonical = MakroScraperService.format_canonical_makro_url(fsn, item_id)
        self.assertEqual(canonical, "https://www.makro.co.za/-/p/GSPHPVTNMFHDAWV4?pid=GSPHPVTNMFHDAWV4")

    def test_client_data_hint_resolution(self):
        class MockStore:
            seller_id = "test_seller"
            fk_csrf_token = "token"
            cookie = "cookie"
            default_location_id = "loc1"
            default_brand = "Generic"

        client_data = {
            "item_id": "itmdda5c11c09523",
            "title": "Max 360 Degree Sprinkler Nozzles",
            "price": 499.0,
            "mrp": 665.0,
            "image_url": "https://www.makro.co.za/asset/rukmini/400/400/img.jpg",
            "seller_name": "pumu222",
            "seller_count": 1
        }
        res = MakroScraperService.resolve_piggyback_product("GSPHPVTNMFHDAWV4", MockStore(), client_data=client_data)
        self.assertEqual(res["makro_product_id"], "GSPHPVTNMFHDAWV4")
        self.assertEqual(res["item_id"], "itmdda5c11c09523")
        self.assertIn(res["original_price"], [498.0, 499.0])
        self.assertEqual(res["original_mrp"], 665.0)
        self.assertTrue(res["original_seller"])
        self.assertGreaterEqual(res["seller_count"], 1)
        self.assertEqual(res["makro_url"], "https://www.makro.co.za/-/p/itmdda5c11c09523?pid=GSPHPVTNMFHDAWV4")

    def test_cookie_sanitization(self):
        """测试清洗 Cookie 剔除反爬追踪特征字段"""
        raw_cookie = "T=token123; _pxvid=abc456; _ga=GA1.2.3; at=auth789; _tt_enable_cookie=1; connect.sid=sess_999; K-ACTION=test; sellerId=sel123"
        cleaned = MakroScraperService.sanitize_makro_cookie(raw_cookie)
        self.assertIn("T=token123", cleaned)
        self.assertIn("at=auth789", cleaned)
        self.assertIn("connect.sid=sess_999", cleaned)
        self.assertIn("sellerId=sel123", cleaned)
        self.assertNotIn("_pxvid", cleaned)
        self.assertNotIn("_ga", cleaned)
        self.assertNotIn("_tt", cleaned)
        self.assertNotIn("K-ACTION", cleaned)

    def test_rich_metadata_resolution(self):
        """测试富元数据字段（品牌、垂直类目、型号、条形码）的完整性传递"""
        class MockStore:
            seller_id = "test_seller"
            fk_csrf_token = "token"
            cookie = "cookie"
            default_location_id = "loc1"
            default_brand = "Generic"

        client_data = {
            "item_id": "itmdda5c11c09523",
            "title": "Max Sprinkler",
            "brand": "MaxPro",
            "vertical": "garden_tool",
            "model_number": "MOD-1234",
            "barcode": "6001234567890",
            "price": 250.0,
            "mrp": 350.0,
            "image_url": "https://www.makro.co.za/img/test.jpg",
            "seller_name": "BestSeller",
            "seller_count": 3
        }
        res = MakroScraperService.resolve_piggyback_product("GSPHPVTNMFHDAWV4", MockStore(), client_data=client_data)
        self.assertEqual(res["brand"], "MaxPro")
        self.assertEqual(res["vertical"], "garden_tool")
        self.assertEqual(res["model_number"], "MOD-1234")
        self.assertEqual(res["barcode"], "6001234567890")
        self.assertEqual(res["original_price"], 250.0)
        self.assertEqual(res["original_mrp"], 350.0)
        self.assertEqual(res["original_seller"], "BestSeller")
        self.assertEqual(res["seller_count"], 3)

if __name__ == "__main__":
    unittest.main()

