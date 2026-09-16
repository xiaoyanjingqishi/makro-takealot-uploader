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
