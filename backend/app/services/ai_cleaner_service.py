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
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
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

    def _clean_with_llm(self, product: Dict[str, Any], target_brand: str) -> Dict[str, Any]:
        """通过大语言模型进行全品类自适应的信息抽取与改写"""
        raw_title = product.get('takealot_title', '')
        category = product.get('takealot_category', '')

        prompt = f"""
你是一名资深的跨境电商商品刊登专家，精通南非电商平台 Takealot 与 Makro (基于沃尔玛/Flipkart 规范) 的数据对齐。
请将下面来自 Takealot 的原始商品数据，智能识别其真实的品类，并清洗转换为符合 Makro 卖家平台要求的规范 JSON 格式。

【品牌规范】:
- 必须使用指定的授权品牌: "{target_brand}"，无论原品牌是什么，强制替换为 "{target_brand}"。

【品类 (Vertical) 映射】:
- 必须根据商品实际属性分析输出最匹配的 Makro 标准 vertical 英文小写下划线代码。
  例如:
  - 网络工具/五金工具/钳子: "crimping_tool", "hand_tool", "hardware_tool", "network_accessory" 等
  - 毛巾/浴巾: "bath_towel"
  - 数据线/充电头: "data_cable", "battery_charger"
  - 家居/日用: "storage_box", "kitchen_tool", "bed_sheet" 等

【标题 (Title) 重写与品牌侵权防范要求】:
- 必须以品牌 "{target_brand}" 开头；
- 严禁包含 Takealot 专有促销词 (如 Deals, Sale, Warranty 等)；
- ★★★【品牌配件防侵权铁律】:
  如果商品是适配知名品牌 (如 Apple, iPhone, Samsung, Dyson, Sony, Huawei, GoPro, Nintendo 等) 的配件 (如手机壳/表带/充电线/滤芯/支架等)：
  - 严禁直接写 "{target_brand} Apple iPhone Case" (此写法会被 Makro 判定为官方冒充侵权)；
  - 必须强制采用第三方兼容声明格式：
    "{target_brand} Third-Party [Item Type] Compatible with [Target Brand] [Device Model]"
    或 "{target_brand} Replacement [Item Type] Suitable for [Target Brand] [Device Model]"；
- 【Makro Model Number 参数规则】:
  Makro 平台官方要求：model_number 与 model_name 绝不能包含品牌名（严禁含有 "{target_brand}"，否则会被平台报错拦截：Brand name should not be part of the attribute value）！
  因此 attributes 中的 "model_number" 字段请填入去除品牌名后的规范商品型号/英文描述 (如 "Third-Party USB Flash Drive Compatible with Apple iPhone and USB-C Devices 1TB")！

【Takealot 原始商品数据】:
原标题: {raw_title}
原品牌: {product.get('takealot_brand')}
原类目路径: {category}
规格参数: {json.dumps(product.get('takealot_specs', {}), ensure_ascii=False)}
原描述: {product.get('takealot_description', '')[:1000]}

【输出要求】:
必须且仅返回纯 JSON 对象，格式如下：
{{
  "vertical": "识别出的最准垂直类目(如 crimping_tool 或 hand_tool 或 bath_towel)",
  "brand": "{target_brand}",
  "makro_title": "{target_brand} 规范英文商品标题",
  "description": "精炼且专业的英文商品卖点描述(4-6条特性)",
  "attributes": {{
    "model_name": "简明型号(绝不包含品牌名)",
    "model_number": "去除品牌名后的规范英文名称/型号(绝不能带品牌名)",
    "brand_colour": "颜色",
    "colour": "标准色(如 Yellow, Black, Blue 等)",
    "material": "材质(如 Steel, Plastic, Microfiber 等)",
    "packaging_type": "Pack",
    "sales_package": "包装清单",
    "ideal_for": "适用对象",
    "design": "no"
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
        elif "crimp" in title_lower or "tool" in title_lower or "cable" in title_lower or "network" in cat_lower:
            vertical = "crimping_tool"
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
