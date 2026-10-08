# -*- coding: utf-8 -*-
"""
Makro 刊登发布服务：涵盖图片上传、草稿创建、412 自愈闭环、变体发布与多店铺状态流转
"""

import time
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models.product import Product, ProductVariant
from app.models.store import Store, ProductStoreListing
from app.services.makro_client import MakroClient
from app.services.vertical_service import VerticalService
from app.services.publisher.payload_builder import build_makro_payload, get_safe_fallback_vertical
from app.services.publisher.payload_healer import MakroPayloadHealer

logger = logging.getLogger(__name__)


def record_store_listing(
    db: Session,
    product_id: int,
    target_store: Any,
    is_success: bool,
    sku_id: Optional[str],
    request_id: Optional[str],
    msg: Optional[str],
    selling_price: Optional[float] = None,
    mrp: Optional[float] = None,
    brand: Optional[str] = None,
    user_id: Optional[int] = None
):
    """记录或更新商品在特定店铺的上架状态与凭据"""
    if not target_store:
        try:
            target_store = db.query(Store).filter(Store.is_default == True).first() or db.query(Store).first()
        except Exception:
            pass

    if not target_store or not hasattr(target_store, "id"):
        return

    try:
        listing = db.query(ProductStoreListing).filter(
            ProductStoreListing.product_id == product_id,
            ProductStoreListing.store_id == target_store.id
        ).first()
        if not listing:
            listing = ProductStoreListing(
                product_id=product_id,
                store_id=target_store.id
            )
            db.add(listing)

        if user_id:
            listing.user_id = user_id

        listing.brand = brand or getattr(target_store, "default_brand", None) or "Beishi"
        listing.status = "SUBMITTED" if is_success else "FAILED"
        if sku_id and (is_success or not listing.makro_sku_id):
            listing.makro_sku_id = sku_id
        if request_id:
            listing.makro_request_id = request_id
        listing.makro_submit_error = None if is_success else msg
        if selling_price is not None:
            listing.selling_price = selling_price
        if mrp is not None:
            listing.mrp = mrp
        listing.submitted_at = datetime.now()
        db.commit()
    except Exception as ex:
        logger.error(f"记录 ProductStoreListing 异常: {ex}", exc_info=True)


def publish_single_product(
    client: MakroClient,
    db: Session,
    product: Product,
    vertical: str,
    vid: Optional[str],
    brand: str,
    target_store: Optional[Any] = None
) -> dict:
    """为单个扁平独立商品创建草稿、上传图组并提交 Makro 发布 (含 500 毒瘤类目自愈降级与 412 自愈)"""
    try:
        draft_resp = client.create_draft(vertical=vertical, brand=brand, vid=vid)
    except Exception as draft_err:
        err_msg = str(draft_err)
        if "500" in err_msg or "Internal Server Error" in err_msg:
            fb_v, fb_vid = get_safe_fallback_vertical(vertical, product)
            logger.warning(f"商品 #{product.id} 在类目 [{vertical}] 创建草稿报 500 异常，启动自愈降级至安全类目 [{fb_v}] (vid: {fb_vid}) 重试...")
            draft_resp = client.create_draft(vertical=fb_v, brand=brand, vid=fb_vid)
            vertical = fb_v
            product.makro_vertical = fb_v
            db.commit()
        else:
            raise

    request_id = draft_resp.get("requestId")
    txn_id = draft_resp.get("txnId")
    req_id = draft_resp.get("reqId")

    if not request_id:
        raise ValueError(f"商品 {product.id} 创建草稿失败: {draft_resp}")

    product.makro_request_id = request_id

    # 上传专属图片
    raw_images = json.loads(product.raw_images) if product.raw_images else []
    images_map = {}
    for idx, img_url in enumerate(raw_images[:5]):
        cdn_url = client.upload_image_from_url(img_url, vertical, request_id)
        if cdn_url:
            images_map[str(len(images_map))] = cdn_url

    if not images_map:
        raise ValueError(f"商品 {product.id} 没有可用的有效图片上传")

    product.makro_images = json.dumps(images_map)

    # 组装 Payload 并提交
    payload = build_makro_payload(
        db, product, request_id, txn_id, req_id, images_map,
        client=client, draft_resp=draft_resp, target_store=target_store, vertical=vertical
    )
    is_success, err_details, msg = client.submit_product(payload)

    # 412 错误自愈闭环重试机制
    if not is_success:
        logger.warning(f"商品 {product.id} 初次提交审核未通过，尝试触发 412 自愈闭环...")
        allowed_attrs = {}
        try:
            v_def_list = VerticalService.get_vertical_definition(vertical, client=client, db=db)
            for item in v_def_list:
                name = item.get("attributeName")
                if name:
                    allowed_attrs[name] = item
        except Exception as e:
            logger.warning(f"自愈闭环获取类目元数据异常: {e}")

        if MakroPayloadHealer.auto_heal_payload(payload, err_details, allowed_attrs):
            logger.info(f"商品 {product.id} 成功应用 412 自愈补丁，发起二次提交重试...")
            time.sleep(1.0)
            is_success, err_details, msg = client.submit_product(payload)
            if is_success:
                logger.info(f"商品 {product.id} 二次重试提交成功！已自愈！")

    if is_success:
        product.status = "SUBMITTED"
        if not product.makro_sku_id:
            product.makro_sku_id = payload.get("skuId")
        product.makro_submit_error = None
    else:
        product.status = "FAILED"
        product.makro_submit_error = msg

    db.commit()

    # 写入多店铺独立 Listing 记录
    record_store_listing(
        db=db,
        product_id=product.id,
        target_store=target_store,
        is_success=is_success,
        sku_id=payload.get("skuId"),
        request_id=request_id,
        msg=msg,
        selling_price=product.makro_selling_price,
        mrp=product.makro_mrp,
        brand=brand,
        user_id=getattr(product, "user_id", None)
    )

    return {
        "product_id": product.id,
        "sku_id": product.sku_id or product.makro_sku_id,
        "success": is_success,
        "request_id": request_id,
        "message": msg,
        "error_details": err_details
    }


