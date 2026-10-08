# -*- coding: utf-8 -*-
"""
设备型号提取、伪规格过滤、配件核心词净化与标题重构规则
"""

import re
import json
from typing import Any, Optional
from app.constants.brands import FAMOUS_BRANDS, ACCESSORY_KEYWORDS
from app.constants.makro_attrs import INVALID_SPEC_TOKENS


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


def clean_spec_value(val: Any) -> Optional[str]:
    """清洗与校验规格值 (排除空值与'多色'/'均码'等非服装伪词)"""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in INVALID_SPEC_TOKENS:
        return None
    return s


def is_pseudo_size(val: Any, vertical: str = "") -> bool:
    """
    检查是否属于非服装类目的纯数字/长宽高伪规格 (如手机壳或数码配件中混入的 15.8, 6.1 等物理尺寸)
    此类数字在配件中买家并不认同为可选尺码，注入标题会导致混淆 (如 Pink, 15.8)
    """
    if not val:
        return True
    s = str(val).strip().lower()
    # 纯浮点数或纯小数 (如 15.8, 6.1) 且不带明确容量/功率单位
    if re.match(r'^\d+\.\d+$', s):
        return True
    # 纯数字且类目是数码配件、手机保护套、滤网等非服装鞋帽类目
    non_apparel_cats = ["cases_covers", "usb", "cable", "protector", "filter", "holder", "mount", "charger", "electronic", "cellphone", "vacuum"]
    v_lower = str(vertical or "").lower()
    if any(k in v_lower for k in non_apparel_cats):
        if re.match(r'^\d+(\.\d+)?(\s*(?:cm|mm|m|inch|in|\"))?$', s):
            return True
    return False


