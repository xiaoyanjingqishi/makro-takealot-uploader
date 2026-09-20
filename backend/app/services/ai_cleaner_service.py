import json
import logging
import re
import base64
import io
import requests
from typing import Dict, Any, Optional
from openai import OpenAI
from PIL import Image
from ..config import settings
from ..models.setting import SystemSetting
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def truncate_title_safely(title: str, max_len: int = 120) -> str:
    """
    智能截断标题：总字符数不超过 max_len，并在单词边界 (空格/破折号/逗号) 友好截断，绝不在单词中间生硬腰斩
    """
    title = re.sub(r'\s+', ' ', str(title or "")).strip()
    if len(title) <= max_len:
        return title
    truncated = title[:max_len]
    last_sep = max(truncated.rfind(' '), truncated.rfind(','), truncated.rfind('-'), truncated.rfind('/'))
    if last_sep > int(max_len * 0.65):
        truncated = truncated[:last_sep].rstrip(' -_,;:/')
    return truncated

class AICleanerService:
    """
    AI 数据清洗与属性规范化服务 (支持通义千问 Qwen 与 DeepSeek，支持纯文本与图文多模态双模式)
    全品类自适应：智能识别类目，不再局限于毛巾浴巾，支持工具、3C数码、家居百货等所有品类
    """

    def __init__(
        self,
        provider: str = None,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
        cleaner_mode: str = None,
        qwen_vision_model: str = None,
        qwen_api_key: str = None,
        qwen_base_url: str = None,
        seo_title_enabled: bool = True,
        seo_title_max_len: int = 120
    ):
        self.provider = provider or settings.AI_PROVIDER
        self.cleaner_mode = cleaner_mode or getattr(settings, "DEFAULT_CLEANER_MODE", "text")
        self.qwen_vision_model = qwen_vision_model or getattr(settings, "DEFAULT_QWEN_VISION_MODEL", "qwen-vl-plus")
        self.seo_title_enabled = seo_title_enabled
        self.seo_title_max_len = max(60, min(180, seo_title_max_len or 120))
        
        # 主模型客户端配置
        if self.provider == "deepseek":
            self.api_key = api_key or settings.DEEPSEEK_API_KEY
            self.base_url = base_url or settings.DEEPSEEK_BASE_URL
            self.model = model or settings.DEEPSEEK_MODEL
        else: # 默认 qwen
            self.api_key = api_key or settings.QWEN_API_KEY
            self.base_url = base_url or settings.QWEN_BASE_URL
            self.model = model or settings.QWEN_MODEL

        self.client = None
        if self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=35.0)
            except Exception as e:
                logger.error(f"初始化 OpenAI 客户端失败: {e}")

        # 视觉多模态大模型客户端 (固定使用 DashScope Qwen-VL)
        self.qwen_api_key = qwen_api_key or (self.api_key if self.provider == "qwen" else settings.QWEN_API_KEY)
        self.qwen_base_url = qwen_base_url or (self.base_url if self.provider == "qwen" else settings.QWEN_BASE_URL)
        self.vision_client = None
        if self.qwen_api_key:
            try:
                self.vision_client = OpenAI(api_key=self.qwen_api_key, base_url=self.qwen_base_url, timeout=45.0)
            except Exception as e:
                logger.error(f"初始化 Qwen 视觉客户端失败: {e}")

    @classmethod
    def from_db(cls, db: Session):
        """从数据库读取配置并实例化"""
        s_provider = db.query(SystemSetting).filter(SystemSetting.key == "ai_provider").first()
        provider = s_provider.value if s_provider and s_provider.value else settings.AI_PROVIDER

        if provider == "deepseek":
            s_key = db.query(SystemSetting).filter(SystemSetting.key == "deepseek_api_key").first()
            s_url = db.query(SystemSetting).filter(SystemSetting.key == "deepseek_base_url").first()
            s_model = db.query(SystemSetting).filter(SystemSetting.key == "deepseek_model").first()
            api_key = s_key.value if s_key else settings.DEEPSEEK_API_KEY
            base_url = s_url.value if s_url else settings.DEEPSEEK_BASE_URL
            model = s_model.value if s_model else settings.DEEPSEEK_MODEL
        else:
            s_key = db.query(SystemSetting).filter(SystemSetting.key == "qwen_api_key").first()
            s_url = db.query(SystemSetting).filter(SystemSetting.key == "qwen_base_url").first()
            s_model = db.query(SystemSetting).filter(SystemSetting.key == "qwen_model").first()
            api_key = s_key.value if s_key else settings.QWEN_API_KEY
            base_url = s_url.value if s_url else settings.QWEN_BASE_URL
            model = s_model.value if s_model else settings.QWEN_MODEL

        s_qwen_key = db.query(SystemSetting).filter(SystemSetting.key == "qwen_api_key").first()
        s_qwen_url = db.query(SystemSetting).filter(SystemSetting.key == "qwen_base_url").first()
        qwen_api_key = s_qwen_key.value if s_qwen_key else settings.QWEN_API_KEY
        qwen_base_url = s_qwen_url.value if s_qwen_url else settings.QWEN_BASE_URL

        s_cleaner_mode = db.query(SystemSetting).filter(SystemSetting.key == "cleaner_mode").first()
        cleaner_mode = s_cleaner_mode.value if s_cleaner_mode and s_cleaner_mode.value else getattr(settings, "DEFAULT_CLEANER_MODE", "text")

        s_vision_model = db.query(SystemSetting).filter(SystemSetting.key == "qwen_vision_model").first()
        qwen_vision_model = s_vision_model.value if s_vision_model and s_vision_model.value else getattr(settings, "DEFAULT_QWEN_VISION_MODEL", "qwen-vl-plus")

        s_seo_en = db.query(SystemSetting).filter(SystemSetting.key == "seo_title_enabled").first()
        seo_title_enabled = (s_seo_en.value.lower() in ["true", "1", "yes"]) if s_seo_en and s_seo_en.value else getattr(settings, "DEFAULT_SEO_TITLE_ENABLED", True)

        s_seo_len = db.query(SystemSetting).filter(SystemSetting.key == "seo_title_max_len").first()
        try:
            seo_title_max_len = int(s_seo_len.value) if s_seo_len and s_seo_len.value else getattr(settings, "DEFAULT_SEO_TITLE_MAX_LEN", 120)
        except Exception:
            seo_title_max_len = getattr(settings, "DEFAULT_SEO_TITLE_MAX_LEN", 120)

        return cls(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            cleaner_mode=cleaner_mode,
            qwen_vision_model=qwen_vision_model,
            qwen_api_key=qwen_api_key,
            qwen_base_url=qwen_base_url,
            seo_title_enabled=seo_title_enabled,
            seo_title_max_len=seo_title_max_len
        )

    def _fetch_primary_image_data_uri(self, raw_images_data: Any) -> Optional[str]:
        """
        高效提取首图并转换为规范的 Base64 Data URI
        限制最大边长 1024px，高质量 JPEG 压缩，将单张图片视觉 token 控制在 ~1000 以内并提速
        """
        image_url = None
        if isinstance(raw_images_data, str):
            try:
                parsed = json.loads(raw_images_data)
                if isinstance(parsed, list) and parsed:
                    image_url = parsed[0]
                elif isinstance(parsed, str):
                    image_url = parsed
            except Exception:
                if raw_images_data.startswith("http"):
                    image_url = raw_images_data
        elif isinstance(raw_images_data, list) and raw_images_data:
            image_url = raw_images_data[0]

        if not image_url or not isinstance(image_url, str) or not image_url.startswith("http"):
            return None

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.takealot.com/"
            }
            resp = requests.get(image_url, headers=headers, timeout=10)
            if resp.status_code != 200 or not resp.content:
                return None

            img = Image.open(io.BytesIO(resp.content))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            # 缩放至最大边长不超过 1024px
            max_edge = 1024
            w, h = img.size
            if max(w, h) > max_edge:
                scale = max_edge / max(w, h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{b64_data}"
        except Exception as e:
            logger.warning(f"下载或压缩商品首图失败，将平滑降级为纯文本清洗: {e}")
            return None

    def clean_product_data(
        self,
        takealot_product: Dict[str, Any],
        target_brand: str = "Beishi",
        clean_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        清洗商品数据并输出符合 Makro 规范的结构化字典
        支持 clean_mode: 'text' (纯文本标准) 或 'vision' (图文多模态)
        """
        actual_mode = (clean_mode or self.cleaner_mode or "text").lower()
        if self.client and self.api_key:
            try:
                return self._clean_with_llm(takealot_product, target_brand, clean_mode=actual_mode)
            except Exception as e:
                logger.error(f"AI 调用失败，执行本地启发式清洗保底: {e}")
                res = self._fallback_rule_clean(takealot_product, target_brand)
                res["clean_mode"] = "fallback"
                return res
        else:
            res = self._fallback_rule_clean(takealot_product, target_brand)
            res["clean_mode"] = "fallback"
            return res

    def _decide_vertical_with_llm(
        self,
        raw_title: str,
        category: str,
        specs: Any,
        description: str,
        candidate_verticals: list,
        image_data_uri: Optional[str] = None
    ) -> str:
        """阶段 1: 极速分类决策 —— 让大模型从候选集精准裁定 1 个 Makro 官方类目 (支持首图多模态)"""
        from .vertical_service import VerticalService

        verticals_map = VerticalService._load_verticals()
        formatted_candidates = []
        for c in candidate_verticals:
            info = verticals_map.get(c)
            if info and isinstance(info, list) and info[0]:
                disp = info[0].get("verticalDisplayName", c)
                path = info[0].get("path", "")
                formatted_candidates.append(f'"{c}" ({disp} | Path: {path})')
            else:
                formatted_candidates.append(f'"{c}"')
        candidates_str = "\n".join(formatted_candidates)

        prompt = f"""You are a professional e-commerce category taxonomy expert for Makro (Flipkart/Walmart SaaS).
Select the SINGLE best matching Makro official vertical code from this candidate list (each item displays its code, display name, and category path):
[
{candidates_str}
]

Category Selection Rules:
- NEVER select "costume_wear" for normal daily bras, underwear, lingerie, everyday clothes, headlamps, or foot sleeves! "costume_wear" is strictly for cosplay and fancy dress party costumes.
- For headlamps, flashlights, or portable lights, choose "torch".
- For foot socks, neuropathy socks, heel protectors, or plantar fasciitis pads, choose "foot_pad".
- For neck warmers, gaiters, beanies, or scarves, choose "cap".
- For lawn signs, garden decor, statues, boundary markers, sculptures, or lawn ornaments, choose "garden_gnome".
- For garden sprayers, hose nozzles, sprinklers, or lawn watering, choose "garden_sprayer".
- For garden tools, pruning shears, loppers, or trowels, choose "garden_tool_set".
- For bathroom grab bars, safety rails, or handicap handles, choose "shower_grab_bar".
- For phone stands, phone holders, or ring grips, choose "mobile_holder".

Product Data:
Title: {raw_title}
Category: {category}
Specs: {json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)}
Description: {description[:300]}

Reply ONLY with a JSON object:
{{"vertical": "<exact_code_from_candidate_list>"}}"""

        if image_data_uri and self.vision_client:
            user_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_uri}}
            ]
            active_client = self.vision_client
            active_model = self.qwen_vision_model
        else:
            user_content = prompt
            active_client = self.client
            active_model = self.model

        try:
            resp = active_client.chat.completions.create(
                model=active_model,
                messages=[
                    {"role": "system", "content": "You are a professional category classifier. Reply ONLY with valid JSON."},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                max_tokens=80
            )
            content = resp.choices[0].message.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            data = json.loads(json_match.group()) if json_match else json.loads(content)
            chosen = str(data.get("vertical", "")).strip().lower().replace("-", "_").replace(" ", "_")
            
            # 防御：普通服饰/头灯/足垫绝不能选 costume_wear
            if chosen == "costume_wear":
                txt_check = f"{raw_title} {category}".lower()
                if any(w in txt_check for w in ["bra", "underwear", "lingerie", "panties", "socks", "headlamp", "lamp", "torch", "foot", "heel", "fasciitis"]):
                    return VerticalService.predict_vertical(title=raw_title, category=category, specs=specs, description=description)

            valid_v, _ = VerticalService.resolve_vertical(chosen)
            if valid_v in candidate_verticals or valid_v != "cases_covers":
                return valid_v
        except Exception as e:
            logger.warning(f"阶段 1 AI 类目定标异常: {e}")

        return VerticalService.predict_vertical(title=raw_title, category=category, specs=specs, description=description)

    def _clean_with_llm(self, product: Dict[str, Any], target_brand: str, clean_mode: str = "text") -> Dict[str, Any]:
        """两阶段流水线：阶段1定标官方类目 -> 阶段2注入官方元数据Schema精准抽取属性与重写标题 (支持纯文本与图文双模式)"""
        raw_title = product.get('takealot_title', '')
        category = product.get('takealot_category', '')
        specs = product.get('takealot_specs', {})
        description = product.get('takealot_description', '')
        preset_vertical = product.get('makro_vertical')

        applied_mode = "text"
        image_data_uri = None
        if clean_mode == "vision":
            image_data_uri = self._fetch_primary_image_data_uri(product.get("raw_images") or product.get("cover_image"))
            if image_data_uri and self.vision_client:
                applied_mode = "vision"
            else:
                logger.info("未获取到可用首图或未配置视觉大模型客户端，平滑降级为纯文本模式")
                applied_mode = "text"

        from .vertical_service import VerticalService

        # ★★★ 阶段 1: 确定唯一的 Makro 官方类目代码 ★★★
        if preset_vertical and preset_vertical != "bath_towel":
            valid_v, _ = VerticalService.resolve_vertical(preset_vertical)
            chosen_vertical = valid_v
        else:
            candidate_verticals = VerticalService.get_candidate_verticals(
                title=raw_title,
                category=category,
                specs=specs,
                description=description
            )
            chosen_vertical = self._decide_vertical_with_llm(
                raw_title=raw_title,
                category=category,
                specs=specs,
                description=description,
                candidate_verticals=candidate_verticals,
                image_data_uri=image_data_uri if applied_mode == "vision" else None
            )

        # ★★★ 阶段 2: 注入该类目的官方元数据 Schema 规范并深度清洗 ★★★
        schema_summary = VerticalService.get_vertical_schema_summary(chosen_vertical)
        guidelines_text = schema_summary.get("guidelines_text", "")

        vision_instructions = ""
        if applied_mode == "vision" and image_data_uri:
            vision_instructions = """
【★★★ 核心要求：附图多模态实物核验与深度校准 (实物对照)】:
- 请仔细观察随附的商品首图实物外观；
- 结合首图真实外观校准商品的实际颜色 (brand_colour)、真实款式类型、材质手感与适用对象；
- 首图与文本参数互为印证：硬性规格/兼容型号以文本 Specs 为准，外观真实形态、主色调与款式以首图为准，绝不可无中生有。
"""

        if self.seo_title_enabled:
            title_instructions = f"""
【★★★ 核心要求：商品标题 SEO 搜索意图拓展与高转化重构 (极重要)】:
1. 买家搜索意图挖掘 (Search Intent & Keyword Mining):
   - 深入分析该商品在南非电商平台 (Makro/Takealot) 买家最常使用的核心搜索词群、品类同义词、高意向长尾词与具体使用场景；
   - 挖掘 2~4 个精准的高相关性搜索关键词 (例如: "High Pressure", "Waterproof", "Garden Hose Sprayer", "Heavy Duty", "Car Wash", "Breathable Hooded" 等)。
2. 结构化电商标题构建范式:
   - 严禁生硬逗号堆砌关键词！必须遵循成熟规范的电商高转化标题结构：
     【{target_brand}】 + 【核心品名 Core Product Name】 + 【高频搜索长尾词/同义词】 + 【使用场景/目标对象 for ... / with ...】 + 【关键材质/颜色/规格】
   - 示例参考:
     * 工具类: "{target_brand} Multi-Function Spray Nozzle Cleaning Tool - High Pressure Water Sprayer Gun for Garden Hose & Car Wash"
     * 雨具类: "{target_brand} Lightweight Waterproof Raincoat - Breathable Hooded Rain Poncho for Outdoor Hiking & Camping"
     * 耗材类: "{target_brand} 12-Piece HSS Twist Drill Bit Set - High-Speed Steel Metal & Wood Hole Drilling Bits for Power Drills"
3. 字符长度控制:
   - 标题总字符数 (包含品牌与空格) 请严格控制在 80 ~ {self.seo_title_max_len} 字符以内！
   - 既要充分拓展搜索关键词增加曝光，又严禁超出 {self.seo_title_max_len} 字符以防平台截断！
4. 品牌与侵权防护:
   - 标题必须且只能以授权品牌 "{target_brand}" 开头；
   - 严禁为了蹭流量在标题中捏造第三方大牌商标 (如 Samsung, Bosch, Nike 等)；若为知名品牌配件，必须保持第三方兼容声明格式:
     "{target_brand} Third-Party [Item] Compatible with [Device]"；
5. 严禁平台违规促销词:
   - 严禁出现 "Best", "Cheap", "Hot Sale", "Free Shipping", "100% Quality", "Deals", "Warranty" 等平台明令禁止的词汇。
"""
            output_schema = f"""{{
  "vertical": "{chosen_vertical}",
  "brand": "{target_brand}",
  "makro_title": "{target_brand} 规范高权重英文商品标题 (自然融入搜索关键词, 长度80~{self.seo_title_max_len}字符)",
  "seo_keywords": ["高频搜索词1", "使用场景词2", "品类同义词3"],
  "description": "精炼且专业的英文商品卖点描述(4-6条特性)",
  "attributes": {{
    // 必须包含上述类目规范中声明的必填项与推荐项
  }}
}}"""
        else:
            title_instructions = f"""
【标题 (Title) 重写与品牌配件防侵权要求】:
- 标题必须以品牌 "{target_brand}" 开头；
- 严禁包含 Takealot 促销词 (如 Deals, Sale, Warranty 等)；
- 若为知名品牌配件（如 Apple/iPhone 保护套等），标题必须采用第三方兼容声明格式：
  "{target_brand} Third-Party [Item] Compatible with [Device]"；
- 标题长度控制在 60 ~ {self.seo_title_max_len} 字符。
"""
            output_schema = f"""{{
  "vertical": "{chosen_vertical}",
  "brand": "{target_brand}",
  "makro_title": "{target_brand} 规范英文商品标题",
  "seo_keywords": [],
  "description": "精炼且专业的英文商品卖点描述(4-6条特性)",
  "attributes": {{
    // 必须包含上述类目规范中声明的必填项与推荐项
  }}
}}"""

        prompt = f"""
你是一名资深的跨境电商商品刊登专家，精通南非电商平台 Takealot 与 Makro (基于沃尔玛/Flipkart 规范) 的数据对齐。
请将下面来自 Takealot 的原始商品数据，转换为符合 Makro 卖家平台要求的规范 JSON 格式。

【基本参数规范】:
- 授权品牌: 必须强制使用指定的授权品牌 "{target_brand}"，所有原品牌一律替换为 "{target_brand}"。
- 目标官方类目 (Vertical): "{chosen_vertical}"。

【Makro 官方类目 [{chosen_vertical}] 规格提取与字段规范 (极重要 - 严格遵守)】:
{guidelines_text if guidelines_text else "请根据商品真实规格提取标准属性 (model_name, brand_colour, material, pack_of 等)。"}

★★★ 核心属性类型铁律 (违者平台直接报错拦截)：
1. DECIMAL / NUMBER 类型属性（例如尺寸 size、件数 pack_of、重量等）：
   - 必须提取纯数字（如 10、28、1.5）！
   - 严禁填入任何中文字符（严禁出现“均码”或“多色”等伪词汇）！
   - 若原商品未指定数值，请根据商品标题/规格智能推算，或回退官方示例数字！
2. 具有可选单位 (qualifier) 的属性：
   - 必须从上述允许的单位列表中匹配（如 size 单位必须是 inch/cm/m，长宽高为 cm，存储为 GB/TB 等）！
3. 枚举字段 (allowedValues)：
   - 必须从官方候选枚举列表中选择最匹配的 1 项！
4. 布尔字段 (BOOLEAN)：
   - 必须且只能输出 "Yes" 或 "No"！
5. model_number 与 model_name：
   - 严禁包含品牌名 "{target_brand}" (平台规则: Brand name should not be part of the attribute value)！
6. 严禁影视/动漫/游戏受保护IP侵权 (极度严格):
   - 严禁在标题、描述、属性中输出任何未经授权的受保护IP或角色名称（如 Spider Man, Batman, Superman, Marvel, Disney, Barbie, Transformers 等）；
   - 若类目为 costume_wear，character 属性必须且只能输出 "Party" 或 "Cosplay" 等通用中性词，绝不可填入 "Spider Man" 等具体IP角色名！

{vision_instructions}

{title_instructions}

【Takealot 原始商品数据】:
原标题: {raw_title}
原品牌: {product.get('takealot_brand')}
原类目: {category}
规格参数: {json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)}
原描述: {description[:1000]}

