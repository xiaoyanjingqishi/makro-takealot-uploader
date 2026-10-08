# -*- coding: utf-8 -*-
"""
Makro 412 错误自适应容错与 Payload 自愈修复器
"""

import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MakroPayloadHealer:
    """
    负责捕获并解析 Makro 卖家网关返回的 412 校验错误，
    根据类目官方属性元数据动态修补 Payload（纠正数值类型、补充缺失必填项、修剪非法属性等）
    """

    @staticmethod
    def auto_heal_payload(payload: dict, err_details: dict, allowed_attrs: dict) -> bool:
        cat_req = payload.get("catalogRequestEntity", {})
        catalog_attrs = cat_req.get("catalogAttributes", {})
        healed = False

        # 提取 CMS 系统属性校验错误
        attr_errs = err_details.get("catalogErrors", {}).get("systemValidationErrors", {}).get("attributeErrors", {})
        if not attr_errs:
            attr_errs = err_details.get("error", {}).get("errorDetails", {}).get("catalogErrors", {}).get("systemValidationErrors", {}).get("attributeErrors", {})

        for attr_name, err_list in (attr_errs or {}).items():
            if not err_list or not isinstance(err_list, list):
                continue
            err_str = str(err_list[0].get("errorString", ""))
            def_item = allowed_attrs.get(attr_name, {})
            allowed_vals = [x.strip() for x in (def_item.get("allowedValues") or "").split("||") if x.strip()]
            qual_vals = [x.strip() for x in (def_item.get("qualifierAllowedValues") or "").split("||") if x.strip()]
            def_qual = def_item.get("defaultQualifier") or (qual_vals[0] if qual_vals else None)
            ex_val = def_item.get("exampleValue")

            # 错误类型 A: 数值解析失败 (Unable to parse value as a DECIMAL / NUMBER)
            if "DECIMAL" in err_str or "NUMBER" in err_str:
                target_num = "1"
                if ex_val:
                    m = re.search(r'(\d+(?:\.\d+)?)', str(ex_val))
                    if m:
                        target_num = m.group(1)
                catalog_attrs[attr_name] = [{"value": target_num, "qualifier": def_qual}]
                logger.info(f"412 自愈修复: 属性 [{attr_name}] 修复为数值 {target_num}, qualifier={def_qual}")
                healed = True

            # 错误类型 B: 缺失必填项 (Mandatory Attribute [...] is missing)
            elif "missing" in err_str.lower() and "mandatory" in err_str.lower():
                val = allowed_vals[0] if allowed_vals else (ex_val or "Standard")
                catalog_attrs[attr_name] = [{"value": str(val), "qualifier": def_qual}]
                logger.info(f"412 自愈修复: 补齐必填属性 [{attr_name}] -> {val}")
                healed = True

            # 错误类型 C: 枚举值不合法 / 超纲
            elif "not allowed" in err_str.lower() or "disallowed" in err_str.lower():
                if allowed_vals:
                    catalog_attrs[attr_name] = [{"value": allowed_vals[0], "qualifier": def_qual}]
                    logger.info(f"412 自愈修复: 纠正超纲枚举 [{attr_name}] -> {allowed_vals[0]}")
                    healed = True

            # 错误类型 D: 品牌名污染 (Brand name should not be part of)
            elif "brand name should not be part" in err_str.lower():
                cur_list = catalog_attrs.get(attr_name, [])
                if cur_list:
                    cur_v = cur_list[0].get("value", "")
                    clean_v = re.sub(r'^[a-zA-Z0-9_\-]+\s*', '', str(cur_v)).strip()
                    catalog_attrs[attr_name] = [{"value": clean_v or "STD-01", "qualifier": None}]
                    logger.info(f"412 自愈修复: 剔除属性 [{attr_name}] 品牌词 -> {clean_v}")
                    healed = True

            # 错误类型 E: 类目不支持该属性 (Category ... has no attribute called [...])
            elif "has no attribute called" in err_str.lower():
                if attr_name in catalog_attrs:
                    del catalog_attrs[attr_name]
                    logger.info(f"412 自愈修复: 移除类目不支持的属性 [{attr_name}]")
                    healed = True

        return healed
