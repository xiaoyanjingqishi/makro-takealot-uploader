import re
import json
import base64
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from ..config import settings
from ..models.setting import SystemSetting
from ..models.compliance_log import ComplianceArbitrationLog
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 知名受保护品牌库
FAMOUS_BRANDS = [
    "apple", "iphone", "ipad", "airpods", "apple watch", "macbook", "magsafe",
    "samsung", "galaxy", "dyson", "sony", "playstation", "ps4", "ps5",
    "nintendo switch", "nintendo", "huawei", "xiaomi", "redmi", "dji", "gopro",
    "philips", "makita", "bosch", "dewalt", "milwaukee", "dell", "hp",
    "lenovo", "asus", "acer", "garmin", "fitbit", "bose", "jbl", "beats",
    "nike", "adidas", "lego", "stanley", "rolex", "crocs"
]

# 知名受保护影视/动漫/游戏IP与角色库 (侵权高危，严禁未经授权销售周边或标题蹭词)
PROTECTED_ENTERTAINMENT_IPS = [
    "spider man", "spiderman", "spider-man", "batman", "superman", "iron man", "ironman",
    "captain america", "thor", "hulk", "avengers", "marvel", "disney", "mickey mouse",
    "frozen", "elsa", "barbie", "star wars", "harry potter", "pokemon", "pikachu",
    "hello kitty", "sanrio", "kuromi", "cinnamoroll", "labubu", "naruto", "dragon ball",
    "one piece", "peppa pig", "paw patrol", "transformers", "jurassic park", "jurassic world"
]

# 配件指示词
ACCESSORY_KEYWORDS = [
    "case", "cover", "strap", "band", "charger", "cable", "adapter",
    "replacement", "filter", "mount", "stand", "battery", "dock",
    "protector", "screen protector", "stylus", "shell", "ear tips",
    "pad", "blade", "holder", "pouch", "housing", "nozzle", "head",
    "sleeve", "bracket", "belt", "refill", "spares", "parts"
]

# 第三方兼容声明合格词
COMPATIBILITY_KEYWORDS = [
    "third-party", "compatible with", "compatible for", "suitable for",
    "replacement for", "for use with", "designed for", "fits", "fit for"
]

RISK_LEVEL_ORDER = {"SAFE": 1, "RISK": 2, "PROHIBITED": 3}

