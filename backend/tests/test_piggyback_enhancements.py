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
        self.assertEqual(res["original_price"], 499.0)
        self.assertEqual(res["original_mrp"], 665.0)
        self.assertEqual(res["original_seller"], "pumu222")
        self.assertEqual(res["seller_count"], 1)
        self.assertEqual(res["makro_url"], "https://www.makro.co.za/-/p/itmdda5c11c09523?pid=GSPHPVTNMFHDAWV4")

if __name__ == "__main__":
    unittest.main()
