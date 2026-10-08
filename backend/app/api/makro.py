import re
import json
import time
import uuid
import logging
from typing import Dict, Any, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from ..database import get_db
from ..models.product import Product, ProductVariant
from ..models.user import User
from ..models.store import Store
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..database import SessionLocal
import threading
from ..models.task import TaskLog
from ..schemas.setting import SyncCredentialsRequest
from ..schemas.product import BatchPublishRequest
from ..services.makro_client import MakroClient
from ..services.vertical_service import VerticalService
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
from ..services.compliance_service import PROTECTED_ENTERTAINMENT_IPS
from ..services.ai_cleaner_service import truncate_title_safely
from ..utils.auth import get_optional_current_user
from ..config import settings
from .products import _format_product


router = APIRouter(prefix="/makro", tags=["Makro上品引擎"])
logger = logging.getLogger(__name__)

def _get_setting_val(db: Session, key: str, default: str) -> str:
    s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return s.value if s and s.value else default

from ..services.publisher import (
    format_attribute_value_and_qualifier as _format_attribute_value_and_qualifier,
    build_makro_payload as _build_makro_payload,
    record_store_listing as _record_store_listing,
    get_safe_fallback_vertical as _get_safe_fallback_vertical,
    publish_single_product as _publish_single_product,
    publish_single_variant as _publish_single_variant,
    MakroPayloadHealer,
)
_auto_heal_payload = MakroPayloadHealer.auto_heal_payload

