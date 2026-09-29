import re
import json
import os
import hashlib
import requests
from typing import List, Optional, Union
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status, UploadFile, File, Form, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload, joinedload
from ..database import get_db, SessionLocal
from ..models.product import Product, ProductVariant
from ..models.store import ProductStoreListing
from ..models.task import TaskLog
from ..models.user import User
from ..services.task_manager import task_manager
from ..services.csv_collector import parse_plids_from_content, batch_collect_plids
from ..utils.auth import get_optional_current_user
from ..schemas.product import (
    TakealotCollectRequest,
    ProductResponse,
    ProductUpdateRequest,
    ProductVariantResponse
)
from ..services.takealot_service import TakealotService
from ..services.translation_service import TranslationService
from ..services.audit_logger import record_audit_log

# 全局高复用图片会话连接池 (复用 media.takealot.com 的 HTTPS 长连接)
_image_session = requests.Session()
_image_adapter = requests.adapters.HTTPAdapter(
    pool_connections=30,
    pool_maxsize=100,
    max_retries=1
)
_image_session.mount("https://", _image_adapter)
_image_session.mount("http://", _image_adapter)

# 图片内存热缓存 (最多缓存 120 张高频小图，避免频繁磁盘 I/O)
_IMAGE_MEM_CACHE = {}
_IMAGE_MEM_CACHE_KEYS = []
_MAX_MEM_IMAGES = 120

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
                "brand": getattr(sl, "brand", None) or (sl.store.default_brand if sl.store else "Beishi"),
                "status": sl.status,
                "makro_sku_id": sl.makro_sku_id,
                "makro_request_id": sl.makro_request_id,
                "makro_submit_error": sl.makro_submit_error,
                "selling_price": sl.selling_price,
                "mrp": sl.mrp,
                "submitted_at": sl.submitted_at
            })

    seo_kw = []
    if getattr(p, "seo_keywords", None):
        try:
            seo_kw = json.loads(p.seo_keywords) if isinstance(p.seo_keywords, str) else p.seo_keywords
        except Exception:
            seo_kw = []

    comp_parsed = json.loads(p.compliance_details) if p.compliance_details else None
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
        "makro_vertical_zh": TranslationService.get_vertical_zh(p.makro_vertical) if p.makro_vertical else None,
        "makro_title": p.makro_title,
        "takealot_title_zh": getattr(p, "takealot_title_zh", None),
        "makro_title_zh": getattr(p, "makro_title_zh", None),
        "seo_keywords": seo_kw,
        "clean_mode": getattr(p, "clean_mode", "text") or "text",
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
        "compliance_details": comp_parsed,
        "brand_nature": comp_parsed.get("brand_nature") if isinstance(comp_parsed, dict) else None,
        "target_compatible_brand": comp_parsed.get("target_compatible_brand") if isinstance(comp_parsed, dict) else None,
        "user_id": p.user_id,
        "creator_name": (p.creator.nickname or p.creator.username) if p.creator else "未分配",
        "creator_username": p.creator.username if p.creator else None,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "variants": variants_data,
        "store_listings": store_listings_data
    }

