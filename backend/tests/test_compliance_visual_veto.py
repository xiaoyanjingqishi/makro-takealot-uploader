import unittest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.compliance_service import ComplianceService

class TestComplianceVisualVeto(unittest.TestCase):
    def setUp(self):
        self.service = ComplianceService()

    def test_sku_gp2611111805294f76_visual_veto(self):
        """测试 SKU: GP2611111805294F76 (LIUHUI露营灯) 真实检测快照的视觉一票否决"""
        qwen_title = {
            "tested": True,
            "has_risk": False,
            "risk_level": "SAFE",
            "detected_brands_or_ips": [],
            "violation_type": "NONE",
            "reasons": ["LIUHUI为授权自有品牌，非知名大牌或受保护IP，属合规白牌"]
        }
        deepseek_title = {
            "tested": True,
            "has_risk": False,
            "risk_level": "SAFE",
            "detected_brands_or_ips": ["LIUHUI"],
            "violation_type": "NONE",
            "reasons": ["标题仅出现授权自有品牌 LIUHUI，无第三方大牌风险"]
        }
        jev_title = {
            "tested": True,
            "risk_level": "SAFE",
            "violation_score": 0.21,
            "confidence": 0.26,
            "brand_nature": "GENERIC_WHITE_LABEL",
            "target_brand": "NONE"
        }
        qwen_image = {
            "tested": True,
            "has_brand_logo": False,
            "logo_names": [],
            "detected_texts": ["LIUHUI"],
            "is_transport_prohibited": False,
            "prohibited_types": [],
            "risk_level": "PROHIBITED",
            "summary": "在产品顶部金属挂扣与线缆连接处的灰色塑料件上，清晰识别到印刷英文字符'LIUHUI'（疑似商号/品牌标识），违反审查铁律第1条：任何品牌商标Logo或商号文字（含卖家白牌/私牌）一律判定PROHIBITED。"
        }
        deepseek_image = {
            "tested": True,
            "has_brand_logo": False,
            "logo_names": [],
            "detected_texts": [],
            "is_transport_prohibited": False,
            "prohibited_types": [],
            "risk_level": "SAFE",
            "summary": "产品机身、挂钩未识别到任何品牌Logo或商号印刷文字，判定SAFE。"
        }
        local_rules = {
            "detected_brands": [],
            "brand_info": {"recommended_title": "Test Title", "is_accessory": False}
        }

        res = self.service._reconcile_dual_verdicts(
            local_rules=local_rules,
            qwen_title=qwen_title,
            deepseek_title=deepseek_title,
            qwen_image=qwen_image,
            deepseek_image=deepseek_image,
            first_img_url="https://test.com/img.jpg",
            target_brand_name="LIUHUI",
            jev_title=jev_title,
            is_piggyback=True,
            original_seller="Lixia Trading",
            eval_title="LIUHUI Rechargeable Camping Lights with Hook",
            category="ceiling_lamp"
        )

        # 核心断言：千问视觉检出实物印字，必须触发 PROHIBITED，严禁被推翻为 SAFE！
        self.assertEqual(res["compliance_status"], "PROHIBITED", "千问视觉检出实物印字必须定级为 PROHIBITED")
        self.assertFalse(res["is_disputed"], "触发一票否决后不应进入待仲裁")
        self.assertIn("PROHIBITED", res["reconciliation_summary"])
        self.assertFalse(res.get("is_white_label", False), "PROHIBITED 禁售商品绝不能标记为白牌合规")
        print("  [OK] test_sku_gp2611111805294f76_visual_veto passed!")

    def test_deepseek_vision_prohibited_veto(self):
        """测试 DeepSeek 视觉检出违禁时，纯文本 Jev 同样无权推翻为 SAFE"""
        qwen_title = {"tested": True, "risk_level": "SAFE", "detected_brands_or_ips": []}
        deepseek_title = {"tested": True, "risk_level": "SAFE", "detected_brands_or_ips": []}
        jev_title = {"tested": True, "risk_level": "SAFE", "violation_score": 0.15}
        qwen_image = {"tested": True, "risk_level": "SAFE", "has_brand_logo": False}
        deepseek_image = {
            "tested": True,
            "risk_level": "PROHIBITED",
            "is_transport_prohibited": True,
            "prohibited_types": ["battery"],
            "summary": "检出纯锂电池，跨境航空禁运。"
        }
        local_rules = {"detected_brands": [], "brand_info": {}}

        res = self.service._reconcile_dual_verdicts(
            local_rules=local_rules,
            qwen_title=qwen_title,
            deepseek_title=deepseek_title,
            qwen_image=qwen_image,
            deepseek_image=deepseek_image,
            first_img_url="https://test.com/img.jpg",
            target_brand_name="generic",
            jev_title=jev_title,
            is_piggyback=True
        )

        self.assertEqual(res["compliance_status"], "PROHIBITED")
        print("  [OK] test_deepseek_vision_prohibited_veto passed!")

    def test_pure_safe_white_label_passes(self):
        """测试纯净合规白牌（文本与首图均无任何印刷文字与禁运）依然合规放行"""
        qwen_title = {"tested": True, "risk_level": "SAFE", "detected_brands_or_ips": []}
        deepseek_title = {"tested": True, "risk_level": "SAFE", "detected_brands_or_ips": []}
        jev_title = {"tested": True, "risk_level": "SAFE", "violation_score": 0.05}
        qwen_image = {"tested": True, "risk_level": "SAFE", "has_brand_logo": False, "detected_texts": []}
        deepseek_image = {"tested": True, "risk_level": "SAFE", "has_brand_logo": False, "detected_texts": []}
        local_rules = {"detected_brands": [], "brand_info": {}}

        res = self.service._reconcile_dual_verdicts(
            local_rules=local_rules,
            qwen_title=qwen_title,
            deepseek_title=deepseek_title,
            qwen_image=qwen_image,
            deepseek_image=deepseek_image,
            first_img_url="https://test.com/img.jpg",
            target_brand_name="SuperLight",
            jev_title=jev_title,
            is_piggyback=True
        )

        self.assertEqual(res["compliance_status"], "SAFE")
        self.assertTrue(res.get("is_white_label", False))
        print("  [OK] test_pure_safe_white_label_passes passed!")

if __name__ == '__main__':
    unittest.main()
