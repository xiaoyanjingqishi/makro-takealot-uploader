import json
import logging
import re
from typing import Dict, Any, Optional
from openai import OpenAI
from ..config import settings
from ..models.setting import SystemSetting
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class AICleanerService:
    """
    AI 数据清洗与属性规范化服务 (支持通义千问 Qwen 与 DeepSeek)
    全品类自适应：智能识别类目，不再局限于毛巾浴巾，支持工具、3C数码、家居百货等所有品类
    """

    def __init__(self, provider: str = None, api_key: str = None, base_url: str = None, model: str = None):
        self.provider = provider or settings.AI_PROVIDER
        
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

        return cls(provider=provider, api_key=api_key, base_url=base_url, model=model)

    def clean_product_data(self, takealot_product: Dict[str, Any], target_brand: str = "Beishi") -> Dict[str, Any]:
        """
        清洗商品数据并输出符合 Makro 规范的结构化字典
        """
        if self.client and self.api_key:
            try:
                return self._clean_with_llm(takealot_product, target_brand)
            except Exception as e:
                logger.error(f"AI 调用失败，执行本地启发式清洗保底: {e}")
                return self._fallback_rule_clean(takealot_product, target_brand)
        else:
            return self._fallback_rule_clean(takealot_product, target_brand)

    def _decide_vertical_with_llm(
        self,
        raw_title: str,
        category: str,
        specs: Any,
        description: str,
        candidate_verticals: list
    ) -> str:
        """阶段 1: 极速分类决策 —— 让大模型从候选集精准裁定 1 个 Makro 官方类目"""
        from .vertical_service import VerticalService

        candidates_str = ", ".join(f'"{c}"' for c in candidate_verticals)
        prompt = f"""You are a professional e-commerce category taxonomy expert for Makro (Flipkart/Walmart SaaS).
Select the SINGLE best matching Makro official vertical code from this candidate list:
[{candidates_str}]

Product Data:
Title: {raw_title}
Category: {category}
Specs: {json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)}
Description: {description[:300]}

Reply ONLY with a JSON object:
{{"vertical": "<exact_code_from_candidate_list>"}}"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a professional category classifier. Reply ONLY with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=80
            )
            content = resp.choices[0].message.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            data = json.loads(json_match.group()) if json_match else json.loads(content)
            chosen = str(data.get("vertical", "")).strip().lower().replace("-", "_").replace(" ", "_")
            valid_v, _ = VerticalService.resolve_vertical(chosen)
            if valid_v in candidate_verticals or valid_v != "bath_towel":
                return valid_v
        except Exception as e:
            logger.warning(f"阶段 1 AI 类目定标异常: {e}")

        return VerticalService.predict_vertical(title=raw_title, category=category, specs=specs, description=description)

    def _clean_with_llm(self, product: Dict[str, Any], target_brand: str) -> Dict[str, Any]:
        """两阶段流水线：阶段1定标官方类目 -> 阶段2注入官方元数据Schema精准抽取属性与重写标题"""
        raw_title = product.get('takealot_title', '')
        category = product.get('takealot_category', '')
        specs = product.get('takealot_specs', {})
        description = product.get('takealot_description', '')
        preset_vertical = product.get('makro_vertical')

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
                candidate_verticals=candidate_verticals
            )

        # ★★★ 阶段 2: 注入该类目的官方元数据 Schema 规范并深度清洗 ★★★
        schema_summary = VerticalService.get_vertical_schema_summary(chosen_vertical)
        guidelines_text = schema_summary.get("guidelines_text", "")

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

【标题 (Title) 重写与品牌配件防侵权要求】:
- 标题必须以品牌 "{target_brand}" 开头；
- 严禁包含 Takealot 促销词 (如 Deals, Sale, Warranty 等)；
- 若为知名品牌配件（如 Apple/iPhone 保护套等），标题必须采用第三方兼容声明格式：
  "{target_brand} Third-Party [Item] Compatible with [Device]"；

【Takealot 原始商品数据】:
原标题: {raw_title}
原品牌: {product.get('takealot_brand')}
原类目: {category}
规格参数: {json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)}
原描述: {description[:1000]}

【输出要求】:
必须且仅返回纯 JSON 对象，格式如下：
{{
  "vertical": "{chosen_vertical}",
  "brand": "{target_brand}",
  "makro_title": "{target_brand} 规范英文商品标题",
  "description": "精炼且专业的英文商品卖点描述(4-6条特性)",
  "attributes": {{
    // 必须包含上述类目规范中声明的必填项与推荐项
  }}
}}
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a professional e-commerce product catalog expert. Always reply with valid JSON."},
                {"role": "user", "content": prompt}
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
        elif any(k in title_lower or k in cat_lower for k in ["bra", "underwear", "sculpting", "lingerie", "corset"]):
            vertical = "costume_wear"
            makro_title = f"{target_brand} Wire-Free Push-Up Full Coverage Comfort Bra ({colour})"
            attrs = {
                "model_name": "Comfort Fit",
                "model_number": model_number,
                "brand_colour": colour,
                "colour": colour,
                "material": "Spandex & Nylon",
                "packaging_type": "Pack",
                "sales_package": "1 Bra",
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
        elif any(k in title_lower or k in cat_lower for k in ["garden", "prun", "shear"]):
            vertical = "garden_tools"
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

        # 确保 model_number 绝不包含目标品牌名
        clean_mn = re.sub(rf'^\s*{re.escape(target_brand)}\s*[-_:]*\s*', '', makro_title, flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_brand)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        attrs["model_number"] = clean_mn[:250] if clean_mn else model_number

        return {
            "vertical": vertical,
            "brand": target_brand,
            "makro_title": makro_title,
            "description": product.get("takealot_description") or f"{raw_title}. Premium quality provided by {target_brand}.",
            "attributes": attrs
        }
