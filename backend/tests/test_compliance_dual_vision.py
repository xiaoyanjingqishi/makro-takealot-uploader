import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import unittest
from unittest.mock import MagicMock, patch

from backend.app.database import SessionLocal
from backend.app.services.compliance_service import ComplianceService, DEEPSEEK_VISION_MODEL_ALIASES

class TestComplianceDualVision(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_deepseek_vision_model_aliases(self):
        self.assertEqual(DEEPSEEK_VISION_MODEL_ALIASES.get("deepseek-flash-vision"), "deepseek-flash")
        self.assertEqual(DEEPSEEK_VISION_MODEL_ALIASES.get("deepseek-v4-flash"), "deepseek-flash")
        self.assertEqual(DEEPSEEK_VISION_MODEL_ALIASES.get("deepseek-v4-flash-vision-exp"), "deepseek-flash")

    def test_deepseek_vision_invocation_not_short_circuited(self):
        """验证已彻底解除 api.deepseek.com 的硬编码阻断"""
        cs = ComplianceService(
            deepseek_api_key="sk-dummy-test-key",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-chat",
            deepseek_vision_model="deepseek-flash"
        )
        self.assertIsNotNone(cs.deepseek_client)

        # Mock _invoke_vision_model
        mock_res = {
            "tested": True,
            "ai_name": "DeepSeek-Flash-Vision",
            "model": "deepseek-flash",
            "image_url": "https://example.com/test.jpg",
            "has_brand_logo": False,
            "logo_names": [],
            "is_transport_prohibited": False,
            "prohibited_types": [],
            "risk_level": "SAFE",
            "summary": "DeepSeek 视觉检测通过"
        }

        with patch.object(cs, "_invoke_vision_model", return_value=mock_res) as mock_invoke:
            with patch("backend.app.services.compliance_service._vision_image_session.get") as mock_get:
                mock_get.return_value.status_code = 200
                mock_get.return_value.content = b"fake-image-bytes"
                mock_get.return_value.headers = {"content-type": "image/jpeg"}

                qwen_res, ds_res = cs._run_round2_vision_audit(
                    image_url="https://example.com/test.jpg",
                    known_brands=["TestBrand"],
                    is_piggyback=True
                )

                # 确认已发起 DeepSeek 视觉模型调用
                mock_invoke.assert_called()
                self.assertTrue(ds_res.get("tested"))
                self.assertEqual(ds_res.get("risk_level"), "SAFE")
                self.assertEqual(ds_res.get("model"), "deepseek-flash")

    def test_reconcile_dual_verdicts_both_tested(self):
        """验证双路视觉均已测试时，裁决引擎能正常对比双方检出"""
        cs = ComplianceService()
        local_rules = {
            "compliance_status": "SAFE",
            "prohibited_items": [],
            "detected_brands": [],
            "risk_reasons": [],
            "suggestions": [],
            "brand_info": {"recommended_title": "Clean Title", "is_accessory": True}
        }
        qwen_title = {"tested": True, "risk_level": "SAFE", "detected_brands_or_ips": []}
        deepseek_title = {"tested": True, "risk_level": "SAFE", "detected_brands_or_ips": []}
        jev_title = {"tested": True, "risk_level": "SAFE", "violation_score": 0.05}

        qwen_image = {
            "tested": True,
            "risk_level": "SAFE",
            "has_brand_logo": False,
            "logo_names": [],
            "is_transport_prohibited": False,
            "prohibited_types": []
        }
        deepseek_image = {
            "tested": True,
            "risk_level": "SAFE",
            "has_brand_logo": False,
            "logo_names": [],
            "is_transport_prohibited": False,
            "prohibited_types": []
        }

        verdict = cs._reconcile_dual_verdicts(
            local_rules=local_rules,
            qwen_title=qwen_title,
            deepseek_title=deepseek_title,
            qwen_image=qwen_image,
            deepseek_image=deepseek_image,
            first_img_url="https://example.com/test.jpg",
            target_brand_name="Generic",
            jev_title=jev_title,
            is_piggyback=True,
            eval_title="Generic Title",
            category="general"
        )

        self.assertEqual(verdict.get("compliance_status"), "SAFE")
        self.assertTrue(verdict["qwen_verdict"]["image_audit"]["tested"])
        self.assertTrue(verdict["deepseek_verdict"]["image_audit"]["tested"])
        self.assertEqual(verdict["vision_breakdown"]["qwen_logos"], [])
        self.assertEqual(verdict["vision_breakdown"]["deepseek_logos"], [])

if __name__ == "__main__":
    unittest.main()
