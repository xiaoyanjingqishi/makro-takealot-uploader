import re
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from ..database import get_db
from ..models.product import Product
from ..models.task import TaskLog
from ..schemas.product import BatchCleanRequest, ProductResponse
from ..services.ai_cleaner_service import AICleanerService
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
from .products import _format_product

router = APIRouter(prefix="/cleaner", tags=["AI清洗与规范化"])

@router.post("/clean/{product_id}", response_model=ProductResponse, summary="单品触发 AI 数据清洗")
def clean_single_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    task = TaskLog(
        product_id=product.id,
        task_type="CLEAN",
        status="RUNNING",
        message=f"正在对商品 {product.id} 执行 AI 清洗...",
        created_at=datetime.now()
    )
    db.add(task)
    db.commit()

    try:
        ai_service = AICleanerService.from_db(db)
        specs = json.loads(product.takealot_specs) if product.takealot_specs else {}
        var_attrs = json.loads(product.variant_attributes) if product.variant_attributes else {}
        combined_specs = {**specs, **var_attrs}
        
        cleaned = ai_service.clean_product_data({
            "takealot_title": product.takealot_title,
            "takealot_brand": product.takealot_brand,
            "takealot_category": product.takealot_category,
            "takealot_specs": combined_specs,
            "takealot_description": product.takealot_description
        }, target_brand=product.makro_brand or "Beishi")

        product.makro_title = cleaned.get("makro_title", product.takealot_title)
        raw_desc = cleaned.get("description", product.takealot_description)
        product.makro_description = "\n".join(str(x) for x in raw_desc) if isinstance(raw_desc, list) else (str(raw_desc) if raw_desc else None)
        product.makro_vertical = cleaned.get("vertical", "bath_towel")
        
        attrs = cleaned.get("attributes", {})
        catalog_attrs = {}
        for k, val in attrs.items():
            qualifier = None
            if k in ["width", "length"]:
                qualifier = "cm"
            catalog_attrs[k] = [{"value": str(val), "qualifier": qualifier}]

        # 变体专有属性直接覆盖/注入
        if product.colour or var_attrs.get("colour"):
            c_val = str(product.colour or var_attrs.get("colour"))
            catalog_attrs["colour"] = [{"value": c_val, "qualifier": None}]
            catalog_attrs["brand_colour"] = [{"value": str(product.brand_colour or c_val), "qualifier": None}]
        if product.size or var_attrs.get("size"):
            catalog_attrs["size"] = [{"value": str(product.size or var_attrs.get("size")), "qualifier": None}]
        if product.pack_of or var_attrs.get("pack_of"):
            catalog_attrs["pack_of"] = [{"value": str(product.pack_of or var_attrs.get("pack_of")), "qualifier": None}]
        cap = var_attrs.get("capacity") or var_attrs.get("storage_capacity")
        if cap:
            catalog_attrs["storage_capacity"] = [{"value": str(cap), "qualifier": None}]

        # 确保 model_number 放入去除品牌名后的商品描述，防止触发限制
        target_b = product.makro_brand or "Beishi"
        clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', product.makro_title or "", flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{product.id}")[:250], "qualifier": None}]

        product.makro_catalog_attributes = json.dumps(catalog_attrs)
        product.makro_submit_error = None
        product.status = "CLEANED"
        task.status = "SUCCESS"
        task.message = f"AI 清洗完成: 生成规范标题「{product.makro_title[:30]}...」与类目属性"
        task.finished_at = datetime.now()
        task.detail_logs = json.dumps(cleaned, ensure_ascii=False)

        db.commit()
        db.refresh(product)
        return _format_product(product)
    except Exception as e:
        task.status = "FAILED"
        task.message = f"清洗失败: {str(e)}"
        task.finished_at = datetime.now()
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/batch-clean", summary="批量执行 AI 数据清洗 (后台异步多线程任务)")
def batch_clean_products(req: BatchCleanRequest, db: Session = Depends(get_db)):
    if not req.product_ids:
        return {"total": 0, "success": 0, "failed": 0, "errors": [], "message": "未选择商品"}

    ai_service = AICleanerService.from_db(db)
    total = len(req.product_ids)
    task = task_manager.create_task("BATCH_CLEAN", "批量AI数据清洗", total, req.product_ids)
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..database import SessionLocal

        def _do_one(pid: int):
            if tm.is_cancelled(tid):
                return pid, False, "任务已取消", ""
            local_db = SessionLocal()
            try:
                prod = local_db.query(Product).filter(Product.id == pid).first()
                if not prod:
                    return pid, False, f"商品 {pid} 不存在", ""
                p_title = prod.takealot_title

                specs = json.loads(prod.takealot_specs) if prod.takealot_specs else {}
                var_attrs = json.loads(prod.variant_attributes) if prod.variant_attributes else {}
                combined_specs = {**specs, **var_attrs}

                cleaned = ai_service.clean_product_data({
                    "takealot_title": prod.takealot_title,
                    "takealot_brand": prod.takealot_brand,
                    "takealot_category": prod.takealot_category,
                    "takealot_specs": combined_specs,
                    "takealot_description": prod.takealot_description
                }, target_brand=prod.makro_brand or "Beishi")

                prod.makro_title = cleaned.get("makro_title", prod.takealot_title)
                raw_desc = cleaned.get("description", prod.takealot_description)
                prod.makro_description = "\n".join(str(x) for x in raw_desc) if isinstance(raw_desc, list) else (str(raw_desc) if raw_desc else None)
                prod.makro_vertical = cleaned.get("vertical", "bath_towel")

                attrs = cleaned.get("attributes", {})
                catalog_attrs = {}
                for k, val in attrs.items():
                    qualifier = None
                    if k in ["width", "length"]:
                        qualifier = "cm"
                    catalog_attrs[k] = [{"value": str(val), "qualifier": qualifier}]

                if prod.colour or var_attrs.get("colour"):
                    c_val = str(prod.colour or var_attrs.get("colour"))
                    catalog_attrs["colour"] = [{"value": c_val, "qualifier": None}]
                    catalog_attrs["brand_colour"] = [{"value": str(prod.brand_colour or c_val), "qualifier": None}]
                if prod.size or var_attrs.get("size"):
                    catalog_attrs["size"] = [{"value": str(prod.size or var_attrs.get("size")), "qualifier": None}]
                if prod.pack_of or var_attrs.get("pack_of"):
                    catalog_attrs["pack_of"] = [{"value": str(prod.pack_of or var_attrs.get("pack_of")), "qualifier": None}]
                cap = var_attrs.get("capacity") or var_attrs.get("storage_capacity")
                if cap:
                    catalog_attrs["storage_capacity"] = [{"value": str(cap), "qualifier": None}]

                target_b = prod.makro_brand or "Beishi"
                clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', prod.makro_title or "", flags=re.I)
                clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
                catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{prod.id}")[:250], "qualifier": None}]
                prod.makro_catalog_attributes = json.dumps(catalog_attrs)
                prod.makro_submit_error = None
                prod.status = "CLEANED"
                local_db.commit()
                return pid, True, None, p_title
            except Exception as e:
                return pid, False, f"商品 {pid} 清洗失败: {str(e)}", ""
            finally:
                local_db.close()

        max_workers = min(6, max(1, total))
        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_do_one, pid): pid for pid in req.product_ids}
            for fut in as_completed(futures):
                if tm.is_cancelled(tid):
                    break
                pid, ok, err, title = fut.result()
                completed_count += 1
                tm.update_progress(
                    tid,
                    current=completed_count,
                    current_title=title or f"商品 ID {pid}",
                    success_inc=1 if ok else 0,
                    fail_inc=0 if ok else 1,
                    error=err
                )

        t_now = tm.get_task(tid)
        succ = t_now["success_count"] if t_now else 0
        fail = t_now["fail_count"] if t_now else 0
        status = "CANCELLED" if tm.is_cancelled(tid) else ("SUCCESS" if fail == 0 else ("FAILED" if succ == 0 else "SUCCESS"))
        tm.finish_task(tid, status=status, message=f"批量AI清洗完成: 成功 {succ} 件, 失败 {fail} 件")

    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total,
        "message": f"已在后台启动批量AI清洗 (共 {total} 件商品)"
    }

