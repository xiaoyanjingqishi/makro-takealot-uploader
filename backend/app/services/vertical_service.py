import json
import logging
import re
from pathlib import Path
from collections import defaultdict
from typing import Tuple, Optional, Dict, Any, List, Set

logger = logging.getLogger(__name__)

VERTICALS_FILE = Path(__file__).resolve().parent / "makro_verticals.json"

class VerticalSemanticRetriever:
    """
    本地超高速语义相关度检索引擎 (方案 3 + 方案 2)
    - 针对 Makro 全部 1,639 个官方类目构建倒排索引 (Path Tokens + DisplayName Tokens + VerticalName Tokens + Synonyms)
    - 0 外部 API 依赖，0 费用，纯本地内存运算 (< 2ms)
    - 自动过滤不可用父节点 (如 garden_tools/8396 等)
    - 支持同义词泛化扩充 (雕像/标识牌 -> garden_gnome, 喷灌 -> garden_sprayer, 浴室扶手 -> shower_grab_bar 等)
    """
    _instance = None

    STOP_WORDS = {
        'and', 'or', 'the', 'a', 'an', 'in', 'on', 'for', 'with', 'to', 'of', 'at', 'by', 'from',
        'set', 'cm', 'mm', 'inch', 'piece', 'pcs', 'pack', 'size', 'color', 'black', 'white', 'blue',
        'red', 'free', 'best', 'high', 'quality', 'new', 'easy', 'design', 'pro', 'plus', 'standard',
        'materials', 'material', 'basic', 'colours', 'limited', 'months', 'warranty', 'categories'
    }

    BLACKLIST_VERTICALS = {
        "vehicle_seat_cover",
        "coffeemachine_accessories",
        "garden_tools",
        "smart_door_bell",
        "string_trimmer",
        "security_software",
        "camera_kit",
        "built_in_braai",
        "extender_adaptors",
        "office_software",
        "electric_food_dehydrator",
        "cider",
        "premix",
        "hobby_machines",
        "hobby_accessories",
        "hobby_consumables",
    }

    CATEGORY_SYNONYMS = {
        "art_craft_kit": ["clay beads", "polymer clay", "bracelet making kit", "jewelry making kit", "bead kit", "diy craft", "craft kit", "loom kit", "diamond painting", "embroidery kit", "mosaic kit", "craft supplies", "hobby consumables", "hobby accessories"],
        "sewing_kit": ["knitting loom", "knitting needles", "crochet hooks", "sewing tools", "needlework", "thread kit", "blanket loom", "crochet loom", "knitting set"],
        "garden_gnome": ["sculpture", "statue", "lawn ornament", "garden decor", "yard sign", "garden sign", "lawn sign", "boundary marker", "keep off the grass", "lawn figurine", "patio ornament", "metal stake", "garden stake", "courtyard decor", "sculptures", "statues"],
        "garden_sprayer": ["sprinkler", "water nozzle", "spray nozzle", "hose sprayer", "irrigation", "lawn sprinkler", "impact water", "garden hose nozzle", "watering wand", "misting nozzle"],
        "garden_tool_set": ["gardening tools", "pruning tool", "pruning shear", "hedge shear", "hedge trimmer", "trowel", "rake", "spade", "shovel", "gardening set", "bypass pruner", "secateurs"],
        "shower_grab_bar": ["safety grab bar", "grab rail", "handicap bar", "shower handle", "bathroom handrail", "toilet support rail", "anti slip handle", "safety handle"],
        "faucet_extension": ["faucet aerator", "tap adapter", "faucet extender", "sink sprayer", "water tap nozzle", "water bubbler", "faucet extension"],
        "mobile_holder": ["phone holder", "phone stand", "ring holder", "ring grip", "phone mount", "cell phone holder", "mobile stand", "car phone mount", "finger ring stand", "magnetic phone holder"],
        "car_door_bumper_guard": ["door edge protector", "door bumper guard", "car door guard", "anti collision strip", "edge protector", "crash protector"],
        "pet_leash_chain": ["dog leash", "pet harness", "dog collar", "pet collar", "lead rope", "retractable leash", "dog chest strap", "pet strap"],
        "voltage_protector": ["surge protector", "voltage regulator", "appliance protector", "refrigerator protector", "plug in surge", "power guard"],
        "cases_covers": ["phone case", "clear case", "silicone cover", "protective case", "tablet cover", "shockproof case", "phone skin", "phone back cover"],
        "protective_glasses": ["safety glasses", "sunglasses", "anti blue light", "eyewear", "spectacles", "reading glasses", "protective eyewear", "goggles"],
        "torch": ["headlamp", "flashlight", "lantern", "spotlight", "led work light", "tactical torch", "head lamp", "rechargeable torch", "camping lamp"],
        "foot_pad": ["heel protector", "plantar fasciitis", "foot sleeve", "arch support", "silicone insole", "compression sock", "heel pad", "gel insole", "foot brace"],
        "smart_switch_plug": ["smart socket", "wifi switch", "timer switch", "tuya plug", "remote switch plug", "smart wall plug", "smart breaker"],
        "plier": ["crimper", "wire stripper", "cutting plier", "needle nose", "crimping tool", "hand tool", "multitool plier", "pliers"],
        "axe": ["deli axe", "hatchet", "splitting axe", "fiberglass axe", "wood chopping axe", "camping axe", "hand axe"],
        "water_bottle": ["drink tumbler", "thermos flask", "sports bottle", "insulated mug", "shaker bottle", "vacuum cup", "stainless steel tumbler", "hydration flask"],
        "bath_towel": ["bath towel", "microfiber towel", "shower towel", "hand towel", "washcloth", "beach towel", "body towel"],
        "knife_tool": ["utility knife", "box cutter", "folding knife", "pocket knife", "retractable blade", "hobby knife", "craft knife"],
        "usb_flash_drive": ["usb flash", "pen drive", "thumb drive", "flash disk", "memory stick", "jump drive", "usb drive"],
        "data_cable": ["charging cable", "usb cable", "type c cable", "lightning cable", "fast charge cable", "sync cord"],
        "headphone": ["earbuds", "earphone", "headset", "tws", "wireless earbuds", "bluetooth earphone", "in ear headphones"],
        "backpack": ["travel bag", "school bag", "laptop backpack", "rucksack", "daypack", "hiking pack"],
        "card_holder": ["wallet", "purse", "money clip", "rfid card case", "bifold wallet", "credit card holder"],
        "bedsheet": ["bed sheet", "fitted sheet", "duvet cover", "pillowcase", "quilt cover", "bedding sheet"],
        "screwdriver_set": ["screwdriver set", "precision screwdriver", "magnetic bit set", "torx bit", "screw driver"],
        "wrench_set": ["socket wrench", "ratchet wrench", "spanner set", "torque wrench", "hex key", "allen key"],
        "curtain_accessory": ["curtain tieback", "curtain rod", "curtain hook", "drapery holdback", "curtain clip"],
        "cleaning_cloth": ["nano cloth", "scratch remover cloth", "car cleaning cloth", "microfiber duster", "polishing rag"],
        "car_seat_belt": ["seat belt buckle", "seatbelt extension", "safety belt clip", "car seatbelt buckle"]
    }

    def __init__(self):
        self.index = defaultdict(list)
        self.vert_info = {}
        self._build_index()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        if not text:
            return []
        words = re.findall(r'[a-zA-Z0-9]+', str(text).lower())
        return [w for w in words if len(w) > 1 and w not in cls.STOP_WORDS]

    def _build_index(self):
        if not VERTICALS_FILE.exists():
            return
        try:
            with open(VERTICALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"VerticalSemanticRetriever 加载 verticals.json 异常: {e}")
            return

        for v_name, v_list in data.items():
            if not v_list or v_name in self.BLACKLIST_VERTICALS:
                continue
            item = v_list[0]
            disp = item.get("verticalDisplayName", "")
            path = item.get("path", "")
            name_tokens = self.tokenize(v_name)
            disp_tokens = self.tokenize(disp)
            path_tokens = self.tokenize(path)
            syn_tokens = []
            if v_name in self.CATEGORY_SYNONYMS:
                for s in self.CATEGORY_SYNONYMS[v_name]:
                    syn_tokens.extend(self.tokenize(s))

            all_toks = set(name_tokens + disp_tokens + path_tokens + syn_tokens)
            self.vert_info[v_name] = {
                "item": item,
                "name_tokens": set(name_tokens),
                "disp_tokens": set(disp_tokens),
                "path_tokens": set(path_tokens),
                "syn_tokens": set(syn_tokens)
            }
            for tok in all_toks:
                self.index[tok].append(v_name)

    @classmethod
    def search_relevant_verticals(cls, text: str, top_k: int = 45) -> List[str]:
        inst = cls.get_instance()
        q_toks = cls.tokenize(text)
        if not q_toks:
            return []

        scores = defaultdict(float)
        text_lower = text.lower()
        for tok in q_toks:
            for v_name in inst.index.get(tok, []):
                info = inst.vert_info.get(v_name)
                if not info:
                    continue
                weight = 1.0
                if tok in info["syn_tokens"]:
                    weight += 4.0
                if tok in info["name_tokens"]:
                    weight += 3.0
                if tok in info["disp_tokens"]:
                    weight += 2.0
                if tok in info["path_tokens"]:
                    weight += 1.5
                scores[v_name] += weight

        # 额外：如果类目完整名称作为子串出现在查询文本中，给予高额置顶奖励 (+6.0)
        for v_name in scores.keys():
            if v_name in text_lower:
                scores[v_name] += 6.0

        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [v for v, sc in sorted_items[:top_k]]

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
            # 园艺与工具
            "garden_tools": "garden_tool_set",
            "garden_tool": "garden_tool_set",
            "gardening_tools": "garden_tool_set",
            "gardening_tool": "garden_tool_set",
            "string_trimmer": "grass_trimmer",
            "built_in_braai": "braai_tool_set",

            # 智能安防与软件
            "smart_door_bell": "electric_door_bell",
            "security_software": "cases_covers",
            "office_software": "cases_covers",

            # 汽车与数码配件
            "vehicle_seat_cover": "vehicle_cover",
            "camera_kit": "dslr_camera",
            "extender_adaptors": "usb_adaptor",

            # 家电与厨房
            "coffeemachine_accessories": "coffee_maker",
            "electric_food_dehydrator": "food_processor",

            # 手工与兴趣耗材
            "hobby_consumables": "art_craft_kit",
            "hobby_accessories": "sewing_kit",
            "hobby_machines": "art_craft_kit",

            # 受限酒水
            "cider": "cases_covers",
            "premix": "cases_covers",
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

            # === 园艺装饰 / 雕塑 / 标识 ===
            "garden_gnome": "garden_gnome",
            "garden_decor": "garden_gnome",
            "garden_ornament": "garden_gnome",
            "garden_sculpture": "garden_gnome",
            "lawn_ornament": "garden_gnome",
            "lawn_sign": "garden_gnome",
            "garden_sign": "garden_gnome",

            # === 园艺工具 / 修枝剪 / 喷灌 ===
            "garden_tool": "garden_tool_set",
            "garden_tools": "garden_tool_set",
            "gardening_tool": "garden_tool_set",
            "gardening_tools": "garden_tool_set",
            "garden_tool_set": "garden_tool_set",
            "garden_sprayer": "garden_sprayer",
            "sprinkler": "garden_sprayer",
            "axe": "axe",
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
            "bed_sheet": "bedsheet",

            # === 手工 DIY / 串珠 / 编织 / 缝纫 ===
            "art_craft_kit": "art_craft_kit",
            "craft_kit": "art_craft_kit",
            "diy_kit": "art_craft_kit",
            "clay_beads": "art_craft_kit",
            "polymer_clay": "art_craft_kit",
            "bead_kit": "art_craft_kit",
            "beads_kit": "art_craft_kit",
            "jewelry_kit": "art_craft_kit",
            "bracelet_kit": "art_craft_kit",
            "sewing_kit": "sewing_kit",
            "knitting_loom": "sewing_kit",
            "crochet_loom": "sewing_kit",
            "loom_set": "sewing_kit",
            "knitting_set": "sewing_kit",
            "crochet_kit": "sewing_kit",
            "needlework": "sewing_kit",
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
        # 园艺装饰/草坪标志/雕塑/摆件
        if any(k in v_clean for k in ["gnome", "sculpture", "statue", "lawn ornament", "garden decor", "lawn sign", "garden sign", "ornament"]):
            return "garden_gnome", "1955"
        # 喷雾器/洒水/喷灌
        if any(k in v_clean for k in ["sprayer", "sprinkler", "irrigation"]):
            return "garden_sprayer", "1978"
        # 斧头
        if "axe" in v_clean:
            return "axe", "1966"
        # 园艺工具与套装
        if any(k in v_clean for k in ["garden", "prun", "shear", "lopper", "trowel", "rake", "spade"]):
            return "garden_tool_set", "1979"
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

        # 9. 园艺装饰/草坪标志/雕塑/摆件
        if has_any(["garden decor", "lawn ornament", "sculpture", "statue", "garden sign", "lawn sign", "gnome", "figurine", "sculptures & statues", "keep off the grass"]):
            return "garden_gnome"

        # 9b. 园艺修剪与种植工具/喷灌
        if has_any(["gardening tool", "gardening tools", "garden tool", "pruner", "pruners", "pruning", "shear", "shears", "hedge", "lopper", "trowel", "secateurs"]):
            return "garden_tool_set"
        if has_any(["sprayer", "sprinkler", "water nozzle", "hose spray"]):
            return "garden_sprayer"

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

        # 23. 手工 DIY / 串珠 / 编织 / 缝纫
        if has_any(["clay beads", "polymer clay", "bracelet making", "jewelry making", "beads kit", "bead kit", "beads", "craft kit", "art craft", "diamond painting", "diy craft", "mosaic kit"]):
            return "art_craft_kit"
        if has_any(["knitting loom", "knitting set", "crochet loom", "blanket loom", "sewing kit", "knitting needle", "crochet hook", "sewing set", "needlework"]):
            return "sewing_kit"

        # 默认安全通用配件 (允许自定义标题，杜绝 Spider Man 戏服标题生成)
        return "cases_covers"

    @classmethod
    def get_candidate_verticals(cls, title: str = "", category: str = "", specs: any = None, description: str = "", max_candidates: int = 65) -> list:
        """
        基于商品标题、类目路径、规格与描述，动态检索并组装最相关的 Makro 官方垂直类目候选集。
        三级漏斗：方案 2 规则直通 -> 方案 3 本地语义倒排检索 Top-50 -> 方案 1 候选扩充至 65 个。
        所有候选代码严格来自于 makro_verticals.json，并由 VerticalSemanticRetriever 自动过滤失效父节点。
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
            "art_craft_kit",       # 手工 DIY / 串珠制作 / 滴胶工艺
            "sewing_kit",          # 编织架 / 钩针编织 / 缝纫工具
            "garden_tool_set",     # 园艺工具套装 / 铲 / 耙
            "garden_gnome",        # 园艺雕塑 / 草坪摆件 / 标识牌 / 装饰
            "garden_sprayer",      # 园艺喷雾器 / 喷灌 / 洒水器
            "shower_grab_bar",     # 浴室扶手 / 安全把手
            "mobile_holder",       # 手机支架 / 指环支架
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
            "axe",                 # 斧头
        ]

        # 1. 方案 2: 预测的首选类目优先加入 (规则直通)
        pred = cls.predict_vertical(title, category, specs, description)
        if pred and pred in verticals and pred not in VerticalSemanticRetriever.BLACKLIST_VERTICALS:
            candidates.append(pred)

        # 2. 方案 3: 本地语义倒排检索引擎精准召回 Top-50
        full_text = f"{title} {category}"
        if isinstance(specs, dict):
            full_text += " " + " ".join(f"{k} {v}" for k, v in specs.items())
        elif isinstance(specs, str):
            full_text += " " + specs
        if description:
            full_text += " " + description[:400]

        semantic_matches = VerticalSemanticRetriever.search_relevant_verticals(full_text, top_k=50)
        for sm in semantic_matches:
            if sm in verticals and sm not in candidates and sm not in VerticalSemanticRetriever.BLACKLIST_VERTICALS:
                candidates.append(sm)
                if len(candidates) >= max_candidates:
                    break

        # 3. 方案 1: 核心高频通用官方类目保底扩充至 65 个
        for core in CORE_CANONICAL:
            if len(candidates) >= max_candidates:
                break
            if core in verticals and core not in candidates and core not in VerticalSemanticRetriever.BLACKLIST_VERTICALS:
                candidates.append(core)

        return candidates[:max_candidates]

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

