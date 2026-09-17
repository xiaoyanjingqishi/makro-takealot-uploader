import re
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.product import Product, ProductVariant
from ..models.task import TaskLog
from ..schemas.product import (
    TakealotCollectRequest,
    ProductResponse,
    ProductUpdateRequest,
    ProductVariantResponse
)
from ..services.takealot_service import TakealotService
from ..services.audit_logger import record_audit_log

router = APIRouter(prefix="/products", tags=["商品管理"])

def _format_product(p: Product) -> dict:
    specs = json.loads(p.takealot_specs) if p.takealot_specs else {}
    raw_images = json.loads(p.raw_images) if p.raw_images else []
    cat_attrs = json.loads(p.makro_catalog_attributes) if p.makro_catalog_attributes else {}
    list_attrs = json.loads(p.makro_listing_attributes) if p.makro_listing_attributes else {}
    pkg_dims = json.loads(p.makro_package_dimensions) if p.makro_package_dimensions else {}
    makro_imgs = json.loads(p.makro_images) if p.makro_images else {}
    var_attrs = json.loads(p.variant_attributes) if p.variant_attributes else {}

    variants_data = []
    for v in p.variants:
        v_imgs = json.loads(v.images) if v.images else []
        v_makro_imgs = json.loads(v.makro_image_urls) if v.makro_image_urls else []
        v_attrs = json.loads(v.variant_attributes) if v.variant_attributes else {}
        v_specs = json.loads(v.specs) if v.specs else {}
        variants_data.append({
            "id": v.id,
            "sku_id": v.sku_id,
            "takealot_variant_id": v.takealot_variant_id,
            "variant_title": v.variant_title,
            "variant_attributes": v_attrs,
            "specs": v_specs,
            "barcode": v.barcode,
            "size": v.size,
            "colour": v.colour,
            "brand_colour": v.brand_colour,
            "pack_of": v.pack_of,
            "takealot_price": v.takealot_price,
            "makro_selling_price": v.makro_selling_price,
            "makro_mrp": v.makro_mrp,
            "images": v_imgs,
            "makro_image_urls": v_makro_imgs,
            "status": v.status,
            "makro_request_id": v.makro_request_id,
            "makro_submit_error": v.makro_submit_error
        })

    store_listings_data = []
    if hasattr(p, "store_listings") and p.store_listings:
        for sl in p.store_listings:
            store_listings_data.append({
                "id": sl.id,
                "store_id": sl.store_id,
                "store_name": sl.store.name if sl.store else f"店铺#{sl.store_id}",
                "status": sl.status,
                "makro_sku_id": sl.makro_sku_id,
                "makro_request_id": sl.makro_request_id,
                "makro_submit_error": sl.makro_submit_error,
                "selling_price": sl.selling_price,
                "mrp": sl.mrp,
                "submitted_at": sl.submitted_at
            })

    return {
        "id": p.id,
        "takealot_id": p.takealot_id,
        "takealot_url": p.takealot_url,
        "takealot_title": p.takealot_title,
        "takealot_price": p.takealot_price,
        "takealot_brand": p.takealot_brand,
        "takealot_category": p.takealot_category,
        "takealot_description": p.takealot_description,
        "takealot_specs": specs,
        "raw_images": raw_images,
        "status": p.status,
        "previous_status": getattr(p, "previous_status", None),
        "makro_vertical": p.makro_vertical,
        "makro_title": p.makro_title,
        "makro_description": p.makro_description,
        "makro_brand": p.makro_brand,
        "makro_selling_price": p.makro_selling_price,
        "makro_mrp": p.makro_mrp,
        "makro_catalog_attributes": cat_attrs,
        "makro_listing_attributes": list_attrs,
        "makro_package_dimensions": pkg_dims,
        "makro_images": makro_imgs,
        "group_code": p.group_code,
        "sku_id": p.sku_id,
        "barcode": p.barcode,
        "variant_attributes": var_attrs,
        "size": p.size,
        "colour": p.colour,
        "brand_colour": p.brand_colour,
        "pack_of": p.pack_of,
        "makro_sku_id": p.makro_sku_id,
        "makro_request_id": p.makro_request_id,
        "makro_submit_error": p.makro_submit_error,
        "compliance_status": p.compliance_status or "PENDING_CHECK",
        "compliance_details": json.loads(p.compliance_details) if p.compliance_details else None,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "variants": variants_data,
        "store_listings": store_listings_data
    }

