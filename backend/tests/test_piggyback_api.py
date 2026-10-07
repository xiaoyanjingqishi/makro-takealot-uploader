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

    def test_batch_set_store_and_batch_publish_with_target_store(self):
        # 准备两个店铺：Store 1 和 Store 2
        store2 = self.db.query(Store).filter(Store.name == "备用测试店铺").first()
        if not store2:
            store2 = Store(
                name="备用测试店铺",
                seller_id="sec80491bf0a34dc5",
                fk_csrf_token="sec_token",
                cookie="sec_cookie",
                default_brand="Store2Brand",
                default_location_id="LOC_TEST_2",
                is_active=True,
                is_default=False
            )
            self.db.add(store2)
            self.db.commit()
            self.db.refresh(store2)

        # 创建属于 store 1 的跟品条目
        item = MakroPiggybackItem(
            store_id=self.store.id,
            user_id=self.user.id,
            makro_product_id="TESTFSN000000001",
            title="Batch Store Test Item",
            seller_sku="GPTESTSTORE001",
            original_price=199.0,
            target_price=198.0,
            target_mrp=299.0,
            min_price_floor=100.0,
            inventory=50,
            status="PENDING",
            compliance_status="SAFE"
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        item_id = item.id

        try:
            # 1. 测试批量修改店铺接口 POST /api/piggyback/batch-set-store
            resp = self.client.post(
                "/api/piggyback/batch-set-store",
                headers=self.headers,
                json={"ids": [item_id], "store_id": store2.id}
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["updated_count"], 1)
            self.assertEqual(data["store_id"], store2.id)

            # 验证数据库中店铺已变更为 store2
            self.db.refresh(item)
            self.assertEqual(item.store_id, store2.id)

            # 2. 测试批量挂靠接口 POST /api/piggyback/batch-publish (指定目标店铺)
            with patch("app.services.makro_piggyback_service.MakroPiggybackService.publish_piggyback_listing") as mock_pub:
                mock_pub.return_value = {"listing_id": "LSTGTEST001", "status": "created"}
                pub_resp = self.client.post(
                    "/api/piggyback/batch-publish",
                    headers=self.headers,
                    json={"ids": [item_id], "store_id": self.store.id}
                )
                self.assertEqual(pub_resp.status_code, 200)
                pub_data = pub_resp.json()
                self.assertTrue(pub_data["success"])
                self.assertIn("task_id", pub_data)
                print(f"[OK] Batch publish with target store returned task_id: {pub_data['task_id']}")
        finally:
            import time
            time.sleep(0.1)
            del_item = self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
            if del_item:
                self.db.delete(del_item)
                self.db.commit()

    @patch("app.services.compliance_service.ComplianceService._invoke_text_model")
    @patch("app.services.compliance_service.ComplianceService._invoke_vision_model")
    @patch("requests.get")
    def test_compliance_check_and_arbitration(self, mock_get, mock_vision, mock_text):
        # 1. 模拟图片下载成功
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest"
        mock_get.return_value = mock_resp

        # 2. 模拟第一轮文本审查 (白牌 HYinjin 判定为 SAFE)
        mock_text.return_value = {
            "tested": True,
            "risk_level": "SAFE",
            "violation_type": "NONE",
            "reasons": ["卖家自造白牌，属于通用耗材配件，依据Makro规则允许跟品"],
            "summary": "SAFE 合规白牌",
            "detected_brands_or_ips": []
        }

        # 3. 模拟第二轮视觉图审 (无品牌 Logo，无违禁品)
        mock_vision.return_value = {
            "tested": True,
            "has_brand_logo": False,
            "logo_names": [],
            "is_transport_prohibited": False,
            "prohibited_types": [],
            "risk_level": "SAFE",
            "summary": "画面为中性清洁配件，无知名品牌商标 Logo，无禁运品"
        }

        item = MakroPiggybackItem(
            store_id=self.store.id,
            user_id=self.user.id,
            makro_product_id="VMKHHSSGMQ9VGBKW",
            title="HYinjin Vacuum Cleaner Accessories Kit",
            brand="HYinjin",
            vertical="vacuum_cleaner",
            image_url="https://1-makro.rukmini.ng.fkcloud.net/image/vacuum.png",
            seller_sku="GPHYINJIN001",
            original_price=399.0,
            target_price=398.0,
            target_mrp=499.0,
            status="PENDING",
            compliance_status="PENDING_CHECK"
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        item_id = item.id

        try:
            # 执行 AI 合规检测接口 POST /api/piggyback/check-compliance/{item_id}
            resp = self.client.post(f"/api/piggyback/check-compliance/{item_id}", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["compliance_status"], "SAFE")
            details = data["details"]
            self.assertTrue(details.get("is_white_label"))
            self.assertIn("white_label_notice", details)
            print(f"[OK] White label compliance check passed: status={data['compliance_status']}, white_label={details.get('is_white_label')}")

            # 验证人工终审仲裁接口 POST /api/piggyback/arbitrate/{item_id}
            arb_resp = self.client.post(
                f"/api/piggyback/arbitrate/{item_id}",
                headers=self.headers,
                json={
                    "human_verdict": "SAFE",
                    "human_notes": "人工确认：HYinjin为白牌，配件无商标侵权"
                }
            )
            self.assertEqual(arb_resp.status_code, 200)
            arb_data = arb_resp.json()
            self.assertTrue(arb_data["success"])
            self.assertEqual(arb_data["compliance_status"], "SAFE")
            self.assertIn("human_arbitration", arb_data["compliance_details"])
            print("[OK] Piggyback arbitration endpoint passed!")
        finally:
            del_item = self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
            if del_item:
                self.db.delete(del_item)
                self.db.commit()

    @patch("app.services.makro_scraper_service.MakroScraperService.resolve_piggyback_product")
    def test_minus_15_strategy_and_defaults(self, mock_resolve):
        """测试 1: -15 兰特默认公式、500 默认库存与自动计算 70% 保本底价"""
        mock_resolve.return_value = {
            "makro_product_id": "TEST_FSN_DEFAULTS_100",
            "makro_url": "https://www.makro.co.za/p/TEST_FSN_DEFAULTS_100",
            "title": "Default Pricing and Inventory Test Item",
            "title_zh": "默认跟价与库存测试品",
            "brand": "Generic",
            "vertical": "general",
            "image_url": "https://www.makro.co.za/img/test.jpg",
            "original_price": 500.0,
            "original_mrp": 800.0
        }

        # 采集不传 price_strategy 与 min_price_floor，检验默认行为
        resp = self.client.post(
            "/api/piggyback/collect",
            headers=self.headers,
            json={
                "url_or_fsn": "TEST_FSN_DEFAULTS_100",
                "store_id": self.store.id
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["item"]
        try:
            # 1. 验证跟价公式默认为 MINUS_15，目标价为 500.0 - 15.0 = 485.0
            self.assertEqual(data["price_strategy"], "MINUS_15")
            self.assertEqual(data["target_price"], 485.0)

            # 2. 验证默认库存为 500
            self.assertEqual(data["inventory"], 500)

            # 3. 验证自动赋予保本底价 (默认 70% -> 500 * 0.7 = 350.0)
            self.assertEqual(data["min_price_floor"], 350.0)
            print("[OK] Test 1: MINUS_15, 500 inventory, 70% default floor passed!")
        finally:
            self.client.delete(f"/api/piggyback/items/{data['id']}", headers=self.headers)

    @patch("app.services.makro_scraper_service.MakroScraperService.resolve_piggyback_product")
    def test_abandon_and_duplicate_prevention(self, mock_resolve):
        """测试 2: 弃用商品池防重采机制、列表筛选与恢复操作"""
        mock_resolve.return_value = {
            "makro_product_id": "TEST_FSN_ABANDON_200",
            "makro_url": "https://www.makro.co.za/p/TEST_FSN_ABANDON_200",
            "title": "Infringement Suspect Brand Bag",
            "title_zh": "疑似侵权品牌包",
            "brand": "FakeBrand",
            "vertical": "bags",
            "image_url": "https://www.makro.co.za/img/bag.jpg",
            "original_price": 600.0,
            "original_mrp": 900.0
        }

        # 1. 采集入库
        resp = self.client.post(
            "/api/piggyback/collect",
            headers=self.headers,
            json={"url_or_fsn": "TEST_FSN_ABANDON_200", "store_id": self.store.id}
        )
        self.assertEqual(resp.status_code, 200)
        item_id = resp.json()["item"]["id"]

        try:
            # 2. 调用弃用接口
            ab_resp = self.client.post(
                f"/api/piggyback/items/{item_id}/abandon",
                headers=self.headers,
                json={"reason": "首图侵权带有Logo"}
            )
            self.assertEqual(ab_resp.status_code, 200)
            self.assertTrue(ab_resp.json()["item"]["is_abandoned"])
            self.assertEqual(ab_resp.json()["item"]["abandoned_reason"], "首图侵权带有Logo")

            # 3. 再次尝试采集此商品 -> 必须被拦截并报 400，提示已被弃用
            dup_resp = self.client.post(
                "/api/piggyback/collect",
                headers=self.headers,
                json={"url_or_fsn": "TEST_FSN_ABANDON_200", "store_id": self.store.id}
            )
            self.assertEqual(dup_resp.status_code, 400)
            self.assertIn("弃用黑名单拦截", dup_resp.json()["detail"])
            print("[OK] Test 2.1: Duplicate collection blocked successfully!")

            # 4. 检查常规列表过滤与已弃用列表筛选
            normal_list = self.client.get("/api/piggyback/items?stage=ALL", headers=self.headers).json()
            normal_ids = [it["id"] for it in normal_list["items"]]
            self.assertNotIn(item_id, normal_ids)

            abandon_list = self.client.get("/api/piggyback/items?stage=ABANDONED", headers=self.headers).json()
            abandon_ids = [it["id"] for it in abandon_list["items"]]
            self.assertIn(item_id, abandon_ids)
            print("[OK] Test 2.2: Stage ABANDONED list filtering passed!")

            # 5. 调用恢复接口
            res_resp = self.client.post(f"/api/piggyback/items/{item_id}/restore", headers=self.headers)
            self.assertEqual(res_resp.status_code, 200)
            self.assertFalse(res_resp.json()["item"]["is_abandoned"])
            print("[OK] Test 2.3: Restore item passed!")
        finally:
            self.client.delete(f"/api/piggyback/items/{item_id}", headers=self.headers)

    def test_vision_zero_tolerance_on_logo(self):
        """测试 3: 图审带 Logo 一票否决为 PROHIBITED (零容忍不论白牌还是大牌)"""
        from app.services.compliance_service import ComplianceService
        service = ComplianceService()

        # 模拟图审结果包含卖家自造白牌 Logo (如 HYinjin / AnyWhiteLabel)
        qwen_image = {
            "tested": True,
            "has_brand_logo": True,
            "logo_names": ["HYinjin_Logo"],
            "is_transport_prohibited": False,
            "prohibited_types": [],
            "risk_level": "RISK",
            "summary": "画面检出机身丝印 Logo"
        }
        deepseek_image = {
            "tested": True,
            "has_brand_logo": True,
            "logo_names": ["HYinjin_Logo"],
            "is_transport_prohibited": False,
            "prohibited_types": [],
            "risk_level": "SAFE",
            "summary": "画面无异常"
        }
        qwen_title = {"tested": True, "risk_level": "SAFE", "reasons": []}
        deepseek_title = {"tested": True, "risk_level": "SAFE", "reasons": []}

        verdict = service._reconcile_dual_verdicts(
            local_rules={},
            qwen_title=qwen_title,
            deepseek_title=deepseek_title,
            qwen_image=qwen_image,
            deepseek_image=deepseek_image,
            first_img_url="https://test.com/logo.jpg",
            target_brand_name="HYinjin",
            is_piggyback=True
        )

        # 验证零容忍一票否决
        self.assertEqual(verdict["compliance_status"], "PROHIBITED")
        self.assertFalse(verdict["is_disputed"])
        self.assertTrue(any("Logo" in r for r in verdict["risk_reasons"]))
        print("[OK] Test 3: Vision zero-tolerance on Logo passed: status=PROHIBITED")

if __name__ == "__main__":
    unittest.main()

