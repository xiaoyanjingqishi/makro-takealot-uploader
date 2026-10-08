# -*- coding: utf-8 -*-
"""
Makro (Flipkart SaaS) 平台属性常量与黑白名单
"""

# 纯 Listing 级属性集合 (严禁混入 Catalog 请求体，否则会触发 412 错误)
LISTING_ONLY_ATTRS = {
    "country_of_origin", "mrp", "flipkart_selling_price", "shipping_days",
    "listing_status", "service_profile", "packer_details", "manufacturer_details",
    "importer_details", "packages", "forbid_shipping", "max_order_quantity_allowed",
    "minimum_order_quantity", "sku_id"
}

# 常见无效规格过滤词
INVALID_SPEC_TOKENS = {
    "多色", "multicolor", "multi-color", "various", "default", "none", "null",
    "均码", "free size", "onesize", "one size", "n/a"
}
