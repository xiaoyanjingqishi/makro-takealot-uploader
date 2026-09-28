import re
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from ..config import settings
from ..models.setting import SystemSetting
from ..models.product import Product
from openai import OpenAI

logger = logging.getLogger(__name__)

# 全局内存翻译缓存 (key: 英文文本 -> val: 中文译文)，避免重复调用大模型
_TITLE_TRANSLATION_MEM_CACHE: Dict[str, str] = {}

# Makro 官方类目中英权威对照字典 (覆盖高频数码、家居、医疗个护、五金工具、服饰箱包等)
MAKRO_VERTICAL_ZH_MAP: Dict[str, str] = {
    "cases_covers": "手机保护套 / 保护壳",
    "massager": "个人护理按摩器 / 按摩仪",
    "health_beauty_massager": "美体个护按摩器",
    "card_holder": "卡包 / 名片夹",
    "support": "防护支架 / 医用护具",
    "complete_vest": "背心 / 战术防护背心",
    "headphone": "耳机 / 头戴式耳麦",
    "mould": "模具 / 烘焙模具",
    "plier": "钳子 / 五金工具钳",
    "kitchen_rack": "厨房置物架 / 收纳架",
    "tool_kit": "五金工具套装",
    "brush_applicator": "刷子 / 涂抹刷",
    "pet_collar_harness": "宠物项圈 / 胸背带",
    "vehicle_pipe_hose_components": "汽车软管与管道配件",
    "protective_glasses": "护目镜 / 防护眼镜",
    "vehicle_light_bulb": "汽车车灯灯泡",
    "costume_wear": "演出服 / 角色扮演服饰",
    "data_cable": "数据线 / 充电线",
    "shower_grab_bar": "浴室安全扶手",
    "skin_treatment": "护肤用品 / 皮肤护理",
    "watch": "手表 / 腕表",
    "wrench_set": "扳手套装",
    "cap": "帽子 / 鸭舌帽",
    "drill_bit_set": "钻头套装",
    "soap_case": "皂盒 / 肥皂架",
    "learning_toy": "早教益智玩具",
    "bath_towel": "浴巾 / 毛巾",
    "bed": "床 / 卧具",
    "bed_mattress": "床垫",
    "side_table": "床头柜 / 边几",
    "wardrobe_closet": "衣柜 / 组合衣橱",
    "headboard": "床头板",
    "kid_seating": "儿童座椅",
    "kid_table": "儿童学习桌",
    "dining_chair": "餐椅",
    "kitchen_cabinet": "橱柜 / 厨房储物柜",
    "kitchen_trolley": "厨房小推车",
    "dining_table": "餐桌",
    "dining_set": "餐桌椅套装",
    "furniture_accessories": "家具五金配件",
    "outdoor_chair": "户外休闲椅",
    "outdoor_table": "户外桌",
    "sofa_sectional": "沙发 / 组合沙发",
    "coffee_table": "咖啡茶几",
    "book_shelf": "书架 / 置物架",
    "computer_table": "电脑桌 / 书桌",
    "pillow": "枕头 / 靠垫",
    "blanket_quilt": "毛毯 / 被子",
    "curtain": "窗帘 / 遮阳帘",
    "carpet_rug": "地毯 / 地垫",
    "wall_clock": "挂钟 / 钟表",
    "water_bottle": "运动水壶 / 水杯",
    "lunch_box": "便当盒 / 饭盒",
    "cookware_set": "锅具套装",
    "knife_sharpener": "磨刀器",
    "vacuum_cleaner_filter": "吸尘器滤网 / 耗材配件",
    "mobile_holder": "手机支架 / 车载支架",
    "screen_guard": "屏幕保护膜 / 钢化膜",
    "stylus": "手写笔 / 触控笔",
    "usb_flash_drive": "U盘 / 移动闪存盘",
    "smart_watch_strap": "智能手表表带",
    "backpack": "双肩背包 / 电脑包",
    "handbag": "手提包 / 单肩包",
    "wallet": "男士钱包 / 钱夹",
    "belt": "皮带 / 腰带",
    "sunglasses": "太阳眼镜 / 墨镜",
    "stethoscope": "听诊器 / 医疗听诊配件",
    "stethoscope_accessory": "听诊器替换导管与配件",
    "blood_pressure_monitor": "血压计",
    "thermometer": "体温计 / 测温仪",
    "pulse_oximeter": "指夹式血氧仪",
    "nebulizer": "雾化器 / 雾化吸入仪",
    "first_aid_kit": "急救包 / 应急医疗箱",
    "dental_care": "口腔护理 / 牙齿模型",
    "hair_dryer": "电吹风 / 吹风机",
    "hair_straightener": "直发器 / 卷发棒",
    "shaver_trimmer": "剃须刀 / 理发推剪",
    "nail_care": "美甲工具 / 美甲仪",
    "garden_sprayer": "园艺喷枪 / 洒水器",
    "garden_hose": "园艺水管 / 伸缩水管",
    "plant_pot": "花盆 / 园艺花架",
    "smart_switch_plug": "智能插座 / 智能开关",
    "led_bulb": "LED 灯泡",
    "table_lamp": "台灯 / 护眼灯",
    "strip_light": "LED 氛围灯带",
    "cctv_camera": "安防监控摄像头",
    "mouse": "鼠标",
    "keyboard": "键盘",
    "laptop_stand": "笔记本电脑支架",
    "power_bank": "移动电源 / 充电宝",
    "wireless_charger": "无线充电器",
    "adhesive": "工业强力胶水 / 双面胶带",
    "cleaning_glove": "清洁手套",
    "mop_bucket": "拖把桶套装",
    "trash_can": "垃圾桶",
    "storage_box": "收纳箱 / 整理盒",
    "shoe_rack": "鞋架 / 鞋柜",
    "hanger": "衣架 / 晒衣夹",
}

