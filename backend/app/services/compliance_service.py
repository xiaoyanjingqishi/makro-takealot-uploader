import re
import json
import base64
import logging
import time
import random
import os
import hashlib
import requests
from urllib3.util import Retry
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from ..config import settings
from ..models.setting import SystemSetting
from ..models.compliance_log import ComplianceArbitrationLog
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 本地首图持久化缓存目录 (复用全局 .image_cache)
IMAGE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".image_cache")
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

# 专属视觉多模态图片下载长连接池 (针对 100 并发场景预设 150 容量，支持指数退避重试)
_vision_image_session = requests.Session()
_vision_image_adapter = requests.adapters.HTTPAdapter(
    pool_connections=50,
    pool_maxsize=150,
    max_retries=Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
)
_vision_image_session.mount("https://", _vision_image_adapter)
_vision_image_session.mount("http://", _vision_image_adapter)

from ..constants.brands import (
    LUXURY_BRANDS,
    FAMOUS_BRANDS,
    ACCESSORY_KEYWORDS,
    COMPATIBILITY_KEYWORDS,
    RISK_LEVEL_ORDER,
    get_higher_risk,
)
from ..constants.protected_ips import PROTECTED_ENTERTAINMENT_IPS


class ComplianceService:
    """
    双 AI 交叉会审合规与侵权风控服务 (通义千问 + DeepSeek-Flash)
    两轮独立风控机制：
      - 第一轮：标题与品牌合规检测 (Qwen 文本模型 + DeepSeek-Flash 文本并发审查)
      - 第二轮：主图视觉与运输合规检测 (Qwen-VL-Max + DeepSeek-Flash 多模态视觉并发审查)
    仲裁与分歧裁决：
      - 判定一致：自动合并为统一诊断结果推送
      - 判定分歧：标记为 DISPUTED (分歧待仲裁)，并排展示双方依据，供人工一键裁定并沉淀为优化语料
    """

    def __init__(
        self,
        qwen_api_key: Optional[str] = None,
        qwen_base_url: Optional[str] = None,
        qwen_model: Optional[str] = None,
        qwen_vision_model: Optional[str] = None,
        deepseek_api_key: Optional[str] = None,
        deepseek_base_url: Optional[str] = None,
        deepseek_model: Optional[str] = None,
        deepseek_vision_model: Optional[str] = None,
        few_shot_examples: Optional[List[Dict[str, Any]]] = None
    ):
        # 通义千问配置
        self.qwen_api_key = qwen_api_key or settings.QWEN_API_KEY
        self.qwen_base_url = qwen_base_url or settings.QWEN_BASE_URL
        self.qwen_model = qwen_model or settings.QWEN_MODEL
        self.qwen_vision_model = qwen_vision_model or getattr(settings, "DEFAULT_QWEN_VISION_MODEL", "qwen-vl-max")

        # DeepSeek 配置 (对接 DeepSeek 官方 API: https://api.deepseek.com)
        self.deepseek_api_key = deepseek_api_key or settings.DEEPSEEK_API_KEY
        self.deepseek_base_url = deepseek_base_url or settings.DEEPSEEK_BASE_URL
        self.deepseek_model = deepseek_model or settings.DEEPSEEK_MODEL
        self.deepseek_vision_model = deepseek_vision_model or getattr(settings, "DEEPSEEK_VISION_MODEL", "deepseek-flash")

        # 动态少样本库 (从人工仲裁日志中沉淀的 Few-Shot 样本)
        self.few_shot_examples = few_shot_examples or []

        # 初始化 Qwen 客户端
        self.qwen_client = None
        if self.qwen_api_key:
            try:
                self.qwen_client = OpenAI(api_key=self.qwen_api_key, base_url=self.qwen_base_url, timeout=35.0)
            except Exception as e:
                logger.warning(f"初始化 Qwen 客户端异常: {e}")

        # 初始化 DeepSeek 客户端
        self.deepseek_client = None
        if self.deepseek_api_key:
            try:
                self.deepseek_client = OpenAI(api_key=self.deepseek_api_key, base_url=self.deepseek_base_url, timeout=35.0)
            except Exception as e:
                logger.warning(f"初始化 DeepSeek 客户端异常: {e}")

    @property
    def has_qwen(self) -> bool:
        return bool(self.qwen_client and self.qwen_api_key)

    @property
    def has_deepseek(self) -> bool:
        return bool(self.deepseek_client and self.deepseek_api_key)

    @property
    def is_dual_ai_mode(self) -> bool:
        return self.has_qwen and self.has_deepseek

    @classmethod
    def from_db(cls, db: Session):
        """从数据库系统设置中动态加载两方模型凭据与配置，并自动检索高质量人工仲裁样本供 Few-Shot 优化"""
        def _get_setting(key: str, default_val: str) -> str:
            s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            return s.value.strip() if s and s.value else default_val

        q_key = _get_setting("qwen_api_key", settings.QWEN_API_KEY)
        q_url = _get_setting("qwen_base_url", settings.QWEN_BASE_URL)
        q_model = _get_setting("qwen_model", settings.QWEN_MODEL)
        q_vmodel = _get_setting("qwen_vision_model", getattr(settings, "DEFAULT_QWEN_VISION_MODEL", "qwen-vl-max"))

        d_key = _get_setting("deepseek_api_key", settings.DEEPSEEK_API_KEY)
        d_url = _get_setting("deepseek_base_url", settings.DEEPSEEK_BASE_URL)
        d_model = _get_setting("deepseek_model", settings.DEEPSEEK_MODEL)
        d_vmodel = _get_setting("deepseek_vision_model", getattr(settings, "DEEPSEEK_VISION_MODEL", "deepseek-flash"))

        # 动态提示词优化工程：加载最近经过人工仲裁的历史样本作为 Few-Shot 优质对比示例
        few_shots = []
        try:
            recent_arbitrations = (
                db.query(ComplianceArbitrationLog)
                .filter(ComplianceArbitrationLog.human_verdict.isnot(None))
                .order_by(ComplianceArbitrationLog.id.desc())
                .limit(4)
                .all()
            )
            for r in recent_arbitrations:
                few_shots.append({
                    "title": r.takealot_title or r.makro_title or "",
                    "brand": r.brand or "Beishi",
                    "human_verdict": r.human_verdict,
                    "notes": r.human_notes or "",
                    "attribution": r.error_attribution or ""
                })
        except Exception as e:
            logger.debug(f"加载 Few-Shot 仲裁样本跳过: {e}")

        return cls(
            qwen_api_key=q_key,
            qwen_base_url=q_url,
            qwen_model=q_model,
            qwen_vision_model=q_vmodel,
            deepseek_api_key=d_key,
            deepseek_base_url=d_url,
            deepseek_model=d_model,
            deepseek_vision_model=d_vmodel,
            few_shot_examples=few_shots
        )

    def check_product(self, product_data: Dict[str, Any], check_image: bool = True, check_ai_title: bool = True) -> Dict[str, Any]:
        """
        双 AI 交叉全量检测入口
        执行两轮独立检测:
          - 第一轮: 标题与品牌独立文本风控 (Qwen + DeepSeek-Flash 并发)
          - 第二轮: 主图视觉与运输合规风控 (Qwen-VL + DeepSeek-Flash 视觉并发)
        比对两方结论: 一致自动合并，分歧标记为 DISPUTED
        """
        title = product_data.get("takealot_title") or product_data.get("title") or ""
        makro_title = product_data.get("makro_title") or ""
        desc = product_data.get("takealot_description") or product_data.get("description") or ""
        specs = product_data.get("takealot_specs") or product_data.get("specs") or ""
        specs_str = json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)
        category = product_data.get("takealot_category") or product_data.get("category_path") or product_data.get("category") or ""
        target_brand_name = product_data.get("makro_brand") or product_data.get("brand") or "Beishi"
        raw_images = product_data.get("raw_images") or product_data.get("images") or []
        if isinstance(raw_images, str):
            try:
                raw_images = json.loads(raw_images)
            except Exception:
                raw_images = [raw_images] if raw_images.startswith("http") else []

        is_piggyback = bool(product_data.get("is_piggyback", False))
        original_seller = str(product_data.get("original_seller") or "").strip()

        full_text = f"{title} {makro_title} {desc} {specs_str} {category}".lower()

        # ----------------------------------------------------
        # 基础本地硬性规则检测 (兜底层: 确保零漏判)
        # ----------------------------------------------------
        local_rules_res = self._check_local_rules(
            title=title,
            makro_title=makro_title,
            full_text=full_text,
            category=category,
            target_brand_name=target_brand_name,
            is_piggyback=is_piggyback,
            original_seller=original_seller
        )

        # ----------------------------------------------------
        # 第一轮: 标题与品牌合规检测 (三 AI 并发文本审查: Qwen + DeepSeek + Jev)
        # ----------------------------------------------------
        qwen_title_res = {"tested": False, "risk_level": "SAFE", "summary": "未启用或未配置"}
        deepseek_title_res = {"tested": False, "risk_level": "SAFE", "summary": "未启用或未配置"}
        jev_title_res = {"tested": False, "risk_level": "SAFE", "violation_score": 0.0, "confidence": 0.0, "summary": "未启用或未配置"}

        if check_ai_title:
            qwen_title_res, deepseek_title_res, jev_title_res = self._run_round1_text_audit(
                raw_title=title,
                makro_title=makro_title,
                brand=target_brand_name,
                category=category,
                description=desc,
                is_piggyback=is_piggyback,
                original_seller=original_seller
            )

        # ----------------------------------------------------
        # 第二轮: 主图视觉与运输合规检测 (双 AI 并发多模态视觉审查)
        # ----------------------------------------------------
        first_img_url = None
        if isinstance(raw_images, list) and len(raw_images) > 0 and isinstance(raw_images[0], str) and raw_images[0].startswith("http"):
            first_img_url = raw_images[0].strip()
            if "{size}" in first_img_url:
                first_img_url = first_img_url.replace("{size}", "pdpxl")

        qwen_image_res = {"tested": False, "image_url": first_img_url, "risk_level": "SAFE", "summary": "未执行视觉审查"}
        deepseek_image_res = {"tested": False, "image_url": first_img_url, "risk_level": "SAFE", "summary": "未执行视觉审查"}

        # 第二轮视觉审查全量执行 (已废弃底层死板硬拦截剪枝，100% 由双 AI 多模态视觉审查)
        if check_image and first_img_url:
            all_known_brands = list(dict.fromkeys(
                local_rules_res.get("detected_brands", []) +
                qwen_title_res.get("detected_brands_or_ips", []) +
                deepseek_title_res.get("detected_brands_or_ips", [])
            ))
            qwen_image_res, deepseek_image_res = self._run_round2_vision_audit(
                image_url=first_img_url,
                known_brands=all_known_brands,
                is_piggyback=is_piggyback,
                original_seller=original_seller
            )


        # ----------------------------------------------------
        # 会审裁决与分歧聚合器 (Reconciliation Engine - 升级支持 3-AI 并发共识)
        # ----------------------------------------------------
        final_result = self._reconcile_dual_verdicts(
            local_rules=local_rules_res,
            qwen_title=qwen_title_res,
            deepseek_title=deepseek_title_res,
            qwen_image=qwen_image_res,
            deepseek_image=deepseek_image_res,
            first_img_url=first_img_url,
            target_brand_name=target_brand_name,
            jev_title=jev_title_res,
            is_piggyback=is_piggyback,
            original_seller=original_seller
        )

        return final_result

    # =========================================================================
    # 第一轮: 标题与品牌文本独立风控 (Prompt 优化工程 + 双 AI 并发)
    # =========================================================================
    def _run_round1_text_audit(
        self,
        raw_title: str,
        makro_title: Optional[str],
        brand: str,
        category: str,
        description: str,
        is_piggyback: bool = False,
        original_seller: str = ""
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """并发运行千问、DeepSeek-Flash 与 Jev (TypeSafe AI) 的第一轮文本审查 (3-AI 并行)"""
        qwen_res = {"tested": False, "risk_level": "SAFE", "reasons": [], "summary": "Qwen 客户端未配置"}
        deepseek_res = {"tested": False, "risk_level": "SAFE", "reasons": [], "summary": "DeepSeek 客户端未配置"}
        jev_res = {"tested": False, "risk_level": "SAFE", "violation_score": 0.0, "confidence": 0.0, "summary": "Jev 未配置或停用"}

        target_title = (makro_title or raw_title or "").strip()

        prompt = self._build_title_ip_prompt(
            raw_title=raw_title,
            makro_title=makro_title,
            brand=brand,
            category=category,
            description=description,
            is_piggyback=is_piggyback,
            original_seller=original_seller
        )

        def _call_qwen():
            if not self.qwen_client:
                return qwen_res
            return self._invoke_text_model(
                client=self.qwen_client,
                model=self.qwen_model,
                ai_name="Qwen-Plus",
                prompt=prompt,
                raw_title=raw_title,
                makro_title=makro_title
            )

        def _call_deepseek():
            if not self.deepseek_client:
                return deepseek_res
            return self._invoke_text_model(
                client=self.deepseek_client,
                model=self.deepseek_model,
                ai_name="DeepSeek-Flash",
                prompt=prompt,
                raw_title=raw_title,
                makro_title=makro_title
            )

        def _call_jev():
            try:
                from .jev_service import JevService
                res = JevService.evaluate_compliance(
                    title=target_title,
                    brand=brand,
                    category=category,
                    description=description
                )
                if res.get("is_fallback") and res.get("error"):
                    return {
                        "tested": False,
                        "risk_level": "SAFE",
                        "violation_score": 0.0,
                        "confidence": 0.0,
                        "summary": f"Jev 未启用或异常: {res.get('error')[:40]}"
                    }
                r_level = res.get("decision", "SAFE")
                v_score = res.get("violation_score", 0.0)
                conf = res.get("confidence", 1.0)
                nature = res.get("nature", "GENERIC_WHITE_LABEL")
                target_brand_res = res.get("target_brand", "NONE")
                return {
                    "tested": True,
                    "ai_name": "Jev-Latest (TypeSafe)",
                    "risk_level": r_level,
                    "violation_score": v_score,
                    "confidence": conf,
                    "probabilities": res.get("probabilities", {}),
                    "brand_nature": nature,
                    "target_brand": target_brand_res,
                    "nature_confidence": res.get("nature_confidence", 0.8),
                    "nature_probabilities": res.get("nature_probabilities", {}),
                    "latency_ms": res.get("latency_ms", 0),
                    "summary": f"Jev 决策: [{r_level}], 形态: [{nature}], 违规分: {v_score:.2f}, 置信度: {conf*100:.0f}% ({res.get('latency_ms')}ms)"
                }
            except Exception as e:
                logger.warning(f"Jev 合规评估异常: {e}")
                return {
                    "tested": False,
                    "risk_level": "SAFE",
                    "violation_score": 0.0,
                    "confidence": 0.0,
                    "summary": f"Jev 审查异常: {str(e)[:40]}"
                }

        # 三 AI 并发推理 (Jev 约 0.2s 极速返回，LLM 约 0.6s~1.2s，并行总耗时保持不变)
        with ThreadPoolExecutor(max_workers=3) as executor:
            fut_q = executor.submit(_call_qwen)
            fut_d = executor.submit(_call_deepseek)
            fut_j = executor.submit(_call_jev)
            try:
                qwen_res = fut_q.result(timeout=40.0)
            except Exception as e:
                logger.warning(f"Qwen 文本审查超时或异常: {e}")
                qwen_res = {"tested": False, "risk_level": "SAFE", "reasons": [f"Qwen 调用异常: {str(e)[:50]}"], "summary": "Qwen 审查异常"}
            try:
                deepseek_res = fut_d.result(timeout=40.0)
            except Exception as e:
                logger.warning(f"DeepSeek 文本审查超时或异常: {e}")
                deepseek_res = {"tested": False, "risk_level": "SAFE", "reasons": [f"DeepSeek 调用异常: {str(e)[:50]}"], "summary": "DeepSeek 审查异常"}
            try:
                jev_res = fut_j.result(timeout=15.0)
            except Exception as e:
                logger.warning(f"Jev 决策审查超时或异常: {e}")
                jev_res = {"tested": False, "risk_level": "SAFE", "violation_score": 0.0, "confidence": 0.0, "summary": f"Jev 调用异常: {str(e)[:50]}"}

        return qwen_res, deepseek_res, jev_res

    def _build_title_ip_prompt(
        self,
        raw_title: str,
        makro_title: Optional[str],
        brand: str,
        category: str,
        description: str,
        is_piggyback: bool = False,
        original_seller: str = ""
    ) -> str:
        """极简高密度标题与知识产权风控审查 Prompt (压缩~70% Token 消耗，保留 100% 审查精确度)"""
        target_title = (makro_title or raw_title or "").strip()

        few_shot_block = ""
        if self.few_shot_examples:
            compact_shots = []
            for item in self.few_shot_examples[:2]:
                compact_shots.append(f'"{item["title"][:35]}"->[{item["human_verdict"]}]({item["notes"][:20] or "规范配件"})')
            few_shot_block = f"\n参考基准: {'; '.join(compact_shots)}"

        clean_desc = re.sub(r'<[^>]+>', ' ', str(description or ""))
        clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()[:80]
        desc_line = f"\n- 描述: {clean_desc}" if clean_desc else ""

        piggyback_block = ""
        if is_piggyback:
            piggyback_block = f"""
【Makro 跟品(Piggybacking/Latching) 特别风控准则】:
* 平台特性: Makro 平台强制在标题最前端拼接原 Listing 品牌名（例如 "{brand} {target_title}"）。
* 卖家自造白牌/杂牌豁免: 若品牌为普通卖家自造商号/白牌(如 HYinjin, Generic, OEM 或拼音杂牌，非全球受保护大牌)，根据 Makro 官方跟品规则，100% 允许合规挂靠跟品！此类必须判定为 SAFE (合规)，绝不能误判为商标侵权！
* 知名大牌一票否决红线: 若标题或品牌指代受保护知名大牌(Apple, Dyson, Sony, Nike, Stanley, Makita 等)或知名影视IP整机，严格判定为 PROHIBITED (禁跟)！
* 副厂兼容配件(Fair Use): 若商品属于通用耗材/滤网/替换配件/外壳(如 vacuum maintenance kit, filter, replacement parts 等)，含有知名品牌用于指示兼容型号的，属于合理使用，判定为 SAFE。"""

        return f"""审查跨境电商商品标题合规与侵权风险，仅返回合法JSON，严禁输出思维过程与闲聊。
【规则】:
1. 商标侵权: 严查受保护大牌(如Apple,Stanley,Nike,Dyson等)。区分语境: 颜色/通用词(如apple green)合规，指代受保护品牌违规。
2. 影视IP: 严禁未经授权蹭用知名动漫潮玩/游戏IP(如Sanrio,Disney,Marvel,Pokemon,One Piece,Frozen,Labubu等)。注意区分语境: 商品件数/规格词(如1 piece, 2 pieces)属合规数量词，严禁误判为海贼王One Piece！冷冻甜品/冰块模具(如frozen mold)属合规用途词，严禁误判为冰雪奇缘Frozen！仅指代动漫角色/衍生周边才判定违规。
3. 配件规范: 兼容大牌配件必须含'Compatible with'或'For'；严禁大牌开头冒充原厂；严禁连续堆砌>=3个大牌。
4. 禁运技术与物品: 严禁蓝牙(Bluetooth)、WiFi、红外线(Infrared)等无线发射设备；严禁液体/香水/精油/乳液/膏霜/易燃化学品跨境航空禁运品。注意区分形态: 硅胶模具、空瓶容器、化妆刷/粉扑、刮痧板按摩石、喷头喷枪工具、无源转接线等实体用具均属合规SAFE；仅商品本身实际灌装/包含液体、膏体、化学药剂才判定为PROHIBITED违规禁运。
5. 评级: SAFE(合规/通用品/规范配件/实体工具), RISK(配件缺少Compatible with声明/可整改瑕疵), PROHIBITED(假冒原厂/大牌整机/未授权IP/禁售无线设备/灌装液体航空违禁品)。{few_shot_block}{piggyback_block}
待审数据:
- 标题: {target_title}
- 授权自有品牌: {brand or "Beishi"}
- 类目: {category or "通用"}{desc_line}
返回JSON:
{{"has_risk":false,"risk_level":"SAFE|RISK|PROHIBITED","detected_brands_or_ips":[],"violation_type":"NONE|TRADEMARK|COPYRIGHT_IP|BRAND_SPAMMING|MISSING_COMPATIBILITY|PROHIBITED_TECH|PROHIBITED_LIQUID","reasons":["简明中文理由(1句)"],"recommended_title":"合规英文建议标题"}}"""

    def _invoke_text_model(
        self,
        client: OpenAI,
        model: str,
        ai_name: str,
        prompt: str,
        raw_title: str,
        makro_title: Optional[str]
    ) -> Dict[str, Any]:
        """向指定模型客户端发送文本风控请求并解析结构化结论 (严格控制 max_tokens 杜绝 token 浪费)"""
        target_title = (makro_title or raw_title or "").strip()
        is_deepseek = "deepseek" in ai_name.lower() or "deepseek" in model.lower()
        tok_limit = 450 if is_deepseek else 220
        sys_msg = (
            f"You are a fast, decisive ecommerce IP compliance auditor for {ai_name}. "
            "Do NOT write long thinking essays. Output the final valid JSON object immediately. Reply ONLY with valid JSON."
        ) if is_deepseek else f"You are a professional ecommerce IP compliance auditor for {ai_name}. Reply ONLY with valid JSON."
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=tok_limit
                )
                choice = resp.choices[0]
                content = choice.message.content or ""
                parsed = self._robust_parse_json(content)
                if not parsed and hasattr(choice.message, "reasoning_content") and choice.message.reasoning_content:
                    parsed = self._robust_parse_json(choice.message.reasoning_content)

                if not parsed:
                    parsed = {"has_risk": False, "risk_level": "SAFE", "reasons": [], "detected_brands_or_ips": []}

                has_risk = bool(parsed.get("has_risk", False))
                risk_level = str(parsed.get("risk_level", "SAFE")).upper()
                if risk_level not in ["SAFE", "RISK", "PROHIBITED"]:
                    risk_level = "RISK" if has_risk else "SAFE"

                detected = parsed.get("detected_brands_or_ips", [])
                reasons = parsed.get("reasons", [])
                rec_title = parsed.get("recommended_title")

                return {
                    "tested": True,
                    "ai_name": ai_name,
                    "model": model,
                    "has_risk": has_risk or (risk_level != "SAFE"),
                    "risk_level": risk_level,
                    "detected_brands_or_ips": detected,
                    "violation_type": parsed.get("violation_type", "NONE"),
                    "reasons": reasons,
                    "recommended_title": rec_title,
                    "summary": "；".join(reasons) if reasons else ("未发现知识产权侵权" if risk_level == "SAFE" else "存在知识产权风险")
                }
            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = any(k in err_str for k in ["429", "rate limit", "too many requests", "rate_limit", "quota"])
                is_transient = is_rate_limit or any(k in err_str for k in ["timeout", "connection", "connecterror", "reset", "502", "503", "504"])
                if attempt < max_retries and is_transient:
                    backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                    logger.warning(f"[{ai_name}] 触发瞬时限流或网络抖动 ({e})，将在 {backoff:.2f}s 后进行第 {attempt + 1} 次重试...")
                    time.sleep(backoff)
                    continue
                logger.warning(f"[{ai_name}] 文本合规调用异常: {e}")
                return {
                    "tested": False,
                    "ai_name": ai_name,
                    "model": model,
                    "has_risk": False,
                    "risk_level": "SAFE",
                    "detected_brands_or_ips": [],
                    "violation_type": "NONE",
                    "reasons": [f"{ai_name} 审查跳过: {str(e)[:50]}"],
                    "recommended_title": target_title,
                    "summary": f"{ai_name} 调用跳过 ({str(e)[:40]})"
                }

    # =========================================================================
    # 第二轮: 主图视觉与运输合规多模态风控 (双 AI 并发视觉审查)
    # =========================================================================
    def _run_round2_vision_audit(
        self,
        image_url: str,
        known_brands: List[str],
        is_piggyback: bool = False,
        original_seller: str = ""
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """并发运行千问 (Qwen-VL) 与 DeepSeek-Flash 视觉多模态审查"""
        if not image_url or not isinstance(image_url, str) or not image_url.startswith("http"):
            empty_res = {"tested": False, "image_url": image_url, "risk_level": "SAFE", "summary": "未提供有效图片URL"}
            return empty_res, empty_res

        clean_url = image_url.replace("{size}", "pdpxl") if "{size}" in image_url else image_url
        qwen_img_res = {"tested": False, "image_url": clean_url, "risk_level": "SAFE", "summary": "Qwen 视觉未配置"}
        deepseek_img_res = {"tested": False, "image_url": clean_url, "risk_level": "SAFE", "summary": "DeepSeek 视觉未配置"}

        # 1. 统一下载图片并转换为 Base64 Data URI (支持本地磁盘缓存 + 专用长连接会话池)
        cache_key = hashlib.md5(clean_url.encode("utf-8")).hexdigest()
        cache_path = os.path.join(IMAGE_CACHE_DIR, f"{cache_key}.img")
        content_type_path = os.path.join(IMAGE_CACHE_DIR, f"{cache_key}.type")

        img_bytes = None
        mime = "image/jpeg"

        # 1.1 命中本地持久化缓存直接流式复用 (0ms，跳过网络抓取)
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, "rb") as f:
                    img_bytes = f.read()
                if os.path.exists(content_type_path):
                    with open(content_type_path, "r", encoding="utf-8") as f:
                        mime = f.read().strip() or "image/jpeg"
            except Exception as ce:
                logger.warning(f"读取首图本地磁盘缓存失败 ({cache_key}): {ce}")
                img_bytes = None

        # 1.2 未命中缓存则通过高并发长连接 Session 抓取远程图片 (自动 Referer + 重试 3 次)
        if not img_bytes:
            is_makro_img = any(k in clean_url.lower() for k in ["makro.co.za", "flixcart.com", "fkcloud", "rukmini"])
            img_referer = "https://www.makro.co.za/" if is_makro_img else "https://www.takealot.com/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": img_referer
            }
            dl_err = None
            for dl_attempt in range(3):
                try:
                    resp = _vision_image_session.get(clean_url, headers=headers, timeout=12)
                    if resp.status_code == 200 and resp.content:
                        img_bytes = resp.content
                        ct = resp.headers.get("content-type", "").split(";")[0].strip()
                        if ct and ct.startswith("image/"):
                            mime = ct
                        elif img_bytes.startswith(b'\x89PNG'):
                            mime = "image/png"
                        elif img_bytes.startswith(b'RIFF') and b'WEBP' in img_bytes[:16]:
                            mime = "image/webp"
                        elif img_bytes.startswith(b'GIF'):
                            mime = "image/gif"
                        else:
                            mime = "image/jpeg"

                        # 异步落盘缓存供后续全流程复用
                        try:
                            with open(cache_path, "wb") as f:
                                f.write(img_bytes)
                            with open(content_type_path, "w", encoding="utf-8") as f:
                                f.write(mime)
                        except Exception:
                            pass
                        break
                    else:
                        dl_err = f"HTTP {resp.status_code}"
                        if dl_attempt < 2:
                            time.sleep(0.4 * (dl_attempt + 1))
                except Exception as ex:
                    dl_err = str(ex)
                    if dl_attempt < 2:
                        time.sleep(0.4 * (dl_attempt + 1))

        # 1.3 绝不默认 SAFE！下载失败直接判出 RISK，阻断漏判静默放行
        if not img_bytes:
            err_msg = f"首图下载失败 (重试3次仍异常: {dl_err or '连接超时或网络阻断'})"
            err_reason = f"【首图下载异常】{err_msg}，无法排查潜在图片商标侵权与禁运品，需人工复核"
            logger.warning(f"首图下载失败: {clean_url} -> {err_msg}")
            fail_res = {
                "tested": False,
                "download_error": True,
                "image_url": clean_url,
                "risk_level": "RISK",
                "has_brand_logo": False,
                "logo_names": [],
                "is_transport_prohibited": False,
                "prohibited_types": [],
                "reasons": [err_reason],
                "summary": err_msg
            }
            return dict(fail_res), dict(fail_res)

        b64_data = base64.b64encode(img_bytes).decode("utf-8")
        data_uri = f"data:{mime};base64,{b64_data}"

        vision_prompt = self._build_vision_prompt(known_brands, is_piggyback=is_piggyback)

        def _call_qwen_vl():
            if not self.qwen_client:
                return qwen_img_res
            return self._invoke_vision_model(
                client=self.qwen_client,
                model=self.qwen_vision_model,
                ai_name="Qwen-VL",
                prompt=vision_prompt,
                data_uri=data_uri,
                image_url=clean_url
            )

        def _call_deepseek_vl():
            if not self.deepseek_client:
                return deepseek_img_res
            return self._invoke_vision_model(
                client=self.deepseek_client,
                model=self.deepseek_vision_model,
                ai_name="DeepSeek-Flash-Vision",
                prompt=vision_prompt,
                data_uri=data_uri,
                image_url=clean_url
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_qv = executor.submit(_call_qwen_vl)
            fut_dv = executor.submit(_call_deepseek_vl)
            try:
                qwen_img_res = fut_qv.result(timeout=45.0)
            except Exception as e:
                logger.warning(f"Qwen-VL 视觉审查异常: {e}")
                qwen_img_res = {"tested": False, "image_url": clean_url, "risk_level": "RISK", "summary": f"Qwen-VL 异常: {str(e)[:40]}"}
            try:
                deepseek_img_res = fut_dv.result(timeout=45.0)
            except Exception as e:
                logger.warning(f"DeepSeek 视觉审查异常: {e}")
                deepseek_img_res = {"tested": False, "image_url": clean_url, "risk_level": "RISK", "summary": f"DeepSeek 视觉异常: {str(e)[:40]}"}

        return qwen_img_res, deepseek_img_res

    def _build_vision_prompt(self, known_brands: List[str], is_piggyback: bool = False) -> str:
        """首图合规视觉审查 Prompt (严格识别一切品牌 Logo 与防盗水印)"""
        brand_hint = f"（重点排查: {', '.join(known_brands[:3])}）" if known_brands else ""
        piggyback_note = "【全品牌Logo零容忍图审准则】: 画面或产品机身/外包装上只要带任何品牌商标Logo、徽标、专有标识（不论是国际名牌还是卖家自造白牌），一律判定为侵权违规 PROHIBITED！仅完全无任何Logo的纯中性白底产品图判定为 SAFE！"

        return f"""电商首图视觉合规审查{brand_hint}。{piggyback_note}仅返回JSON，禁止多余输出。
【准则】:
1. 品牌Logo零容忍: 严查任何品牌Logo、商标图样、徽标、文字图形标、防盗水印。只要画面或产品本体/包装带有任何品牌Logo（包含知名大牌或卖家白牌Logo），一律判定为 PROHIBITED，严禁放行！仅无任何Logo的纯中性/白牌通用产品图判定为 SAFE。
2. 运输违禁: 严查液体/精油/香水/喷雾、纯电池/易燃易爆品、管制刀具等航空安检禁运形态，检出判定为 PROHIBITED。
3. 评级定义: SAFE(纯中性中立白底产品图，完全无任何品牌Logo且无违禁品), RISK(画面疑似隐蔽Logo/水印模糊), PROHIBITED(明确检出任何品牌Logo/商标文字/危险禁运品)。
返回JSON:
{{"has_brand_logo":false,"detected_logos":[],"is_transport_prohibited":false,"prohibited_types":[],"risk_level":"SAFE|RISK|PROHIBITED","summary":"1句中文结论"}}"""

    @staticmethod
    def _robust_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """多重容错提取并解析 JSON 对象 (支持截断、额外 prose、嵌入 markdown 块)"""
        if not text or not text.strip():
            return None
        # 1. 整体直接解析
        try:
            d = json.loads(text.strip())
            if isinstance(d, dict):
                return d
        except Exception:
            pass

        # 2. 从第一个 '{' 开始尝试 raw_decode
        start_idx = text.find('{')
        decoder = json.JSONDecoder()
        while start_idx != -1:
            try:
                obj, _ = decoder.raw_decode(text[start_idx:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
            start_idx = text.find('{', start_idx + 1)

        # 3. 正则贪婪匹配
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                d = json.loads(json_match.group())
                if isinstance(d, dict):
                    return d
            except Exception:
                pass

        # 4. 逐个非贪婪匹配
        for m in re.finditer(r'\{[^{}]*\}', text):
            try:
                d = json.loads(m.group())
                if isinstance(d, dict):
                    return d
            except Exception:
                continue

        return None

    def _invoke_vision_model(
        self,
        client: OpenAI,
        model: str,
        ai_name: str,
        prompt: str,
        data_uri: str,
        image_url: str
    ) -> Dict[str, Any]:
        """向指定多模态视觉模型发送图像审查请求 (严格限流 max_tokens)"""
        is_deepseek = "deepseek" in ai_name.lower() or "deepseek" in model.lower()
        tok_limit = 350 if is_deepseek else 180
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": data_uri}}
                            ]
                        }
                    ],
                    temperature=0.1,
                    max_tokens=tok_limit
                )
                choice = response.choices[0]
                content = choice.message.content or ""
                parsed = self._robust_parse_json(content)

                if not parsed and hasattr(choice.message, "reasoning_content") and choice.message.reasoning_content:
                    parsed = self._robust_parse_json(choice.message.reasoning_content)

                if not parsed:
                    parsed = {"has_brand_logo": False, "is_transport_prohibited": False, "risk_level": "SAFE", "summary": f"{ai_name} 视觉未检出异常"}

                has_logo = bool(parsed.get("has_brand_logo", False))
                is_prohib = bool(parsed.get("is_transport_prohibited", False))
                risk_level = str(parsed.get("risk_level", "SAFE")).upper()
                if risk_level not in ["SAFE", "RISK", "PROHIBITED"]:
                    risk_level = "PROHIBITED" if is_prohib else ("RISK" if has_logo else "SAFE")

                return {
                    "tested": True,
                    "ai_name": ai_name,
                    "model": model,
                    "image_url": image_url,
                    "has_brand_logo": has_logo,
                    "logo_names": parsed.get("detected_logos", []),
                    "is_transport_prohibited": is_prohib,
                    "prohibited_types": parsed.get("prohibited_types", []),
                    "risk_level": risk_level,
                    "summary": parsed.get("summary", f"{ai_name} 视觉检测完成")
                }
            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = any(k in err_str for k in ["429", "rate limit", "too many requests", "rate_limit", "quota"])
                is_transient = is_rate_limit or any(k in err_str for k in ["timeout", "connection", "connecterror", "reset", "502", "503", "504"])
                if attempt < max_retries and is_transient:
                    backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                    logger.warning(f"[{ai_name}] 视觉审查触发瞬时限流或网络抖动 ({e})，将在 {backoff:.2f}s 后进行第 {attempt + 1} 次重试...")
                    time.sleep(backoff)
                    continue
                logger.warning(f"[{ai_name}] 视觉检测调用异常: {e}")
                return {
                    "tested": False,
                    "ai_name": ai_name,
                    "model": model,
                    "image_url": image_url,
                    "has_brand_logo": False,
                    "logo_names": [],
                    "is_transport_prohibited": False,
                    "prohibited_types": [],
                    "risk_level": "RISK",
                    "summary": f"{ai_name} 视觉检测跳过 ({str(e)[:40]})"
                }

    # =========================================================================
    # 会审聚合与分歧裁决器 (Reconciliation Engine)
    # =========================================================================
    def _reconcile_dual_verdicts(
        self,
        local_rules: Dict[str, Any],
        qwen_title: Dict[str, Any],
        deepseek_title: Dict[str, Any],
        qwen_image: Dict[str, Any],
        deepseek_image: Dict[str, Any],
        first_img_url: Optional[str],
        target_brand_name: str,
        jev_title: Optional[Dict[str, Any]] = None,
        is_piggyback: bool = False,
        original_seller: str = ""
    ) -> Dict[str, Any]:
        """
        裁决聚合器 (Reconciliation Engine)：
        1. 计算 Qwen 综合评级 = max(Qwen 文本, Qwen 视觉)
        2. 计算 DeepSeek 综合评级 = max(DeepSeek 文本, DeepSeek 视觉)
        3. 接入 Jev (TypeSafe AI) 决策模型，升级为【三 AI 并发交叉会审与加权仲裁】
        4. 细粒度归因分析与分歧根因溯源
        5. 本地规则硬性兜底防护
        """
        # 检查首图下载状态 (异常绝不能默认 SAFE)
        has_dl_error = bool(qwen_image.get("download_error") or deepseek_image.get("download_error"))

        # 千问文本与视觉评级
        qwen_text_level = qwen_title.get("risk_level", "SAFE") if qwen_title.get("tested") else "SAFE"
        if has_dl_error:
            qwen_img_level = "RISK"
        elif qwen_image.get("tested"):
            qwen_img_level = qwen_image.get("risk_level", "SAFE")
        elif deepseek_image.get("tested"):
            # 若千问视觉未测试但 DeepSeek 视觉已测试，继承已测试模型结论，避免虚假分歧
            qwen_img_level = deepseek_image.get("risk_level", "SAFE")
        else:
            qwen_img_level = "SAFE"
        qwen_overall = get_higher_risk(qwen_text_level, qwen_img_level)

        # DeepSeek 文本与视觉评级
        deepseek_text_level = deepseek_title.get("risk_level", "SAFE") if deepseek_title.get("tested") else "SAFE"
        if has_dl_error:
            deepseek_img_level = "RISK"
        elif deepseek_image.get("tested"):
            deepseek_img_level = deepseek_image.get("risk_level", "SAFE")
        elif qwen_image.get("tested"):
            # 若 DeepSeek 视觉未测试但千问视觉已测试 (如 DeepSeek 官方无 Vision API)，继承千问结论，避免虚假分歧
            deepseek_img_level = qwen_image.get("risk_level", "SAFE")
        else:
            deepseek_img_level = "SAFE"
        deepseek_overall = get_higher_risk(deepseek_text_level, deepseek_img_level)

        # Jev 评级与违规分 (System 1 独立裁判)
        jev_tested = bool(jev_title and jev_title.get("tested"))
        jev_level = str(jev_title.get("risk_level", "SAFE")).upper() if jev_tested else "SAFE"
        jev_score = float(jev_title.get("violation_score", 0.0)) if jev_tested else 0.0
        jev_conf = float(jev_title.get("confidence", 1.0)) if jev_tested else 0.0
        jev_latency = jev_title.get("latency_ms", 0) if jev_tested else 0

        # 组装千问专属快照
        qwen_verdict = {
            "tested": qwen_title.get("tested", False) or qwen_image.get("tested", False),
            "overall_risk": qwen_overall,
            "title_audit": qwen_title,
            "image_audit": qwen_image
        }

        # 组装 DeepSeek 专属快照
        deepseek_verdict = {
            "tested": deepseek_title.get("tested", False) or deepseek_image.get("tested", False),
            "overall_risk": deepseek_overall,
            "title_audit": deepseek_title,
            "image_audit": deepseek_image
        }

        # 组装 Jev 专属快照 (含品牌形态极速三元判定)
        jev_brand_nature = jev_title.get("brand_nature", "GENERIC_WHITE_LABEL") if jev_title else "GENERIC_WHITE_LABEL"
        jev_target_brand = jev_title.get("target_brand", "NONE") if jev_title else "NONE"
        jev_verdict = {
            "tested": jev_tested,
            "ai_name": "Jev-Latest (TypeSafe)",
            "risk_level": jev_level,
            "violation_score": jev_score,
            "confidence": jev_conf,
            "probabilities": jev_title.get("probabilities", {}) if jev_title else {},
            "brand_nature": jev_brand_nature,
            "target_brand": jev_target_brand,
            "nature_confidence": jev_title.get("nature_confidence", 0.8) if jev_title else 0.8,
            "nature_probabilities": jev_title.get("nature_probabilities", {}) if jev_title else {},
            "latency_ms": jev_latency,
            "summary": jev_title.get("summary", "") if jev_title else "未测试"
        }

        # 品牌检出归因分析 (Brand Attribution Breakdown)
        qwen_brands = list(dict.fromkeys(qwen_title.get("detected_brands_or_ips", [])))
        deepseek_brands = list(dict.fromkeys(deepseek_title.get("detected_brands_or_ips", [])))
        local_brands = list(dict.fromkeys(local_rules.get("detected_brands", [])))

        consensus_brands = [b for b in qwen_brands if b in deepseek_brands]
        qwen_only_brands = [b for b in qwen_brands if b not in deepseek_brands]
        deepseek_only_brands = [b for b in deepseek_brands if b not in qwen_brands]
        all_detected_brands = list(dict.fromkeys(local_brands + qwen_brands + deepseek_brands))

        brand_source_tags = []
        if consensus_brands:
            brand_source_tags.append(f"双 AI 共同检出: {', '.join(consensus_brands)}")
        if deepseek_only_brands:
            brand_source_tags.append(f"DeepSeek 独家检出: {', '.join(deepseek_only_brands)} (千问未检出)")
        if qwen_only_brands:
            brand_source_tags.append(f"千问独家检出: {', '.join(qwen_only_brands)} (DeepSeek 未检出)")
        brand_source_summary = "；".join(brand_source_tags) if brand_source_tags else "双方均未检出受保护品牌"

        # 首图视觉检出归因分析 (Vision Attribution Breakdown)
        qwen_logos = list(dict.fromkeys(qwen_image.get("logo_names", [])))
        deepseek_logos = list(dict.fromkeys(deepseek_image.get("logo_names", [])))
        consensus_logos = [l for l in qwen_logos if l in deepseek_logos]
        qwen_only_logos = [l for l in qwen_logos if l not in deepseek_logos]
        deepseek_only_logos = [l for l in deepseek_logos if l not in qwen_logos]
        image_logos = list(dict.fromkeys(qwen_logos + deepseek_logos))

        qwen_prohibs = list(dict.fromkeys(qwen_image.get("prohibited_types", [])))
        deepseek_prohibs = list(dict.fromkeys(deepseek_image.get("prohibited_types", [])))
        image_prohibs = list(dict.fromkeys(qwen_prohibs + deepseek_prohibs))

        # 推荐合规标题
        recommended_title = (
            deepseek_title.get("recommended_title") or
            qwen_title.get("recommended_title") or
            local_rules.get("brand_info", {}).get("recommended_title")
        )

        brand_breakdown = {
            "has_brand": bool(all_detected_brands),
            "all_brands": all_detected_brands,
            "qwen_brands": qwen_brands,
            "deepseek_brands": deepseek_brands,
            "consensus_brands": consensus_brands,
            "qwen_only_brands": qwen_only_brands,
            "deepseek_only_brands": deepseek_only_brands,
            "source_summary": brand_source_summary,
            "is_accessory": local_rules.get("brand_info", {}).get("is_accessory", True),
            "recommended_title": recommended_title,
            "brand_nature": jev_brand_nature,
            "target_compatible_brand": jev_target_brand
        }

        vision_breakdown = {
            "tested": bool(qwen_image.get("tested") or deepseek_image.get("tested")),
            "qwen_logos": qwen_logos,
            "deepseek_logos": deepseek_logos,
            "consensus_logos": consensus_logos,
            "qwen_only_logos": qwen_only_logos,
            "deepseek_only_logos": deepseek_only_logos,
            "all_logos": image_logos,
            "qwen_prohibs": qwen_prohibs,
            "deepseek_prohibs": deepseek_prohibs,
            "all_prohibs": image_prohibs,
            "qwen_risk": qwen_img_level,
            "deepseek_risk": deepseek_img_level
        }

        # 轮次级判定对比
        round_1_match = (qwen_text_level == deepseek_text_level)
        round_2_match = (qwen_img_level == deepseek_img_level)

        round_1_status = {
            "match": round_1_match,
            "qwen_level": qwen_text_level,
            "deepseek_level": deepseek_text_level,
            "status": qwen_text_level if round_1_match else "DISPUTED",
            "summary": f"双方一致判定为 [{qwen_text_level}]" if round_1_match else f"分歧: 千问 [{qwen_text_level}] vs DeepSeek [{deepseek_text_level}]"
        }

        if has_dl_error:
            round_2_status = {
                "match": True,
                "download_error": True,
                "qwen_level": "RISK",
                "deepseek_level": "RISK",
                "status": "RISK",
                "summary": "⚠️ 首图下载失败 (未完成视觉排查，需人工复核)"
            }
        elif qwen_image.get("tested") and not deepseek_image.get("tested"):
            round_2_status = {
                "match": True,
                "qwen_level": qwen_img_level,
                "deepseek_level": deepseek_img_level,
                "status": qwen_img_level,
                "summary": f"通义千问视觉审查完成: [{qwen_img_level}] (DeepSeek 视觉未配置/未测试)"
            }
        elif deepseek_image.get("tested") and not qwen_image.get("tested"):
            round_2_status = {
                "match": True,
                "qwen_level": qwen_img_level,
                "deepseek_level": deepseek_img_level,
                "status": deepseek_img_level,
                "summary": f"DeepSeek 视觉审查完成: [{deepseek_img_level}] (千问视觉未配置/未测试)"
            }
        elif not qwen_image.get("tested") and not deepseek_image.get("tested"):
            round_2_status = {
                "match": True,
                "qwen_level": "SAFE",
                "deepseek_level": "SAFE",
                "status": "SAFE",
                "summary": "未执行首图视觉审查"
            }
        else:
            round_2_status = {
                "match": round_2_match,
                "qwen_level": qwen_img_level,
                "deepseek_level": deepseek_img_level,
                "status": qwen_img_level if round_2_match else "DISPUTED",
                "summary": f"双方一致判定为 [{qwen_img_level}]" if round_2_match else f"分歧: 千问 [{qwen_img_level}] vs DeepSeek [{deepseek_img_level}]"
            }

        prohibited_items = []
        risk_reasons = []
        suggestions = []

        if has_dl_error:
            dl_err_reason = "【首图下载异常】首图下载失败 (网络连接受限或重试超时)，未完成潜在图片商标与违禁特征排查，已标记为 RISK 待人工复核"
            risk_reasons.append(dl_err_reason)
            suggestions.append("人工核验首图: 请在跟卖前点击首图缩略图手动核实画面是否含有原卖家品牌Logo或侵权元素")

        if image_logos:
            risk_reasons.append(f"【首图 Logo 识别】画面检出商标/标志: [{', '.join(image_logos)}]")
        if image_prohibs:
            risk_reasons.append(f"【首图运输违禁】画面检出航空禁运形态: [{', '.join(image_prohibs)}]")

        if recommended_title and recommended_title not in suggestions:
            suggestions.append(f"AI 推荐合规修改标题: \"{recommended_title}\"")

        # 判定分歧机制与根因溯源 (Dispute Root Cause Analysis)
        has_dual = qwen_verdict["tested"] and deepseek_verdict["tested"]
        is_disputed = False
        reconciliation_summary = ""
        final_status = "SAFE"
        dispute_root_cause = None
        consensus_ratio = "1:0"

        if has_dual and jev_tested:
            # ★★★ 三 AI (Qwen + DeepSeek + Jev) 并发交叉仲裁 ★★★
            if qwen_overall == deepseek_overall == jev_level:
                final_status = qwen_overall
                consensus_ratio = "3:0"
                is_disputed = False
                reconciliation_summary = f"三 AI 并发交叉会审达成 3:0 全票共识：千问、DeepSeek 与 Jev 全票判定为 [{final_status}] (Jev 违规分: {jev_score:.2f})"
                for r in qwen_title.get("reasons", []) + deepseek_title.get("reasons", []):
                    if r and r not in risk_reasons:
                        risk_reasons.append(r)
            elif qwen_overall == deepseek_overall:
                consensus_ratio = "2:1"
                # 安全一票否决检查：若 LLM 判安全但 Jev 极高置信度判定 PROHIBITED 且违规分>=0.85
                if qwen_overall == "SAFE" and jev_level == "PROHIBITED" and jev_score >= 0.85:
                    final_status = "PROHIBITED"
                    is_disputed = False
                    reconciliation_summary = f"Jev 高确信检出高危违禁/假冒侵权 (违规分: {jev_score:.2f})，触发一票阻断安全防护"
                    risk_reasons.insert(0, f"Jev 独立决策引擎一票拦截：{jev_title.get('summary', '检出严重知识产权侵权或违禁品')}")
                else:
                    final_status = qwen_overall
                    is_disputed = False
                    reconciliation_summary = f"三 AI 交叉会审达成 2:1 多数决议：千问与 DeepSeek 共同判定为 [{final_status}] (Jev 独立参考: [{jev_level}], 违规分: {jev_score:.2f})"
                    for r in qwen_title.get("reasons", []) + deepseek_title.get("reasons", []):
                        if r and r not in risk_reasons:
                            risk_reasons.append(r)
            elif qwen_overall == jev_level:
                # Qwen 与 Jev 达成一致，Jev 作为独立裁判打破 DeepSeek 分歧平局！
                final_status = qwen_overall
                consensus_ratio = "2:1"
                is_disputed = False
                reconciliation_summary = f"三 AI 交叉会审达成 2:1 多数决议：通义千问与 Jev 共同裁定为 [{final_status}] (Jev 独立打破 DeepSeek 分歧平局，违规分: {jev_score:.2f})"
                for r in qwen_title.get("reasons", []):
                    if r and r not in risk_reasons:
                        risk_reasons.append(r)
            elif deepseek_overall == jev_level:
                # DeepSeek 与 Jev 达成一致，Jev 作为独立裁判打破 Qwen 分歧平局！
                final_status = deepseek_overall
                consensus_ratio = "2:1"
                is_disputed = False
                reconciliation_summary = f"三 AI 交叉会审达成 2:1 多数决议：DeepSeek 与 Jev 共同裁定为 [{final_status}] (Jev 独立打破千问分歧平局，违规分: {jev_score:.2f})"
                for r in deepseek_title.get("reasons", []):
                    if r and r not in risk_reasons:
                        risk_reasons.append(r)
            else:
                # 三方各不相同 (SAFE, RISK, PROHIBITED) -> 彻底分歧
                final_status = "DISPUTED"
                consensus_ratio = "DISPUTED"
                is_disputed = True
                title_msg = "【三 AI 产生多元认知分歧】"
                detail_msg = f"通义千问 [{qwen_overall}] vs DeepSeek [{deepseek_overall}] vs Jev [{jev_level}] (违规分: {jev_score:.2f})。"
                rec_msg = "三家模型意见完全分散，请人工综合审查标题及首图，点击下方对应按钮进行终审裁决。"
                dispute_root_cause = {
                    "is_disputed": True,
                    "stage": "THREE_AI_SPLIT",
                    "title": title_msg,
                    "detail": detail_msg,
                    "recommendation": rec_msg,
                    "qwen_overall": qwen_overall,
                    "deepseek_overall": deepseek_overall,
                    "jev_overall": jev_level,
                    "jev_score": jev_score
                }
                reconciliation_summary = f"{title_msg} {detail_msg}"
                risk_reasons.insert(0, reconciliation_summary)
                suggestions.insert(0, f"分歧待仲裁：{rec_msg}")
        elif has_dual:
            if qwen_overall == deepseek_overall:
                final_status = qwen_overall
                is_disputed = False
                consensus_ratio = "2:0"
                reconciliation_summary = f"双 AI 交叉会审达成一致：千问与 DeepSeek 共同判定为 [{final_status}]"
                for r in qwen_title.get("reasons", []) + deepseek_title.get("reasons", []):
                    if r and r not in risk_reasons:
                        risk_reasons.append(r)
            else:
                final_status = "DISPUTED"
                is_disputed = True
                consensus_ratio = "1:1"
                
                # 精确归因：第一轮分歧、第二轮分歧、或双轮均分歧
                if round_1_match and not round_2_match:
                    stage = "ROUND_2_VISION"
                    title = "【分歧焦点：第二轮首图视觉审查】"
                    detail = (
                        f"第一轮标题文本双方均判定一致为 [{qwen_text_level}]（未发生分歧）；"
                        f"分歧源于第二轮首图视觉：通义千问判定为 [{qwen_img_level}]（{qwen_image.get('summary', '无品牌Logo/合规')}），"
                        f"而 DeepSeek 判定为 [{deepseek_img_level}]（{deepseek_image.get('summary', '检出风险标识')}）。"
                    )
                    rec = "首图视觉认知分歧：若画面标识仅为普通产品型号参数（如HW300PRO等非知名注册商标），建议人工裁定为【SAFE 安全合规】；若画面确含商标侵权或盗图，请裁定为【RISK】。"
                elif not round_1_match and round_2_match:
                    stage = "ROUND_1_TEXT"
                    title = "【分歧焦点：第一轮标题与品牌文本风控】"
                    detail = (
                        f"第二轮首图视觉双方均判定一致为 [{qwen_img_level}]（未发生分歧）；"
                        f"分歧源于第一轮标题文本：通义千问判定为 [{qwen_text_level}]（{qwen_title.get('summary', '合规')}），"
                        f"而 DeepSeek 判定为 [{deepseek_text_level}]（{deepseek_title.get('summary', '检出风险')}）。"
                        f"（品牌来源: {brand_source_summary}）"
                    )
                    rec = "标题文本认知分歧：若检出品牌属于正常第三方配件兼容范畴或描述误报，可根据实际情况人工裁定为【SAFE】或采纳合规标题；若涉嫌商标侵权，请裁定为【RISK】。"
                else:
                    stage = "BOTH_ROUNDS"
                    title = "【分歧焦点：第一轮文本与第二轮视觉均存在分歧】"
                    detail = (
                        f"第一轮标题文本千问 [{qwen_text_level}] vs DeepSeek [{deepseek_text_level}]；"
                        f"第二轮首图视觉千问 [{qwen_img_level}] vs DeepSeek [{deepseek_img_level}]。"
                    )
                    rec = "双轮审查均产生分歧，请人工综合审查标题及首图，点击下方对应按钮进行终审裁决。"

                dispute_root_cause = {
                    "is_disputed": True,
                    "stage": stage,
                    "title": title,
                    "detail": detail,
                    "recommendation": rec,
                    "qwen_overall": qwen_overall,
                    "deepseek_overall": deepseek_overall,
                    "round_1_qwen": qwen_text_level,
                    "round_1_deepseek": deepseek_text_level,
                    "round_2_qwen": qwen_img_level,
                    "round_2_deepseek": deepseek_img_level,
                    "brand_source_summary": brand_source_summary
                }
                reconciliation_summary = f"{title} {detail}"
                risk_reasons.insert(0, reconciliation_summary)
                suggestions.insert(0, f"分歧待仲裁：{rec}")
        elif qwen_verdict["tested"]:
            final_status = qwen_overall
            is_disputed = False
            reconciliation_summary = "通义千问单模型审查完成 (DeepSeek 未配置 API Key，已自动平滑降级为单模型)"
            for r in qwen_title.get("reasons", []):
                if r and r not in risk_reasons:
                    risk_reasons.append(r)
        elif deepseek_verdict["tested"]:
            final_status = deepseek_overall
            is_disputed = False
            reconciliation_summary = "DeepSeek 单模型审查完成 (通义千问未配置 API Key)"
            for r in deepseek_title.get("reasons", []):
                if r and r not in risk_reasons:
                    risk_reasons.append(r)
        elif jev_tested:
            final_status = jev_level
            is_disputed = False
            reconciliation_summary = f"Jev 独立快决策引擎审查完成 (判定为 [{final_status}], 违规分: {jev_score:.2f})"
        else:
            final_status = "SAFE"
            reconciliation_summary = "未启用/未配置大模型客户端"

        # ★★★ 核心风控升级：图审带 Logo 品牌的一律判定为侵权违规 (PROHIBITED) 不论白牌还是名牌 ★★★
        has_any_image_logo = bool(
            image_logos or
            qwen_image.get("has_brand_logo") or
            deepseek_image.get("has_brand_logo") or
            (qwen_image.get("logo_names") and len(qwen_image.get("logo_names")) > 0) or
            (deepseek_image.get("logo_names") and len(deepseek_image.get("logo_names")) > 0)
        )
        if has_any_image_logo:
            final_status = "PROHIBITED"
            is_disputed = False
            detected_logo_names = ", ".join(image_logos) if image_logos else "产品图附带品牌Logo/独占标识"
            logo_reason = f"【图审红线违规一票拦截】首图检出品牌Logo/专有标志 [{detected_logo_names}]（依据严格风控准则：首图含任何Logo不论白牌或名牌均判定为侵权，禁止跟品挂靠）"
            if logo_reason not in risk_reasons:
                risk_reasons.insert(0, logo_reason)
            if f"首图附带品牌Logo/独占标识 ({detected_logo_names})" not in prohibited_items:
                prohibited_items.append(f"首图附带品牌Logo/独占标识 ({detected_logo_names})")
            reconciliation_summary = f"首图检出品牌Logo [{detected_logo_names}]，触发全品牌零容忍一票拦截"

        # AI 违禁品特征归集 (仅当 AI 判定为 PROHIBITED 时提取 AI 识别出的具体违禁特征，彻底消除死板正则误杀)
        if final_status == "PROHIBITED":
            if image_prohibs:
                for p in image_prohibs:
                    prohibited_items.append(f"首图禁运形态 ({p})")
            for t_res in [qwen_title, deepseek_title]:
                v_type = t_res.get("violation_type", "")
                r_lvl = t_res.get("risk_level", "SAFE")
                if r_lvl == "PROHIBITED":
                    if v_type == "PROHIBITED_TECH" and "受限无线通讯技术 (蓝牙/WiFi/红外)" not in prohibited_items:
                        prohibited_items.append("受限无线通讯技术 (蓝牙/WiFi/红外)")
                    elif v_type == "PROHIBITED_LIQUID" and "跨境航空禁运液体/化学品" not in prohibited_items:
                        prohibited_items.append("跨境航空禁运液体/化学品")
                    elif v_type == "COPYRIGHT_IP" and "影视/动漫IP严重侵权" not in prohibited_items:
                        prohibited_items.append("影视/动漫IP严重侵权")
                    elif v_type == "TRADEMARK" and "知名品牌假冒侵权" not in prohibited_items:
                        prohibited_items.append("知名品牌假冒侵权")
            if not prohibited_items:
                prohibited_items.append("AI判定严重违规/禁售品")
        else:
            prohibited_items = []

        # Makro 跟品白牌识别与运营建议
        brand_clean = (target_brand_name or "").strip()
        brand_lower = brand_clean.lower()
        is_famous_brand = any(b == brand_lower or b in brand_lower for b in FAMOUS_BRANDS)
        is_white_label = is_piggyback and (not is_famous_brand) and bool(brand_lower and brand_lower not in ["beishi", "generic", "oem", "none", "n/a"])

        white_label_notice = None
        if is_piggyback and is_white_label:
            white_label_notice = f"Listing 前缀品牌「{brand_clean}」属于卖家自造白牌，依据 Makro 平台规则完全支持跟品挂靠。"
            if final_status in ["SAFE", "RISK"]:
                advice = f"💡 Makro 跟品运营防守建议: 前缀品牌「{brand_clean}」属于卖家自造白牌，技术与平台规则允许合规跟品。发货时请务必使用纯中性外包装，切勿印制原卖家私有品牌标志。"
                if advice not in suggestions:
                    suggestions.append(advice)

        brand_info = local_rules.get("brand_info", {})
        brand_info["detected_brands"] = all_detected_brands
        brand_info["is_white_label"] = is_white_label
        brand_info["is_famous_brand"] = is_famous_brand
        if recommended_title:
            brand_info["recommended_title"] = recommended_title

        # 首图下载失败安全兜底：绝不能默认 SAFE！
        if has_dl_error:
            final_status = get_higher_risk(final_status, "RISK")
            if final_status == "RISK" and not is_disputed:
                reconciliation_summary = "文本审查正常，但首图下载失败未完成视觉排查，安全兜底判定为 [RISK] (需人工核实首图)"

        # 首图聚合卡片
        merged_image_inspection = {
            "tested": bool(qwen_image.get("tested") or deepseek_image.get("tested")),
            "download_error": has_dl_error,
            "image_url": first_img_url,
            "has_brand_logo": bool(qwen_image.get("has_brand_logo") or deepseek_image.get("has_brand_logo")),
            "is_prohibited": bool(qwen_image.get("is_transport_prohibited") or deepseek_image.get("is_transport_prohibited")),
            "logo_names": image_logos,
            "prohibited_types": image_prohibs,
            "summary": "首图下载失败 (重试超时)，未完成视觉排查" if has_dl_error else f"千问: {qwen_image.get('summary', '无')} | DeepSeek: {deepseek_image.get('summary', '无')}"
        }

        return {
            "compliance_status": final_status,
            "is_disputed": is_disputed,
            "dual_ai_mode": has_dual,
            "three_ai_mode": bool(has_dual and jev_tested),
            "consensus_ratio": consensus_ratio,
            "brand_nature": jev_brand_nature,
            "target_compatible_brand": jev_target_brand,
            "qwen_verdict": qwen_verdict,
            "deepseek_verdict": deepseek_verdict,
            "jev_verdict": jev_verdict,
            "reconciliation_summary": reconciliation_summary,
            "dispute_root_cause": dispute_root_cause,
            "brand_breakdown": brand_breakdown,
            "vision_breakdown": vision_breakdown,
            "round_1_status": round_1_status,
            "round_2_status": round_2_status,
            "prohibited_items": list(set(prohibited_items)),
            "brand_info": brand_info,
            "image_inspection": merged_image_inspection,
            "risk_reasons": list(dict.fromkeys(risk_reasons)),
            "suggestions": list(dict.fromkeys(suggestions)),
            "is_piggyback": is_piggyback,
            "is_white_label": is_white_label,
            "white_label_notice": white_label_notice,
            "original_seller": original_seller
        }

    # =========================================================================
    # 辅助品牌词与配件特征提取器 (已废弃底层死板硬拦截，100% 由双 AI 语境化裁定合规与违禁)
    # =========================================================================
    def _check_local_rules(
        self,
        title: str,
        makro_title: Optional[str],
        full_text: str,
        category: str,
        target_brand_name: str,
        is_piggyback: bool = False,
        original_seller: str = ""
    ) -> Dict[str, Any]:
        """
        辅助品牌词与配件特征提取器 (已彻底废弃底层死板正则硬拦截，100% 由双 AI 语境化裁定合规与违禁)
        仅提取大牌候选词供 AI 提示词与前台参考，不再通过本地硬规则拦截或判定 PROHIBITED。
        """
        eval_title = (makro_title or title).strip()
        eval_title_lower = eval_title.lower()

        title_detected_brands = [b.title() for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', eval_title_lower)]
        title_detected_brands = list(dict.fromkeys(title_detected_brands))

        all_detected_brands = [b.title() for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', full_text)]
        all_detected_brands = list(dict.fromkeys(all_detected_brands))

        is_accessory = any(re.search(rf'\b{acc}\b', full_text, re.IGNORECASE) for acc in ACCESSORY_KEYWORDS)

        brand_clean = (target_brand_name or "").strip()
        brand_lower = brand_clean.lower()
        is_famous_brand = any(b == brand_lower or b in brand_lower for b in FAMOUS_BRANDS)
        is_white_label = is_piggyback and (not is_famous_brand) and bool(brand_lower and brand_lower not in ["beishi", "generic", "oem", "none", "n/a"])

        recommended_title = None
        if title_detected_brands:
            from .ai_cleaner_service import reconstruct_accessory_title
            recommended_title = reconstruct_accessory_title(
                makro_title=eval_title,
                raw_title=title,
                target_brand=target_brand_name,
                nature="COMPATIBLE_ACCESSORY",
                vertical=category
            )

        return {
            "compliance_status": "SAFE",
            "prohibited_items": [],
            "detected_brands": all_detected_brands,
            "is_white_label": is_white_label,
            "is_famous_brand": is_famous_brand,
            "risk_reasons": [],
            "suggestions": [],
            "brand_info": {
                "detected_brands": all_detected_brands,
                "title_detected_brands": title_detected_brands,
                "is_accessory": is_accessory,
                "recommended_title": recommended_title,
                "is_white_label": is_white_label,
                "is_famous_brand": is_famous_brand
            }
        }