@router.post("/collect", summary="接收插件采集的 Takealot 商品")
def collect_product(req: TakealotCollectRequest, db: Session = Depends(get_db)):
    products = TakealotService.save_collected_product(db, req)
    v_count = len(products) if isinstance(products, list) else 1
    p_obj = products[0] if isinstance(products, list) else products
    record_audit_log(
        task_type="COLLECT",
        status="SUCCESS",
        message=f"浏览器插件采集: {req.takealot_title[:35]} (共 {v_count} 个独立变体)",
        product_id=p_obj.id if p_obj else None,
        detail_logs={"url": req.url, "title": req.takealot_title, "variants_count": v_count, "plid": req.takealot_id},
        db=db
    )
    if isinstance(products, list):
        primary = _format_product(products[0]) if products else {}
        return {
            "total_variants": len(products),
            "items": [_format_product(p) for p in products],
            **primary
        }
    return _format_product(products)

@router.post("/collect-by-plid", summary="通过 Takealot PLID 或 URL 直接请求官方 API 极速采集")
def collect_by_plid(payload: dict, db: Session = Depends(get_db)):
    plid_or_url = payload.get("plid") or payload.get("url")
    if not plid_or_url:
        raise HTTPException(status_code=400, detail="请提供 plid 或 url 参数")
    try:
        products = TakealotService.fetch_and_save_by_plid(plid_or_url, db)
        v_count = len(products) if isinstance(products, list) else 1
        p_obj = products[0] if isinstance(products, list) else products
        record_audit_log(
            task_type="COLLECT",
            status="SUCCESS",
            message=f"PLID极速采集: {plid_or_url} (入库 {v_count} 个变体)",
            product_id=p_obj.id if p_obj else None,
            detail_logs={"plid_or_url": plid_or_url, "variants_count": v_count},
            db=db
        )
        if isinstance(products, list):
            primary = _format_product(products[0]) if products else {}
            return {
                "total_variants": len(products),
                "items": [_format_product(p) for p in products],
                **primary
            }
        return _format_product(products)
    except Exception as e:
        record_audit_log(
            task_type="COLLECT",
            status="FAILED",
            message=f"PLID极速采集失败: {plid_or_url} 异常: {str(e)}",
            detail_logs={"plid_or_url": plid_or_url, "error": str(e)},
            db=db
        )
        raise HTTPException(status_code=500, detail=f"采集异常: {str(e)}")

@router.post("/check-existence", summary="批量检查商品/PLID是否已被采集入库")
def check_products_existence(payload: dict, db: Session = Depends(get_db)):
    raw_plids = payload.get("plids", [])
    if not raw_plids:
        return {"exists": {}}

    lookup_keys = set()
    key_mapping = {}
    for raw in raw_plids:
        raw_str = str(raw).strip()
        if not raw_str:
            continue
        clean_num = re.sub(r'[^0-9]', '', raw_str)
        candidates = [raw_str]
        if clean_num:
            candidates.extend([f"PLID{clean_num}", clean_num])
        for c in candidates:
            lookup_keys.add(c)
            if c not in key_mapping:
                key_mapping[c] = []
            if raw_str not in key_mapping[c]:
                key_mapping[c].append(raw_str)

    if not lookup_keys:
        return {"exists": {}}

    rows = db.query(
        Product.group_code,
        Product.takealot_id,
        func.count(Product.id).label("cnt"),
        func.max(Product.status).label("status"),
        func.max(Product.takealot_title).label("title")
    ).filter(
        Product.group_code.in_(lookup_keys) | Product.takealot_id.in_(lookup_keys)
    ).group_by(Product.group_code).all()

    exists = {}
    for r in rows:
        matched_keys = set()
        if r.group_code: matched_keys.add(r.group_code)
        if r.takealot_id: matched_keys.add(r.takealot_id)

        info = {
            "collected": True,
            "count": r.cnt,
            "status": r.status,
            "title": r.title
        }
        for mk in matched_keys:
            for orig in key_mapping.get(mk, []):
                exists[orig] = info
                clean_n = re.sub(r'[^0-9]', '', orig)
                if clean_n:
                    exists[clean_n] = info
                    exists[f"PLID{clean_n}"] = info

    return {"exists": exists}