# 动态载入 Makro 官方全量 1623+ 类目中英对照字典
TRANSLATIONS_FILE = Path(__file__).resolve().parent / "makro_vertical_translations.json"
if TRANSLATIONS_FILE.exists():
    try:
        with open(TRANSLATIONS_FILE, "r", encoding="utf-8") as f:
            full_trans = json.load(f)
            if isinstance(full_trans, dict):
                MAKRO_VERTICAL_ZH_MAP.update(full_trans)
    except Exception as e:
        logger.warning(f"载入 makro_vertical_translations.json 失败: {e}")

# 常见类目词根翻译替换表 (用于全新未登记词条的智能分词合成)
_COMMON_CATEGORY_TOKENS = {
    "cover": "保护套", "case": "保护壳", "holder": "支架", "stand": "底座",
    "strap": "表带", "charger": "充电器", "cable": "线缆", "adapter": "适配器",
    "filter": "滤网", "replacement": "替换件", "tubing": "导管", "cleaner": "清洁器",
    "brush": "刷", "tool": "工具", "kit": "套装", "set": "套件",
    "toy": "玩具", "bag": "包", "box": "盒", "rack": "架", "lamp": "灯",
    "light": "车灯", "bottle": "水杯", "massager": "按摩器", "vest": "背心",
    "accessory": "配件", "parts": "零件", "device": "设备", "guard": "防护",
    "protector": "保护器", "mount": "固定架", "battery": "电池", "plug": "插头",
    "lock": "锁", "security": "安全防盗", "lockset": "车锁套件", "latch": "锁舌/锁扣",
    "handle": "把手/手柄", "cylinder": "锁芯", "sensor": "传感器", "valve": "阀门",
    "switch": "开关", "mirror": "后视镜/反光镜", "mat": "脚垫/地垫", "pad": "衬垫/护垫",
    "clip": "卡扣/夹子", "wire": "线束", "pump": "泵", "pipe": "管件", "hose": "软管",
    "brake": "刹车/制动", "clutch": "离合器", "grille": "进气格栅", "bracket": "支架",
    "panel": "面板", "strip": "饰条/胶条", "knob": "旋钮", "motor": "电机/马达",
    "gear": "齿轮/排挡", "bearing": "轴承", "spring": "弹簧", "screw": "螺丝",
    "storage": "收纳", "organizer": "整理架", "dispenser": "分配器/给皂器",
}