@router.post("/batch-compliance", summary="批量执行 AI 侵权与合规检测 (后台异步多线程任务)")
def batch_check_compliance(req: BatchCleanRequest, db: Session = Depends(get_db)):
    if not req.product_ids:
        return {"total": 0, "success": 0, "failed": 0, "errors": [], "message": "未选择商品"}

    from ..services.compliance_service import ComplianceService
    cs = ComplianceService.from_db(db)
    total = len(req.product_ids)
    task = task_manager.create_task("BATCH_COMPLIANCE", "批量合规与侵权排查", total, req.product_ids)
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..database import SessionLocal

        def _do_one(pid: int):
            if tm.is_cancelled(tid):
                return pid, False, "任务已取消", ""
            local_db = SessionLocal()
            try:
                prod = local_db.query(Product).filter(Product.id == pid).first()
                if not prod:
                    return pid, False, f"商品 {pid} 不存在", ""
                p_title = prod.takealot_title

                specs = json.loads(prod.takealot_specs) if prod.takealot_specs else {}
                comp_res = cs.check_product({
                    "takealot_title": prod.takealot_title,
                    "makro_title": prod.makro_title,
                    "takealot_brand": prod.takealot_brand,
                    "takealot_category": prod.takealot_category,
                    "takealot_specs": specs,
                    "takealot_description": prod.takealot_description,
                    "makro_brand": prod.makro_brand,
                    "raw_images": prod.raw_images
                }, check_image=True)

                prod.compliance_status = comp_res.get("compliance_status", "SAFE")
                prod.compliance_details = json.dumps(comp_res, ensure_ascii=False)
                local_db.commit()
                return pid, True, None, p_title
            except Exception as e:
                return pid, False, f"商品 {pid} 合规检测异常: {str(e)}", ""
            finally:
                local_db.close()

        max_workers = min(6, max(1, total))
        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_do_one, pid): pid for pid in req.product_ids}
            for fut in as_completed(futures):
                if tm.is_cancelled(tid):
                    break
                pid, ok, err, title = fut.result()
                completed_count += 1
                tm.update_progress(
                    tid,
                    current=completed_count,
                    current_title=title or f"商品 ID {pid}",
                    success_inc=1 if ok else 0,
                    fail_inc=0 if ok else 1,
                    error=err
                )

        t_now = tm.get_task(tid)
        succ = t_now["success_count"] if t_now else 0
        fail = t_now["fail_count"] if t_now else 0
        status = "CANCELLED" if tm.is_cancelled(tid) else ("SUCCESS" if fail == 0 else ("FAILED" if succ == 0 else "SUCCESS"))
        tm.finish_task(tid, status=status, message=f"批量合规排查完成: 成功 {succ} 件, 失败 {fail} 件")

    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total,
        "message": f"已在后台启动批量合规排查 (共 {total} 件商品)"
    }