@router.get("", summary="获取商品列表 (支持状态、合规筛选与分页)")
def list_products(
    status: Optional[str] = Query(None, description="状态: PENDING_CLEAN, CLEANED, SUBMITTED, FAILED"),
    compliance_status: Optional[str] = Query(None, description="合规状态: PENDING_CHECK, SAFE, RISK, PROHIBITED"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    min_price: Optional[float] = Query(None, description="最低售价 (ZAR)"),
    max_price: Optional[float] = Query(None, description="最高售价 (ZAR)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db)
):
    query = db.query(Product)
    if status:
        query = query.filter(Product.status == status)
    else:
        # 默认“全部商品”不展示已弃用的商品 (弃用商品展示在专门的弃用箱中)
        query = query.filter(Product.status != "ABANDONED")

    if compliance_status:
        query = query.filter(Product.compliance_status == compliance_status)
    if min_price is not None:
        query = query.filter(Product.makro_selling_price >= min_price)
    if max_price is not None:
        query = query.filter(Product.makro_selling_price <= max_price)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            Product.takealot_title.ilike(s) |
            Product.makro_title.ilike(s) |
            Product.group_code.ilike(s) |
            Product.takealot_id.ilike(s) |
            Product.sku_id.ilike(s) |
            Product.barcode.ilike(s) |
            Product.makro_sku_id.ilike(s)
        )

    total = query.count()
    items = query.order_by(Product.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # 统计各流程状态商品数量
    counts_raw = db.query(Product.status, func.count(Product.id)).group_by(Product.status).all()
    status_counts = {k: 0 for k in ["PENDING_CLEAN", "CLEANED", "SUBMITTED", "FAILED", "ABANDONED"]}
    total_valid = 0
    for st, cnt in counts_raw:
        if st in status_counts:
            status_counts[st] = cnt
        if st != "ABANDONED":
            total_valid += cnt
    status_counts["ALL"] = total_valid

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_format_product(p) for p in items],
        "status_counts": status_counts
    }

@router.get("/{product_id}", response_model=ProductResponse, summary="获取单个商品详情")
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")
    return _format_product(product)