def publish_single_variant(
    client: MakroClient,
    db: Session,
    product: Product,
    variant: ProductVariant,
    vertical: str,
    vid: Optional[str],
    brand: str,
    target_store: Optional[Any] = None
) -> dict:
    """发布特定子变体 (含 500 自愈降级与 412 自愈)"""
    try:
        draft_resp = client.create_draft(vertical=vertical, brand=brand, vid=vid)
    except Exception as draft_err:
        err_msg = str(draft_err)
        if "500" in err_msg or "Internal Server Error" in err_msg:
            fb_v, fb_vid = get_safe_fallback_vertical(vertical, product)
            logger.warning(f"变体 {variant.sku_id} 在类目 [{vertical}] 创建草稿报 500 异常，启动自愈降级至安全类目 [{fb_v}] (vid: {fb_vid}) 重试...")
            draft_resp = client.create_draft(vertical=fb_v, brand=brand, vid=fb_vid)
            vertical = fb_v
            product.makro_vertical = fb_v
            db.commit()
        else:
            raise

    request_id = draft_resp.get("requestId")
    txn_id = draft_resp.get("txnId")
    req_id = draft_resp.get("reqId")

    if not request_id:
        raise ValueError(f"变体 {variant.sku_id} 创建草稿失败: {draft_resp}")

    variant.makro_request_id = request_id

    var_images = json.loads(variant.images) if variant.images else []
    if not var_images:
        var_images = json.loads(product.raw_images) if product.raw_images else []

    images_map = {}
    for idx, img_url in enumerate(var_images[:5]):
        cdn_url = client.upload_image_from_url(img_url, vertical, request_id)
        if cdn_url:
            images_map[str(len(images_map))] = cdn_url

    if not images_map:
        raise ValueError(f"变体 {variant.sku_id} 没有可用的有效图片上传")

    variant.makro_image_urls = json.dumps(images_map)

    payload = build_makro_payload(
        db, product, request_id, txn_id, req_id, images_map,
        client=client, draft_resp=draft_resp, variant=variant, target_store=target_store, vertical=vertical
    )
    is_success, err_details, msg = client.submit_product(payload)

    # 412 错误自愈闭环重试机制
    if not is_success:
        logger.warning(f"变体 {variant.sku_id} 初次提交审核未通过，尝试触发 412 自愈闭环...")
        allowed_attrs = {}
        try:
            v_def_list = VerticalService.get_vertical_definition(vertical, client=client, db=db)
            for item in v_def_list:
                name = item.get("attributeName")
                if name:
                    allowed_attrs[name] = item
        except Exception as e:
            logger.warning(f"自愈闭环获取类目元数据异常: {e}")

        if MakroPayloadHealer.auto_heal_payload(payload, err_details, allowed_attrs):
            logger.info(f"变体 {variant.sku_id} 成功应用 412 自愈补丁，发起二次提交重试...")
            time.sleep(1.0)
            is_success, err_details, msg = client.submit_product(payload)
            if is_success:
                logger.info(f"变体 {variant.sku_id} 二次重试提交成功！已自愈！")

    if is_success:
        variant.status = "SUBMITTED"
        if not variant.makro_sku_id:
            variant.makro_sku_id = payload.get("skuId")
        if not product.makro_sku_id:
            product.makro_sku_id = payload.get("skuId")
        variant.makro_submit_error = None
    else:
        variant.status = "FAILED"
        variant.makro_submit_error = msg

    db.commit()

    record_store_listing(
        db=db,
        product_id=product.id,
        target_store=target_store,
        is_success=is_success,
        sku_id=payload.get("skuId"),
        request_id=request_id,
        msg=msg,
        selling_price=variant.makro_selling_price or product.makro_selling_price,
        mrp=variant.makro_mrp or product.makro_mrp,
        brand=brand
    )

    return {
        "variant_id": variant.id,
        "sku_id": variant.sku_id,
        "success": is_success,
        "request_id": request_id,
        "message": msg,
        "error_details": err_details
    }
