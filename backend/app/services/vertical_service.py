import json
import logging
import re
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

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
          'brassiere' -> ('costume_wear', '7682')
          'underwear' -> ('costume_wear', '7682')
          'glasses' -> ('protective_glasses', '7787')
          'gardening_tool' -> ('garden_tools', '8396')
          'bath_towel' -> ('bath_towel', '407')
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
            # === 派对变装 / 戏服 (仅限明确 Cosplay/Party 变装，严禁普通日常服饰错挂) ===
            "costume": "costume_wear",
            "costumes": "costume_wear",
            "costume_wear": "costume_wear",
            "cosplay": "costume_wear",
            "fancy_dress": "costume_wear",

            # === 帽子 / 围巾 / 护颈 / 冬季穿戴 ===
            "cap": "cap",
            "caps": "cap",
            "hat": "cap",
            "hats": "cap",
            "beanie": "cap",
            "scarf": "cap",
            "scarves": "cap",
            "neck_warmer": "cap",
            "neck_gaiter": "cap",
            "snood": "cap",
            "balaclava": "cap",

            # === 手套 / 雨衣 ===
            "glove": "glove",
            "gloves": "glove",
            "raincoat": "raincoat",

            # === 照明 / 手电筒 / 头灯 ===
            "torch": "torch",
            "headlamp": "torch",
            "head_lamp": "torch",
            "headlight": "torch",
            "flashlight": "torch",
            "lantern": "torch",
            "work_light": "torch",

            # === 足部护理 / 矫形足垫 / 足套 ===
            "foot_pad": "foot_pad",
            "foot_sock": "foot_pad",
            "foot_socks": "foot_pad",
            "heel_sock": "foot_pad",
            "heel_socks": "foot_pad",
            "heel_protector": "foot_pad",
            "foot_sleeve": "foot_pad",
            "plantar_fasciitis": "foot_pad",
            "neuropathy_socks": "foot_pad",
            "insole": "foot_pad",
            "arch_support": "foot_pad",

            # === 手表 / 计时器 ===
            "watch": "watch",
            "smart_watch": "watch",
            "wrist_watch": "watch",

            # === 工具刀 / 美工刀 ===
            "knife": "knife_tool",
            "utility_knife": "knife_tool",
            "knife_tool": "knife_tool",

            # === 眼镜 / 护目镜 ===
            "glasses": "protective_glasses",
            "sunglasses": "protective_glasses",
            "eyewear": "protective_glasses",
            "spectacles": "protective_glasses",
            "anti_blue_glasses": "protective_glasses",
            "reading_glasses": "protective_glasses",
            "protective_glasses": "protective_glasses",

            # === 园艺工具 / 修枝剪 ===
            "garden_tool": "garden_tools",
            "garden_tools": "garden_tools",
            "gardening_tool": "garden_tools",
            "gardening_tools": "garden_tools",
            "pruner": "pruner",
            "pruners": "pruner",
            "pruning_shear": "pruner",
            "pruning_shears": "pruner",
            "shear": "hedge_shear",
            "shears": "hedge_shear",
            "hedge_shear": "hedge_shear",
            "hedge_trimmer": "hedge_shear",

            # === 五金与手工工具 ===
            "crimping_tool": "plier",
            "crimp_tool": "plier",
            "crimper": "plier",
            "wire_stripper": "plier",
            "pliers": "plier",
            "plier": "plier",
            "hand_tool": "plier",
            "hardware_tool": "plier",
            "screwdriver": "screwdriver_set",
            "screwdriver_set": "screwdriver_set",
            "wrench": "wrench_set",
            "wrench_set": "wrench_set",
            "knife": "knife_tool",
            "knife_tool": "knife_tool",

            # === 瓶罐 / 分装器 / 日用家居 ===
            "liquid_dispenser": "liquid_dispenser",
            "dispenser": "liquid_dispenser",
            "fluid_dispenser": "liquid_dispenser",
            "soap_dispenser": "liquid_dispenser",
            "water_bottle": "water_bottle",
            "bottle": "bottle",
            "bottles": "bottle",
            "container": "container",
            "storage_box": "container",

            # === 智能设备与电工 ===
            "smart_switch": "smart_switch_plug",
            "smart_plug": "smart_switch_plug",
            "geyser_timer": "smart_switch_plug",
            "timer_switch": "smart_switch_plug",
            "smart_timer": "smart_switch_plug",
            "circuit_breaker": "smart_switch_plug",
            "breaker": "smart_switch_plug",
            "switch_plug": "smart_switch_plug",
            "smart_switch_plug": "smart_switch_plug",

            # === 数码配件 ===
            "cable": "data_cable",
            "data_cable": "data_cable",
            "lan_cable": "data_cable",
            "ethernet_cable": "data_cable",
            "usb_cable": "data_cable",
            "charging_cable": "data_cable",
            "charger": "battery_charger",
            "battery_charger": "battery_charger",
            "cellphone_case": "cases_covers",
            "phone_case": "cases_covers",
            "case": "cases_covers",
            "mobile_case": "cases_covers",
            "cases_covers": "cases_covers",
            "usb_flash_drive": "usb_flash_drive",
            "flash_drive": "usb_flash_drive",
            "pendrive": "usb_flash_drive",
            "pen_drive": "usb_flash_drive",
            "headphone": "headphone",
            "headphones": "headphone",
            "earphone": "headphone",
            "earphones": "headphone",
            "mouse": "mouse",
            "keyboard": "keyboard",

            # === 箱包皮具 ===
            "wallet": "card_holder",
            "purse": "clutch",
            "card_holder": "card_holder",
            "backpack": "backpack",

            # === 毛巾与床品 ===
            "towel": "bath_towel",
            "towels": "bath_towel",
            "bath_towel": "bath_towel",
            "washcloth": "bath_towel",
            "pillow": "pillow",
            "bedsheet": "bedsheet",
            "bed_sheet": "bedsheet"
        }

        if v_clean in ALIASES:
            target = ALIASES[v_clean]
            if target in verticals and verticals[target]:
                return target, str(verticals[target][0]["id"])

        # 3. 遍历垂直字典查找分词完全匹配的垂直类目 (下划线分词精准比对，防止 "bra" 误中 "brake")
        keywords = [k for k in v_clean.split("_") if len(k) >= 3]
        for kw in keywords:
            for name, items in verticals.items():
                name_tokens = name.split("_")
                if kw in name_tokens and items:
                    return name, str(items[0]["id"])

        # 4. 语义智能识别兜底 (规避特殊受限类目)
        # 手电筒/头灯/户外照明
        if any(k in v_clean for k in ["torch", "headlamp", "flashlight", "lantern", "lighting"]):
            return "torch", "2287"
        # 足部护理/足垫/足套/筋膜炎
        if any(k in v_clean for k in ["foot", "heel", "fasciitis", "insole"]):
            return "foot_pad", "7808"
        # 帽子/围巾/护颈
        if any(k in v_clean for k in ["cap", "hat", "beanie", "scarf", "warmer", "gaiter"]):
            return "cap", "1209"
        # 手套
        if any(k in v_clean for k in ["glove", "mitten"]):
            return "glove", "4244"
        # 眼镜
        if any(k in v_clean for k in ["glass", "spectacle", "eyewear"]):
            return "protective_glasses", "7787"
        # 园艺
        if any(k in v_clean for k in ["garden", "prun", "shear", "lopper"]):
            return "garden_tools", "8396"
        # 五金工具
        if any(k in v_clean for k in ["tool", "plier", "wrench", "screw"]):
            return "plier", "2109"
        # 手表
        if any(k in v_clean for k in ["watch", "timepiece"]):
            return "watch", "5009"
        # 工具刀
        if any(k in v_clean for k in ["knife", "blade", "cutter"]):
            return "knife_tool", "865"
        # 保护套/数码壳
        if any(k in v_clean for k in ["case", "cover", "protector"]):
            return "cases_covers", "1148"
        # 分装/瓶罐
        if any(k in v_clean for k in ["dispens", "pump"]):
            return "liquid_dispenser", "2539"
        if "bottle" in v_clean:
            return "bottle", "647"
        # 毛巾真实匹配
        if any(k in v_clean for k in ["towel", "bath"]):
            return "bath_towel", "407"
        # 仅限真正的派对戏服变装道具
        if any(k in v_clean for k in ["costume", "cosplay", "fancy_dress"]):
            return "costume_wear", "7682"

        # 终极安全兜底：数码与日用保护类目 (允许自定义标题，杜绝 Spider Man 戏服标题生成)
        return "cases_covers", "1148"

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

        # 1. 眼镜 / 防蓝光镜 / 太阳镜
        if has_any(["glasses", "sunglasses", "spectacles", "anti-blue", "anti blue", "eyewear", "reading glasses", "protective glasses", "lens", "frames", "blue light"]):
            return "protective_glasses"

        # 2. 照明 / 手电筒 / 头灯 / 露营灯
        if has_any(["headlamp", "head lamp", "headlight", "torch", "flashlight", "lantern", "spotlight", "led light", "work light", "lumens"]):
            return "torch"

        # 3. 足部护理 / 矫形足垫 / 足套 / 筋膜炎套
        if has_any(["foot pad", "foot socks", "heel sock", "heel protector", "plantar fasciitis", "neuropathy", "foot sleeve", "compression foot", "insole", "gel pad", "foot support", "arch support", "silicone foot"]):
            return "foot_pad"

        # 4. 帽子 / 围巾 / 护颈套 / 户外头巾
        if has_any(["cap", "caps", "hat", "hats", "beanie", "scarf", "scarves", "neck warmer", "neck gaiter", "balaclava", "snood"]):
            return "cap"

        # 5. 手套
        if has_any(["glove", "gloves", "mitten", "mittens"]):
            return "glove"

        # 6. 雨衣
        if has_any(["raincoat", "rain coat", "rain poncho", "poncho"]):
            return "raincoat"

        # 7. 手表 / 腕表
        if has_any(["watch", "watches", "wrist watch", "smart watch", "digital watch"]):
            return "watch"

        # 8. 工具刀 / 美工刀
        if has_any(["utility knife", "pocket knife", "folding knife", "box cutter", "blade knife", "fixed blade"]):
            return "knife_tool"

        # 9. 园艺修剪工具
        if has_any(["gardening tool", "gardening tools", "garden tool", "pruner", "pruners", "pruning", "shear", "shears", "hedge", "lopper", "trowel", "secateurs"]):
            return "garden_tools"

        # 10. 分装器 / 液体瓶
        if has_any(["fluid dispenser", "liquid dispenser", "soap dispenser", "dispenser bottle", "pump dispenser", "reusable fluid"]):
            return "liquid_dispenser"

        # 11. 水杯 / 水壶
        if has_any(["water bottle", "drink bottle", "flask", "tumbler"]):
            return "water_bottle"

        # 12. 手机壳与保护套
        if has_any(["phone case", "clear case", "silicone case", "cover case", "protective cover", "phone cover", "cases & covers", "cases_covers"]):
            return "cases_covers"

        # 13. 钱包/卡包
        if has_any(["wallet", "wallets", "purse", "card holder", "leather wallet", "bifold", "money clip", "card_holder"]):
            return "card_holder"

        # 14. U盘/闪存
        if has_any(["usb flash", "flash drive", "pendrive", "pen drive", "thumb drive", "usb3.0", "usb2.0", "usb_flash_drive"]):
            return "usb_flash_drive"

        # 15. 五金工具/钳子
        if has_any(["plier", "pliers", "crimper", "crimping", "wire stripper", "cutting plier", "hand tool", "hardware tool"]):
            return "plier"

        # 16. 背包/书包
        if has_any(["backpack", "backpacks", "travel bag", "laptop bag", "schoolbag", "rucksack", "duffel bag"]):
            return "backpack"

        # 17. 耳机
        if has_any(["headphone", "headphones", "earphone", "earphones", "earbuds", "headset", "tws"]):
            return "headphone"

        # 18. 数据线
        if has_any(["data cable", "charging cable", "usb cable", "type-c cable", "lightning cable"]):
            return "data_cable"

        # 19. 智能开关插座
        if has_any(["smart plug", "smart switch", "wifi plug", "socket plug", "power socket"]):
            return "smart_switch_plug"

        # 20. 毛巾浴巾 (必须有明确毛巾关键词才匹配)
        if has_any(["towel", "towels", "bath towel", "hand towel", "washcloth", "microfiber towel", "bath_towel"]):
            return "bath_towel"

        # 21. 床品
        if has_any(["bedsheet", "bed sheet", "duvet cover", "fitted sheet", "pillowcase", "bedding"]):
            return "bedsheet"

        # 22. 真正的变装/万圣节/派对道具服 (仅限明确变装，严禁日常服装内衣误入)
        if has_any(["cosplay", "fancy dress", "halloween costume", "party costume", "carnival costume"]):
            return "costume_wear"

        # 默认安全通用配件 (允许自定义标题，杜绝 Spider Man 戏服标题生成)
        return "cases_covers"

    @classmethod
    def get_candidate_verticals(cls, title: str = "", category: str = "", specs: any = None, description: str = "", max_candidates: int = 35) -> list:
        """
        基于商品标题、类目路径、规格与描述，动态检索并组装最相关的 Makro 官方垂直类目候选集。
        所有候选代码严格来自于 makro_verticals.json。
        """
        verticals = cls._load_verticals()
        candidates = []

        # 1. 核心高频通用官方类目（确保主流品类均有明确选项）
        CORE_CANONICAL = [
            "protective_glasses",  # 眼镜 / 太阳镜 / 防蓝光眼镜 / 护目镜
            "torch",               # 手电筒 / 头灯 / 户外照明
            "foot_pad",            # 足垫 / 矫形垫 / 足部护理
            "cap",                 # 帽子 / 围巾 / 护颈
            "glove",               # 手套
            "raincoat",            # 雨衣
            "garden_tools",        # 园艺工具 / 修枝剪 / 高枝剪 / 铲
            "pruner",              # 修枝剪
            "plier",               # 五金钳子 / 压线钳 / 剥线钳 / 工具
            "screwdriver_set",     # 螺丝刀 / 批头套装
            "wrench_set",          # 扳手 / 套筒
            "knife_tool",          # 工具刀 / 美工刀
            "cases_covers",        # 手机壳 / 平板保护套
            "liquid_dispenser",    # 洗手液机 / 皂液器 / 液体分装泵
            "water_bottle",        # 水杯 / 运动水壶 / 保温杯
            "bottle",              # 瓶子 / 分装瓶
            "container",           # 储物盒 / 收纳盒
            "card_holder",         # 钱包 / 卡包 / 证件夹
            "backpack",            # 双肩背包 / 旅行包
            "clutch",              # 手包 / 手拿包
            "bath_towel",          # 毛巾 / 浴巾
            "pillow",              # 枕头 / 靠垫
            "bedsheet",            # 床单 / 被套 / 床品
            "data_cable",          # 数据线 / 充电线
            "battery_charger",     # 充电器 / 适配器
            "smart_switch_plug",   # 智能开关 / 定时插座
            "usb_flash_drive",     # U盘 / 闪存盘
            "headphone",           # 耳机 / 耳麦
            "mouse",               # 鼠标
            "keyboard",            # 键盘
            "watch",               # 手表
        ]

        # 2. 预测的首选类目优先加入
        pred = cls.predict_vertical(title, category, specs, description)
        if pred and pred in verticals and pred not in candidates:
            candidates.append(pred)

        # 3. 提取商品核心业务名词关键词（跳过通用修饰词与停用词）
        full_text = f"{title} {category}".lower()
        if isinstance(specs, dict):
            full_text += " " + " ".join(f"{k} {v}" for k, v in specs.items()).lower()
        elif isinstance(specs, str):
            full_text += " " + specs.lower()
        if description:
            full_text += " " + description[:300].lower()

        stop_words = {
            "the", "and", "for", "with", "this", "that", "from", "pack", "size", "color",
            "black", "white", "blue", "red", "free", "best", "high", "quality", "home",
            "shop", "online", "full", "back", "side", "wide", "heavy", "duty", "light",
            "anti", "tree", "accessories", "compatible", "standard", "wireless", "portable",
            "multi", "piece", "mini", "type", "universal", "ultra", "pro", "plus", "set",
            "new", "easy", "design", "cover", "safe", "fast", "speed", "power", "steel"
        }
        words = set(re.findall(r'[a-z]{4,}', full_text))
        filtered_words = [w for w in words if w not in stop_words]

        # 匹配库中包含这些核心名词的 vertical 代码
        matched_from_db = []
        for word in filtered_words:
            for v_name in verticals.keys():
                if word in v_name:
                    if v_name not in candidates and v_name not in matched_from_db:
                        matched_from_db.append(v_name)

        # 4. 组合结果：预测首选 -> 动态关键词匹配项 -> 核心高频兜底项
        for m in matched_from_db:
            if len(candidates) >= max_candidates:
                break
            candidates.append(m)

        for core in CORE_CANONICAL:
            if core in verticals and core not in candidates:
                candidates.append(core)

        return candidates

    @classmethod
    def get_vertical_definition(cls, vertical: str, client=None, db=None) -> list:
        """获取并本地缓存垂直类目的官方属性定义列表 (definitionList)"""
        valid_v, _ = cls.resolve_vertical(vertical)
        cache_dir = Path(__file__).resolve().parent.parent / "cache" / "vertical_defs"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{valid_v}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"读取本地类目缓存 {valid_v} 失败: {e}")

        # 尝试通过 client 或 db 获取并写缓存
        try:
            if not client and db:
                from .makro_client import MakroClient
                client = MakroClient.from_db(db)
            if client:
                vdef = client.get_vertical_definition(valid_v)
                deflist = vdef.get("entityDefinitionMap", {}).get(valid_v, {}).get("definitionList", [])
                if deflist:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(deflist, f, ensure_ascii=False, indent=2)
                    return deflist
        except Exception as e:
            logger.warning(f"从平台获取类目 {valid_v} 元数据失败: {e}")

        return []

    @classmethod
    def get_vertical_schema_summary(cls, vertical: str, client=None, db=None) -> Dict[str, Any]:
        """
        解析并输出供大模型与强类型契约引擎消费的高可读 Schema 描述
        包含字段类型约束、允许枚举、限定符与官方示例
        """
        deflist = cls.get_vertical_definition(vertical, client=client, db=db)
        mandatory_items = []
        recommended_items = []
        guidelines_lines = []
        allowed_attr_names = set()

        for d in deflist:
            source = d.get("source")
            if source != "CATALOG":
                continue
            name = d.get("attributeName")
            if not name:
                continue

            allowed_attr_names.add(name)
            prio = (d.get("attributePriority") or "").lower()
            atype = (d.get("attributeType") or "TEXT").upper()
            allowed = [x.strip() for x in (d.get("allowedValues") or "").split("||") if x.strip()]
            quals = [x.strip() for x in (d.get("qualifierAllowedValues") or "").split("||") if x.strip()]
            ex = d.get("exampleValue")
            desc = d.get("attributeDescription") or ""

            entry = {
                "name": name,
                "display_name": d.get("attributeDisplayName") or name,
                "priority": prio,
                "type": atype,
                "allowedValues": allowed,
                "qualifiers": quals,
                "example": ex,
                "description": desc
            }

            if prio == "mandatory":
                mandatory_items.append(entry)
                rule_str = f"- `{name}`: [必填] 数据类型: {atype}。"
                if atype in ["DECIMAL", "NUMBER", "INTEGER"]:
                    rule_str += " 【重要规则: 必须为纯数字（如 10、28），严禁填入任何中文、‘均码’或非数字字符！】"
                if quals:
                    rule_str += f" 必须从单位列表挑选 qualifier: {quals}。"
                if allowed:
                    rule_str += f" 必须从候选枚举挑选: {allowed[:10]}。"
                if ex:
                    rule_str += f" (官方示例: {ex})"
                guidelines_lines.append(rule_str)
            elif name in ["model_name", "material", "colour", "brand_colour", "warranty_summary", "description", "sales_package", "pack_of"]:
                recommended_items.append(entry)
                rule_str = f"- `{name}`: [推荐] 数据类型: {atype}。"
                if allowed:
                    rule_str += f" 候选枚举: {allowed[:8]}。"
                if quals:
                    rule_str += f" 候选单位: {quals}。"
                guidelines_lines.append(rule_str)

        return {
            "vertical": vertical,
            "mandatory": mandatory_items,
            "recommended": recommended_items,
            "allowed_names": list(allowed_attr_names),
            "guidelines_text": "\n".join(guidelines_lines)
        }

