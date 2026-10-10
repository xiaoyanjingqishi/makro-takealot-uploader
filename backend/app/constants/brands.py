# -*- coding: utf-8 -*-
"""
品牌、奢侈品与第三方兼容配件规则字典
"""

# 国际顶级奢侈品牌库 (侵权零容忍，严禁上架任何箱包、服饰、配饰)
LUXURY_BRANDS = [
    "chanel", "louis vuitton", "gucci", "hermes", "prada", "dior", "balenciaga",
    "fendi", "burberry", "celine", "bottega veneta", "saint laurent", "ysl",
    "versace", "cartier", "coach", "michael kors"
]

# 知名受保护品牌库 (包含数码科技、家电工具、运动潮流及顶级奢侈品)
FAMOUS_BRANDS = [
    "apple", "iphone", "ipad", "airpods", "airtag", "apple watch", "apple pencil", "macbook", "imac", "magsafe",
    "samsung", "galaxy", "dyson", "sony", "playstation", "playstation 5", "playstation 4", "ps4", "ps5", "ps5 slim", "ps5 pro",
    "nintendo switch", "nintendo", "steam deck", "xbox", "huawei", "xiaomi", "redmi", "dji", "gopro",
    "philips", "makita", "bosch", "dewalt", "milwaukee", "dell", "hp",
    "lenovo", "asus", "acer", "garmin", "fitbit", "bose", "jbl", "beats",
    "canon", "nikon", "sony alpha", "fujifilm", "olympus", "panasonic", "lumix", "pentax", "minolta", "leica", "hasselblad", "sigma", "tamron", "tokina", "insta360",
    "nike", "adidas", "lego", "stanley", "rolex", "crocs", "kindle",
    "chanel", "louis vuitton", "gucci", "hermes", "prada", "dior", "balenciaga",
    "fendi", "burberry", "celine", "bottega veneta", "saint laurent", "ysl",
    "versace", "cartier", "coach", "michael kors"
]

# 配件指示词
ACCESSORY_KEYWORDS = [
    "case", "cover", "strap", "band", "charger", "cable", "adapter",
    "replacement", "filter", "mount", "stand", "battery", "dock",
    "protector", "screen protector", "stylus", "shell", "ear tips",
    "pad", "blade", "holder", "pouch", "housing", "nozzle", "head",
    "sleeve", "bracket", "belt", "refill", "spares", "parts",
    "lens cap", "body cap", "rear cap", "cap", "hood", "filter ring", "adapter ring", "mount adapter"
]

# 第三方兼容声明合格词
COMPATIBILITY_KEYWORDS = [
    "third-party", "compatible with", "compatible for", "suitable for",
    "replacement for", "for use with", "designed for", "fits", "fit for"
]

RISK_LEVEL_ORDER = {"SAFE": 1, "RISK": 2, "PROHIBITED": 3}

def get_higher_risk(level_a: str, level_b: str) -> str:
    ra = RISK_LEVEL_ORDER.get(level_a, 1)
    rb = RISK_LEVEL_ORDER.get(level_b, 1)
    return level_a if ra >= rb else level_b