def _format_product_summary(p: Product) -> dict:
    """列表高性能精简序列化：剔除大长文描述与复杂全量属性字典，大幅缩减 90% 数据传输体积与 Vue 渲染开销"""
    raw_images = json.loads(p.raw_images) if p.raw_images else []
    var_attrs = json.loads(p.variant_attributes) if p.variant_attributes else {}

    store_listings_data = []
    if hasattr(p, "store_listings") and p.store_listings:
        for sl in p.store_listings:
            store_listings_data.append({
                "id": sl.id,
                "store_id": sl.store_id,
                "store_name": sl.store.name if sl.store else f"店铺#{sl.store_id}",
                "brand": getattr(sl, "brand", None) or (sl.store.default_brand if sl.store else "Beishi"),
                "status": sl.status,
                "makro_sku_id": sl.makro_sku_id,
                "makro_request_id": sl.makro_request_id,
                "makro_submit_error": sl.makro_submit_error,
                "selling_price": sl.selling_price,
                "mrp": sl.mrp,
                "submitted_at": sl.submitted_at
            })

    comp_details = None
    if p.compliance_details:
        try:
            cd = json.loads(p.compliance_details) if isinstance(p.compliance_details, str) else p.compliance_details
            if cd:
                comp_details = cd
        except Exception:
            pass

    return {
        "id": p.id,
        "takealot_id": p.takealot_id,
        "takealot_url": p.takealot_url,
        "takealot_title": p.takealot_title,
        "takealot_price": p.takealot_price,
        "takealot_brand": p.takealot_brand,
        "takealot_category": p.takealot_category,
        "raw_images": raw_images[:3],
        "status": p.status,
        "previous_status": getattr(p, "previous_status", None),
        "makro_vertical": p.makro_vertical,
        "makro_vertical_zh": TranslationService.get_vertical_zh(p.makro_vertical) if p.makro_vertical else None,
        "makro_title": p.makro_title,
        "takealot_title_zh": getattr(p, "takealot_title_zh", None),
        "makro_title_zh": getattr(p, "makro_title_zh", None),
        "clean_mode": getattr(p, "clean_mode", "text") or "text",
        "makro_brand": p.makro_brand,
        "makro_selling_price": p.makro_selling_price,
        "makro_mrp": p.makro_mrp,
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
        "compliance_details": comp_details,
        "user_id": p.user_id,
        "creator_name": (p.creator.nickname or p.creator.username) if p.creator else "未分配",
        "creator_username": p.creator.username if p.creator else None,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "store_listings": store_listings_data
    }