def extract_device_model(raw_title: str, specs: Any = None, category: str = "") -> Optional[str]:
    """
    从 Takealot 原始规格参数与原始标题中智能提取精确目标设备型号 (如 Apple iPhone 11, Dyson V11, PS5 等)
    确保清洗后的标题 100% 保留具体适用代际型号，杜绝被抽象品牌 (如 Apple, Dyson) 覆盖。
    """
    # 1. 优先从 specs 字典中读取官方兼容性声明 (Takealot 官方参数最精准)
    if specs:
        specs_dict = {}
        if isinstance(specs, dict):
            specs_dict = specs
        elif isinstance(specs, str):
            try:
                specs_dict = json.loads(specs)
            except Exception:
                specs_dict = {}

        compat_keys = [
            "Cellphone Compatibility", "Compatibility", "Compatible with",
            "Compatible Model", "Model Compatibility", "Suitable for", "Compatible Brand"
        ]
        for k in compat_keys:
            val = specs_dict.get(k)
            if val and isinstance(val, str) and len(val.strip()) > 1:
                v = val.strip()
                if v.lower() not in ["universal", "generic", "all", "none", "n/a", "other"]:
                    m_sub = re.search(r'\b(iPhone\s+[0-9]{1,2}(?:\s*(?:Pro\s*Max|Pro|Plus|Mini))?|Galaxy\s+[S|A|Z|Note][0-9]{1,2}(?:\s*(?:Ultra|Plus|FE|\+))?|Dyson\s+V[0-9]{1,2}(?:\s*[A-Za-z0-9]+)?)\b', v, re.I)
                    if m_sub:
                        return m_sub.group(1).strip()
                    return v

    # 2. 从原始标题中使用正向精准正则匹配主流设备机型
    title_text = str(raw_title or "")

    # 2.1 苹果生态
    m_airtag = re.search(r'\b(?:Apple\s+)?AirTags?\b', title_text, re.I)
    if m_airtag:
        return "Apple AirTag"

    m_pencil = re.search(r'\b(?:Apple\s+)?Pencil(?:\s*(?:1st|2nd|USB-C|Pro))?\b', title_text, re.I)
    if m_pencil:
        p_name = m_pencil.group(0).strip()
        return p_name if p_name.lower().startswith("apple") else f"Apple {p_name}"

    m_iphone = re.search(r'\b(iPhone\s+(?:SE|[0-9]{1,2}(?:\s*(?:Pro\s*Max|Pro|Plus|Mini))?))\b', title_text, re.I)
    if m_iphone:
        return m_iphone.group(1).strip()

    m_ipad = re.search(r'\b(iPad\s+(?:Air|Pro|Mini|[0-9]{1,2}(?:th|st|nd|rd)?\s*Gen[a-z]*|[0-9]{1,2}\.?[0-9]?\s*inch)?)\b', title_text, re.I)
    if m_ipad:
        return m_ipad.group(1).strip()

    m_watch = re.search(r'\b(?:Apple\s+)?Watch\s*(?:Ultra|Series\s*[0-9]+|SE|[0-9]{2}mm)?\b', title_text, re.I)
    if m_watch:
        w_name = m_watch.group(0).strip()
        return w_name if w_name.lower().startswith("apple") else f"Apple {w_name}"

    m_airpods = re.search(r'\bAirPods\s*(?:Pro\s*[0-9]?|Max|[0-9]+)?\b', title_text, re.I)
    if m_airpods:
        return f"Apple {m_airpods.group(0).strip()}"

    m_mac = re.search(r'\b(MacBook\s*(?:Air|Pro)?|iMac)\b', title_text, re.I)
    if m_mac:
        return f"Apple {m_mac.group(1).strip()}"

    # 2.2 三星生态
    m_galaxy = re.search(r'\b(Galaxy\s+[S|A|Z|Note][0-9]{1,2}(?:\s*(?:Ultra|Plus|FE|\+))?)\b', title_text, re.I)
    if m_galaxy:
        return f"Samsung {m_galaxy.group(1).strip()}"

    # 2.3 戴森生态
    m_dyson = re.search(r'\b(Dyson\s+(?:V[0-9]{1,2}|SV[0-9]{2}|Gen5|Airwrap|Supersonic)(?:\s*(?:Absolute|Animal|Total\s*Clean|Motorhead|Slim))?)\b', title_text, re.I)
    if m_dyson:
        return m_dyson.group(1).strip()

    # 2.4 索尼 PlayStation
    m_sony = re.search(r'\b(?:Sony\s+)?(PlayStation\s*5(?:\s*Slim|\s*Pro)?|PS5(?:\s*Slim|\s*Pro)?|PlayStation\s*4(?:\s*Slim|\s*Pro)?|PS4(?:\s*Slim|\s*Pro)?)\b', title_text, re.I)
    if m_sony:
        matched_str = m_sony.group(1).strip()
        if "slim" in matched_str.lower():
            return "PlayStation 5 Slim"
        elif "pro" in matched_str.lower() and ("5" in matched_str or "ps5" in matched_str.lower()):
            return "PlayStation 5 Pro"
        elif "4" in matched_str or "ps4" in matched_str.lower():
            return "PlayStation 4"
        return "PlayStation 5"

    # 2.5 任天堂 Switch
    m_switch = re.search(r'\b(Nintendo\s+Switch(?:\s*OLED|\s*Lite)?)\b', title_text, re.I)
    if m_switch:
        return m_switch.group(1).strip()

    # 2.6 电子书阅读器 Kindle
    m_kindle = re.search(r'\b(?:Amazon\s+)?(Kindle(?:\s*(?:Paperwhite|Oasis|Basic|Scribe))?)\b', title_text, re.I)
    if m_kindle:
        return f"Amazon {m_kindle.group(1).strip()}"

    # 2.7 掌机 Steam Deck
    m_steam = re.search(r'\b(Steam\s*Deck(?:\s*OLED)?)\b', title_text, re.I)
    if m_steam:
        return f"Valve {m_steam.group(1).strip()}"

    # 2.8 微软 Xbox
    m_xbox = re.search(r'\b(?:Microsoft\s+)?(Xbox\s*(?:Series\s*[XS]|One(?:\s*[XS])?)?)\b', title_text, re.I)
    if m_xbox:
        return f"Microsoft {m_xbox.group(1).strip()}"

    # 3. 语法介词匹配提取
    m_prep = re.search(r'(?:compatible with|for|suitable for|replacement for|fits?)\s+([A-Za-z0-9\s\+\-\.\/]+?)(?:\s+(?:case|cover|holder|protector|screen|lens|with|and|\-|,|\())', title_text, re.I)
    if m_prep:
        candidate = m_prep.group(1).strip()
        if len(candidate) >= 3 and len(candidate) <= 30 and not any(k in candidate.lower() for k in ["men", "women", "kids", "home", "car", "quality"]):
            return candidate

    return None