@router.post("/publish/{product_id}", summary="自动执行全流程上品到 Makro (支持指定店铺或全部店铺)")
def publish_product_to_makro(
    product_id: int,
    store_id: Optional[int] = None,
    publish_all_stores: bool = False,
    force: bool = False,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    u_id = current_user.id if current_user else product.user_id
    op_name = (current_user.nickname or current_user.username) if current_user else None


    # 违禁品安全防护
    if product.compliance_status == "PROHIBITED" and not force:
        comp_details = json.loads(product.compliance_details) if product.compliance_details else {}
        items = comp_details.get("prohibited_items", [])
        items_str = "、".join(items) if items else "蓝牙/WiFi/红外/液体"
        raise HTTPException(
            status_code=400,
            detail=f"【违禁品拦截】该商品命中平台禁售规则（包含：{items_str}），严禁直接上品以防店铺封禁！如需强制发布请开启强制开关。"
        )

    # 确定目标店铺列表
    target_stores = []
    if publish_all_stores:
        target_stores = db.query(Store).filter(Store.is_active == True).all()
        if not target_stores:
            target_stores = [None]
    elif store_id:
        store = db.query(Store).filter(Store.id == store_id).first()
        if not store:
            raise HTTPException(status_code=404, detail="指定的店铺未找到")
        target_stores = [store]
    else:
        # 默认店铺
        default_store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()
        if not default_store:
            default_store = db.query(Store).filter(Store.is_active == True).first()
        target_stores = [default_store] if default_store else [None]

    raw_vertical = product.makro_vertical or "bath_towel"
    valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
    if valid_vertical != product.makro_vertical:
        product.makro_vertical = valid_vertical
        db.commit()

    store_results = []
    any_success = False

    for s_item in target_stores:
        s_name = s_item.name if s_item else "默认店铺"
        s_id = s_item.id if s_item else None
        client = MakroClient.from_store(s_item) if s_item else MakroClient.from_db(db)
        target_brand = (
            (s_item.default_brand.strip() if s_item and getattr(s_item, "default_brand", None) and s_item.default_brand.strip() else None)
            or _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND)
            or product.makro_brand
            or "Beishi"
        )
        brand = target_brand

        task = TaskLog(
            product_id=product.id,
            task_type="SUBMIT_LISTING",
            status="RUNNING",
            user_id=u_id,
            operator_name=op_name,
            message=f"开始向店铺【{s_name}】(刊登品牌: {brand}) 执行 Makro 上品调用...",
            created_at=datetime.now()
        )
        db.add(task)
        db.commit()

        if not client.cookie:
            msg = f"店铺【{s_name}】未检测到登录态 Cookie！请先在多店铺管理中配置 Cookie。"
            _record_store_listing(
                db=db,
                product_id=product.id,
                target_store=s_item,
                is_success=False,
                sku_id=None,
                request_id=None,
                msg=msg,
                selling_price=product.makro_selling_price,
                mrp=product.makro_mrp,
                brand=brand,
                user_id=u_id
            )

            task.status = "FAILED"
            task.message = msg
            task.finished_at = datetime.now()
            db.commit()
            store_results.append({
                "store_id": s_id,
                "store_name": s_name,
                "success": False,
                "message": msg
            })
            continue

        try:
            if product.variants and len(product.variants) > 0:
                v_results = []
                for v in product.variants:
                    try:
                        v_res = _publish_single_variant(client, db, product, v, valid_vertical, vid, brand, target_store=s_item)
                        v_results.append(v_res)
                    except Exception as ex:
                        logger.error(f"变体 {v.sku_id} 在店铺 {s_name} 上架异常: {ex}", exc_info=True)
                        v.status = "FAILED"
                        v.makro_submit_error = str(ex)
                        db.commit()
                        v_results.append({"variant_id": v.id, "sku_id": v.sku_id, "success": False, "message": str(ex)})

                v_succ = sum(1 for r in v_results if r["success"])
                is_store_success = (v_succ == len(product.variants))
                if is_store_success:
                    any_success = True
                    task.status = "SUCCESS"
                    task.message = f"全量成功上架所有 {v_succ} 个变体至【{s_name}】(刊登品牌: {brand})！"
                elif v_succ > 0:
                    any_success = True
                    task.status = "WARNING"
                    task.message = f"部分变体提交成功至【{s_name}】({v_succ}/{len(product.variants)}, 刊登品牌: {brand})"
                else:
                    task.status = "FAILED"
                    task.message = f"店铺【{s_name}】所有变体提交均失败"

                task.detail_logs = json.dumps(v_results, ensure_ascii=False)
                task.finished_at = datetime.now()
                db.commit()

                store_results.append({
                    "store_id": s_id,
                    "store_name": s_name,
                    "success": is_store_success,
                    "message": task.message,
                    "results": v_results
                })
            else:
                # 扁平独立商品直接发布
                p_res = _publish_single_product(client, db, product, valid_vertical, vid, brand, target_store=s_item)
                if p_res["success"]:
                    any_success = True
                    task.status = "SUCCESS"
                    task.message = f"成功上架商品至店铺【{s_name}】(刊登品牌: {brand}, 已提交审核)！"
                else:
                    task.status = "FAILED"
                    task.message = f"店铺【{s_name}】上架失败: {p_res['message']}"

                task.request_id = p_res.get("request_id")
                task.detail_logs = json.dumps(p_res, ensure_ascii=False)
                task.finished_at = datetime.now()
                db.commit()

                store_results.append({
                    "store_id": s_id,
                    "store_name": s_name,
                    "success": p_res["success"],
                    "request_id": p_res.get("request_id"),
                    "message": p_res.get("message")
                })
        except Exception as e:
            logger.error(f"店铺 {s_name} 上架异常: {e}", exc_info=True)
            task.status = "FAILED"
            task.message = f"店铺【{s_name}】上架异常: {e}"
            task.finished_at = datetime.now()
            db.commit()
            store_results.append({
                "store_id": s_id,
                "store_name": s_name,
                "success": False,
                "message": str(e)
            })

    if any_success:
        product.status = "SUBMITTED"
        product.makro_submit_error = None
    elif not product.status or product.status != "SUBMITTED":
        product.status = "FAILED"
        if store_results:
            product.makro_submit_error = store_results[0].get("message")
    db.commit()
    db.refresh(product)

    succ_count = sum(1 for r in store_results if r["success"])
    succ_names = "、".join([r["store_name"] for r in store_results if r["success"]]) or "无"
    record_audit_log(
        task_type="SUBMIT_LISTING",
        status="SUCCESS" if any_success else "FAILED",
        message=f"商品 #{product.id} 完成跨店铺上品 (成功店铺: {succ_names})",
        detail_logs={"product_id": product.id, "store_results": store_results},
        user_id=u_id,
        operator_name=op_name,
        db=db
    )


    return {
        "success": any_success,
        "message": f"跨店铺上品处理完成: 成功 {succ_count}/{len(store_results)} 家店铺",
        "request_id": product.makro_request_id,
        "store_results": store_results,
        "product": _format_product(product)
    }

