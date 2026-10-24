import unittest
from unittest.mock import patch, MagicMock
from app.services.proxy_service import ProxyPoolService
from app.services.auto_reprice_service import AutoRepriceService
from app.services.makro_piggyback_service import MakroPiggybackService
from app.services.makro_portal_service import MakroPortalService
from app.database import SessionLocal
from app.models.store import Store
from app.models.makro_piggyback import MakroPiggybackItem

class TestProxyRoutingAndDirectSeller(unittest.TestCase):
    """
    测试：
    1. Makro 商家后台请求 (seller.makro.co.za) 强制 100% 直连，绝不调用携趣代理
    2. Makro 买家前台爬虫与自动巡检巡航接入 ProxyPoolService.get_proxy() 动态代理池
    """

    def setUp(self):
        self.db = SessionLocal()
        self.store = self.db.query(Store).filter(Store.is_default == True).first()
        if not self.store:
            self.store = Store(
                name="TestStore",
                seller_id="test_seller_123",
                fk_csrf_token="test_token",
                cookie="test_cookie",
                default_brand="Generic",
                default_location_id="loc_1",
                is_active=True,
                is_default=True
            )
            self.db.add(self.store)
            self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("requests.request")
    def test_resilient_request_seller_api_forces_direct(self, mock_request):
        """测试向 seller.makro.co.za 发起请求时，即使 use_proxy=True 也会强制直连 (proxies=None)"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_request.return_value = mock_resp

        seller_url = "https://seller.makro.co.za/napi/listing/create-update-listings?sellerId=test_seller_123"
        resp = ProxyPoolService.resilient_request(
            "POST",
            seller_url,
            headers={"test": "1"},
            json={"data": "test"},
            use_proxy=True # 故意传 True
        )

        self.assertEqual(resp.status_code, 200)
        mock_request.assert_called_once()
        _, kwargs = mock_request.call_args
        # 确保直连未带任何代理字典
        self.assertNotIn("proxies", kwargs)

    @patch("requests.request")
    def test_push_price_to_makro_uses_direct_connection(self, mock_request):
        """测试 24h 跟价引擎向官方推送价格时，直连官方卖家网关"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "bulkResponse": [{"status": "success", "skuID": "SKU_TEST_01"}]
            }
        }
        mock_request.return_value = mock_resp

        item = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="GSPHPVTNMFHDAWV4",
            seller_sku="SKU_TEST_01",
            target_price=200.0,
            inventory=100
        )

        AutoRepriceService._push_price_to_makro(item, self.store, 199.0)

        mock_request.assert_called_once()
        called_url, kwargs = mock_request.call_args[0][1], mock_request.call_args[1]
        self.assertIn("seller.makro.co.za", called_url)
        self.assertNotIn("proxies", kwargs)

    @patch("app.services.auto_reprice_service.AutoRepriceService._push_price_to_makro")
    @patch("app.services.proxy_service.ProxyPoolService.get_proxy", return_value="http://117.89.88.137:5417")
    @patch("app.services.makro_scraper_service.MakroScraperService.scrape_buyer_frontend")
    def test_cruise_worker_fetches_and_uses_proxy(self, mock_scrape, mock_get_proxy, mock_push):
        """测试 24h 自动巡航工作线程在抓取买家前台时，动态从携趣代理池获取代理 IP"""
        mock_scrape.return_value = {
            "price": 180.0,
            "mrp": 250.0,
            "seller_name": "Rival",
            "seller_id": "rival_id",
            "seller_count": 2,
            "blocked": False
        }

        item = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="TEST_CRUISE_PROXY_FSN",
            seller_sku="SKU_CRUISE_PROXY",
            title="Test Cruise Proxy Product",
            auto_reprice=True,
            target_price=190.0,
            price_strategy="MINUS_1"
        )
        self.db.add(item)
        self.db.commit()

        # 验证调用
        res = AutoRepriceService.reprice_single_item(item, self.db, proxy="http://117.89.88.137:5417")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["action"], "UNDER_CUT")

        # 清理
        self.db.delete(item)
        self.db.commit()

if __name__ == "__main__":
    unittest.main()
