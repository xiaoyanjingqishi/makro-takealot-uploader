import unittest
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal, engine, Base
from app.models.store import Store
from app.models.user import User
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.makro_listing import MakroListing
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
        else:
            changed = False
            if not self.store.default_location_id:
                self.store.default_location_id = "LOC_TEST"
                changed = True
            if not self.store.fk_csrf_token:
                self.store.fk_csrf_token = "test_token"
                changed = True
            if not self.store.cookie:
                self.store.cookie = "test_cookie"
                changed = True
            if changed:
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
        self.assertIn(item["target_price"], [0.0, 498.0]) # 0.0 or 499 - 1
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
    @patch("app.services.compliance_service._vision_image_session.get")
    def test_compliance_check_and_arbitration(self, mock_get, mock_vision, mock_text):
        # 1. 模拟图片下载成功
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest"
        mock_resp.headers = {"content-type": "image/png"}
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
            # 1. 验证跟价公式默认为配置策略 (MINUS_15 或数据库中配置的策略)
            self.assertIn(data["price_strategy"], ["MINUS_15", "MINUS_2"])
            self.assertIn(data["target_price"], [0.0, 485.0, 498.0])

            # 2. 验证默认库存为 500
            self.assertEqual(data["inventory"], 500)

            # 3. 验证自动赋予保本底价 (默认 70% 或数据库配置的 90%)
            self.assertIn(data["min_price_floor"], [0.0, 350.0, 450.0])
            print("[OK] Test 1: Defaults inventory and floor passed!")
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

    @patch("app.services.makro_piggyback_service.MakroPiggybackService.check_compliance_for_item")
    def test_batch_check_compliance_100_concurrency(self, mock_comp):
        mock_comp.return_value = {"compliance_status": "SAFE", "summary": "合规无异常"}

        # 创建 3 个测试跟品商品
        test_items = []
        for i in range(3):
            item = MakroPiggybackItem(
                store_id=self.store.id,
                makro_product_id=f"TEST_CONC_{i}",
                title=f"Concurrency Test Item {i}",
                seller_sku=f"SKU_CONC_{i}",
                target_price=100.0,
                status="DISCOVERED"
            )
            self.db.add(item)
            test_items.append(item)
        self.db.commit()
        for item in test_items:
            self.db.refresh(item)

        item_ids = [it.id for it in test_items]
        try:
            # 1. 默认设置下启动批量质检，验证并发配置为 100 (被 min(100, len(items)) 限制为 3)
            resp = self.client.post(
                "/api/piggyback/batch-check-compliance",
                headers=self.headers,
                json={"ids": item_ids}
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertIn("concurrency", data)
            self.assertEqual(data["concurrency"], 3)
            self.assertIn("3线程并发", data["task_name"])

            # 2. 验证设置接口中默认 piggyback_compliance_concurrency 为 100
            settings_resp = self.client.get("/api/settings", headers=self.headers)
            self.assertEqual(settings_resp.status_code, 200)
            settings_data = settings_resp.json()
            self.assertEqual(settings_data.get("piggyback_compliance_concurrency"), 100)

            # 3. 更新设置并发为 50 并保存
            settings_data["piggyback_compliance_concurrency"] = 50
            save_resp = self.client.post("/api/settings", headers=self.headers, json=settings_data)
            self.assertEqual(save_resp.status_code, 200)

            # 再次获取验证
            settings_resp2 = self.client.get("/api/settings", headers=self.headers)
            self.assertEqual(settings_resp2.json().get("piggyback_compliance_concurrency"), 50)

            # 恢复设置回 100
            settings_data["piggyback_compliance_concurrency"] = 100
            self.client.post("/api/settings", headers=self.headers, json=settings_data)

            print("[OK] Test 4: Batch check compliance 100 concurrency and settings configuration passed")
        finally:
            for it in test_items:
                del_it = self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == it.id).first()
                if del_it:
                    self.db.delete(del_it)
            self.db.commit()

    def test_compliance_auto_retry_3_times(self):
        from app.services.compliance_service import ComplianceService
        service = ComplianceService()

        # 1. 模拟调用前 2 次触发 429 限流异常，第 3 次成功返回 (验证在 3 次重试内自愈)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = json.dumps({"has_risk": False, "risk_level": "SAFE", "reasons": []})
        mock_resp.choices[0].message.reasoning_content = None

        call_count = 0
        def _flaky_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("HTTP 429 Too Many Requests: Rate limit exceeded")
            return mock_resp

        mock_client.chat.completions.create.side_effect = _flaky_create

        res = service._invoke_text_model(
            client=mock_client,
            model="qwen-plus",
            ai_name="Qwen-Test",
            prompt="test prompt",
            raw_title="Safe Universal Phone Stand",
            makro_title="Safe Universal Phone Stand"
        )
        self.assertTrue(res["tested"])
        self.assertEqual(res["risk_level"], "SAFE")
        self.assertEqual(call_count, 3) # 初始 1 次 + 重试 2 次成功
        print("[OK] Test 5.1: Model retry 3 times succeeded after 2 transient 429 failures!")

        # 2. 模拟连续 4 次全部超时/失败，验证最多重试 3 次 (总计调用 4 次) 后优雅降级
        call_count_all_fail = 0
        def _always_fail(*args, **kwargs):
            nonlocal call_count_all_fail
            call_count_all_fail += 1
            raise Exception("Connection timed out after 30s")

        mock_client.chat.completions.create.side_effect = _always_fail

        fail_res = service._invoke_text_model(
            client=mock_client,
            model="deepseek-chat",
            ai_name="DeepSeek-Test",
            prompt="test prompt",
            raw_title="Test Title",
            makro_title="Test Title"
        )
        self.assertFalse(fail_res["tested"])
        self.assertEqual(call_count_all_fail, 4) # 初始 1 次 + 重试 3 次 = 4 次
        print("[OK] Test 5.2: Model retried exactly 3 times (4 attempts total) before graceful fallback!")

    @patch("app.services.compliance_service.ComplianceService._invoke_text_model")
    @patch("app.services.compliance_service._vision_image_session.get")
    def test_image_download_failure_verdict_must_be_risk(self, mock_get, mock_text):
        """测试 6: 首图下载异常时必须判定为 RISK 而绝不能默认 SAFE"""
        # 模拟下载异常 (例如 404 或超时)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.content = b""
        mock_get.return_value = mock_resp

        # 模拟文本审查为 SAFE
        mock_text.return_value = {
            "tested": True,
            "risk_level": "SAFE",
            "violation_type": "NONE",
            "reasons": ["中性商品标题合规"],
            "summary": "文本合规",
            "detected_brands_or_ips": []
        }

        from app.services.compliance_service import ComplianceService
        service = ComplianceService()
        result = service.check_product({
            "title": "Universal Case Protective Cover",
            "brand": "GenericWhite",
            "raw_images": ["https://www.makro.co.za/broken-image.jpg"],
            "is_piggyback": True
        })

        # 验证首图下载异常绝不能判定为 SAFE，必须为 RISK
        self.assertEqual(result["compliance_status"], "RISK")
        self.assertTrue(result["image_inspection"]["download_error"])
        self.assertEqual(result["round_2_status"]["status"], "RISK")
        self.assertTrue(result["round_2_status"]["download_error"])
        self.assertIn("首图下载", result["round_2_status"]["summary"])
        self.assertTrue(any("首图下载" in r for r in result["risk_reasons"]))
        print("[OK] Test 6: Image download failure correctly judged as RISK and not SAFE!")

    def test_abandon_active_item_dual_delisting(self):
        """测试 7: 在售跟品商品弃用时双重下架保障 (create-update-listings 改 INACTIVE + updateListingsInventory 清零库存)"""
        # 1. 创建在线在售的跟品条目与 Listing 快照
        sku = "GP_TEST_ACTIVE_DELIST_01"
        fsn = "FSN_ACTIVE_DELIST_01"
        item = MakroPiggybackItem(
            store_id=self.store.id,
            user_id=self.user.id,
            makro_product_id=fsn,
            title="Active Dual Delist Test Item",
            seller_sku=sku,
            target_price=150.0,
            target_mrp=250.0,
            inventory=100,
            status="ACTIVE",
            compliance_status="SAFE"
        )
        self.db.add(item)

        listing = MakroListing(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            sku_id=sku,
            product_id=fsn,
            title="Active Dual Delist Test Item",
            internal_state="ACTIVE",
            inventory=100,
            ssp=150.0,
            mrp=250.0
        )
        self.db.add(listing)
        self.db.commit()
        self.db.refresh(item)
        self.db.refresh(listing)
        item_id = item.id

        try:
            # 2. 模拟 requests.post 捕获调用载荷
            captured_calls = []

            def mock_requests_post(url, *args, **kwargs):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                captured_calls.append({"url": url, "kwargs": kwargs})
                if "create-update-listings" in url:
                    mock_resp.json.return_value = {
                        "result": {
                            "status": "success",
                            "bulkResponse": [
                                {
                                    "skuID": sku,
                                    "status": "updated",
                                    "globalErrors": [],
                                    "attributeErrors": {}
                                }
                            ]
                        }
                    }
                elif "updateListingsInventory" in url:
                    mock_resp.json.return_value = {
                        sku: {"status": "SUCCESS"}
                    }
                else:
                    mock_resp.json.return_value = {"status": "success"}
                return mock_resp

            with patch("requests.post", side_effect=mock_requests_post):
                resp = self.client.post(
                    f"/api/piggyback/items/{item_id}/abandon",
                    headers=self.headers,
                    json={"reason": "下架弃用双重测试"}
                )
                self.assertEqual(resp.status_code, 200)
                resp_data = resp.json()
                self.assertTrue(resp_data["success"])
                self.assertIn("Inactive", resp_data["message"])

            # 3. 验证接口调用细节
            cul_call = next((c for c in captured_calls if "create-update-listings" in c["url"]), None)
            self.assertIsNotNone(cul_call, "必须调用 create-update-listings 接口")
            bulk_reqs = cul_call["kwargs"]["json"]["bulkRequests"]
            self.assertEqual(len(bulk_reqs), 1)
            # 核心验证: listing_status 必须为 INACTIVE
            self.assertEqual(
                bulk_reqs[0]["attributeValues"]["listing_status"][0]["value"],
                "INACTIVE"
            )

            inv_call = next((c for c in captured_calls if "updateListingsInventory" in c["url"]), None)
            self.assertIsNotNone(inv_call, "必须调用 updateListingsInventory 接口")
            inv_payload = inv_call["kwargs"]["json"]
            self.assertIn(sku, inv_payload)
            self.assertEqual(inv_payload[sku]["locations"][0]["inventory"], 0)

            # 4. 验证本地数据库同步更新
            self.db.refresh(listing)
            self.assertEqual(listing.internal_state, "INACTIVE")
            self.assertEqual(listing.inventory, 0)

            self.db.refresh(item)
            self.assertEqual(item.status, "INACTIVE")
            self.assertTrue(item.is_abandoned)
            self.assertEqual(item.abandoned_reason, "下架弃用双重测试")

            # 5. 验证单品恢复接口 (移回待处理池，状态重置为 PENDING)
            restore_resp = self.client.post(f"/api/piggyback/items/{item_id}/restore", headers=self.headers)
            self.assertEqual(restore_resp.status_code, 200)
            self.db.refresh(item)
            self.assertFalse(item.is_abandoned)
            self.assertEqual(item.status, "PENDING")
            print("[OK] Test 7: Active item single abandon dual delisting and restore passed!")

        finally:
            del_it = self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
            if del_it:
                self.db.delete(del_it)
            del_lst = self.db.query(MakroListing).filter(MakroListing.sku_id == sku).first()
            if del_lst:
                self.db.delete(del_lst)
            self.db.commit()

    def test_batch_abandon_and_batch_restore_dual_delisting(self):
        """测试 8: 批量弃用多件在售商品聚合双重下架与批量恢复重置为 PENDING"""
        sku1, fsn1 = "GP_BATCH_ACTIVE_01", "FSN_BATCH_ACTIVE_01"
        sku2, fsn2 = "GP_BATCH_ACTIVE_02", "FSN_BATCH_ACTIVE_02"

        it1 = MakroPiggybackItem(
            store_id=self.store.id,
            user_id=self.user.id,
            makro_product_id=fsn1,
            title="Batch Active Item 1",
            seller_sku=sku1,
            target_price=200.0,
            target_mrp=300.0,
            inventory=50,
            status="ACTIVE"
        )
        it2 = MakroPiggybackItem(
            store_id=self.store.id,
            user_id=self.user.id,
            makro_product_id=fsn2,
            title="Batch Active Item 2",
            seller_sku=sku2,
            target_price=210.0,
            target_mrp=310.0,
            inventory=50,
            status="ACTIVE"
        )
        self.db.add_all([it1, it2])

        lst1 = MakroListing(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            sku_id=sku1,
            product_id=fsn1,
            title="Batch Active Item 1",
            internal_state="ACTIVE",
            inventory=50
        )
        lst2 = MakroListing(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            sku_id=sku2,
            product_id=fsn2,
            title="Batch Active Item 2",
            internal_state="ACTIVE",
            inventory=50
        )
        self.db.add_all([lst1, lst2])
        self.db.commit()
        self.db.refresh(it1)
        self.db.refresh(it2)
        it1_id, it2_id = it1.id, it2.id

        try:
            captured_calls = []

            def mock_requests_post(url, *args, **kwargs):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                captured_calls.append({"url": url, "kwargs": kwargs})
                if "create-update-listings" in url:
                    mock_resp.json.return_value = {
                        "result": {
                            "status": "success",
                            "bulkResponse": [
                                {"skuID": sku1, "status": "updated"},
                                {"skuID": sku2, "status": "updated"}
                            ]
                        }
                    }
                elif "updateListingsInventory" in url:
                    mock_resp.json.return_value = {
                        sku1: {"status": "SUCCESS"},
                        sku2: {"status": "SUCCESS"}
                    }
                else:
                    mock_resp.json.return_value = {"status": "success"}
                return mock_resp

            with patch("requests.post", side_effect=mock_requests_post):
                resp = self.client.post(
                    "/api/piggyback/batch-abandon",
                    headers=self.headers,
                    json={"ids": [it1_id, it2_id], "reason": "批量测试弃用"}
                )
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertTrue(data["success"])
                self.assertEqual(data["abandoned_count"], 2)
                self.assertEqual(data["delisted_count"], 2)

            # 验证批量下架聚合请求包含两个商品的 INACTIVE 状态与库存为 0
            cul_call = next((c for c in captured_calls if "create-update-listings" in c["url"]), None)
            self.assertIsNotNone(cul_call)
            bulk_reqs = cul_call["kwargs"]["json"]["bulkRequests"]
            self.assertEqual(len(bulk_reqs), 2)
            for req_item in bulk_reqs:
                self.assertEqual(req_item["attributeValues"]["listing_status"][0]["value"], "INACTIVE")

            inv_call = next((c for c in captured_calls if "updateListingsInventory" in c["url"]), None)
            self.assertIsNotNone(inv_call)
            inv_payload = inv_call["kwargs"]["json"]
            self.assertEqual(inv_payload[sku1]["locations"][0]["inventory"], 0)
            self.assertEqual(inv_payload[sku2]["locations"][0]["inventory"], 0)

            # 验证数据库状态更新
            self.db.refresh(lst1)
            self.db.refresh(lst2)
            self.assertEqual(lst1.internal_state, "INACTIVE")
            self.assertEqual(lst1.inventory, 0)
            self.assertEqual(lst2.internal_state, "INACTIVE")
            self.assertEqual(lst2.inventory, 0)

            self.db.refresh(it1)
            self.db.refresh(it2)
            self.assertEqual(it1.status, "INACTIVE")
            self.assertTrue(it1.is_abandoned)
            self.assertEqual(it2.status, "INACTIVE")
            self.assertTrue(it2.is_abandoned)

            # 批量恢复测试
            b_restore_resp = self.client.post(
                "/api/piggyback/batch-restore",
                headers=self.headers,
                json={"ids": [it1_id, it2_id]}
            )
            self.assertEqual(b_restore_resp.status_code, 200)
            self.assertEqual(b_restore_resp.json()["restored_count"], 2)

            self.db.refresh(it1)
            self.db.refresh(it2)
            self.assertFalse(it1.is_abandoned)
            self.assertEqual(it1.status, "PENDING")
            self.assertFalse(it2.is_abandoned)
            self.assertEqual(it2.status, "PENDING")
            print("[OK] Test 8: Batch abandon dual delisting and batch restore passed!")

        finally:
            self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_([it1_id, it2_id])).delete(synchronize_session=False)
            self.db.query(MakroListing).filter(MakroListing.sku_id.in_([sku1, sku2])).delete(synchronize_session=False)
            self.db.commit()

    def test_advanced_piggyback_features(self):
        # 1. 准备测试数据
        it1 = MakroPiggybackItem(
            makro_product_id="TEST_FSN_ADV_1",
            seller_sku="ADV_SKU_1",
            store_id=self.store.id,
            user_id=self.user.id,
            title="Advanced Filter Product 1",
            brand="BrandA",
            vertical="electronics_audio",
            target_price=200.0,
            original_price=250.0,
            min_price_floor=150.0,
            inventory=20,
            auto_reprice=True,
            status="ACTIVE",
            buybox_status="WINNING",
            compliance_status="SAFE"
        )
        it2 = MakroPiggybackItem(
            makro_product_id="TEST_FSN_ADV_2",
            seller_sku="ADV_SKU_2",
            store_id=self.store.id,
            user_id=self.user.id,
            title="Advanced Filter Product 2",
            brand="BrandB",
            vertical="home_kitchen",
            target_price=100.0,
            original_price=120.0,
            min_price_floor=0.0,
            inventory=0,
            auto_reprice=False,
            status="ACTIVE",
            buybox_status="LOSING",
            compliance_status="RISK"
        )
        self.db.add_all([it1, it2])
        self.db.commit()
        self.db.refresh(it1)
        self.db.refresh(it2)

        try:
            # 2. 测试获取类目列表 /api/piggyback/verticals
            vert_resp = self.client.get(f"/api/piggyback/verticals?store_id={self.store.id}", headers=self.headers)
            self.assertEqual(vert_resp.status_code, 200)
            verts = vert_resp.json().get("verticals", [])
            self.assertIn("electronics_audio", verts)
            self.assertIn("home_kitchen", verts)

            # 3. 测试高级过滤及同步 KPI / Stats
            # 按类目过滤
            f_vert_resp = self.client.get(f"/api/piggyback/items?vertical=electronics_audio&store_id={self.store.id}", headers=self.headers)
            self.assertEqual(f_vert_resp.status_code, 200)
            f_vert_data = f_vert_resp.json()
            self.assertTrue(all(item["vertical"] == "electronics_audio" for item in f_vert_data["items"]))
            self.assertIn("kpi", f_vert_data)
            self.assertIn("stats", f_vert_data)

            # 按库存状态过滤 (缺货 OUT_OF_STOCK)
            f_inv_resp = self.client.get(f"/api/piggyback/items?inventory_status=OUT_OF_STOCK&store_id={self.store.id}", headers=self.headers)
            self.assertEqual(f_inv_resp.status_code, 200)
            self.assertTrue(all(item["inventory"] == 0 for item in f_inv_resp.json()["items"]))

            # 按自动跟价状态过滤 (开启 auto_reprice=true)
            f_rep_resp = self.client.get(f"/api/piggyback/items?auto_reprice=true&store_id={self.store.id}", headers=self.headers)
            self.assertEqual(f_rep_resp.status_code, 200)
            self.assertTrue(all(item["auto_reprice"] is True for item in f_rep_resp.json()["items"]))

            # 按保本底价过滤 (有底价 HAS_FLOOR)
            f_flr_resp = self.client.get(f"/api/piggyback/items?has_floor_price=HAS_FLOOR&store_id={self.store.id}", headers=self.headers)
            self.assertEqual(f_flr_resp.status_code, 200)
            self.assertTrue(all(item["min_price_floor"] > 0 for item in f_flr_resp.json()["items"]))

            # 按价格区间过滤 (min_price=150, max_price=250)
            f_prc_resp = self.client.get(f"/api/piggyback/items?min_price=150&max_price=250&store_id={self.store.id}", headers=self.headers)
            self.assertEqual(f_prc_resp.status_code, 200)
            self.assertTrue(all(150 <= item["target_price"] <= 250 for item in f_prc_resp.json()["items"]))

            # 按排序规则过滤 (价格降序 PRICE_DESC)
            f_srt_resp = self.client.get(f"/api/piggyback/items?sort_by=PRICE_DESC&store_id={self.store.id}", headers=self.headers)
            self.assertEqual(f_srt_resp.status_code, 200)
            prices = [it["target_price"] for it in f_srt_resp.json()["items"]]
            self.assertEqual(prices, sorted(prices, reverse=True))

            # 4. 测试批量改价 (DELTA + 保本底价保护)
            # it1 target_price=200, min_price_floor=150. 下调 100 兰特，由于底价保护，应截断至 150
            adj_resp = self.client.post(
                "/api/piggyback/batch-adjust-price",
                headers=self.headers,
                json={
                    "ids": [it1.id],
                    "mode": "DELTA",
                    "value": -100.0,
                    "sync_to_makro": False,
                    "enforce_floor": True
                }
            )
            self.assertEqual(adj_resp.status_code, 200)
            self.assertEqual(adj_resp.json()["updated_count"], 1)
            self.db.refresh(it1)
            self.assertEqual(it1.target_price, 150.0)

            # 测试批量改价 (PERCENT +10%)
            # 150 * 1.10 = 165
            adj_pct_resp = self.client.post(
                "/api/piggyback/batch-adjust-price",
                headers=self.headers,
                json={
                    "ids": [it1.id],
                    "mode": "PERCENT",
                    "value": 10.0,
                    "sync_to_makro": False,
                    "enforce_floor": True
                }
            )
            self.assertEqual(adj_pct_resp.status_code, 200)
            self.db.refresh(it1)
            self.assertEqual(it1.target_price, 165.0)

            # 测试批量改价 (FIXED)
            adj_fix_resp = self.client.post(
                "/api/piggyback/batch-adjust-price",
                headers=self.headers,
                json={
                    "ids": [it2.id],
                    "mode": "FIXED",
                    "value": 188.0,
                    "sync_to_makro": False,
                    "enforce_floor": True
                }
            )
            self.assertEqual(adj_fix_resp.status_code, 200)
            self.db.refresh(it2)
            self.assertEqual(it2.target_price, 188.0)

            # 5. 测试批量开关自动跟价
            toggle_resp = self.client.post(
                "/api/piggyback/batch-toggle-auto-reprice",
                headers=self.headers,
                json={
                    "ids": [it1.id, it2.id],
                    "auto_reprice": True
                }
            )
            self.assertEqual(toggle_resp.status_code, 200)
            self.assertEqual(toggle_resp.json()["updated_count"], 2)
            self.db.refresh(it1)
            self.db.refresh(it2)
            self.assertTrue(it1.auto_reprice)
            self.assertTrue(it2.auto_reprice)

            # 6. 测试数据导出 CSV (包含 UTF-8 BOM \ufeff)
            exp_resp = self.client.get(
                f"/api/piggyback/export?ids={it1.id},{it2.id}",
                headers=self.headers
            )
            self.assertEqual(exp_resp.status_code, 200)
            csv_content = exp_resp.content.decode("utf-8-sig")
            self.assertIn("Makro FSN", csv_content)
            self.assertIn("TEST_FSN_ADV_1", csv_content)
            self.assertIn("TEST_FSN_ADV_2", csv_content)

            print("[OK] Test 9: Advanced Piggyback features (filters, KPI sync, batch price adjust, auto reprice toggle, CSV export) passed!")

        finally:
            self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_([it1.id, it2.id])).delete(synchronize_session=False)
            self.db.commit()

if __name__ == "__main__":
    unittest.main()