@router.put("/{product_id}", response_model=ProductResponse, summary="更新商品与 Makro 属性")
def update_product(product_id: int, req: ProductUpdateRequest, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    if req.makro_vertical is not None: product.makro_vertical = req.makro_vertical
    if req.makro_title is not None: product.makro_title = req.makro_title
    if req.makro_brand is not None: product.makro_brand = req.makro_brand
    if req.makro_selling_price is not None: product.makro_selling_price = req.makro_selling_price
    if req.makro_mrp is not None: product.makro_mrp = req.makro_mrp
    if req.makro_description is not None: product.makro_description = req.makro_description
    if req.group_code is not None: product.group_code = req.group_code
    if req.sku_id is not None: product.sku_id = req.sku_id
    if req.barcode is not None: product.barcode = req.barcode
    if req.size is not None: product.size = req.size
    if req.colour is not None: product.colour = req.colour
    if req.brand_colour is not None: product.brand_colour = req.brand_colour
    if req.pack_of is not None: product.pack_of = req.pack_of
    if req.variant_attributes is not None:
        product.variant_attributes = json.dumps(req.variant_attributes)
    
    if req.makro_catalog_attributes is not None:
        product.makro_catalog_attributes = json.dumps(req.makro_catalog_attributes)
    if req.makro_package_dimensions is not None:
        product.makro_package_dimensions = json.dumps(req.makro_package_dimensions)

    db.commit()
    db.refresh(product)
    record_audit_log(
        task_type="UPDATE",
        status="SUCCESS",
        message=f"修改商品属性: ID {product_id}「{(product.makro_title or product.takealot_title)[:35]}」",
        product_id=product_id,
        db=db
    )
    return _format_product(product)

@router.delete("/{product_id}", summary="删除商品 (普通状态移入弃用箱，弃用箱中执行则彻底删除)")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")
    p_title = product.takealot_title or f"ID {product_id}"
    p_plid = product.takealot_id or ""

    if product.status != "ABANDONED":
        # 移入弃用箱 (软删除/弃用)
        product.previous_status = product.status
        product.status = "ABANDONED"
        db.commit()

        record_audit_log(
            task_type="ABANDON",
            status="SUCCESS",
            message=f"移入弃用箱: ID {product_id} (原状态: {product.previous_status}, 标题: {p_title[:35]})",
            product_id=product_id,
            db=db
        )
        return {"action": "abandoned", "message": "商品已移入弃用箱，可在弃用箱中恢复或彻底删除", "id": product_id}
    else:
        # 已经在弃用箱中，彻底删除 (Permanent Delete)
        db.query(ProductVariant).filter(ProductVariant.product_id == product_id).delete(synchronize_session=False)
        db.query(TaskLog).filter(TaskLog.product_id == product_id).delete(synchronize_session=False)
        db.delete(product)
        db.commit()

        record_audit_log(
            task_type="DELETE",
            status="SUCCESS",
            message=f"彻底删除商品: ID {product_id} (PLID: {p_plid}, 标题: {p_title[:35]})",
            product_id=None,
            db=db
        )
        return {"action": "deleted", "message": "商品已从数据库永久彻底删除", "id": product_id}

@router.post("/batch-delete", summary="批量删除商品 (普通状态移入弃用箱，弃用箱中执行则彻底删除)")
def batch_delete_products(req: dict, db: Session = Depends(get_db)):
    product_ids = req.get("product_ids", [])
    if not product_ids:
        return {"deleted_count": 0, "abandoned_count": 0, "message": "未选择商品"}

    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    to_abandon = [p for p in products if p.status != "ABANDONED"]
    to_hard_delete = [p for p in products if p.status == "ABANDONED"]

    abandoned_count = 0
    if to_abandon:
        for p in to_abandon:
            p.previous_status = p.status
            p.status = "ABANDONED"
        abandoned_count = len(to_abandon)
        record_audit_log(
            task_type="ABANDON",
            status="SUCCESS",
            message=f"批量移入弃用箱: 成功将 {abandoned_count} 件商品移入弃用箱",
            detail_logs={"abandoned_ids": [p.id for p in to_abandon][:50]},
            db=db
        )

    deleted_count = 0
    if to_hard_delete:
        hard_ids = [p.id for p in to_hard_delete]
        db.query(ProductVariant).filter(ProductVariant.product_id.in_(hard_ids)).delete(synchronize_session=False)
        db.query(TaskLog).filter(TaskLog.product_id.in_(hard_ids)).delete(synchronize_session=False)
        deleted_count = db.query(Product).filter(Product.id.in_(hard_ids)).delete(synchronize_session=False)
        record_audit_log(
            task_type="BATCH_DELETE",
            status="SUCCESS",
            message=f"批量彻底删除: 永久删除 {deleted_count} 件弃用商品",
            detail_logs={"deleted_ids": hard_ids[:50]},
            db=db
        )

    db.commit()

    msg_parts = []
    if abandoned_count > 0:
        msg_parts.append(f"成功将 {abandoned_count} 件商品移入弃用箱")
    if deleted_count > 0:
        msg_parts.append(f"成功彻底删除 {deleted_count} 件商品")
    message = "，".join(msg_parts) if msg_parts else "操作成功"

    return {
        "message": message,
        "abandoned_count": abandoned_count,
        "deleted_count": deleted_count
    }

@router.post("/{product_id}/restore", summary="从弃用箱恢复商品")
def restore_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")
    if product.status != "ABANDONED":
        return {"message": "商品未处于弃用状态", "status": product.status}

    restore_target = product.previous_status or ("CLEANED" if product.makro_title else "PENDING_CLEAN")
    product.status = restore_target
    product.previous_status = None
    db.commit()

    p_title = product.takealot_title or f"ID {product_id}"
    record_audit_log(
        task_type="RESTORE",
        status="SUCCESS",
        message=f"从弃用箱恢复商品: ID {product_id}「{p_title[:35]}」恢复至【{restore_target}】",
        product_id=product_id,
        db=db
    )
    return {"message": "商品已成功恢复至选品箱", "status": restore_target, "id": product_id}

@router.post("/batch-restore", summary="批量从弃用箱恢复商品")
def batch_restore_products(req: dict, db: Session = Depends(get_db)):
    product_ids = req.get("product_ids", [])
    if not product_ids:
        return {"restored_count": 0, "message": "未选择商品"}

    products = db.query(Product).filter(Product.id.in_(product_ids), Product.status == "ABANDONED").all()
    restored_count = 0
    for p in products:
        target = p.previous_status or ("CLEANED" if p.makro_title else "PENDING_CLEAN")
        p.status = target
        p.previous_status = None
        restored_count += 1

    db.commit()

    record_audit_log(
        task_type="BATCH_RESTORE",
        status="SUCCESS",
        message=f"批量恢复商品: 成功从弃用箱恢复 {restored_count} 件商品至选品箱",
        detail_logs={"restored_ids": [p.id for p in products][:50]},
        db=db
    )
    return {"message": f"成功从弃用箱恢复 {restored_count} 件商品至选品箱", "restored_count": restored_count}

@router.post("/batch-update-price", summary="批量修改商品售价与MRP")
def batch_update_price(payload: dict, db: Session = Depends(get_db)):
    product_ids = payload.get("product_ids", [])
    if not product_ids:
        raise HTTPException(status_code=400, detail="请选择要修改价格的商品")
    
    mode = payload.get("mode", "formula")  # "fixed" 或 "formula"
    fixed_price = payload.get("fixed_price")
    multiplier = payload.get("multiplier", 1.0)
    fixed_offset = payload.get("fixed_offset", 0.0)

    from ..services.pricing_service import get_pricing_rules
    _, _, mrp_ratio = get_pricing_rules(db)
    if payload.get("mrp_ratio"):
        try:
            mrp_ratio = float(payload.get("mrp_ratio"))
        except (ValueError, TypeError):
            pass

    products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    updated_count = 0

    for p in products:
        if mode == "fixed":
            if fixed_price is None or float(fixed_price) <= 0:
                continue
            new_selling = float(fixed_price)
        else:
            curr = float(p.makro_selling_price or p.takealot_price or 0.0)
            mult = float(multiplier) if multiplier is not None else 1.0
            offset = float(fixed_offset) if fixed_offset is not None else 0.0
            new_selling = curr * mult + offset

        new_selling = max(1, round(new_selling))
        new_mrp = max(new_selling, round(new_selling * mrp_ratio))

        p.makro_selling_price = new_selling
        p.makro_mrp = new_mrp
        updated_count += 1

    db.commit()

    record_audit_log(
        task_type="BATCH_PRICE",
        status="SUCCESS",
        message=f"批量改价: 修改了 {updated_count} 件商品 (模式: {'固定价 R' + str(fixed_price) if mode == 'fixed' else f'公式 ×{multiplier} +R{fixed_offset}'}, MRP倍率 {mrp_ratio})",
        detail_logs={"product_ids": product_ids[:50], "mode": mode, "updated_count": updated_count},
        db=db
    )

    return {
        "updated_count": updated_count,
        "message": f"成功批量修改 {updated_count} 件商品售价与划线原价"
    }
