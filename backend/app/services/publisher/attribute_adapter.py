# -*- coding: utf-8 -*-
"""
Makro 官方类目属性定义适配器：解析并规范化属性值与单位限定符 (Qualifier)
"""

import re
from typing import Any, Optional, Tuple


def format_attribute_value_and_qualifier(
    attr_name: str,
    raw_val: Any,
    raw_qual: Optional[str],
    def_item: Optional[dict],
    brand: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    根据 Makro 官方类目属性定义，自动解析并规范化属性值与单位限定符 (Qualifier)
    """
    val_str = str(raw_val).strip() if raw_val is not None else ""
    qual = raw_qual

    # 1. 严防品牌名污染 model_number 与 model_name (Makro 官方限制: Brand name should not be part of the attribute value)
    if attr_name in ["model_number", "model_name"] and brand:
        val_str = re.sub(rf'^\s*{re.escape(brand)}\s*[-_:]*\s*', '', val_str, flags=re.I)
        val_str = re.sub(rf'\b{re.escape(brand)}\b', '', val_str, flags=re.I).strip(' -_,:;')
        if not val_str:
            val_str = "STD-01"

    if not def_item:
        return val_str, qual

    qual_vals = [x.strip() for x in (def_item.get("qualifierAllowedValues") or "").split("||") if x.strip()]
    default_qual = def_item.get("defaultQualifier") or (qual_vals[0] if qual_vals else None)
    allowed_vals = [x.strip() for x in (def_item.get("allowedValues") or "").split("||") if x.strip()]
    attr_type = (def_item.get("attributeType") or "TEXT").upper()

    # 通用数值类型强校验与转换 (DECIMAL / NUMBER / INTEGER / POSITIVE_INTEGER)
    if attr_type in ["DECIMAL", "NUMBER", "INTEGER", "POSITIVE_INTEGER"]:
        is_valid_num = False
        try:
            f_val = float(val_str)
            is_valid_num = True
            if attr_type in ["INTEGER", "POSITIVE_INTEGER", "NUMBER"] and f_val.is_integer():
                val_str = str(int(f_val))
            else:
                val_str = str(int(f_val)) if f_val.is_integer() else str(f_val)
        except (ValueError, TypeError):
            is_valid_num = False

        if not is_valid_num:
            # 尝试正则从文本中提取第 1 个数值
            num_match = re.search(r'(\d+(?:\.\d+)?)', val_str)
            if num_match:
                extracted = float(num_match.group(1))
                val_str = str(int(extracted)) if extracted.is_integer() else str(extracted)
            else:
                ex_val = def_item.get("exampleValue")
                ex_num = re.search(r'(\d+(?:\.\d+)?)', str(ex_val or ''))
                if ex_num:
                    val_str = ex_num.group(1)
                else:
                    val_str = "1"

        # 强制挂载合法单位限定符
        if qual_vals:
            if not qual or qual not in qual_vals:
                qual = default_qual or qual_vals[0]

        return val_str, qual

    # 通用布尔类型强校验与转换 (BOOLEAN)
    if attr_type == "BOOLEAN":
        val_lower = val_str.lower()
        if val_lower in ["yes", "true", "1", "y", "是", "有"]:
            val_str = "Yes"
        elif val_lower in ["no", "false", "0", "n", "否", "无"]:
            val_str = "No"
        else:
            val_str = "Yes" if "yes" in [v.lower() for v in allowed_vals] else (allowed_vals[0] if allowed_vals else "Yes")
        return val_str, None

    # 2. 处理容量/存储相关字段
    if attr_name in ["storage_capacity", "capacity", "internal_storage", "ram", "memory"]:
        m = re.search(r'([\d\.]+)\s*([a-zA-Z]+)?', val_str)
        if m:
            num_val = float(m.group(1))
            unit = (m.group(2) or "").upper()

            if unit in ["TB", "T"]:
                qual = "TB" if "TB" in qual_vals else default_qual
                val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
            elif unit in ["GB", "G"]:
                if num_val >= 1000 and "TB" in qual_vals:
                    qual = "TB"
                    tb_val = num_val / 1000.0
                    val_str = str(int(tb_val)) if tb_val.is_integer() else str(tb_val)
                else:
                    qual = "GB" if "GB" in qual_vals else default_qual
                    val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
            elif unit in ["MB", "M"]:
                qual = "MB" if "MB" in qual_vals else default_qual
                val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
            else:
                qual = default_qual
                val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
        elif qual_vals and not qual:
            qual = default_qual

        if allowed_vals and val_str not in allowed_vals:
            matched = next((av for av in allowed_vals if av == val_str), allowed_vals[0])
            val_str = matched

        return val_str, qual

    # 3. 处理尺寸长度
    if attr_name in ["overall_length", "width", "length", "height", "depth"]:
        if not qual or qual not in qual_vals:
            qual = next((q for q in qual_vals if q.lower() == "cm"), default_qual or "cm")
        return val_str, qual

    # 4. 处理重量
    if attr_name in ["weight"]:
        if not qual or qual not in qual_vals:
            qual = next((q for q in qual_vals if q.lower() in ["g", "kg"]), default_qual or "g")
        return val_str, qual

    # 5. 处理速度
    if attr_name in ["speed", "read_speed", "write_speed"]:
        if not qual or qual not in qual_vals:
            qual = next((q for q in qual_vals if "mbps" in q.lower() or "mb/s" in q.lower()), default_qual)
        return val_str, qual

    # 6. 通用 Qualifier 强制匹配
    if qual_vals:
        if not qual or qual not in qual_vals:
            qual = default_qual or qual_vals[0]

    # 7. 通用枚举值 AllowedValues 匹配
    if allowed_vals and val_str not in allowed_vals:
        matched = next((av for av in allowed_vals if val_str.lower() in av.lower() or av.lower() in val_str.lower()), allowed_vals[0])
        val_str = matched

    return val_str, qual
