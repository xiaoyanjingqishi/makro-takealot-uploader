import time
import json
import logging
import requests
from typing import Dict, Any, List, Optional
from ..config import settings
from ..models.setting import SystemSetting

logger = logging.getLogger(__name__)

class JevService:
    """
    Jev (TypeSafe AI) 决策模型系统服务 (System 1 快决策引擎)
    
    核心特性：
    - 非生成式决策引擎，专精分类(Choice)、打分(Score)与布尔真伪评估(Noul)
    - 响应耗时 100~300ms，输出 Token 永久免费
    - 原生强类型输出，杜绝大模型幻觉与格式破损
    """

    @classmethod
    def get_config(cls, db=None) -> Dict[str, Any]:
        """获取当前有效的 Jev 配置 (优先从数据库 SystemSetting 获取，降级至 config.settings)"""
        api_key = getattr(settings, "JEV_API_KEY", "")
        base_url = getattr(settings, "JEV_BASE_URL", "https://api.typesafe.ai")
        model = getattr(settings, "JEV_MODEL", "jev-latest")
        enabled = getattr(settings, "JEV_ENABLED", True)

        if db:
            try:
                db_settings = db.query(SystemSetting).filter(
                    SystemSetting.key.in_(["jev_api_key", "jev_base_url", "jev_model", "jev_enabled"])
                ).all()
                s_map = {s.key: s.value for s in db_settings}
                if s_map.get("jev_api_key"):
                    api_key = s_map["jev_api_key"]
                if s_map.get("jev_base_url"):
                    base_url = s_map["jev_base_url"]
                if s_map.get("jev_model"):
                    model = s_map["jev_model"]
                if s_map.get("jev_enabled") is not None:
                    enabled = str(s_map["jev_enabled"]).lower() in ["true", "1", "yes"]
            except Exception as e:
                logger.warning(f"从数据库读取 Jev 配置异常: {e}")

        # 确保 base_url 不带尾部斜杠
        base_url = base_url.rstrip("/")
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "enabled": enabled
        }

    @classmethod
    def query(
        cls,
        state: str,
        questions: Dict[str, Any],
        db=None,
        timeout: float = 18.0
    ) -> Dict[str, Any]:
        """
        向 Jev 决策网关发起单次原子系统评估
        
        :param state: 被评估上下文 (如商品标题、描述、属性摘要)
        :param questions: 问题集字典 (支持 choice, noul, score)
        :return: 包含 answers, usage, latency_ms 的结果字典
        """
        cfg = cls.get_config(db)
        if not cfg["enabled"]:
            return {"success": False, "error": "Jev 服务已在系统设置中停用"}
        if not cfg["api_key"]:
            return {"success": False, "error": "未配置 Jev API Key"}

        url = f"{cfg['base_url']}/v1/systemone"
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": cfg["model"],
            "state": state[:3500],  # 保持输入高效精炼
            "questions": questions
        }

        t0 = time.time()
        for attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
                latency_ms = round((time.time() - t0) * 1000, 1)

                if resp.status_code == 200:
                    res_json = resp.json()
                    return {
                        "success": True,
                        "model": res_json.get("model", cfg["model"]),
                        "answers": res_json.get("answers", {}),
                        "usage": res_json.get("usage", {}),
                        "latency_ms": latency_ms
                    }
                else:
                    err_text = resp.text[:300]
                    logger.warning(f"Jev API 返回非 200 异常 (HTTP {resp.status_code}): {err_text}")
                    return {
                        "success": False,
                        "status_code": resp.status_code,
                        "error": f"HTTP {resp.status_code}: {err_text}",
                        "latency_ms": latency_ms
                    }
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.6)
                    continue
                latency_ms = round((time.time() - t0) * 1000, 1)
                logger.error(f"调用 Jev API 网络异常 (重试后失败): {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "latency_ms": latency_ms
                }

    @classmethod
    def decide_brand_nature(
        cls,
        title: str,
        brand: str = "",
        category: str = "",
        specs: Any = None,
        description: str = "",
        db=None
    ) -> Dict[str, Any]:
        """
        【功能 1】品牌形态极速三元判定
        - ORIGINAL_BRAND: 国际知名品牌原装整机 (严禁强行贴牌 Beishi，防止假冒封店)
        - COMPATIBLE_ACCESSORY: 知名品牌第三方兼容配件/耗材 (触发国际知识产权合理使用兼容句式)
        - GENERIC_WHITE_LABEL: 纯中性白牌日用品 (安全贴牌 Beishi)
        """
        state_parts = [
            f"Product Title: {title}",
            f"Original Brand: {brand or 'Generic'}",
            f"Category: {category or 'Unknown'}"
        ]
        if specs:
            specs_str = json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)
            state_parts.append(f"Specs: {specs_str[:300]}")
        if description:
            state_parts.append(f"Description: {description[:300]}")

        state = "\n".join(state_parts)

        questions = {
            "product_nature": {
                "type": "choice",
                "instructions": "Classify the commercial and branding nature of this product listing.",
                "criteria": {
                    "ORIGINAL_BRAND": "Original branded device, electronics, apparel, or main unit from a globally recognized brand (e.g., Apple, Sony, Dyson, Nike, Samsung, Nintendo, Stanley, GoPro).",
                    "COMPATIBLE_ACCESSORY": "Third-party accessory, replacement part, case, strap, cable, filter, dock, or consumable designed to fit or work with a major brand device.",
                    "GENERIC_WHITE_LABEL": "Generic, unbranded, white-label everyday tool, hardware, wig, homeware, or utility with no strict attachment to a protected trademark."
                },
                "options": ["ORIGINAL_BRAND", "COMPATIBLE_ACCESSORY", "GENERIC_WHITE_LABEL"]
            },
            "target_famous_brand": {
                "type": "choice",
                "instructions": "If this is an accessory, identify the primary target brand ecosystem it is compatible with.",
                "criteria": {
                    "APPLE": "Designed for Apple ecosystem (iPhone, iPad, AirPods, Apple Watch, MacBook, MagSafe).",
                    "SAMSUNG": "Designed for Samsung Galaxy, Galaxy Watch, etc.",
                    "DYSON": "Designed for Dyson vacuum cleaners or hair styling tools.",
                    "SONY": "Designed for Sony PlayStation consoles, controllers, or cameras.",
                    "OTHER_FAMOUS": "Designed for other major brands (GoPro, Nintendo, Stanley, Makita, Bosch, etc.).",
                    "NONE": "Pure generic or self-contained product not designed for a famous brand."
                },
                "options": ["APPLE", "SAMSUNG", "DYSON", "SONY", "OTHER_FAMOUS", "NONE"]
            },
            "infringement_risk": {
                "type": "noul",
                "instructions": "Does this listing pose high risk of trademark infringement, counterfeit claims, or deceptive branding?"
            }
        }

        res = cls.query(state=state, questions=questions, db=db, timeout=18.0)
        if not res.get("success"):
            # 优雅降级：默认中性白牌
            return {
                "nature": "GENERIC_WHITE_LABEL",
                "confidence": 0.5,
                "target_brand": "NONE",
                "risk_score": 0.1,
                "latency_ms": res.get("latency_ms", 0),
                "is_fallback": True
            }

        answers = res.get("answers", {})
        nature_ans = answers.get("product_nature", {})
        nature = nature_ans.get("choice", "GENERIC_WHITE_LABEL")
        conf = float(nature_ans.get("confidence", 0.8))

        target_ans = answers.get("target_famous_brand", {})
        target_b = target_ans.get("choice", "NONE")

        risk_ans = answers.get("infringement_risk", {})
        risk_score = float(risk_ans.get("noul", 0.0))

        return {
            "nature": nature,
            "confidence": conf,
            "probabilities": nature_ans.get("probabilities", {}),
            "target_brand": target_b,
            "risk_score": risk_score,
            "latency_ms": res.get("latency_ms", 0),
            "is_fallback": False
        }

    @classmethod
    def evaluate_compliance(
        cls,
        title: str,
        brand: str = "",
        category: str = "",
        description: str = "",
        db=None
    ) -> Dict[str, Any]:
        """
        【功能 2】合规侵权极速量化评估与品牌形态识别 (四问合一，零额外开销)
        - compliance_decision: Choice 评估 (SAFE / RISK / PROHIBITED)
        - violation_probability: Noul 评估违规概率 (0.0 ~ 1.0)
        - product_nature: ORIGINAL_BRAND / COMPATIBLE_ACCESSORY / GENERIC_WHITE_LABEL
        - target_famous_brand: APPLE / SAMSUNG / DYSON / SONY / OTHER_FAMOUS / NONE
        """
        state = f"Title: {title}\nBrand: {brand or 'Generic'}\nCategory: {category}\nDescription: {description[:400]}"

        questions = {
            "compliance_decision": {
                "type": "choice",
                "instructions": "Determine the legal compliance and trademark safety level of this e-commerce listing for the South African market (Makro/Walmart).",
                "criteria": {
                    "SAFE": "Safe, legitimate, non-infringing product. Either generic or properly compliant with fair use.",
                    "RISK": "Contains brand mentions, ambiguous claims, or minor trademark keywords that require human review or minor phrasing tweaks.",
                    "PROHIBITED": "Blatant counterfeit, replica, fake brand usage, unauthorized luxury clone, weapons, drugs, or severe policy violation."
                },
                "options": ["SAFE", "RISK", "PROHIBITED"]
            },
            "violation_probability": {
                "type": "noul",
                "instructions": "Is this product listing infringing upon protected trademarks, counterfeit, or prohibited by e-commerce platform safety policies?"
            },
            "product_nature": {
                "type": "choice",
                "instructions": "Classify the commercial and branding nature of this product listing.",
                "criteria": {
                    "ORIGINAL_BRAND": "Original branded device, electronics, apparel, or main unit from a globally recognized brand.",
                    "COMPATIBLE_ACCESSORY": "Third-party accessory, replacement part, case, strap, cable, filter, dock, or consumable designed to fit or work with a major brand device.",
                    "GENERIC_WHITE_LABEL": "Generic, unbranded, white-label everyday tool, hardware, wig, homeware, or utility with no strict attachment to a protected trademark."
                },
                "options": ["ORIGINAL_BRAND", "COMPATIBLE_ACCESSORY", "GENERIC_WHITE_LABEL"]
            },
            "target_famous_brand": {
                "type": "choice",
                "instructions": "If this is an accessory, identify the primary target brand ecosystem it is compatible with.",
                "criteria": {
                    "APPLE": "Designed for Apple ecosystem (iPhone, iPad, AirPods, Apple Watch, MacBook, MagSafe).",
                    "SAMSUNG": "Designed for Samsung Galaxy, Galaxy Watch, etc.",
                    "DYSON": "Designed for Dyson vacuum cleaners or hair styling tools.",
                    "SONY": "Designed for Sony PlayStation consoles, controllers, or cameras.",
                    "OTHER_FAMOUS": "Designed for other major brands (GoPro, Nintendo, Stanley, Makita, Bosch, etc.).",
                    "NONE": "Pure generic or self-contained product not designed for a famous brand."
                },
                "options": ["APPLE", "SAMSUNG", "DYSON", "SONY", "OTHER_FAMOUS", "NONE"]
            }
        }

        res = cls.query(state=state, questions=questions, db=db, timeout=18.0)
        if not res.get("success"):
            return {
                "decision": "SAFE",
                "confidence": 0.5,
                "violation_score": 0.1,
                "probabilities": {},
                "nature": "GENERIC_WHITE_LABEL",
                "nature_confidence": 0.5,
                "nature_probabilities": {},
                "target_brand": "NONE",
                "latency_ms": res.get("latency_ms", 0),
                "is_fallback": True,
                "error": res.get("error")
            }

        answers = res.get("answers", {})
        dec_ans = answers.get("compliance_decision", {})
        viol_ans = answers.get("violation_probability", {})
        nature_ans = answers.get("product_nature", {})
        target_ans = answers.get("target_famous_brand", {})

        decision = dec_ans.get("choice", "SAFE")
        conf = float(dec_ans.get("confidence", 0.8))
        prob_map = dec_ans.get("probabilities", {})
        violation_score = float(viol_ans.get("noul", 0.0))

        nature = nature_ans.get("choice", "GENERIC_WHITE_LABEL")
        nature_conf = float(nature_ans.get("confidence", 0.8))
        nature_probs = nature_ans.get("probabilities", {})
        target_b = target_ans.get("choice", "NONE")

        return {
            "decision": decision,
            "confidence": conf,
            "probabilities": prob_map,
            "violation_score": violation_score,
            "nature": nature,
            "nature_confidence": nature_conf,
            "nature_probabilities": nature_probs,
            "target_brand": target_b,
            "latency_ms": res.get("latency_ms", 0),
            "is_fallback": False
        }

    @classmethod
    def match_category(
        cls,
        title: str,
        category: str,
        candidate_verticals: List[str],
        candidate_info_map: Dict[str, str],
        db=None
    ) -> Dict[str, Any]:
        """
        【功能 3】1,639 个 Makro 官方类目候选集极速裁定
        :param candidate_verticals: 候选官方类目代码列表 (如 ['hair_wig', 'beauty_hair_care', ...])
        :param candidate_info_map: 每个类目的中文/英文说明与路径映射
        """
        if not candidate_verticals:
            return {"vertical": None, "confidence": 0.0, "is_fallback": True}

        # 确保最多 10 个选项
        opts = candidate_verticals[:10]
        criteria_dict = {}
        for c in opts:
            info = candidate_info_map.get(c, "")
            criteria_dict[c] = f"Makro category vertical: {info}" if info else f"Category {c}"

        state = f"Product Title: {title}\nSource Category: {category}"

        questions = {
            "best_vertical": {
                "type": "choice",
                "instructions": "Select the SINGLE most appropriate Makro catalog vertical code for this product from the allowed options.",
                "criteria": criteria_dict,
                "options": opts
            }
        }

        res = cls.query(state=state, questions=questions, db=db, timeout=18.0)
        if not res.get("success"):
            return {
                "vertical": opts[0],
                "confidence": 0.5,
                "probabilities": {},
                "latency_ms": res.get("latency_ms", 0),
                "is_fallback": True
            }

        answers = res.get("answers", {})
        vert_ans = answers.get("best_vertical", {})
        chosen = vert_ans.get("choice", opts[0])
        conf = float(vert_ans.get("confidence", 0.8))

        return {
            "vertical": chosen,
            "confidence": conf,
            "probabilities": vert_ans.get("probabilities", {}),
            "latency_ms": res.get("latency_ms", 0),
            "is_fallback": False
        }

    @classmethod
    def test_connectivity(cls, db=None) -> Dict[str, Any]:
        """连通性与性能测试接口"""
        state = "Beishi Precision Screwdriver Set 24-in-1 Magnetic Repair Kit"
        questions = {
            "test_eval": {
                "type": "noul",
                "instructions": "Is this a tool product?"
            }
        }
        res = cls.query(state=state, questions=questions, db=db, timeout=18.0)
        cfg = cls.get_config(db)
        if res.get("success"):
            ans = res.get("answers", {}).get("test_eval", {})
            return {
                "success": True,
                "message": f"🎉 Jev 决策网关连通成功！模型: {res.get('model')}，响应时延: {res.get('latency_ms')} ms",
                "model": res.get("model"),
                "latency_ms": res.get("latency_ms"),
                "answers": ans,
                "config": {
                    "base_url": cfg["base_url"],
                    "model": cfg["model"],
                    "enabled": cfg["enabled"],
                    "api_key_masked": f"{cfg['api_key'][:10]}...{cfg['api_key'][-6:]}" if cfg["api_key"] else "未配置"
                }
            }
        else:
            return {
                "success": False,
                "message": f"❌ Jev 连通失败: {res.get('error')}",
                "latency_ms": res.get("latency_ms"),
                "config": {
                    "base_url": cfg["base_url"],
                    "model": cfg["model"],
                    "enabled": cfg["enabled"]
                }
            }
