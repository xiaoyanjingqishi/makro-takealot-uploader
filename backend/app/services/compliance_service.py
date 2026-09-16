import re
import json
import base64
import logging
import requests
from typing import Dict, Any, List, Optional
from openai import OpenAI
from ..config import settings
from ..models.setting import SystemSetting
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

class ComplianceService:
    """
    商品合规与侵权检测服务
    1. 违禁品检测: 蓝牙 (Bluetooth)、WiFi、红外线 (Infrared)、液体 (Liquid)
    2. 品牌与配件侵权检测: 自动识别知名品牌，配件必须带 'Third-Party ... Compatible with'
    3. 首图 AI 多模态视觉检测: 调用 qwen-vl-max 识别首图 Logo、违禁品特征与官方水印
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.QWEN_API_KEY
        self.base_url = base_url or settings.QWEN_BASE_URL
        self.client = None
        if self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception as e:
                logger.warning(f"初始化 ComplianceService OpenAI 客户端失败: {e}")

    @classmethod
    def from_db(cls, db: Session):
        s_key = db.query(SystemSetting).filter(SystemSetting.key == "qwen_api_key").first()
        s_url = db.query(SystemSetting).filter(SystemSetting.key == "qwen_base_url").first()
        api_key = s_key.value if s_key and s_key.value else settings.QWEN_API_KEY
        base_url = s_url.value if s_url and s_url.value else settings.QWEN_BASE_URL
        return cls(api_key=api_key, base_url=base_url)

    def check_product(self, product_data: Dict[str, Any], check_image: bool = True) -> Dict[str, Any]:
        """
        全量检测入口
        """
        title = product_data.get("takealot_title") or ""
        makro_title = product_data.get("makro_title") or ""
        desc = product_data.get("takealot_description") or ""
        specs = product_data.get("takealot_specs") or ""
        specs_str = json.dumps(specs, ensure_ascii=False) if isinstance(specs, (dict, list)) else str(specs)
        category = product_data.get("takealot_category") or ""
        raw_images = product_data.get("raw_images") or []
        if isinstance(raw_images, str):
            try:
                raw_images = json.loads(raw_images)
            except:
                raw_images = []

        full_text = f"{title} {makro_title} {desc} {specs_str} {category}".lower()

        prohibited_items_found = []
        risk_reasons = []
        suggestions = []

        # 1. 违禁品检测 (蓝牙 / WiFi / 红外线 / 液体)
        # A. 蓝牙检测
        if re.search(r'\b(bluetooth|bt\s*[45]\.\d|ble|a2dp|wireless\s*audio)\b', full_text) or "蓝牙" in full_text:
            prohibited_items_found.append("蓝牙 (Bluetooth)")
            risk_reasons.append("商品包含蓝牙 (Bluetooth) 无线通讯技术，属于平台违禁或严格受限品类。")
            suggestions.append("不可在 Makro 平台销售含蓝牙功能的设备，建议下架或改为有线版本。")

        # B. WiFi 检测
        if re.search(r'\b(wifi|wi-fi|802\.11|wlan|2\.4ghz\s*wifi|5ghz\s*wifi)\b', full_text) or "无线网络" in full_text:
            prohibited_items_found.append("WiFi")
            risk_reasons.append("商品包含 WiFi 无线通讯技术，属于平台违禁或严格受限品类。")
            suggestions.append("不可在 Makro 平台销售含 WiFi 功能的联网设备，建议下架或改为非智能/无无线模块版本。")

        # C. 红外线检测
        if re.search(r'\b(infrared|ir\s*remote|ir\s*blaster|ir\s*control|ir\s*sensor|ir\s*emitter)\b', full_text) or "红外" in full_text:
            prohibited_items_found.append("红外线 (Infrared)")
            risk_reasons.append("商品包含红外线 (Infrared/IR) 遥控或发射功能，属于平台受限品类。")
            suggestions.append("不可销售红外发射/控制设备，请核实产品规格。")

        # D. 液体检测
        liquid_match = re.search(
            r'\b(liquid|fluid|oil|essential\s*oil|lotion|perfume|cologne|fragrance|spray|serum|essence|shampoo|conditioner|gel|cream|toner|lubricant|cleanser|beverage|edible|syrup|mouthwash|ink)\b',
            full_text
        ) or re.search(r'(液体|精油|香水|喷雾|乳液|凝胶|膏霜|洗发水|沐浴露|口服液|润滑油)', full_text)
        
        is_liquid_cat = any(x in category.lower() for x in ["perfume", "fragrance", "essential oil", "liquid", "oils & fluids"])
        if liquid_match or is_liquid_cat:
            prohibited_items_found.append("液体 (Liquid)")
            risk_reasons.append("商品属于液体/精油/香水/喷雾形态，跨境物流及平台禁售或禁止航空运输。")
            suggestions.append("液体类商品无法通过跨境物流和 Makro 平台审核，请停止刊登。")

        # 2. 知名品牌与第三方配件侵权检测
        detected_brands = []
        for brand in FAMOUS_BRANDS:
            if re.search(rf'\b{brand}\b', full_text, re.IGNORECASE):
                detected_brands.append(brand.title())

        is_accessory = any(re.search(rf'\b{acc}\b', full_text, re.IGNORECASE) for acc in ACCESSORY_KEYWORDS)
        
        brand_infringement_info = {
            "detected_brands": list(set(detected_brands)),
            "is_accessory": is_accessory,
            "has_compatibility_notice": False,
            "recommended_title": None
        }

        if detected_brands:
            check_title = (makro_title or title).lower()
            has_compat = any(kw in check_title for kw in COMPATIBILITY_KEYWORDS)
            brand_infringement_info["has_compatibility_notice"] = has_compat

            target_brand_name = product_data.get("makro_brand") or "Beishi"
            first_brand = detected_brands[0]

            if is_accessory:
                if not has_compat:
                    risk_reasons.append(f"检测到涉及知名品牌 [{first_brand}] 的配件，但标题缺少【第三方配件适用于】声明，容易被判冒充官方配件导致侵权封店！")
                    clean_core_title = re.sub(rf'\b{first_brand}\b', '', title, flags=re.IGNORECASE).strip()
                    clean_core_title = re.sub(r'\s+', ' ', clean_core_title)
                    recommended = f"{target_brand_name} Third-Party {clean_core_title[:60]} Compatible with {first_brand}"
                    brand_infringement_info["recommended_title"] = recommended
                    suggestions.append(f"请将标题规范为第三方配件格式，例如：\"{recommended}\"")
            else:
                risk_reasons.append(f"检测到包含知名品牌 [{first_brand}]，疑似非配件商品直接销售知名品牌，存在直接商标侵权风险！")
                suggestions.append(f"若非官方授权店铺，严禁销售带有 [{first_brand}] 品牌的整机商品。")

        # 3. 首图 AI 多模态视觉检测
        image_inspection = {
            "tested": False,
            "image_url": None,
            "has_brand_logo": False,
            "is_prohibited": False,
            "logo_names": [],
            "summary": "未执行或无有效图片"
        }

        if check_image and raw_images and self.client:
            first_img_url = raw_images[0]
            image_inspection = self._inspect_image_with_vl(first_img_url, detected_brands)
            if image_inspection.get("is_prohibited"):
                prohibited_items_found.append("首图违禁特征 (视觉检出)")
                risk_reasons.append(f"首图视觉检测发现违禁特征: {image_inspection.get('summary')}")
            if image_inspection.get("has_brand_logo"):
                risk_reasons.append(f"首图检测到品牌 Logo/受限商标 [{', '.join(image_inspection.get('logo_names', []))}]: {image_inspection.get('summary')}")
                suggestions.append("建议替换首图为纯净白底商品图，抹除未授权的品牌 Logo 或商标。")

        # 4. 综合判定风险等级
        if prohibited_items_found:
            compliance_status = "PROHIBITED"
        elif risk_reasons:
            compliance_status = "RISK"
        else:
            compliance_status = "SAFE"

        return {
            "compliance_status": compliance_status,
            "prohibited_items": list(set(prohibited_items_found)),
            "brand_info": brand_infringement_info,
            "image_inspection": image_inspection,
            "risk_reasons": risk_reasons,
            "suggestions": suggestions
        }

    def _inspect_image_with_vl(self, image_url: str, text_detected_brands: List[str]) -> Dict[str, Any]:
        """使用 Qwen-VL-Max 视觉多模态大模型分析首图"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.takealot.com/"
            }
            resp = requests.get(image_url, headers=headers, timeout=12)
            if resp.status_code != 200:
                return {
                    "tested": False,
                    "image_url": image_url,
                    "summary": f"图片下载失败 (HTTP {resp.status_code})"
                }

            b64_data = base64.b64encode(resp.content).decode("utf-8")
            data_uri = f"data:image/jpeg;base64,{b64_data}"

            prompt = """你是一名资深电商商品合规与知识产权审核专家。请仔细分析这张电商商品首图，排查侵权与违禁风险：
1. 【知名品牌与商标 Logo】: 画面中是否有明显的知名品牌 Logo (如 Apple、Nike、Sony、Samsung、Dyson 等) 或受保护的官方商标 (如 Wi-Fi Alliance 标志、Bluetooth 标志)？若有，指出具体名称。
2. 【违禁品形态】: 商品本身是否属于液体、香水、精油瓶装，或明显带有危险化学品、武器特征？
3. 【结论与判定】: 
   - 品牌侵权风险: SAFE 或 RISK
   - 违禁品风险: SAFE 或 PROHIBITED

请必须且仅返回如下格式的 JSON 对象：
{
  "has_brand_logo": true/false,
  "detected_logos": ["识别到的Logo名称，无则为空列表"],
  "is_liquid_or_prohibited": true/false,
  "risk_level": "SAFE" 或 "RISK" 或 "PROHIBITED",
  "summary": "一句话中文总结首图检测发现"
}
"""
            response = self.client.chat.completions.create(
                model="qwen-vl-max",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}}
                        ]
                    }
                ],
                temperature=0.1
            )
            content = response.choices[0].message.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            parsed = json.loads(json_match.group()) if json_match else json.loads(content)

            return {
                "tested": True,
                "image_url": image_url,
                "has_brand_logo": parsed.get("has_brand_logo", False),
                "is_prohibited": parsed.get("is_liquid_or_prohibited", False),
                "logo_names": parsed.get("detected_logos", []),
                "image_risk_level": parsed.get("risk_level", "SAFE"),
                "summary": parsed.get("summary", "图片检测完成")
            }
        except Exception as e:
            logger.warning(f"首图视觉多模态检测调用异常: {e}")
            return {
                "tested": False,
                "image_url": image_url,
                "has_brand_logo": False,
                "is_prohibited": False,
                "logo_names": [],
                "summary": f"视觉模型检测异常: {str(e)[:80]}"
            }
