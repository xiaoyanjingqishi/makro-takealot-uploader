import math
from typing import Tuple, Dict, Any, Optional
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


def calculate_1688_pricing(
    purchase_price_cny: float,
    length_cm: float = 0.0,
    width_cm: float = 0.0,
    height_cm: float = 0.0,
    actual_weight_kg: float = 0.0,
    domestic_freight_cny: float = 8.0,
    first_leg_rate_cny: float = 95.0,
    volumetric_divisor: float = 6000.0,
    last_leg_base_zar: float = 70.0,
    last_leg_vat_rate: float = 0.15,
    exchange_rate: float = 0.40,  # 1 ZAR = 0.40 CNY
    commission_rate: float = 0.15,
    commission_vat_rate: float = 0.15,
    target_margin: float = 0.30
) -> Dict[str, Any]:
    """
    基于 1688 采购成本、实重材积、头程尾程、税费与目标毛利率的全链路精准定价计算器
    """
    # 1. 材积与计费重量
    volumetric_weight = round((length_cm * width_cm * height_cm) / volumetric_divisor, 3) if volumetric_divisor > 0 else 0.0
    chargeable_weight = max(round(actual_weight_kg, 3), volumetric_weight)

    # 2. 头程与国内运费 (CNY)
    first_leg_freight_cny = round(chargeable_weight * first_leg_rate_cny, 2)
    domestic_freight_cny = round(domestic_freight_cny, 2)

    # 3. 尾程运费 (含 15% VAT) 与折算
    last_leg_freight_zar = round(last_leg_base_zar * (1.0 + last_leg_vat_rate), 2)
    last_leg_freight_cny = round(last_leg_freight_zar * exchange_rate, 2)

    # 4. 商品总成本
    total_cost_cny = round(purchase_price_cny + domestic_freight_cny + first_leg_freight_cny + last_leg_freight_cny, 2)
    total_cost_zar = round(total_cost_cny / exchange_rate, 2) if exchange_rate > 0 else 0.0

    # 5. 平台综合扣点率 (佣金 15% + 佣金增值税 15% = 17.25%)
    platform_fee_ratio = round(commission_rate * (1.0 + commission_vat_rate), 4)

    # 6. 定价系数与反推售价
    # 售价 * (1 - 平台扣点 - 目标毛利) = 总成本
    cost_ratio = 1.0 - platform_fee_ratio - target_margin
    if cost_ratio <= 0:
        cost_ratio = 0.01  # 防止除以零或负数

    calculated_selling_zar = total_cost_zar / cost_ratio
    selling_price_int = max(1, int(math.ceil(calculated_selling_zar)))

    # 心理定价建议 (例如 184 -> 189 或 199)
    mod10 = selling_price_int % 10
    if mod10 < 9:
        suggested_price_int = selling_price_int + (9 - mod10)
    else:
        suggested_price_int = selling_price_int

    # 7. 划线价 MRP (默认 1.5 倍取整)
    mrp_int = max(suggested_price_int, int(round(suggested_price_int * 1.5)))

    # 8. 财务毛利细算 (基于建议售价)
    platform_deduction_zar = round(suggested_price_int * platform_fee_ratio, 2)
    net_payout_zar = round(suggested_price_int - platform_deduction_zar, 2)
    gross_profit_zar = round(net_payout_zar - total_cost_zar, 2)
    gross_profit_cny = round(gross_profit_zar * exchange_rate, 2)
    actual_margin = round((gross_profit_zar / suggested_price_int) * 100, 2) if suggested_price_int > 0 else 0.0

    # 9. 智能风控与选品提示
    warnings = []
    if volumetric_weight > actual_weight_kg * 1.5 and volumetric_weight > 0.3:
        warnings.append(f"⚠️ 轻泡货预警：材积重({volumetric_weight}kg)显著大于实重({actual_weight_kg}kg)，头程运费占比达 {round((first_leg_freight_cny / total_cost_cny) * 100, 1)}%！建议压缩包装。")
    if purchase_price_cny < 12:
        warnings.append(f"⚠️ 低客单价预警：尾程固定成本({last_leg_freight_zar}兰特/约{last_leg_freight_cny}元)占总成本高达 {round((last_leg_freight_cny / total_cost_cny) * 100, 1)}%！建议以 3件套/5件套打包销售。")
    if chargeable_weight >= 1.0:
        warnings.append("⚠️ 大件包裹预警：计费重量 ≥ 1.0kg，头程物流成本较高，请评估商品溢价能力。")

    return {
        "params": {
            "purchase_price_cny": purchase_price_cny,
            "domestic_freight_cny": domestic_freight_cny,
            "actual_weight_kg": actual_weight_kg,
            "volumetric_weight_kg": volumetric_weight,
            "chargeable_weight_kg": chargeable_weight,
            "first_leg_rate_cny": first_leg_rate_cny,
            "last_leg_base_zar": last_leg_base_zar,
            "last_leg_vat_rate": last_leg_vat_rate,
            "exchange_rate": exchange_rate,
            "commission_rate": commission_rate,
            "commission_vat_rate": commission_vat_rate,
            "platform_fee_ratio": platform_fee_ratio,
            "target_margin": target_margin
        },
        "costs": {
            "first_leg_freight_cny": first_leg_freight_cny,
            "last_leg_freight_zar": last_leg_freight_zar,
            "last_leg_freight_cny": last_leg_freight_cny,
            "total_cost_cny": total_cost_cny,
            "total_cost_zar": total_cost_zar
        },
        "pricing": {
            "calculated_selling_zar": round(calculated_selling_zar, 2),
            "selling_price_int": selling_price_int,
            "suggested_price_int": suggested_price_int,
            "mrp_int": mrp_int,
            "multiplier_factor": round(total_cost_cny * 4.7393, 2)
        },
        "financials": {
            "platform_deduction_zar": platform_deduction_zar,
            "net_payout_zar": net_payout_zar,
            "gross_profit_zar": gross_profit_zar,
            "gross_profit_cny": gross_profit_cny,
            "actual_margin_percent": actual_margin
        },
        "warnings": warnings
    }