@router.post("/batch-publish", summary="批量上品到 Makro (支持受控多线程并发与店铺间并行，后台异步执行)")
def batch_publish_products(
    req: BatchPublishRequest,
    force: Optional[bool] = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    if not req.product_ids:
        return {"total": 0, "success": 0, "failed": 0, "results": [], "message": "未选择商品"}

    u_id = current_user.id if current_user else None
    op_name = (current_user.nickname or current_user.username) if current_user else None

    effective_force = bool(getattr(req, "force", False) or (force is True))


    # 读取批量上品并发度配置 (支持请求显式指定，默认取系统配置 publish_concurrency，限制 1~5 线程)
    setting_concurrency = db.query(SystemSetting).filter(SystemSetting.key == "publish_concurrency").first()
    try:
        cfg_concurrency = int(setting_concurrency.value) if (setting_concurrency and setting_concurrency.value) else getattr(settings, "DEFAULT_PUBLISH_CONCURRENCY", 2)
    except Exception:
        cfg_concurrency = getattr(settings, "DEFAULT_PUBLISH_CONCURRENCY", 2)

    req_concurrency = getattr(req, "concurrency", None)
    raw_concurrency = req_concurrency if (req_concurrency is not None and req_concurrency > 0) else cfg_concurrency
    concurrency = max(1, min(5, raw_concurrency))

    target_stores = []
    if req.publish_all_stores:
        target_stores = db.query(Store).filter(Store.is_active == True).all()
        if not target_stores:
            target_stores = [None]
    elif req.store_id:
        store = db.query(Store).filter(Store.id == req.store_id).first()
        if store:
            target_stores = [store]
    elif req.store_ids:
        target_stores = db.query(Store).filter(Store.id.in_(req.store_ids), Store.is_active == True).all()

    if not target_stores:
        def_s = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first() or db.query(Store).filter(Store.is_active == True).first()
        target_stores = [def_s] if def_s else [None]

    total_ops = len(req.product_ids) * len(target_stores)
    store_names_str = "、".join([s.name if s else "默认店铺" for s in target_stores])
    force_tag = " [强制上架模式]" if effective_force else ""
    multi_store_tag = f" · {len(target_stores)}店并行" if len(target_stores) > 1 else ""
    concurrency_tag = f" [{concurrency}线程受控并发{multi_store_tag}]"
    task_title = f"批量上品至 Makro [{store_names_str}]{force_tag}{concurrency_tag} (共 {len(req.product_ids)} 件品 × {len(target_stores)} 店铺)"
    task = task_manager.create_task("BATCH_PUBLISH", task_title, total_ops, req.product_ids)
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):

        completed_lock = threading.Lock()
        completed_count = 0

        def _do_one_product(p_idx: int, pid: int):
            nonlocal completed_count
            if tm.is_cancelled(tid):
                return

            local_db = SessionLocal()
            try:
                product = local_db.query(Product).filter(Product.id == pid).first()
                if not product:
                    with completed_lock:
                        for _ in target_stores:
                            completed_count += 1
                            tm.update_progress(tid, current=completed_count, fail_inc=1, error=f"商品 {pid} 不存在")
                    return

                p_title = product.takealot_title

                if product.compliance_status == "PROHIBITED" and not effective_force:
                    with completed_lock:
                        for _ in target_stores:
                            completed_count += 1
                            tm.update_progress(tid, current=completed_count, current_title=p_title, fail_inc=1, error="违禁品拦截，跳过发布")
                    return

                raw_vertical = product.makro_vertical or "bath_towel"
                valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
                if valid_vertical != product.makro_vertical:
                    product.makro_vertical = valid_vertical
                    local_db.commit()

                # 店铺间交替轮询调度：不同商品由不同店铺优先启动，实现多店铺天然并行
                n_stores = len(target_stores)
                ordered_stores = [target_stores[(p_idx + s_offset) % n_stores] for s_offset in range(n_stores)]

                for s_item in ordered_stores:
                    if tm.is_cancelled(tid):
                        break

                    s_name = s_item.name if s_item else "默认店铺"
                    local_client = MakroClient.from_store(s_item) if s_item else MakroClient.from_db(local_db)
                    target_brand = (
                        (s_item.default_brand.strip() if s_item and getattr(s_item, "default_brand", None) and s_item.default_brand.strip() else None)
                        or _get_setting_val(local_db, "default_brand", settings.DEFAULT_BRAND)
                        or product.makro_brand
                        or "Beishi"
                    )
                    brand = target_brand
                    disp_title = f"【{s_name}·{brand}】{p_title[:16]}"

                    if not local_client.cookie:
                        with completed_lock:
                            completed_count += 1
                            cur_c = completed_count
                        tm.update_progress(tid, current=cur_c, current_title=disp_title, fail_inc=1, error=f"店铺【{s_name}】未配置 Cookie")
                        _record_store_listing(local_db, product.id, s_item, False, None, None, f"店铺【{s_name}】未配置 Cookie", product.makro_selling_price, product.makro_mrp, brand=brand, user_id=u_id)
                        continue

                    try:
                        res = _publish_single_product(local_client, local_db, product, valid_vertical, vid, brand, target_store=s_item)
                        with completed_lock:
                            completed_count += 1
                            cur_c = completed_count
                        if res["success"]:
                            tm.update_progress(tid, current=cur_c, current_title=disp_title, success_inc=1)
                        else:
                            tm.update_progress(tid, current=cur_c, current_title=disp_title, fail_inc=1, error=res.get("message"))
                    except Exception as ex:
                        logger.error(f"商品 {pid} 在店铺 {s_name} 批量上架异常: {ex}", exc_info=True)
                        with completed_lock:
                            completed_count += 1
                            cur_c = completed_count
                        tm.update_progress(tid, current=cur_c, current_title=disp_title, fail_inc=1, error=str(ex))
            finally:
                local_db.close()

        actual_workers = min(concurrency, max(1, len(req.product_ids)))
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            futures = [executor.submit(_do_one_product, idx, pid) for idx, pid in enumerate(req.product_ids)]
            for fut in as_completed(futures):
                if tm.is_cancelled(tid):
                    break
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"批量上品子任务执行异常: {e}", exc_info=True)

        t_now = tm.get_task(tid)
        succ = t_now["success_count"] if t_now else 0
        fail = t_now["fail_count"] if t_now else 0
        status = "CANCELLED" if tm.is_cancelled(tid) else ("SUCCESS" if fail == 0 else ("FAILED" if succ == 0 else "SUCCESS"))
        msg = f"批量上品完成 (受控并发度: {actual_workers} 线程): 成功 {succ} 次, 失败 {fail} 次"
        tm.finish_task(tid, status=status, message=msg)
        record_audit_log(
            task_type="BATCH_PUBLISH",
            status=status,
            message=msg,
            detail_logs={"total": total_ops, "success": succ, "failed": fail, "product_ids": req.product_ids[:50]},
            user_id=u_id,
            operator_name=op_name
        )


    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total_ops,
        "concurrency": concurrency,
        "message": f"已在后台启动多店铺批量上品至 Makro (共 {total_ops} 次任务分发 · {concurrency} 线程并发)"
    }