@router.post("/check-compliance/{product_id}", summary="单品独立触发 AI 侵权与违禁品全量检测 (含首图视觉)")
def check_single_compliance(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    from ..services.compliance_service import ComplianceService
    cs = ComplianceService.from_db(db)
    specs = json.loads(product.takealot_specs) if product.takealot_specs else {}

    comp_res = cs.check_product({
        "takealot_title": product.takealot_title,
        "makro_title": product.makro_title,
        "takealot_brand": product.takealot_brand,
        "takealot_category": product.takealot_category,
        "takealot_specs": specs,
        "takealot_description": product.takealot_description,
        "makro_brand": product.makro_brand,
        "raw_images": product.raw_images
    }, check_image=True)

    product.compliance_status = comp_res.get("compliance_status", "SAFE")
    product.compliance_details = json.dumps(comp_res, ensure_ascii=False)
    db.commit()
    db.refresh(product)

    # 记录单品合规检测操作日志
    record_audit_log(
        task_type="COMPLIANCE",
        status="SUCCESS",
        message=f"商品 ID {product.id} 合规检测完成: 状态={product.compliance_status}",
        product_id=product.id,
        detail_logs=comp_res,
        db=db
    )

    return {
        "product_id": product.id,
        "compliance_status": product.compliance_status,
        "compliance_details": comp_res
    }

@router.get("/verticals", summary="获取系统支持的全部 Makro 垂直类目")
def get_supported_verticals():
    from ..services.vertical_service import VerticalService
    return {
        "supported_verticals": VerticalService.list_verticals()
    }