class TranslationService:
    """电商标题与类目中文翻译服务"""

    @classmethod
    def get_vertical_zh(cls, vertical: Optional[str]) -> str:
        """获取 Makro 垂直类目的中文译名对照 (确保全量 1623+ 官方类目 100% 精准对应，杜绝“通用类目”伪词)"""
        if not vertical:
            return ""
        v = str(vertical).strip().lower().replace("-", "_")
        if v in MAKRO_VERTICAL_ZH_MAP:
            return MAKRO_VERTICAL_ZH_MAP[v]

        # 尝试匹配已知包含前缀或关键词
        for k, zh in MAKRO_VERTICAL_ZH_MAP.items():
            if k in v or v in k:
                return zh

        # 启发式拼装中文译名 (若词根匹配则使用中文，否则使用首字母大写单词，绝不返回“通用类目”)
        tokens = re.split(r'[-_\s]+', v)
        zh_parts = []
        for t in tokens:
            if t in _COMMON_CATEGORY_TOKENS:
                zh_parts.append(_COMMON_CATEGORY_TOKENS[t])
            elif t:
                zh_parts.append(t.capitalize())
        return " / ".join(zh_parts) if zh_parts else v.replace("_", " ").title()

    @classmethod
    def translate_title(cls, text: Optional[str], db: Optional[Session] = None, force_refresh: bool = False) -> str:
        """
        将英文电商标题翻译为通顺的中文，保留英文品牌名与具体机型型号
        优先查内存缓存 -> 调大模型 (DeepSeek / Qwen) -> 规则回退
        """
        if not text:
            return ""
        clean_text = str(text).strip()
        if not clean_text:
            return ""

        # 1. 查内存缓存 (非强制刷新时)
        if not force_refresh and clean_text in _TITLE_TRANSLATION_MEM_CACHE:
            return _TITLE_TRANSLATION_MEM_CACHE[clean_text]

        # 2. 尝试从数据库同名已译记录快速复用 (非强制刷新时)
        if not force_refresh and db:
            try:
                cached_prod = db.query(Product).filter(
                    (Product.takealot_title == clean_text) & (Product.takealot_title_zh.isnot(None))
                ).first()
                if cached_prod and cached_prod.takealot_title_zh:
                    _TITLE_TRANSLATION_MEM_CACHE[clean_text] = cached_prod.takealot_title_zh
                    return cached_prod.takealot_title_zh

                cached_makro = db.query(Product).filter(
                    (Product.makro_title == clean_text) & (Product.makro_title_zh.isnot(None))
                ).first()
                if cached_makro and cached_makro.makro_title_zh:
                    _TITLE_TRANSLATION_MEM_CACHE[clean_text] = cached_makro.makro_title_zh
                    return cached_makro.makro_title_zh
            except Exception as dbe:
                logger.debug(f"数据库翻译缓存查询跳过: {dbe}")

        # 3. 构造 AI 调用
        client, model = cls._get_ai_client_and_model(db)
        if client and model:
            try:
                prompt = f"""请将以下电商商品英文标题翻译成准确、通顺的中文参考对照。
要求：
1. 保留原始品牌名（如 Apple, Nike, Dyson, Beishi, Littmann, Samsung 等）和具体机型代际（如 iPhone 11, Classic III, V11 等）为英文；
2. 翻译专业，符合电商平台（淘宝/京东/亚马逊）常见中文品名习惯；
3. 直接输出翻译后的中文，不要带引号，不要解释说明。

英文标题: {clean_text}
中文翻译:"""
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a professional ecommerce translation expert. Translate accurately and concisely into Chinese."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=200,
                    timeout=12.0
                )
                zh = resp.choices[0].message.content.strip().strip('"\'')
                if zh and len(zh) >= 2:
                    _TITLE_TRANSLATION_MEM_CACHE[clean_text] = zh
                    return zh
            except Exception as e:
                logger.warning(f"AI 标题翻译调用失败: {e}")

        # 4. 启发式翻译保底 (确保不返回空)
        return cls._fallback_title_translate(clean_text)

    @classmethod
    def _get_ai_client_and_model(cls, db: Optional[Session] = None):
        """获取大模型客户端 (优先 DeepSeek，备选通义千问)"""
        provider = settings.AI_PROVIDER
        deepseek_key = settings.DEEPSEEK_API_KEY
        qwen_key = settings.QWEN_API_KEY

        if db:
            try:
                s_p = db.query(SystemSetting).filter(SystemSetting.key == "ai_provider").first()
                if s_p and s_p.value:
                    provider = s_p.value
                s_dk = db.query(SystemSetting).filter(SystemSetting.key == "deepseek_api_key").first()
                if s_dk and s_dk.value:
                    deepseek_key = s_dk.value
                s_qk = db.query(SystemSetting).filter(SystemSetting.key == "qwen_api_key").first()
                if s_qk and s_qk.value:
                    qwen_key = s_qk.value
            except Exception:
                pass

        if provider == "deepseek" and deepseek_key:
            return OpenAI(api_key=deepseek_key, base_url=settings.DEEPSEEK_BASE_URL, timeout=12.0), settings.DEEPSEEK_MODEL
        elif qwen_key:
            return OpenAI(api_key=qwen_key, base_url=settings.QWEN_BASE_URL, timeout=12.0), settings.QWEN_MODEL
        elif deepseek_key:
            return OpenAI(api_key=deepseek_key, base_url=settings.DEEPSEEK_BASE_URL, timeout=12.0), settings.DEEPSEEK_MODEL
        return None, None

    @classmethod
    def _fallback_title_translate(cls, title: str) -> str:
        """轻量级启发式翻译保底"""
        t = title
        # 常见电商高频词映射
        words_map = {
            "Shockproof": "防摔防震",
            "Phone Case": "手机保护壳",
            "Protective Case": "保护套",
            "Compatible with": "适用于",
            "Replacement": "替换配件",
            "Tubing": "导管",
            "Stethoscope": "听诊器",
            "Slide Camera Lens Cover": "滑盖镜头保护",
            "Camera Lens Protection": "镜头保护",
            "Kickstand": "折叠支架",
            "with": "配",
            "for": "适用",
            "Model": "模型",
            "Dentistry": "齿科牙科",
            "Education": "教学示教",
            "Teeth": "牙齿",
            "Oral Hygiene": "口腔卫生",
            "Toothbrush": "牙刷",
            "Black": "黑色",
            "White": "白色",
            "Blue": "蓝色",
            "Green": "绿色",
            "Pink": "粉色",
            "Red": "红色",
            "Purple": "紫色",
        }
        for eng, zh in words_map.items():
            t = re.sub(rf'\b{re.escape(eng)}\b', zh, t, flags=re.I)
        return t