@router.post("/publish-variant/{variant_id}", summary="单变体独立上品或重新上架到 Makro")
def publish_single_variant_to_makro(
    variant_id: int,
    store_id: Optional[int] = None,
    force: bool = False,
    db: Session = Depends(get_db)
):
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="变体未找到")
    product = variant.product
    if not product:
        raise HTTPException(status_code=404, detail="所属商品未找到")

    if product.compliance_status == "PROHIBITED" and not force:
        raise HTTPException(status_code=400, detail="【违禁品拦截】该商品命中平台禁售规则！")

    target_store = None
    if store_id:
        target_store = db.query(Store).filter(Store.id == store_id).first()
    if not target_store:
        target_store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()
    if not target_store:
        target_store = db.query(Store).filter(Store.is_active == True).first()

    client = MakroClient.from_store(target_store) if target_store else MakroClient.from_db(db)
    if not client.cookie:
        raise HTTPException(status_code=400, detail="未检测到 Makro 登录态 Cookie！请先在多店铺管理中配置 Cookie。")

    raw_vertical = product.makro_vertical or "bath_towel"
    valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
    target_brand = (
        (target_store.default_brand.strip() if target_store and getattr(target_store, "default_brand", None) and target_store.default_brand.strip() else None)
        or _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND)
        or product.makro_brand
        or "Beishi"
    )
    brand = target_brand

    try:
        res = _publish_single_variant(client, db, product, variant, valid_vertical, vid, brand, target_store=target_store)
        all_submitted = all(v.status == "SUBMITTED" for v in product.variants)
        any_submitted = any(v.status == "SUBMITTED" for v in product.variants)

        if all_submitted:
            product.status = "SUBMITTED"
            product.makro_submit_error = None
        elif any_submitted:
            product.status = "PARTIAL_SUBMITTED"
        db.commit()

        return {
            "success": res["success"],
            "message": res["message"],
            "result": res,
            "variant": {
                "id": variant.id,
                "sku_id": variant.sku_id,
                "status": variant.status,
                "makro_request_id": variant.makro_request_id,
                "makro_submit_error": variant.makro_submit_error
            }
        }
    except Exception as e:
        logger.error(f"单变体上架异常: {e}", exc_info=True)
        variant.status = "FAILED"
        variant.makro_submit_error = str(e)
        db.commit()
        return {"success": False, "message": str(e), "variant_id": variant.id}

