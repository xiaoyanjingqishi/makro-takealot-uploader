import math
from typing import Tuple
from ..models.setting import SystemSetting
from ..config import settings
from sqlalchemy.orm import Session

def get_pricing_rules(db: Session = None) -> Tuple[float, float, float]:
    """
    获取当前配置的加价系数、固定加价与MRP倍率
    """
    markup_ratio = settings.DEFAULT_MARKUP_RATIO
    fixed_markup = settings.DEFAULT_FIXED_MARKUP
    mrp_ratio = settings.DEFAULT_MRP_RATIO

    if db:
        s_markup = db.query(SystemSetting).filter(SystemSetting.key == "markup_ratio").first()
        s_fixed = db.query(SystemSetting).filter(SystemSetting.key == "fixed_markup").first()
        s_mrp = db.query(SystemSetting).filter(SystemSetting.key == "mrp_ratio").first()
        
        if s_markup and s_markup.value:
            try: markup_ratio = float(s_markup.value)
            except ValueError: pass
        if s_fixed and s_fixed.value:
            try: fixed_markup = float(s_fixed.value)
            except ValueError: pass
        if s_mrp and s_mrp.value:
            try: mrp_ratio = float(s_mrp.value)
            except ValueError: pass

    return markup_ratio, fixed_markup, mrp_ratio

def calculate_prices(takealot_price: float, db: Session = None) -> Tuple[int, int]:
    """
    根据 Takealot 兰特售价计算 Makro 售价与 MRP 划线原价
    返回: (selling_price, mrp) 均为正整数(符合 Makro POSITIVE_INTEGER 要求)
    """
    if not takealot_price or takealot_price <= 0:
        return 199, 299

    markup_ratio, fixed_markup, mrp_ratio = get_pricing_rules(db)
    
    # 售价 = 原价 * 1.35 + 20
    calc_selling = takealot_price * markup_ratio + fixed_markup
    # 向上取整或四舍五入为整数
    selling_price = max(1, int(round(calc_selling)))
    
    # MRP = 售价 * 1.5
    calc_mrp = selling_price * mrp_ratio
    mrp = max(selling_price, int(round(calc_mrp)))

    return selling_price, mrp