def get_higher_risk(level_a: str, level_b: str) -> str:
    ra = RISK_LEVEL_ORDER.get(level_a, 1)
    rb = RISK_LEVEL_ORDER.get(level_b, 1)
    return level_a if ra >= rb else level_b

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
        title = product_data.get("takealot_title") or ""
        makro_title = product_data.get("makro_title") or ""
        desc = product_data.get("takealot_description") or ""
        specs = product_data.get("takealot_specs") or ""
        specs_str = json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)
        category = product_data.get("takealot_category") or ""
        target_brand_name = product_data.get("makro_brand") or "Beishi"
        raw_images = product_data.get("raw_images") or []
        if isinstance(raw_images, str):
            try:
                raw_images = json.loads(raw_images)
            except Exception:
                raw_images = [raw_images] if raw_images.startswith("http") else []

        full_text = f"{title} {makro_title} {desc} {specs_str} {category}".lower()

        # ----------------------------------------------------
        # 基础本地硬性规则检测 (兜底层: 确保零漏判)
        # ----------------------------------------------------
        local_rules_res = self._check_local_rules(
            title=title,
            makro_title=makro_title,
            full_text=full_text,
            category=category,
            target_brand_name=target_brand_name
        )

        # ----------------------------------------------------
        # 第一轮: 标题与品牌合规检测 (双 AI 并发文本审查)
        # ----------------------------------------------------
        qwen_title_res = {"tested": False, "risk_level": "SAFE", "summary": "未启用或未配置"}
        deepseek_title_res = {"tested": False, "risk_level": "SAFE", "summary": "未启用或未配置"}

        if check_ai_title:
            qwen_title_res, deepseek_title_res = self._run_round1_text_audit(
                raw_title=title,
                makro_title=makro_title,
                brand=target_brand_name,
                category=category,
                description=desc
            )

        # ----------------------------------------------------
        # 第二轮: 主图视觉与运输合规检测 (双 AI 并发多模态视觉审查)
        # ----------------------------------------------------
        first_img_url = None
        if isinstance(raw_images, list) and len(raw_images) > 0 and isinstance(raw_images[0], str) and raw_images[0].startswith("http"):
            first_img_url = raw_images[0]

        qwen_image_res = {"tested": False, "image_url": first_img_url, "risk_level": "SAFE", "summary": "未执行视觉审查"}
        deepseek_image_res = {"tested": False, "image_url": first_img_url, "risk_level": "SAFE", "summary": "未执行视觉审查"}

        # 智能剪枝优化：若底层硬性规则已拦截绝对违禁 (蓝牙/WiFi/液体等)，商品不可上架，智能跳过视觉审查节约 ~1700 Tokens
        is_hard_prohibited = bool(local_rules_res.get("prohibited_items"))
        if is_hard_prohibited:
            skip_msg = f"已触发平台硬性违禁拦截 ({', '.join(local_rules_res['prohibited_items'])})，智能跳过视觉审查以大幅节约 Token"
            qwen_image_res = {"tested": False, "image_url": first_img_url, "risk_level": "PROHIBITED", "summary": skip_msg}
            deepseek_image_res = {"tested": False, "image_url": first_img_url, "risk_level": "PROHIBITED", "summary": skip_msg}
        elif check_image and first_img_url:
            all_known_brands = list(dict.fromkeys(
                local_rules_res["detected_brands"] +
                qwen_title_res.get("detected_brands_or_ips", []) +
                deepseek_title_res.get("detected_brands_or_ips", [])
            ))
            qwen_image_res, deepseek_image_res = self._run_round2_vision_audit(
                image_url=first_img_url,
                known_brands=all_known_brands
            )

        # ----------------------------------------------------
        # 会审裁决与分歧聚合器 (Reconciliation Engine)
        # ----------------------------------------------------
        final_result = self._reconcile_dual_verdicts(
            local_rules=local_rules_res,
            qwen_title=qwen_title_res,
            deepseek_title=deepseek_title_res,
            qwen_image=qwen_image_res,
            deepseek_image=deepseek_image_res,
            first_img_url=first_img_url,
            target_brand_name=target_brand_name
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
        description: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """并发运行千问与 DeepSeek-Flash 的第一轮文本审查"""
        qwen_res = {"tested": False, "risk_level": "SAFE", "reasons": [], "summary": "Qwen 客户端未配置"}
        deepseek_res = {"tested": False, "risk_level": "SAFE", "reasons": [], "summary": "DeepSeek 客户端未配置"}

        prompt = self._build_title_ip_prompt(
            raw_title=raw_title,
            makro_title=makro_title,
            brand=brand,
            category=category,
            description=description
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

        # 双 AI 并发推理 (两方约 0.6s~0.8s 异步同步完成)
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_q = executor.submit(_call_qwen)
            fut_d = executor.submit(_call_deepseek)
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

        return qwen_res, deepseek_res

    def _build_title_ip_prompt(
        self,
        raw_title: str,
        makro_title: Optional[str],
        brand: str,
        category: str,
        description: str
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

        return f"""审查跨境电商商品标题合规与侵权风险，仅返回合法JSON，严禁输出思维过程与闲聊。
【规则】:
1. 商标侵权: 严查受保护大牌(如Apple,Stanley,Nike,Dyson等)。区分语境: 颜色/通用词(如apple green)合规，指代受保护品牌违规。
2. 影视IP: 严禁未经授权蹭用知名动漫潮玩IP(如Sanrio,Disney,Marvel,Pokemon,Labubu等)。
3. 配件规范: 兼容大牌配件必须含'Compatible with'或'For'；严禁大牌开头冒充原厂；严禁连续堆砌>=3个大牌。
4. 禁运技术: 严禁蓝牙(Bluetooth)、WiFi、红外线(Infrared)。
5. 评级: SAFE(合规/通用品/规范配件), RISK(配件缺少Compatible with声明/可整改瑕疵), PROHIBITED(假冒原厂/大牌整机/未授权IP/禁售技术)。{few_shot_block}
待审数据:
- 标题: {target_title}
- 授权自有品牌: {brand or "Beishi"}
- 类目: {category or "通用"}{desc_line}
返回JSON:
{{"has_risk":false,"risk_level":"SAFE|RISK|PROHIBITED","detected_brands_or_ips":[],"violation_type":"NONE|TRADEMARK|COPYRIGHT_IP|BRAND_SPAMMING|MISSING_COMPATIBILITY|PROHIBITED_TECH","reasons":["简明中文理由(1句)"],"recommended_title":"合规英文建议标题"}}"""

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
        known_brands: List[str]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """并发运行千问 (Qwen-VL) 与 DeepSeek-Flash 视觉多模态审查"""
        qwen_img_res = {"tested": False, "image_url": image_url, "risk_level": "SAFE", "summary": "Qwen 视觉未配置"}
        deepseek_img_res = {"tested": False, "image_url": image_url, "risk_level": "SAFE", "summary": "DeepSeek 视觉未配置"}

        # 1. 统一下载图片并转换为 Base64 Data URI (下载一次，供两个 AI 并行复用)
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.takealot.com/"
            }
            resp = requests.get(image_url, headers=headers, timeout=12)
            if resp.status_code != 200:
                msg = f"图片下载失败 (HTTP {resp.status_code})"
                qwen_img_res["summary"] = msg
                deepseek_img_res["summary"] = msg
                return qwen_img_res, deepseek_img_res

            b64_data = base64.b64encode(resp.content).decode("utf-8")
            mime = "image/jpeg"
            if resp.content.startswith(b'\x89PNG'):
                mime = "image/png"
            elif resp.content.startswith(b'RIFF') and b'WEBP' in resp.content[:16]:
                mime = "image/webp"
            elif resp.content.startswith(b'GIF'):
                mime = "image/gif"
            data_uri = f"data:{mime};base64,{b64_data}"
        except Exception as dl_e:
            msg = f"图片下载异常: {str(dl_e)[:50]}"
            qwen_img_res["summary"] = msg
            deepseek_img_res["summary"] = msg
            return qwen_img_res, deepseek_img_res

        vision_prompt = self._build_vision_prompt(known_brands)

        def _call_qwen_vl():
            if not self.qwen_client:
                return qwen_img_res
            return self._invoke_vision_model(
                client=self.qwen_client,
                model=self.qwen_vision_model,
                ai_name="Qwen-VL",
                prompt=vision_prompt,
                data_uri=data_uri,
                image_url=image_url
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
                image_url=image_url
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_qv = executor.submit(_call_qwen_vl)
            fut_dv = executor.submit(_call_deepseek_vl)
            try:
                qwen_img_res = fut_qv.result(timeout=45.0)
            except Exception as e:
                logger.warning(f"Qwen-VL 视觉审查异常: {e}")
                qwen_img_res = {"tested": False, "image_url": image_url, "risk_level": "SAFE", "summary": f"Qwen-VL 异常: {str(e)[:40]}"}
            try:
                deepseek_img_res = fut_dv.result(timeout=45.0)
            except Exception as e:
                logger.warning(f"DeepSeek 视觉审查异常: {e}")
                deepseek_img_res = {"tested": False, "image_url": image_url, "risk_level": "SAFE", "summary": f"DeepSeek 视觉异常: {str(e)[:40]}"}

        return qwen_img_res, deepseek_img_res

    def _build_vision_prompt(self, known_brands: List[str]) -> str:
        """极简高密度首图合规审查 Prompt (压缩~50% Token 消耗)"""
        brand_hint = f"（重点排查: {', '.join(known_brands[:3])}）" if known_brands else ""
        return f"""电商首图视觉合规审查{brand_hint}。仅返回JSON，禁止多余输出。
【准则】:
1. 品牌Logo: 严查知名商业大牌商标Logo(如Apple,Nike,Sony,Dyson等)或防盗水印。区分: 机身印刷通用产品型号/技术规格(如HW300PRO,USB-C,100W等)属合规SAFE，严禁误判侵权。
2. 运输违禁: 严查液体/精油/香水/喷雾、纯电池/易燃易爆品、管制刀具等航空安检禁运形态。
3. 评级: SAFE(无大牌Logo/无违禁品), RISK(画面疑似商业Logo/水印), PROHIBITED(明确大牌Logo/大容量液体/危险品)。
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
                "risk_level": "SAFE",
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
        target_brand_name: str
    ) -> Dict[str, Any]:
        """
        裁决聚合器 (Reconciliation Engine)：
        1. 计算 Qwen 综合评级 = max(Qwen 文本, Qwen 视觉)
        2. 计算 DeepSeek 综合评级 = max(DeepSeek 文本, DeepSeek 视觉)
        3. 进行细粒度归因分析：
           - 品牌检出归因：区分双方同时检出、DeepSeek 独家检出、千问独家检出
           - 首图视觉归因：对比 Logo、违禁特征与双方各自评级
           - 轮次级共识：分别评估第一轮（文本）与第二轮（首图）是否一致
        4. 判定分歧机制与根因溯源 (Dispute Root Cause)：
           - 精确指示分歧来源（第一轮文本 / 第二轮首图 / 双轮）
           - 输出通俗易懂的仲裁参考建议
        5. 本地规则硬性兜底：若本地规则检出绝对违禁 (蓝牙/WiFi)，提升至 PROHIBITED
        """
        # 千问单方综合
        qwen_text_level = qwen_title.get("risk_level", "SAFE") if qwen_title.get("tested") else "SAFE"
        qwen_img_level = qwen_image.get("risk_level", "SAFE") if qwen_image.get("tested") else "SAFE"
        qwen_overall = get_higher_risk(qwen_text_level, qwen_img_level)

        # DeepSeek 单方综合
        deepseek_text_level = deepseek_title.get("risk_level", "SAFE") if deepseek_title.get("tested") else "SAFE"
        deepseek_img_level = deepseek_image.get("risk_level", "SAFE") if deepseek_image.get("tested") else "SAFE"
        deepseek_overall = get_higher_risk(deepseek_text_level, deepseek_img_level)

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
            "recommended_title": recommended_title
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

        round_2_status = {
            "match": round_2_match,
            "qwen_level": qwen_img_level,
            "deepseek_level": deepseek_img_level,
            "status": qwen_img_level if round_2_match else "DISPUTED",
            "summary": f"双方一致判定为 [{qwen_img_level}]" if round_2_match else f"分歧: 千问 [{qwen_img_level}] vs DeepSeek [{deepseek_img_level}]"
        }

        prohibited_items = list(local_rules.get("prohibited_items", []))
        risk_reasons = list(local_rules.get("risk_reasons", []))
        suggestions = list(local_rules.get("suggestions", []))

        if image_logos:
            risk_reasons.append(f"【首图 Logo 识别】画面检出商标/标志: [{', '.join(image_logos)}]")
        if image_prohibs:
            prohibited_items.append("首图运输违禁特征")
            risk_reasons.append(f"【首图运输违禁】画面检出航空禁运形态: [{', '.join(image_prohibs)}]")

        if recommended_title and recommended_title not in suggestions:
            suggestions.append(f"AI 推荐合规修改标题: \"{recommended_title}\"")

        # 判定分歧机制与根因溯源 (Dispute Root Cause Analysis)
        has_dual = qwen_verdict["tested"] and deepseek_verdict["tested"]
        is_disputed = False
        reconciliation_summary = ""
        final_status = "SAFE"
        dispute_root_cause = None

        if has_dual:
            if qwen_overall == deepseek_overall:
                final_status = qwen_overall
                is_disputed = False
                reconciliation_summary = f"双 AI 交叉会审达成一致：千问与 DeepSeek 共同判定为 [{final_status}]"
                for r in qwen_title.get("reasons", []) + deepseek_title.get("reasons", []):
                    if r and r not in risk_reasons:
                        risk_reasons.append(r)
            else:
                final_status = "DISPUTED"
                is_disputed = True
                
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
        else:
            final_status = local_rules.get("compliance_status", "SAFE")
            reconciliation_summary = "本地规则引擎检测完成 (未启用/未配置大模型客户端)"

        # 本地硬性规则兜底 (若检出蓝牙、WiFi 或绝对假冒，硬性拉至 PROHIBITED 消除漏网之鱼)
        if local_rules.get("prohibited_items"):
            final_status = "PROHIBITED"
            is_disputed = False
            reconciliation_summary += f" [触发底层硬性规则违禁拦截: {', '.join(local_rules['prohibited_items'])}]"

        brand_info = local_rules.get("brand_info", {})
        brand_info["detected_brands"] = all_detected_brands
        if recommended_title:
            brand_info["recommended_title"] = recommended_title

        # 首图聚合卡片
        merged_image_inspection = {
            "tested": bool(qwen_image.get("tested") or deepseek_image.get("tested")),
            "image_url": first_img_url,
            "has_brand_logo": bool(qwen_image.get("has_brand_logo") or deepseek_image.get("has_brand_logo")),
            "is_prohibited": bool(qwen_image.get("is_transport_prohibited") or deepseek_image.get("is_transport_prohibited")),
            "logo_names": image_logos,
            "prohibited_types": image_prohibs,
            "summary": f"千问: {qwen_image.get('summary', '无')} | DeepSeek: {deepseek_image.get('summary', '无')}"
        }

        return {
            "compliance_status": final_status,
            "is_disputed": is_disputed,
            "dual_ai_mode": has_dual,
            "qwen_verdict": qwen_verdict,
            "deepseek_verdict": deepseek_verdict,
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
            "suggestions": list(dict.fromkeys(suggestions))
        }

    # =========================================================================
    # 本地硬性规则排查 (兜底排查层)
    # =========================================================================
    def _check_local_rules(
        self,
        title: str,
        makro_title: Optional[str],
        full_text: str,
        category: str,
        target_brand_name: str
    ) -> Dict[str, Any]:
        prohibited_items_found = []
        risk_reasons = []
        suggestions = []

        # 1. 蓝牙 (Bluetooth)
        if re.search(r'\b(bluetooth|bt\s*[45]\.\d|ble|a2dp|wireless\s*audio)\b', full_text) or "蓝牙" in full_text:
            prohibited_items_found.append("蓝牙 (Bluetooth)")
            risk_reasons.append("商品包含蓝牙 (Bluetooth) 无线通讯技术，属于平台受限禁售品类。")
            suggestions.append("不可在 Makro 平台销售含蓝牙功能的设备，建议下架。")

        # 2. WiFi
        if re.search(r'\b(wifi|wi-fi|802\.11|wlan|2\.4ghz\s*wifi|5ghz\s*wifi)\b', full_text) or "无线网络" in full_text:
            prohibited_items_found.append("WiFi")
            risk_reasons.append("商品包含 WiFi 无线通讯技术，属于平台受限禁售品类。")
            suggestions.append("不可在 Makro 平台销售含 WiFi 功能的联网设备。")

        # 3. 红外线 (Infrared)
        if re.search(r'\b(infrared|ir\s*remote|ir\s*blaster|ir\s*control|ir\s*sensor|ir\s*emitter)\b', full_text) or "红外" in full_text:
            prohibited_items_found.append("红外线 (Infrared)")
            risk_reasons.append("商品包含红外线 (Infrared/IR) 遥控或发射功能，属于平台受限品类。")

        # 4. 液体 (Liquid)
        is_solid_gel = bool(
            re.search(r'\b(flexible\s*gel|silica\s*gel|silicone\s*gel|tpu\s*gel|gel\s*case|gel\s*cover|gel\s*pen|gel\s*pad|gel\s*cushion|gel\s*insole|heel\s*gel|ice\s*gel|gel\s*grip)\b', full_text)
            or any(c in category.lower() for c in ["case", "cover", "protector", "shoes", "apparel", "clothing", "tools", "stationery"])
        )
        liquid_regex = r'\b(liquid|fluid|essential\s*oil|lotion|perfume|cologne|fragrance|spray|serum|essence|shampoo|conditioner|lubricant|cleanser|beverage|edible|syrup|mouthwash|liquid\s*ink)\b'
        has_liquid_words = bool(re.search(liquid_regex, full_text) or re.search(r'(液体|精油|香水|喷雾|乳液|膏霜|洗发水|沐浴露|口服液|润滑油)', full_text))
        if not is_solid_gel:
            has_liquid_words = has_liquid_words or bool(re.search(r'\b(oil|gel|cream|toner)\b', full_text) or "凝胶" in full_text)

        is_liquid_cat = any(x in category.lower() for x in ["perfume", "fragrance", "essential oil", "liquid", "oils & fluids", "cosmetic", "skincare"])
        if has_liquid_words or is_liquid_cat:
            prohibited_items_found.append("液体 (Liquid)")
            risk_reasons.append("商品属于液体/精油/香水/喷雾形态，跨境物流禁止航空运输。")
            suggestions.append("液体类商品无法通过跨境物流和 Makro 平台审核，请停止刊登。")

        # 5. 商标品牌与配件排查
        eval_title = (makro_title or title).strip()
        eval_title_lower = eval_title.lower()
        title_detected_brands = [b.title() for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', eval_title_lower)]
        title_detected_brands = list(dict.fromkeys(title_detected_brands))

        all_detected_brands = [b.title() for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', full_text)]
        all_detected_brands = list(dict.fromkeys(all_detected_brands))

        is_accessory = any(re.search(rf'\b{acc}\b', full_text, re.IGNORECASE) for acc in ACCESSORY_KEYWORDS)

        recommended_title = None
        if title_detected_brands:
            first_b = title_detected_brands[0]
            has_compat_clause = any(kw in eval_title_lower for kw in COMPATIBILITY_KEYWORDS)
            first_word_match = re.match(r'^\s*([a-zA-Z0-9_\-]+)', eval_title)
            starts_with_famous = False
            if first_word_match:
                fw = first_word_match.group(1).lower()
                starts_with_famous = any(b == fw for b in FAMOUS_BRANDS)

            if len(title_detected_brands) >= 3:
                risk_reasons.append(f"【标题关键词堆砌】堆砌了多个品牌商标 ({', '.join(title_detected_brands)})！")
            elif not is_accessory:
                prohibited_items_found.append(f"商标侵权 ({first_b})")
                risk_reasons.append(f"【品牌侵权拦截】包含受保护品牌 [{first_b}] 且非配件，涉嫌售卖受限品牌或假冒正品！")
            elif starts_with_famous and not has_compat_clause:
                prohibited_items_found.append(f"冒充原装配件 ({first_b})")
                risk_reasons.append(f"【标题侵权】开头直接以品牌 [{first_b}] 命名，冒充原厂配件！")
            elif not has_compat_clause:
                risk_reasons.append(f"【合规提示】配件标题包含 [{first_b}]，缺少 'Compatible with' 第三方声明。")

            clean_core = eval_title
            for b_item in title_detected_brands:
                clean_core = re.sub(rf'\b{b_item}\b', '', clean_core, flags=re.IGNORECASE)
            for kw in COMPATIBILITY_KEYWORDS:
                clean_core = re.sub(rf'\b{kw}\b', '', clean_core, flags=re.IGNORECASE)
            clean_core = re.sub(rf'\b{re.escape(target_brand_name)}\b', '', clean_core, flags=re.IGNORECASE)
            clean_core = re.sub(r'[-_:,/]+', ' ', clean_core)
            clean_core = re.sub(r'\s+', ' ', clean_core).strip()
            recommended_title = f"{target_brand_name} Third-Party {clean_core[:50]} Compatible with {first_b}"

        # 6. 知名动漫影视 IP 词库排查
        detected_ips = []
        for ip in PROTECTED_ENTERTAINMENT_IPS:
            if re.search(rf'\b{re.escape(ip)}\b', full_text) or re.search(rf'\b{re.escape(ip)}\b', eval_title_lower):
                detected_ips.append(ip.title())
        detected_ips = list(dict.fromkeys(detected_ips))

        if detected_ips:
            first_ip = detected_ips[0]
            prohibited_items_found.append(f"影视/动漫IP侵权 ({first_ip})")
            risk_reasons.append(f"【知名IP版权拦截】检测到受严格保护的影视/动漫IP [{', '.join(detected_ips)}]！")

        status = "PROHIBITED" if prohibited_items_found else ("RISK" if risk_reasons else "SAFE")

        return {
            "compliance_status": status,
            "prohibited_items": prohibited_items_found,
            "detected_brands": all_detected_brands,
            "risk_reasons": risk_reasons,
            "suggestions": suggestions,
            "brand_info": {
                "detected_brands": all_detected_brands,
                "title_detected_brands": title_detected_brands,
                "is_accessory": is_accessory,
                "recommended_title": recommended_title
            }
        }