@router.get("/build-payload/{product_id}", summary="预览构建的 Makro 上品请求体 (可用于调试或插件代发)")
def get_submit_payload_preview(
    product_id: int,
    variant_id: Optional[int] = None,
    store_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    mock_req_id = product.makro_request_id or "REQMOCK12345678"
    mock_txn = f"TXN-{product.id}"
    mock_req = f"REQ-{product.id}"
    images = json.loads(product.makro_images) if product.makro_images else {"0": "https://www.makro.co.za/asset/cms/sample"}
    
    target_variant = None
    if variant_id:
        target_variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()

    target_store = None
    if store_id:
        target_store = db.query(Store).filter(Store.id == store_id).first()
    if not target_store:
        target_store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()

    payload = _build_makro_payload(db, product, mock_req_id, mock_txn, mock_req, images, variant=target_variant, target_store=target_store)
    return payload

@router.post("/sync-credentials", summary="从浏览器插件同步 Makro 登录态凭据")
def sync_credentials(req: SyncCredentialsRequest, db: Session = Depends(get_db)):
    updates = {}
    if req.seller_id:
        updates["seller_id"] = req.seller_id
    if req.fk_csrf_token:
        updates["fk_csrf_token"] = req.fk_csrf_token
    if req.cookie:
        updates["cookie"] = req.cookie

    for k, v in updates.items():
        item = db.query(SystemSetting).filter(SystemSetting.key == k).first()
        if item:
            item.value = v
        else:
            db.add(SystemSetting(key=k, value=v))

    # 联动同步更新 stores 表中的店铺实体 (优先按 seller_id 匹配，兜底按默认/首个活跃店)
    matched_stores = []
    if req.seller_id:
        matched_stores = db.query(Store).filter(Store.seller_id == req.seller_id).all()
    if not matched_stores:
        matched_stores = db.query(Store).filter(Store.is_default == True, Store.is_active == True).all()
    if not matched_stores:
        matched_stores = db.query(Store).filter(Store.is_active == True).all()

    for s in matched_stores:
        if req.fk_csrf_token:
            s.fk_csrf_token = req.fk_csrf_token
        if req.cookie:
            s.cookie = req.cookie
        s.updated_at = datetime.now()

    db.commit()

    store_names = [s.name for s in matched_stores]
    return {
        "message": f"凭据同步成功 (已同步至系统配置及 {len(matched_stores)} 个店铺: {', '.join(store_names)})",
        "synced_keys": list(updates.keys()),
        "updated_stores": store_names
    }