@router.post("/collect", summary="接收插件采集的 Takealot 商品")
def collect_product(
    req: TakealotCollectRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    user_id = current_user.id if current_user else None
    if user_id is None:
        if req.user_id:
            user_id = req.user_id
        elif req.collector_username:
            u = db.query(User).filter(User.username == req.collector_username.strip()).first()
            if u:
                user_id = u.id

    products = TakealotService.save_collected_product(db, req, user_id=user_id)
    v_count = len(products) if isinstance(products, list) else 1
    p_obj = products[0] if isinstance(products, list) else products
    record_audit_log(
        task_type="COLLECT",
        status="SUCCESS",
        message=f"浏览器插件采集: {req.takealot_title[:35]} (共 {v_count} 个独立变体, 归属用户ID: {user_id})",
        product_id=p_obj.id if p_obj else None,
        detail_logs={"url": req.takealot_url, "title": req.takealot_title, "variants_count": v_count, "plid": req.takealot_id, "user_id": user_id},
        db=db
    )
    if isinstance(products, list):
        primary = _format_product(products[0]) if products else {}
        return {
            "total_variants": len(products),
            "items": [_format_product(p) for p in products],
            **(primary or {})
        }
    return _format_product(products)

@router.get("/vertical-translations", summary="获取 Makro 全部官方类目中文翻译映射字典")
def get_vertical_translations():
    from ..services.translation_service import MAKRO_VERTICAL_ZH_MAP
    return {
        "total": len(MAKRO_VERTICAL_ZH_MAP),
        "translations": MAKRO_VERTICAL_ZH_MAP
    }

@router.post("/collect-by-plid", summary="通过 Takealot PLID 或 URL 直接请求官方 API 极速采集")
def collect_by_plid(
    payload: dict,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    raw_plid = payload.get("plid")
    raw_url = payload.get("url")
    plid_or_url = raw_plid or raw_url
    if not plid_or_url:
        raise HTTPException(status_code=400, detail="请提供 plid 或 url 参数")
    try:
        user_id = current_user.id if current_user else None
        if user_id is None:
            req_uid = payload.get("user_id") or payload.get("collector_user_id")
            req_uname = payload.get("username") or payload.get("collector_username")
            if req_uid:
                try:
                    user_id = int(req_uid)
                except (ValueError, TypeError):
                    pass
            elif req_uname:
                u = db.query(User).filter(User.username == str(req_uname).strip()).first()
                if u:
                    user_id = u.id

        products = TakealotService.fetch_and_save_by_plid(plid_or_url, db, custom_url=raw_url, user_id=user_id)
        v_count = len(products) if isinstance(products, list) else 1
        p_obj = products[0] if isinstance(products, list) else products
        record_audit_log(
            task_type="COLLECT",
            status="SUCCESS",
            message=f"PLID极速采集: {plid_or_url} (入库 {v_count} 个变体, 归属用户ID: {user_id})",
            product_id=p_obj.id if p_obj else None,
            detail_logs={"plid_or_url": plid_or_url, "variants_count": v_count, "user_id": user_id},
            db=db
        )
        if isinstance(products, list):
            primary = _format_product(products[0]) if products else {}
            return {
                "total_variants": len(products),
                "items": [_format_product(p) for p in products],
                **(primary or {})
            }
        return _format_product(products)
    except ValueError as ve:
        err_msg = str(ve)
        record_audit_log(
            task_type="COLLECT",
            status="FAILED",
            message=f"PLID极速采集失败: {plid_or_url} 原因: {err_msg}",
            detail_logs={"plid_or_url": plid_or_url, "error": err_msg},
            db=db
        )
        raise HTTPException(status_code=400, detail=err_msg)
    except Exception as e:
        record_audit_log(
            task_type="COLLECT",
            status="FAILED",
            message=f"PLID极速采集失败: {plid_or_url} 异常: {str(e)}",
            detail_logs={"plid_or_url": plid_or_url, "error": str(e)},
            db=db
        )
        raise HTTPException(status_code=500, detail=f"采集异常: {str(e)}")

@router.post("/import-csv", summary="上传 CSV/TXT 文件或提交列表批量采集 Takealot 商品")
async def import_csv_products(
    request: Request,
    file: Optional[UploadFile] = File(None),
    raw_content: Optional[str] = Form(None),
    skip_existing: bool = Form(True),
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    全自动 CSV / TXT 批量采集接口：
    - 支持直接上传 Takealot 导出的 CSV/TXT 表格文件
    - 支持以 JSON 格式提交 PLID 列表或大文本
    - 智能提取 PLID 与 TSIN，支持跳过已有已刊登商品
    - 接入 TaskManager 挂载异步后台任务并支持实时进度监控
    """
    content_bytes = b""
    content_type = request.headers.get("content-type", "").lower()
    
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                skip_existing = bool(body.get("skip_existing", True))
                if body.get("plids") and isinstance(body["plids"], list):
                    content_bytes = "\n".join(str(p) for p in body["plids"]).encode("utf-8")
                elif body.get("content"):
                    content_bytes = str(body["content"]).encode("utf-8")
        except Exception as je:
            logger.debug(f"JSON 解析跳过: {je}")
    elif file is not None:
        content_bytes = await file.read()
    elif raw_content:
        content_bytes = raw_content.encode("utf-8")

    if not content_bytes:
        raise HTTPException(status_code=400, detail="请上传 CSV/TXT 表格文件，或在请求体中提供商品编号")

    plid_items = parse_plids_from_content(content_bytes)
    if not plid_items:
        raise HTTPException(status_code=400, detail="未能从上传内容中识别出有效的 Takealot PLID/TSIN 编号，请核对文件格式")

    user_id = current_user.id if current_user else None
    task_name = f"CSV批量采集 ({len(plid_items)} 个商品)"
    task = task_manager.create_task("BATCH_COLLECT", task_name, len(plid_items))

    def _worker(tm, tid):
        thread_db = SessionLocal()
        try:
            res = batch_collect_plids(
                plid_items=plid_items,
                db=thread_db,
                user_id=user_id,
                skip_existing=skip_existing,
                max_workers=6,
                task_id=tid
            )
            msg = f"批量采集完成: 成功新入库 {res['success_count']} 个商品 ({res['variant_count']} 个独立变体), 跳过已有 {res['skipped_count']} 项, 失败 {res['failed_count']} 项"
            tm.finish_task(tid, status="SUCCESS" if res["success_count"] > 0 or res["skipped_count"] > 0 else "FAILED", message=msg)
        except Exception as e:
            logger.error(f"后台批量采集任务异常: {e}", exc_info=True)
            tm.finish_task(tid, status="FAILED", message=f"采集异常中断: {str(e)[:150]}")
        finally:
            thread_db.close()

    task_manager.start_task(task["id"], _worker)

    return {
        "success": True,
        "task_id": task["id"],
        "total_plids": len(plid_items),
        "message": f"已识别 {len(plid_items)} 个商品编号，后台批量采集任务已成功启动！"
    }

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
        Product.status,
        Product.takealot_title
    ).filter(
        Product.group_code.in_(lookup_keys) | Product.takealot_id.in_(lookup_keys)
    ).all()

    # 在 Python 内存中高速聚合，免除 SQLite 临时 B-Tree 排序开销
    group_map = {}
    for r in rows:
        gk = r.group_code or r.takealot_id
        if gk not in group_map:
            group_map[gk] = {
                "group_code": r.group_code,
                "takealot_id": r.takealot_id,
                "cnt": 1,
                "status": r.status,
                "title": r.takealot_title
            }
        else:
            group_map[gk]["cnt"] += 1

    exists = {}
    for gk, info_dict in group_map.items():
        matched_keys = set()
        if info_dict["group_code"]: matched_keys.add(info_dict["group_code"])
        if info_dict["takealot_id"]: matched_keys.add(info_dict["takealot_id"])

        info = {
            "collected": True,
            "count": info_dict["cnt"],
            "status": info_dict["status"],
            "title": info_dict["title"]
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
    user_id: Optional[str] = Query(None, description="按员工用户ID过滤 (管理员可用, 支持 'unassigned')"),
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="请先登录系统以访问选品数据",
            headers={"WWW-Authenticate": "Bearer"}
        )

    query = db.query(Product)

    # 权限与用户数据隔离：普通员工只能看到自己名下的选品；管理员可查看全部或指定筛选某员工
    filter_user_id = None
    is_unassigned_filter = False
    if current_user.role != "ADMIN":
        filter_user_id = current_user.id
    elif user_id is not None:
        if str(user_id).lower() in ["unassigned", "none", "null"]:
            is_unassigned_filter = True
        else:
            try:
                filter_user_id = int(user_id)
            except (ValueError, TypeError):
                pass

    if is_unassigned_filter:
        query = query.filter(Product.user_id.is_(None))
    elif filter_user_id is not None:
        query = query.filter(Product.user_id == filter_user_id)

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
        s_clean = search.strip()
        s = f"%{s_clean}%"
        conds = [
            Product.takealot_title.ilike(s),
            Product.makro_title.ilike(s),
            Product.group_code.ilike(s),
            Product.takealot_id.ilike(s),
            Product.sku_id.ilike(s),
            Product.barcode.ilike(s),
            Product.makro_sku_id.ilike(s),
            Product.variants.any(ProductVariant.sku_id.ilike(s)),
            Product.store_listings.any(ProductStoreListing.makro_sku_id.ilike(s))
        ]
        if s_clean.isdigit():
            conds.append(Product.id == int(s_clean))
        query = query.filter(or_(*conds))

    total = query.count()
    # 列表极速查询：预加载关联店铺与创建人记录，消除无用的变体关联查询与大字段解析 (DB 查询降低至 ~10ms)
    items = (
        query.options(
            selectinload(Product.store_listings).joinedload(ProductStoreListing.store),
            joinedload(Product.creator)
        )
        .order_by(Product.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # 统计各流程状态商品数量 (必须与当前用户权限隔离范围保持一致)
    counts_q = db.query(Product.status, func.count(Product.id))
    if is_unassigned_filter:
        counts_q = counts_q.filter(Product.user_id.is_(None))
    elif filter_user_id is not None:
        counts_q = counts_q.filter(Product.user_id == filter_user_id)
    counts_raw = counts_q.group_by(Product.status).all()
    status_counts = {k: 0 for k in ["PENDING_CLEAN", "CLEANED", "SUBMITTED", "FAILED", "ABANDONED"]}
    total_valid = 0
    for st, cnt in counts_raw:
        if st in status_counts:
            status_counts[st] = cnt
        if st != "ABANDONED":
            total_valid += cnt
    status_counts["ALL"] = total_valid

    # 统计各合规状态商品数量 (根据当前流程状态 status 与权限隔离动态联动，耗时 < 2ms)
    comp_q = db.query(Product.compliance_status, func.count(Product.id))
    if is_unassigned_filter:
        comp_q = comp_q.filter(Product.user_id.is_(None))
    elif filter_user_id is not None:
        comp_q = comp_q.filter(Product.user_id == filter_user_id)

    if status:
        comp_q = comp_q.filter(Product.status == status)
    else:
        comp_q = comp_q.filter(Product.status != "ABANDONED")

    comp_raw = comp_q.group_by(Product.compliance_status).all()
    compliance_counts = {
        "ALL": 0,
        "DISPUTED": 0,
        "SAFE": 0,
        "RISK": 0,
        "PROHIBITED": 0,
        "PENDING_CHECK": 0
    }
    total_comp = 0
    for cs, cnt in comp_raw:
        cs_key = cs if cs in compliance_counts else "PENDING_CHECK"
        compliance_counts[cs_key] = compliance_counts.get(cs_key, 0) + cnt
        total_comp += cnt
    compliance_counts["ALL"] = total_comp

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_format_product_summary(p) for p in items],
        "status_counts": status_counts,
        "compliance_counts": compliance_counts
    }

@router.get("/ids", summary="获取当前过滤条件下的所有商品 ID 列表 (用于一键全选过滤项)")
def list_product_ids(
    status: Optional[str] = Query(None, description="状态: PENDING_CLEAN, CLEANED, SUBMITTED, FAILED"),
    compliance_status: Optional[str] = Query(None, description="合规状态: PENDING_CHECK, SAFE, RISK, PROHIBITED"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    min_price: Optional[float] = Query(None, description="最低售价 (ZAR)"),
    max_price: Optional[float] = Query(None, description="最高售价 (ZAR)"),
    user_id: Optional[str] = Query(None, description="按员工用户ID过滤 (管理员可用, 支持 'unassigned')"),
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="请先登录系统以访问选品数据",
            headers={"WWW-Authenticate": "Bearer"}
        )

    query = db.query(Product.id)

    filter_user_id = None
    is_unassigned_filter = False
    if current_user.role != "ADMIN":
        filter_user_id = current_user.id
    elif user_id is not None:
        if str(user_id).lower() in ["unassigned", "none", "null"]:
            is_unassigned_filter = True
        else:
            try:
                filter_user_id = int(user_id)
            except (ValueError, TypeError):
                pass

    if is_unassigned_filter:
        query = query.filter(Product.user_id.is_(None))
    elif filter_user_id is not None:
        query = query.filter(Product.user_id == filter_user_id)

    if status:
        query = query.filter(Product.status == status)
    else:
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
            Product.makro_sku_id.ilike(s) |
            Product.store_listings.any(ProductStoreListing.makro_sku_id.ilike(s))
        )

    rows = query.order_by(Product.id.desc()).all()
    ids = [r[0] for r in rows]
    return {
        "total": len(ids),
        "ids": ids
    }

class BatchAssignUserRequest(BaseModel):
    product_ids: List[int]
    user_id: Optional[int] = None  # None 表示设置为未分配

@router.post("/batch-assign-user", summary="批量变更商品归属员工 (管理员可用)")
def batch_assign_user(
    req: BatchAssignUserRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    if not current_user or current_user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员有权限批量分配商品归属")

    if not req.product_ids:
        raise HTTPException(status_code=400, detail="请选择要分配归属的商品")

    target_user = None
    if req.user_id is not None:
        target_user = db.query(User).filter(User.id == req.user_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="指定的目标员工不存在")

    updated_count = (
        db.query(Product)
        .filter(Product.id.in_(req.product_ids))
        .update({"user_id": req.user_id}, synchronize_session=False)
    )
    db.commit()

    target_name = (target_user.nickname or target_user.username) if target_user else "未分配"
    record_audit_log(
        task_type="BATCH_ASSIGN_USER",
        status="SUCCESS",
        message=f"管理员【{current_user.username}】批量指派了 {updated_count} 件商品归属至【{target_name}】",
        detail_logs={"product_ids_count": len(req.product_ids), "target_user_id": req.user_id},
        db=db
    )
    return {
        "success": True,
        "updated_count": updated_count,
        "target_user_id": req.user_id,
        "target_name": target_name,
        "message": f"成功将 {updated_count} 件商品分配给【{target_name}】！"
    }

IMAGE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".image_cache")
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

@router.get("/image-proxy", summary="高可用图片代理中转与加速缓存")
def proxy_image(url: str = Query(..., description="远程图片完整URL")):
    """
    针对国内网络/客户端 DNS 无法解析 media.takealot.com 或 Cloudflare WAF 防爬风控提供的兜底高可用中转与本地缓存服务。
    内置多级缓存: 内存热缓存 (0ms) -> 本地持久化缓存 -> 全局长连接复用池抓取
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing image url")
    
    # 自动纠偏 Takealot {size} 占位符为最高清的 pdpxl 规格
    if "{size}" in url:
        url = url.replace("{size}", "pdpxl")
    
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Invalid image url protocol")
    
    cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()

    # 0. 优先命中内存高速热缓存 (0ms，跳过任何磁盘文件 I/O)
    if cache_key in _IMAGE_MEM_CACHE:
        mem_item = _IMAGE_MEM_CACHE[cache_key]
        return Response(
            content=mem_item["data"],
            media_type=mem_item["type"],
            headers={"Cache-Control": "public, max-age=604800, immutable"}
        )

    cache_path = os.path.join(IMAGE_CACHE_DIR, f"{cache_key}.img")
    content_type_path = os.path.join(IMAGE_CACHE_DIR, f"{cache_key}.type")
    
    # 1. 命中本地持久化缓存直接流式返回并存入内存热缓存
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        try:
            with open(cache_path, "rb") as f:
                img_data = f.read()
            media_type = "image/jpeg"
            if os.path.exists(content_type_path):
                with open(content_type_path, "r", encoding="utf-8") as f:
                    media_type = f.read().strip() or "image/jpeg"
            
            # 更新内存热缓存
            if len(_IMAGE_MEM_CACHE_KEYS) >= _MAX_MEM_IMAGES:
                oldest_k = _IMAGE_MEM_CACHE_KEYS.pop(0)
                _IMAGE_MEM_CACHE.pop(oldest_k, None)
            _IMAGE_MEM_CACHE_KEYS.append(cache_key)
            _IMAGE_MEM_CACHE[cache_key] = {"data": img_data, "type": media_type}

            return Response(
                content=img_data,
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=604800, immutable"}
            )
        except Exception:
            pass
            
    # 2. 请求远程图片 (使用全局 Session 连接池复用 HTTPS 连接)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.takealot.com/"
    }
    try:
        resp = _image_session.get(url, headers=headers, timeout=12)
        if resp.status_code == 200 and resp.content:
            media_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            try:
                with open(cache_path, "wb") as f:
                    f.write(resp.content)
                with open(content_type_path, "w", encoding="utf-8") as f:
                    f.write(media_type)
            except Exception:
                pass

            # 存入内存热缓存
            if len(_IMAGE_MEM_CACHE_KEYS) >= _MAX_MEM_IMAGES:
                oldest_k = _IMAGE_MEM_CACHE_KEYS.pop(0)
                _IMAGE_MEM_CACHE.pop(oldest_k, None)
            _IMAGE_MEM_CACHE_KEYS.append(cache_key)
            _IMAGE_MEM_CACHE[cache_key] = {"data": resp.content, "type": media_type}

            return Response(
                content=resp.content,
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=604800, immutable"}
            )
    except Exception:
        pass
        
    fallback_svg = """<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160' viewBox='0 0 160 160'><rect width='100%' height='100%' fill='#f1f5f9'/><text x='50%' y='46%' dominant-baseline='middle' text-anchor='middle' fill='#94a3b8' font-size='14' font-family='sans-serif'>图片载入中</text><text x='50%' y='62%' dominant-baseline='middle' text-anchor='middle' fill='#cbd5e1' font-size='11' font-family='sans-serif'>或网络受限</text></svg>"""
    return Response(content=fallback_svg.encode("utf-8"), media_type="image/svg+xml", status_code=200)

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
    if req.takealot_title_zh is not None: product.takealot_title_zh = req.takealot_title_zh
    if req.makro_title_zh is not None: product.makro_title_zh = req.makro_title_zh
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

@router.post("/{product_id}/translate", summary="按需实时重新翻译商品中英文标题")
def translate_product_titles(
    product_id: int,
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    if product.takealot_title:
        product.takealot_title_zh = TranslationService.translate_title(product.takealot_title, db=db, force_refresh=True)
    if product.makro_title:
        product.makro_title_zh = TranslationService.translate_title(product.makro_title, db=db, force_refresh=True)

    db.commit()
    db.refresh(product)
    return {
        "id": product.id,
        "takealot_title_zh": product.takealot_title_zh,
        "makro_title_zh": product.makro_title_zh,
        "makro_vertical_zh": TranslationService.get_vertical_zh(product.makro_vertical) if product.makro_vertical else None
    }

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
        curr = float(p.makro_selling_price or p.takealot_price or 0.0)
        if mode == "fixed":
            if fixed_price is None or float(fixed_price) <= 0:
                continue
            new_selling = float(fixed_price)
        else:
            mult = float(multiplier) if (multiplier is not None and str(multiplier).strip() != "") else 1.0
            offset = float(fixed_offset) if (fixed_offset is not None and str(fixed_offset).strip() != "") else 0.0
            new_selling = curr * mult + offset

        new_selling = max(1, round(new_selling))
        new_mrp = max(new_selling, round(new_selling * mrp_ratio))

        p.makro_selling_price = new_selling
        p.makro_mrp = new_mrp

        # 同步更新变体价格
        if hasattr(p, "variants") and p.variants:
            for v in p.variants:
                if mode == "fixed":
                    v.makro_selling_price = new_selling
                    v.makro_mrp = new_mrp
                else:
                    v_curr = float(v.makro_selling_price or v.takealot_price or curr)
                    v_new = max(1, round(v_curr * mult + offset))
                    v.makro_selling_price = v_new
                    v.makro_mrp = max(v_new, round(v_new * mrp_ratio))

        # 同步更新多店铺已关联的 Listing 价格
        if hasattr(p, "store_listings") and p.store_listings:
            for sl in p.store_listings:
                sl.selling_price = new_selling
                sl.mrp = new_mrp

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