【输出要求】:
必须且仅返回纯 JSON 对象，格式如下：
{output_schema}
"""
        if applied_mode == "vision" and image_data_uri:
            user_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_uri}}
            ]
            active_client = self.vision_client
            active_model = self.qwen_vision_model
        else:
            user_content = prompt
            active_client = self.client
            active_model = self.model

        response = active_client.chat.completions.create(
            model=active_model,
            messages=[
                {"role": "system", "content": "You are a professional e-commerce product catalog expert. Always reply with valid JSON."},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2
        )
        content = response.choices[0].message.content
        
        # 解析返回的 JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        data = json.loads(json_match.group()) if json_match else json.loads(content)
        
        # 确保 description 始终为字符串，防止 LLM 返回列表导致 SQLite 写入失败
        desc = data.get("description", "")
        if isinstance(desc, list):
            data["description"] = "\n".join(str(x) for x in desc)
        elif not isinstance(desc, str):
            data["description"] = str(desc)

        data["vertical"] = chosen_vertical
        data["clean_mode"] = applied_mode

        # 代码保底：确保 Model Number 包含完整标题，并在配件命中知名品牌时兜底添加第三方兼容声明
        makro_title = data.get("makro_title") or raw_title
        attrs = data.get("attributes") or {}

        # 检查是否涉及知名品牌且为配件
        from .compliance_service import FAMOUS_BRANDS, ACCESSORY_KEYWORDS, COMPATIBILITY_KEYWORDS
        full_text = f"{raw_title} {makro_title}".lower()
        matched_brand = next((b for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', full_text)), None)
        is_acc = any(re.search(rf'\b{acc}\b', full_text) for acc in ACCESSORY_KEYWORDS)
        has_compat = any(kw in makro_title.lower() for kw in COMPATIBILITY_KEYWORDS)

        if matched_brand and is_acc and not has_compat:
            brand_display = matched_brand.title()
            clean_t = re.sub(rf'\b{matched_brand}\b', '', makro_title, flags=re.I).replace(target_brand, "").strip()
            clean_t = re.sub(r'\s+', ' ', clean_t)
            makro_title = f"{target_brand} Third-Party {clean_t[:60]} Compatible with {brand_display}"
            data["makro_title"] = makro_title

        # 安全截断标题至最大字符限制
        makro_title = truncate_title_safely(makro_title, self.seo_title_max_len)
        data["makro_title"] = makro_title

        # 提取与规整搜索关键词
        raw_seo_kw = data.get("seo_keywords") or []
        if isinstance(raw_seo_kw, list):
            data["seo_keywords"] = [str(x).strip() for x in raw_seo_kw if str(x).strip()][:6]
        else:
            data["seo_keywords"] = []

        # 将去除品牌名后的规范标题注入 model_number 参数 (规避 Brand name should not be part of attribute value)
        clean_mn = re.sub(rf'^\s*{re.escape(target_brand)}\s*[-_:]*\s*', '', makro_title, flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_brand)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        attrs["model_number"] = clean_mn[:250] if clean_mn else "STD-01"

        if "model_name" in attrs:
            clean_mname = re.sub(rf'^\s*{re.escape(target_brand)}\s*[-_:]*\s*', '', str(attrs["model_name"]), flags=re.I)
            clean_mname = re.sub(rf'\b{re.escape(target_brand)}\b', '', clean_mname, flags=re.I).strip(' -_,:;')
            attrs["model_name"] = clean_mname[:40] if clean_mname else "Standard"

        # 提取 clean size, colour, pack_of 供商品主字段更新
        if "size" in attrs:
            clean_s = str(attrs["size"]).strip()
            if clean_s and clean_s != "均码":
                data["size"] = clean_s
        if "colour" in attrs or "brand_colour" in attrs:
            c = attrs.get("colour") or attrs.get("brand_colour")
            if c and str(c) != "多色":
                data["colour"] = str(c)
        if "pack_of" in attrs:
            data["pack_of"] = str(attrs["pack_of"])

        data["attributes"] = attrs
            
        return data

    def _fallback_rule_clean(self, product: Dict[str, Any], target_brand: str) -> Dict[str, Any]:
        """无大模型 API Key 时的智能全品类启发式清洗"""
        raw_title = product.get("takealot_title", "")
        raw_cat = product.get("takealot_category", "")
        clean_digits = re.sub(r'\D', '', raw_title)[:6] or '1001'
        model_number = f"BS-{int(clean_digits)}"

        # 提取颜色
        colour = "Multicolor"
        colors = ["Yellow", "Black", "Blue", "Red", "Green", "White", "Grey", "Orange", "Pink"]
        for c in colors:
            if re.search(rf"\b{c}\b", raw_title, re.I):
                colour = c
                break

        # 判断品类
        title_lower = raw_title.lower()
        cat_lower = raw_cat.lower()

        if "towel" in title_lower or "bath" in title_lower:
            vertical = "bath_towel"
            makro_title = f"{target_brand} (70 cm x 140 cm) Premium Microfiber Bath Towel ({colour})"
            attrs = {
                "model_name": "Quick Dry",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Microfiber",
                "size": "L",
                "pack_of": "1",
                "width": "70",
                "length": "140",
                "packaging_type": "Pack",
                "bath_towel_type": "Cloth",
                "towel_type": "Bath",
                "sales_package": "1 Towel",
                "ideal_for": "Men & Women",
                "design": "no"
            }
        elif any(k in title_lower or k in cat_lower for k in ["case", "cover", "phone", "iphone", "samsung", "cellphone"]):
            vertical = "cases_covers"
            makro_title = f"{target_brand} Shockproof Protective Clear Phone Case ({colour})"
            attrs = {
                "model_name": "Ultra Slim Fit",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "TPU & Polycarbonate",
                "packaging_type": "Box",
                "sales_package": "1 Protective Case",
                "ideal_for": "Everyday Use",
                "design": "Shockproof"
            }
        elif any(k in title_lower or k in cat_lower for k in ["headlamp", "flashlight", "torch", "lantern", "headlight", "lumens"]):
            vertical = "torch"
            makro_title = f"{target_brand} Rechargeable Waterproof LED Headlamp Flashlight ({colour})"
            attrs = {
                "model_name": "LED Spotlight",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "ABS & Aluminum",
                "packaging_type": "Box",
                "sales_package": "1 LED Headlamp",
                "ideal_for": "Outdoor, Camping, Hiking",
                "power_source": "Rechargeable Battery"
            }
        elif any(k in title_lower or k in cat_lower for k in ["foot", "heel", "fasciitis", "neuropathy", "insole", "foot sock"]):
            vertical = "foot_pad"
            makro_title = f"{target_brand} Compression Foot Sleeve & Plantar Fasciitis Support Pad ({colour})"
            attrs = {
                "model_name": "Foot Care Sleeve",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Silicone & Elastic Fabric",
                "packaging_type": "Pack",
                "sales_package": "1 Pair Foot Sleeves",
                "ideal_for": "Men & Women"
            }
        elif any(k in title_lower or k in cat_lower for k in ["neck warmer", "neck gaiter", "scarf", "snood", "beanie"]):
            vertical = "cap"
            makro_title = f"{target_brand} Winter Warm Fleece Neck Warmer Gaiter Scarf ({colour})"
            attrs = {
                "model_name": "Winter Neck Warmer",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Fleece & Cotton",
                "packaging_type": "Pack",
                "sales_package": "1 Neck Warmer",
                "ideal_for": "Unisex"
            }
        elif any(k in title_lower or k in cat_lower for k in ["costume", "cosplay", "fancy dress"]):
            vertical = "costume_wear"
            makro_title = f"{target_brand} Party Cosplay Costume Wear ({colour})"
            attrs = {
                "model_name": "Party Wear",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "character": "Party",
                "theme": "Party & Celebration",
                "material": "Polyester",
                "packaging_type": "Pack",
                "sales_package": "1 Costume Set",
                "ideal_for": "Unisex"
            }
        elif any(k in title_lower or k in cat_lower for k in ["bra", "underwear", "sculpting", "lingerie", "corset"]):
            # Makro 平台无成人日常内衣专属类目，严防错挂为 costume_wear 导致平台强拼接 Spider Man 标题
            vertical = "cases_covers"
            makro_title = f"{target_brand} Comfort Fit Seamless Wire-Free Bra Underwear Accessory ({colour})"
            attrs = {
                "model_name": "Comfort Fit",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Spandex & Nylon",
                "packaging_type": "Pack",
                "sales_package": "1 Bra Accessory",
                "ideal_for": "Women",
                "design": "Full Coverage"
            }
        elif any(k in title_lower or k in cat_lower for k in ["wallet", "purse", "card holder", "leather"]):
            vertical = "card_holder"
            makro_title = f"{target_brand} Men's Genuine Leather RFID Blocking Slim Wallet ({colour})"
            attrs = {
                "model_name": "Slim Card Holder",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Genuine Leather",
                "packaging_type": "Gift Box",
                "sales_package": "1 Wallet",
                "ideal_for": "Men",
                "design": "Bifold"
            }
        elif any(k in title_lower or k in cat_lower for k in ["usb", "flash drive", "pendrive", "storage", "thumb"]):
            vertical = "usb_flash_drive"
            makro_title = f"{target_brand} High Speed USB 3.0 Metal Flash Drive ({colour})"
            attrs = {
                "model_name": "Metal Mini",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Metal Alloy",
                "packaging_type": "Blister Pack",
                "sales_package": "1 USB Flash Drive",
                "ideal_for": "PC, Mac & Mobile",
                "design": "Compact"
            }
        elif "crimp" in title_lower or "tool" in title_lower or "cable" in title_lower or "network" in cat_lower:
            vertical = "plier"
            makro_title = f"{target_brand} Professional RJ45 Network Cable Crimp Tool Cat5e/Cat6 ({colour})"
            attrs = {
                "model_name": "EZ Pass-through",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "High Carbon Steel & Plastic",
                "packaging_type": "Box",
                "sales_package": "1 Crimping Tool",
                "ideal_for": "Professional & DIY",
                "design": "Ergonomic"
            }
        elif any(k in title_lower or k in cat_lower for k in ["glasses", "sunglasses", "eyewear", "spectacles"]):
            vertical = "protective_glasses"
            makro_title = f"{target_brand} Classic UV400 Protective Sunglasses ({colour})"
            attrs = {
                "model_name": "Vision Pro",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Polycarbonate",
                "packaging_type": "Box",
                "sales_package": "1 Sunglasses with Case",
                "ideal_for": "Men & Women",
                "design": "Classic"
            }
        elif any(k in title_lower or k in cat_lower for k in ["decor", "ornament", "sculpture", "statue", "sign", "gnome"]):
            vertical = "garden_gnome"
            makro_title = f"{target_brand} Decorative Garden Lawn Sign Ornament ({colour})"
            attrs = {
                "model_name": "Garden Decor",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Cast Iron",
                "packaging_type": "Box",
                "sales_package": "1 Garden Lawn Sign",
                "weather_resistant": "Yes",
                "design": "Classic"
            }
        elif any(k in title_lower or k in cat_lower for k in ["sprayer", "sprinkler", "hose"]):
            vertical = "garden_sprayer"
            makro_title = f"{target_brand} Adjustable Garden Sprayer Nozzle Sprinkler ({colour})"
            attrs = {
                "model_name": "Pro Sprayer",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "ABS Plastic",
                "packaging_type": "Box",
                "sales_package": "1 Sprayer",
                "design": "Ergonomic"
            }
        elif any(k in title_lower or k in cat_lower for k in ["garden", "prun", "shear", "axe", "rake", "spade"]):
            vertical = "garden_tool_set"
            makro_title = f"{target_brand} Heavy Duty Bypass Pruning Shears Garden Tool ({colour})"
            attrs = {
                "model_name": "Garden Master",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Carbon Steel",
                "packaging_type": "Blister Pack",
                "sales_package": "1 Pruning Shear",
                "ideal_for": "Garden & Yard Work",
                "design": "Ergonomic"
            }
        else:
            from .vertical_service import VerticalService
            vertical = VerticalService.predict_vertical(title=raw_title, category=raw_cat, description=product.get("takealot_description", ""))
            clean_t = re.sub(r'[^\w\s-]', '', raw_title)[:60]
            makro_title = f"{target_brand} Premium Quality {clean_t} ({colour})"
            attrs = {
                "model_name": "Standard Series",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Standard Quality Material",
                "packaging_type": "Pack",
                "sales_package": "1 Unit",
                "ideal_for": "All Users",
                "design": "Standard"
            }
        # 检查是否涉及知名品牌且为配件
        from .compliance_service import FAMOUS_BRANDS, ACCESSORY_KEYWORDS, COMPATIBILITY_KEYWORDS
        full_text = f"{raw_title} {makro_title}".lower()
        matched_brand = next((b for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', full_text)), None)
        is_acc = any(re.search(rf'\b{acc}\b', full_text) for acc in ACCESSORY_KEYWORDS)
        has_compat = any(kw in makro_title.lower() for kw in COMPATIBILITY_KEYWORDS)

        if matched_brand and is_acc and not has_compat:
            brand_display = matched_brand.title()
            clean_t = re.sub(rf'\b{matched_brand}\b', '', makro_title, flags=re.I).replace(target_brand, "").strip()
            clean_t = re.sub(r'\s+', ' ', clean_t)
            makro_title = f"{target_brand} Third-Party {clean_t[:60]} Compatible with {brand_display}"

        # 截断与安全长度保护
        makro_title = truncate_title_safely(makro_title, self.seo_title_max_len)

        # 确保 model_number 绝不包含目标品牌名
        clean_mn = re.sub(rf'^\s*{re.escape(target_brand)}\s*[-_:]*\s*', '', makro_title, flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_brand)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        attrs["model_number"] = clean_mn[:250] if clean_mn else model_number

        return {
            "vertical": vertical,
            "brand": target_brand,
            "makro_title": makro_title,
            "seo_keywords": [],
            "description": product.get("takealot_description") or f"{raw_title}. Premium quality provided by {target_brand}.",
            "attributes": attrs
        }