def sanitize_accessory_core_name(
    core_text: str,
    target_brand: str = "Beishi",
    device_model: str = "",
    vertical: str = ""
) -> str:
    """
    清洗配件标题中置于 'Compatible with' 前面的核心品名 (Generic Product Noun)，
    坚决剔除任何第三方品牌词、型号词或专有名词，
    将专有配件词转换为通用中性名词 (如 AirTag Holder -> Tracker Holder, PS5 Console Bracket -> Console Mounting Bracket)，
    彻底杜绝因品牌置于兼容前缀前而被电商平台算法判定为侵权的风险。
    """
    if not core_text:
        return target_brand

    # 1. 临时剥离目标品牌前缀
    clean = re.sub(rf'^\s*{re.escape(target_brand)}\s*[-_:]*\s*', '', core_text, flags=re.I).strip()
    clean = re.sub(rf'\b{re.escape(target_brand)}\b', '', clean, flags=re.I).strip()

    # 2. 剥离可能存在的 "Third-Party" 前缀与多余的兼容前缀
    clean = re.sub(r'\bThird-Party\s+', '', clean, flags=re.I)
    clean = re.sub(r'\b(?:Compatible\s+with|Compatible\s+for|Suitable\s+for|Designed\s+for|Replacement\s+for)\b.*$', '', clean, flags=re.I).strip()

    # 3. 专有品牌词/设备名向通用中性品名转写
    PROPRIETARY_REPLACEMENTS = [
        # Apple AirTag
        (r'\b(?:Apple\s+)?AirTags?\s+Holders?\b', 'Tracker Holder'),
        (r'\b(?:Apple\s+)?AirTags?\s+Cases?\b', 'Tracker Case'),
        (r'\b(?:Apple\s+)?AirTags?\s+Covers?\b', 'Tracker Cover'),
        (r'\b(?:Apple\s+)?AirTags?\s+Mounts?\b', 'Tracker Mount'),
        (r'\b(?:Apple\s+)?AirTags?\s+Collars?\b', 'Collar with Tracker Holder'),
        (r'\b(?:Apple\s+)?AirTags?\b', 'Tracker'),

        # Apple MagSafe
        (r'\bMagSafe\s+Wallets?\b', 'Magnetic Wallet'),
        (r'\bMagSafe\s+Chargers?\b', 'Magnetic Charger'),
        (r'\bMagSafe\s+Cases?\b', 'Magnetic Case'),
        (r'\bMagSafe\s+Mounts?\b', 'Magnetic Mount'),
        (r'\bMagSafe\b', 'Magnetic'),

        # Apple Watch
        (r'\b(?:Apple\s+)?Watch\s+Bands?\b', 'Smartwatch Band'),
        (r'\b(?:Apple\s+)?Watch\s+Straps?\b', 'Smartwatch Strap'),
        (r'\b(?:Apple\s+)?Watch\s+Cases?\b', 'Smartwatch Case'),
        (r'\b(?:Apple\s+)?Watch\s+Protectors?\b', 'Smartwatch Screen Protector'),

        # AirPods
        (r'\bAirPods?\s+Cases?\b', 'Earphone Case'),
        (r'\bAirPods?\s+Covers?\b', 'Earphone Cover'),
        (r'\bAirPods?\b', 'Earphones'),

        # iPad
        (r'\biPads?\s+Cases?\b', 'Tablet Case'),
        (r'\biPads?\s+Covers?\b', 'Tablet Cover'),
        (r'\biPads?\s+Stands?\b', 'Tablet Stand'),
        (r'\biPads?\b', 'Tablet'),

        # iPhone
        (r'\biPhones?\s+Cases?\b', 'Phone Case'),
        (r'\biPhones?\s+Covers?\b', 'Phone Cover'),
        (r'\biPhones?\b', 'Phone'),

        # MacBook
        (r'\bMacBooks?\s+Cases?\b', 'Laptop Case'),
        (r'\bMacBooks?\s+Sleeves?\b', 'Laptop Sleeve'),
        (r'\bMacBooks?\b', 'Laptop'),

        # Apple Pencil
        (r'\b(?:Apple\s+)?Pencils?\s+Cases?\b', 'Stylus Case'),
        (r'\b(?:Apple\s+)?Pencils?\s+Holders?\b', 'Stylus Holder'),
        (r'\b(?:Apple\s+)?Pencils?\b', 'Stylus Pen'),

        # Sony PlayStation (PS5 / PS4)
        (r'\b(?:Sony\s+)?(?:PlayStation\s*[45]|PS[45])(?:\s*(?:Slim|Pro))?\s+Consoles?\b', 'Console'),
        (r'\b(?:Sony\s+)?(?:PlayStation\s*[45]|PS[45])(?:\s*(?:Slim|Pro))?\s+Controllers?\b', 'Controller'),
        (r'\b(?:Sony\s+)?(?:PlayStation\s*[45]|PS[45])(?:\s*(?:Slim|Pro))?\s+Gamepads?\b', 'Gamepad'),
        (r'\b(?:Sony\s+)?(?:PlayStation\s*[45]|PS[45])(?:\s*(?:Slim|Pro))?\b', ''),

        # Nintendo Switch
        (r'\b(?:Nintendo\s+)?Switch(?:\s*(?:OLED|Lite))?\s+Consoles?\b', 'Gaming Console'),
        (r'\b(?:Nintendo\s+)?Switch(?:\s*(?:OLED|Lite))?\s+Joy-?Cons?\b', 'Gaming Controller'),
        (r'\b(?:Nintendo\s+)?Switch(?:\s*(?:OLED|Lite))?\b', ''),

        # Microsoft Xbox
        (r'\b(?:Microsoft\s+)?Xbox(?:\s*(?:Series\s*[XS]|One))?\s+Consoles?\b', 'Gaming Console'),
        (r'\b(?:Microsoft\s+)?Xbox(?:\s*(?:Series\s*[XS]|One))?\s+Controllers?\b', 'Gaming Controller'),
        (r'\b(?:Microsoft\s+)?Xbox(?:\s*(?:Series\s*[XS]|One))?\b', ''),

        # Dyson
        (r'\bDyson\s+Vacuum\s+Cleaners?\b', 'Vacuum Cleaner'),
        (r'\bDyson\s+Vacuums?\b', 'Vacuum'),
        (r'\bDyson\s+Filters?\b', 'Vacuum Cleaner Filter'),
        (r'\bDyson\b', 'Vacuum'),

        # GoPro
        (r'\bGoPro\s+Cameras?\b', 'Action Camera'),
        (r'\bGoPro\b', 'Action Camera'),

        # DJI
        (r'\bDJI\s+Drones?\b', 'Drone'),
        (r'\bDJI\b', ''),
    ]

    for pat, repl in PROPRIETARY_REPLACEMENTS:
        clean = re.sub(pat, repl, clean, flags=re.I)

    # 4. 彻底剔除核心品名中的知名第三方品牌孤立词
    BRANDS_TO_REMOVE = [
        r'\bApple\b', r'\biPhone\b', r'\biPad\b', r'\bAirPods?\b', r'\bAirTags?\b', r'\bMacBook\b', r'\biMac\b', r'\bMagSafe\b',
        r'\bSony\b', r'\bPlayStation\b', r'\bPS[45]\b', r'\bPulse\s*3D\b',
        r'\bSamsung\b', r'\bGalaxy\b',
        r'\bDyson\b',
        r'\bNintendo\b', r'\bSwitch\b',
        r'\bXbox\b', r'\bMicrosoft\b',
        r'\bSteam\s*Deck\b',
        r'\bKindle\b', r'\bAmazon\b',
        r'\bGoPro\b', r'\bDJI\b',
        r'\bGarmin\b', r'\bFitbit\b',
        r'\bHuawei\b', r'\bXiaomi\b', r'\bRedmi\b',
        r'\bDell\b', r'\bHP\b', r'\bLenovo\b', r'\bAsus\b', r'\bAcer\b',
        r'\bBose\b', r'\bJBL\b', r'\bBeats\b',
        r'\bDeWalt\b', r'\bMakita\b', r'\bBosch\b', r'\bMilwaukee\b',
        r'\bStanley\b',
    ]
    for b_pat in BRANDS_TO_REMOVE:
        clean = re.sub(b_pat, '', clean, flags=re.I)

    # 5. 若已知具体的 device_model (例如 PlayStation 5 Slim 或 Apple AirTag)，从核心品名中剥离其零散单词
    if device_model:
        dev_words = re.split(r'[\s\-_]+', device_model)
        for w in dev_words:
            w_str = w.strip()
            if len(w_str) >= 2 and w_str.lower() not in ["for", "with", "and", "pro", "max", "plus", "mini", "case", "cover", "stand"]:
                clean = re.sub(rf'\b{re.escape(w_str)}\b', '', clean, flags=re.I)

    # 6. 清理残留悬挂介词与标点
    clean = re.sub(r'\s*[_:,/|]+\s*', ' ', clean)
    clean = re.sub(r'\s+-\s+', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip(' -_,:;')

    trailing_prep_regex = r'\b(?:for|with|to|of|and|in|on|at|by|from|compatible|suitable|fit|fits|designed)\s*$'
    while re.search(trailing_prep_regex, clean, re.I):
        clean = re.sub(trailing_prep_regex, '', clean, flags=re.I).strip(' -_,:;')

    # 7. 兜底保护：若核心品名被洗空或短于 3 字符，给予体面保底品名
    if len(clean) < 3:
        v_low = str(vertical or "").lower()
        if "holder" in v_low or "mount" in v_low:
            clean = "Mount Bracket Holder"
        elif "case" in v_low or "cover" in v_low:
            clean = "Protective Case Cover"
        elif "filter" in v_low:
            clean = "Replacement Filter"
        elif "stand" in v_low:
            clean = "Stand Bracket Holder"
        elif "band" in v_low or "strap" in v_low:
            clean = "Replacement Strap Band"
        else:
            clean = "Replacement Accessory"

    return f"{target_brand} {clean}".strip()


def reconstruct_accessory_title(
    makro_title: str,
    raw_title: str,
    target_brand: str = "Beishi",
    nature: str = "GENERIC_WHITE_LABEL",
    target_famous: str = "NONE",
    specs: Any = None,
    vertical: str = "",
    max_len: int = 120
) -> str:
    """
    智能重构配件兼容标题：
    1. 确保精确保留具体的设备型号 (如 iPhone 11、Dyson V11、Apple AirTag、PlayStation 5 Slim)，绝不被抽象品牌名覆盖；
    2. ★★★ 彻底清洗 Compatible with 之前的核心品名，严禁任何第三方品牌/商标出现在兼容句式之前 (防平台侵权)；
    3. 解决 'for ... Compatible with ...' 双重介词与语法冲突；
    4. 彻底废除机械暴力切片 [:60]，在全词边界安全截断。
    """
    full_text = f"{raw_title} {makro_title}".lower()
    matched_brand = next((b for b in FAMOUS_BRANDS if re.search(rf'\b{b}\b', full_text)), None)
    is_acc = (nature == "COMPATIBLE_ACCESSORY") or any(re.search(rf'\b{acc}\b', full_text) for acc in ACCESSORY_KEYWORDS)

    if not is_acc and not any(k in full_text for k in ["compatible with", "compatible for", "suitable for", "replacement for"]):
        return makro_title

    # 1. 优先提取具体的目标设备型号 (从 raw_title 或 makro_title)
    device_model = extract_device_model(raw_title, specs=specs, category=vertical) or extract_device_model(makro_title, specs=specs, category=vertical)

    target_ref = device_model or (target_famous if (target_famous and target_famous != "NONE") else (matched_brand or ""))
    if not target_ref and not any(k in full_text for k in ["compatible with", "compatible for", "suitable for"]):
        return makro_title

    target_display = (target_ref or "").strip()
    if target_display.isupper():
        target_display = target_display.title()

    title_curr = makro_title.strip()

    # 1.5 强力预清洗：清除生硬的 "Third-Party" 前缀与残留的截断词
    title_curr = re.sub(r'\bThird-Party\s+', '', title_curr, flags=re.I)
    title_curr = re.sub(r'\biPhon\b', 'iPhone', title_curr, flags=re.I)

    # 消除紧邻兼容句式前的重复 for 介词短语
    title_curr = re.sub(
        r'\s+for\s+[A-Za-z0-9\s]+(?=\s+(?:compatible\s+with|compatible\s+for|suitable\s+for|designed\s+for|fits?))',
        '',
        title_curr,
        flags=re.I
    )

    # 2. 检查是否已经包含合规兼容声明关键字
    m_comp = re.search(r'\b(compatible\s+with|compatible\s+for|replacement\s+for|suitable\s+for|designed\s+for)\s+([^()]+)', title_curr, re.I)

    abstract_brands = ["apple", "samsung", "dyson", "sony", "huawei", "xiaomi", "nintendo"]
    if m_comp:
        existing_prep = m_comp.group(1).strip()
        existing_target = m_comp.group(2).strip()
        et_lower = existing_target.lower()
        prefix_core = title_curr[:m_comp.start()].strip()
        suffix = title_curr[m_comp.end(2):]
        if suffix and not suffix.startswith(" "):
            suffix = " " + suffix

        # 检查是否已有优质具体机型
        is_generic_or_abbrev = (
            et_lower in abstract_brands or
            et_lower in ["generic", "universal", "phone", "tv", "console", "gamepad", "remote", "stylus", "watch", "airtag"] or
            len(et_lower) <= 2
        )

        if is_generic_or_abbrev and device_model:
            reconstructed_target = device_model
        else:
            reconstructed_target = existing_target

        cleaned_core = sanitize_accessory_core_name(prefix_core, target_brand=target_brand, device_model=device_model, vertical=vertical)
        final_constructed = f"{cleaned_core} {existing_prep} {reconstructed_target}{suffix}".strip()
        return truncate_title_safely(final_constructed, max_len=max_len)

    # 3. 尚未包含兼容句式 -> 结构化组装
    cleaned_core = sanitize_accessory_core_name(title_curr, target_brand=target_brand, device_model=device_model, vertical=vertical)

    # 提取末尾括号内的规格
    spec_suffix = ""
    m_bracket = re.search(r'(\s*[\(\[][^()\[\]]+[\)\]]\s*)$', cleaned_core)
    if m_bracket:
        spec_suffix = m_bracket.group(1)
        cleaned_core = cleaned_core[:m_bracket.start()].strip()

    final_constructed = f"{cleaned_core} Compatible with {target_display}{spec_suffix}".strip()
    return truncate_title_safely(final_constructed, max_len=max_len)


def format_title_with_specs(
    title: str,
    brand: str = "Beishi",
    color: Optional[str] = None,
    size: Optional[str] = None,
    max_len: int = 120,
    vertical: str = ""
) -> str:
    """
    将颜色与尺寸规范地以风格 B 括号格式 (Color, Size) 融入标题末尾，
    并保证品牌前缀正确与总长安全截断。
    自动识别并过滤非服装类目纯数字伪尺寸 (如手机壳 15.8cm)。
    """
    c_clean = clean_spec_value(color)
    s_clean = clean_spec_value(size)

    # 过滤非服装类目中出现的纯数字/长宽高尺寸 (如 15.8, 6.1)
    if s_clean and is_pseudo_size(s_clean, vertical=vertical):
        s_clean = None

    # 构造规格后缀 (Style B: 括号包裹)
    spec_part = ""
    if c_clean and s_clean:
        spec_part = f"({c_clean}, {s_clean})"
    elif c_clean:
        spec_part = f"({c_clean})"
    elif s_clean:
        spec_part = f"({s_clean})"

    raw_title = re.sub(r'\s+', ' ', str(title or "")).strip()
    # 检查末尾现有的括号规格如 (Black, XL) 或 (Multicolor)
    m = re.search(r'\s*[\(\[]([^()\[\]]+)[\)\]]\s*$', raw_title)
    if m:
        inner_spec = m.group(1).strip()
        # 如果即将注入新规格，或者现有括号本身是无效伪词 (如 Multicolor, 均码)，则剥离原括号
        if spec_part or clean_spec_value(inner_spec) is None:
            raw_title = raw_title[:m.start()].strip()

    if spec_part:
        # 移除末尾可能残留的连字符尾缀如 " - Black, XL"
        base_title = re.sub(r'\s+-\s+[A-Za-z0-9\s,/]+$', '', raw_title).strip()
        if not base_title:
            base_title = raw_title
    else:
        # 即使无 spec_part，也要清理 " - Multicolor" 这类无效连字符尾缀
        m_dash = re.search(r'\s+-\s+([A-Za-z0-9\s,/]+)$', raw_title)
        if m_dash and clean_spec_value(m_dash.group(1).strip()) is None:
            base_title = raw_title[:m_dash.start()].strip()
        else:
            base_title = raw_title

    brand_str = str(brand or "").strip()
    if brand_str and not base_title.lower().startswith(brand_str.lower()):
        base_title = f"{brand_str} {base_title}"

    if not spec_part:
        return truncate_title_safely(base_title, max_len)

    if spec_part.lower() in base_title.lower():
        return truncate_title_safely(base_title, max_len)

    allowed_base_len = max(40, max_len - len(spec_part) - 1)
    safe_base = truncate_title_safely(base_title, allowed_base_len)
    return f"{safe_base} {spec_part}".strip()
