import json
import logging
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

VERTICALS_FILE = Path(__file__).resolve().parent / "makro_verticals.json"

class VerticalService:
    _cache = None

    @classmethod
    def _load_verticals(cls):
        if cls._cache is not None:
            return cls._cache
        if VERTICALS_FILE.exists():
            try:
                with open(VERTICALS_FILE, "r", encoding="utf-8") as f:
                    cls._cache = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load makro_verticals.json: {e}")
                cls._cache = {}
        else:
            cls._cache = {}
        return cls._cache

    @classmethod
    def resolve_vertical(cls, vertical_name: str) -> Tuple[str, str]:
        """
        解析并验证垂直类目，返回 (valid_vertical_name, vid)
        例如:
          'plier' -> ('plier', '2109')
          'crimping_tool' -> ('plier', '2109')
          'bath_towel' -> ('bath_towel', '407')
        如果未完全匹配，执行别名映射与模糊匹配；兜底返回 ('plier', '2109') 或 ('bath_towel', '407')
        """
        verticals = cls._load_verticals()
        v_clean = (vertical_name or "").strip().lower().replace("-", "_").replace(" ", "_")

        # 0. 禁用或受限类目重定向 (Makro 平台部分类目对普通商家返回 500，需重定向至通用有效类目)
        RESTRICTED_MAP = {
            "smart_door_bell": "smart_switch_plug",
        }
        if v_clean in RESTRICTED_MAP:
            v_clean = RESTRICTED_MAP[v_clean]

        # 1. 直接精确匹配 verticalName
        if v_clean in verticals and verticals[v_clean]:
            vid = str(verticals[v_clean][0]["id"])
            return v_clean, vid

        # 2. 常见别名映射表 (Takealot/AI 常见预测 -> Makro 官方垂直类目)
        ALIASES = {
            "crimping_tool": "plier",
            "crimp_tool": "plier",
            "crimper": "plier",
            "wire_stripper": "plier",
            "pliers": "plier",
            "smart_switch": "smart_switch_plug",
            "smart_plug": "smart_switch_plug",
            "geyser_timer": "smart_switch_plug",
            "timer_switch": "smart_switch_plug",
            "smart_timer": "smart_switch_plug",
            "circuit_breaker": "smart_switch_plug",
            "breaker": "smart_switch_plug",
            "switch_plug": "smart_switch_plug",
            "towel": "bath_towel",
            "towels": "bath_towel",
            "screwdriver": "screwdriver_set",
            "wrench": "wrench_set",
            "knife": "knife_tool",
            "cable": "data_cable",
            "lan_cable": "data_cable",
            "ethernet_cable": "data_cable",
            "usb_cable": "data_cable",
            "charger": "battery_charger",
            "cellphone_case": "cases_covers",
            "phone_case": "cases_covers",
            "case": "cases_covers",
            "mobile_case": "cases_covers",
            "cases_covers": "cases_covers",
            "bra": "costume_wear",
            "women_bra": "costume_wear",
            "underwear": "costume_wear",
            "lingerie": "costume_wear",
            "wallet": "card_holder",
            "purse": "clutch",
            "card_holder": "card_holder",
            "usb_flash_drive": "usb_flash_drive",
            "flash_drive": "usb_flash_drive",
            "pendrive": "usb_flash_drive",
            "pen_drive": "usb_flash_drive",
            "headphone": "headphone",
            "earphone": "headphone",
            "mouse": "mouse",
            "keyboard": "keyboard",
            "backpack": "backpack",
            "pillow": "pillow",
            "bedsheet": "bedsheet"
        }

        if v_clean in ALIASES:
            target = ALIASES[v_clean]
            if target in verticals and verticals[target]:
                return target, str(verticals[target][0]["id"])

        # 3. 遍历垂直字典查找包含关键字的垂直类目
        keywords = v_clean.split("_")
        for kw in keywords:
            if len(kw) < 3:
                continue
            for name, items in verticals.items():
                if kw in name and items:
                    return name, str(items[0]["id"])

        # 4. 无法匹配时，根据是否含 tool 兜底
        if "tool" in v_clean:
            return "plier", "2109"

        # 默认兜底 bath_towel
        return "bath_towel", "407"

    @classmethod
    def get_all_vertical_names(cls) -> list:
        verticals = cls._load_verticals()
        return list(verticals.keys())

    @classmethod
    def predict_vertical(cls, title: str = "", category: str = "", specs: any = None, description: str = "") -> str:
        """
        基于标题、Takealot 类目树、规格参数与描述，智能推断最适合的 Makro 官方垂直类目 (Vertical)
        使用整词匹配 (\bword\b)，防止如 'bra' 误判 'brand' 等问题
        """
        import re
        text = f"{title} {category}".lower()
        if isinstance(specs, dict):
            text += " " + " ".join(f"{k} {v}" for k, v in specs.items()).lower()
        elif isinstance(specs, str):
            text += " " + specs.lower()
        if description:
            text += " " + description[:500].lower()

        def has_any(keywords):
            for k in keywords:
                if " " in k or "-" in k or "_" in k:
                    if k in text:
                        return True
                else:
                    if re.search(rf'\b{re.escape(k)}\b', text):
                        return True
            return False

        # 手机壳与保护套
        if has_any(["phone case", "clear case", "silicone case", "cover case", "protective cover", "phone cover", "cases & covers", "cases_covers"]):
            return "cases_covers"
        # 内衣/文胸/服饰 (整词匹配，彻底规避 brand)
        if has_any(["bra", "bras", "push-up", "wire-free", "brassiere", "lingerie", "underwear", "panties", "boxer", "briefs", "costume_wear"]):
            return "costume_wear"
        # 钱包/卡包
        if has_any(["wallet", "wallets", "purse", "card holder", "leather wallet", "bifold", "money clip", "card_holder"]):
            return "card_holder"
        # U盘/闪存
        if has_any(["usb flash", "flash drive", "pendrive", "pen drive", "thumb drive", "usb3.0", "usb2.0", "usb_flash_drive"]):
            return "usb_flash_drive"
        # 五金工具/钳子
        if has_any(["plier", "pliers", "crimper", "crimping", "wire stripper", "cutting plier", "hand tool", "hardware tool"]):
            return "plier"
        # 背包/书包
        if has_any(["backpack", "backpacks", "travel bag", "laptop bag", "schoolbag", "rucksack", "duffel bag"]):
            return "backpack"
        # 耳机
        if has_any(["headphone", "headphones", "earphone", "earphones", "earbuds", "headset", "tws"]):
            return "headphone"
        # 数据线
        if has_any(["data cable", "charging cable", "usb cable", "type-c cable", "lightning cable"]):
            return "data_cable"
        # 智能开关插座
        if has_any(["smart plug", "smart switch", "wifi plug", "socket plug", "power socket"]):
            return "smart_switch_plug"
        # 毛巾浴巾
        if has_any(["towel", "towels", "bath towel", "hand towel", "washcloth", "microfiber towel", "bath_towel"]):
            return "bath_towel"
        # 床品
        if has_any(["bedsheet", "bed sheet", "duvet cover", "fitted sheet", "pillowcase", "bedding"]):
            return "bedsheet"
        
        return "bath_towel"

