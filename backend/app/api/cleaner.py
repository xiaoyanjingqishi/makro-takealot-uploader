import re
import json
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from ..database import get_db
from ..models.product import Product
from ..models.task import TaskLog
from ..models.compliance_log import ComplianceArbitrationLog
from ..schemas.product import BatchCleanRequest, ProductResponse
from ..services.ai_cleaner_service import AICleanerService
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
from .products import _format_product

class ArbitrateComplianceRequest(BaseModel):
    human_verdict: str  # 'SAFE', 'RISK', 'PROHIBITED'
    human_notes: Optional[str] = None

router = APIRouter(prefix="/cleaner", tags=["AI清洗与规范化"])

LISTING_ONLY_ATTRS = {
    "country_of_origin", "mrp", "flipkart_selling_price", "shipping_days",
    "listing_status", "service_profile", "packer_details", "manufacturer_details",
    "importer_details", "packages", "forbid_shipping", "max_order_quantity_allowed",
    "minimum_order_quantity", "sku_id"
}

@router.post("/clean/{product_id}", response_model=ProductResponse, summary="单品触发 AI 数据清洗")
def clean_single_product(
    product_id: int,
    mode: Optional[str] = Query(None, description="清洗模式: 'text'(纯文本) 或 'vision'(图文多模态)"),
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    mode_label = "图文多模态" if mode == "vision" else "纯文本"
    task = TaskLog(
        product_id=product.id,
        task_type="CLEAN",
        status="RUNNING",
        message=f"正在对商品 {product.id} 执行 AI {mode_label}清洗...",
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
            "id": product.id,
            "takealot_title": product.takealot_title,
            "takealot_category": product.takealot_category,
            "takealot_specs": combined_specs,
            "takealot_description": product.takealot_description,
            "raw_images": product.raw_images,
            "cover_image": product.raw_images
        }, target_brand=product.makro_brand or "Beishi", clean_mode=mode)

        product.makro_title = cleaned.get("makro_title", product.takealot_title)
        raw_seo_kw = cleaned.get("seo_keywords") or []
        if isinstance(raw_seo_kw, list) and raw_seo_kw:
            product.seo_keywords = json.dumps([str(x).strip() for x in raw_seo_kw if str(x).strip()][:6], ensure_ascii=False)

        raw_desc = cleaned.get("description", product.takealot_description)
        product.makro_description = "\n".join(str(x) for x in raw_desc) if isinstance(raw_desc, list) else (str(raw_desc) if raw_desc else None)
        
        from ..services.vertical_service import VerticalService
        raw_vertical = cleaned.get("vertical", "")
        resolved_v, _ = VerticalService.resolve_vertical(raw_vertical)
        if resolved_v == "bath_towel" and "towel" not in (product.takealot_title or "").lower():
            resolved_v = VerticalService.predict_vertical(
                title=product.takealot_title,
                category=product.takealot_category or "",
                specs=combined_specs,
                description=product.takealot_description or ""
            )
        product.makro_vertical = resolved_v
        
        attrs = cleaned.get("attributes", {})
        catalog_attrs = {}
        for k, val in attrs.items():
            if k in LISTING_ONLY_ATTRS:
                continue  # 排除纯 Listing 级属性，严防混入 Catalog 导致 412
            qualifier = None
            if k in ["width", "length"]:
                qualifier = "cm"
            catalog_attrs[k] = [{"value": str(val), "qualifier": qualifier}]

        # 同步更新商品主属性 (排除非服装下的假数据)
        if cleaned.get("size"):
            product.size = str(cleaned.get("size"))
        elif resolved_v != "costume_wear" and product.size == "均码":
            product.size = None

        if cleaned.get("colour"):
            product.colour = str(cleaned.get("colour"))
        elif resolved_v != "costume_wear" and product.colour == "多色":
            product.colour = None

        if cleaned.get("pack_of"):
            product.pack_of = str(cleaned.get("pack_of"))

        # 变体专有属性直接覆盖/注入 (严格排斥非服装下的“均码”与“多色”)
        clean_c = product.colour or var_attrs.get("colour")
        if clean_c and str(clean_c) != "多色":
            catalog_attrs["colour"] = [{"value": str(clean_c), "qualifier": None}]
            catalog_attrs["brand_colour"] = [{"value": str(product.brand_colour or clean_c), "qualifier": None}]

        clean_s = product.size or var_attrs.get("size")
        if clean_s and (str(clean_s) != "均码" or resolved_v == "costume_wear"):
            catalog_attrs["size"] = [{"value": str(clean_s), "qualifier": None}]

        clean_p = product.pack_of or var_attrs.get("pack_of")
        if clean_p:
            catalog_attrs["pack_of"] = [{"value": str(clean_p), "qualifier": None}]

        cap = var_attrs.get("capacity") or var_attrs.get("storage_capacity")
        if cap:
            catalog_attrs["storage_capacity"] = [{"value": str(cap), "qualifier": None}]

        # 确保 model_number 放入去除品牌名后的商品描述，防止触发限制
        target_b = product.makro_brand or "Beishi"
        clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', product.makro_title or "", flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{product.id}")[:250], "qualifier": None}]

        product.clean_mode = cleaned.get("clean_mode", mode or "text")
        product.makro_catalog_attributes = json.dumps(catalog_attrs)
        product.makro_submit_error = None
        product.status = "CLEANED"
        task.status = "SUCCESS"
        applied_mode_label = "图文多模态" if product.clean_mode == "vision" else "纯文本"
        task.message = f"AI {applied_mode_label}清洗完成: 生成规范标题「{product.makro_title[:30]}...」与类目属性"
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
    clean_mode = req.clean_mode
    mode_label = "图文多模态" if clean_mode == "vision" else ("纯文本" if clean_mode == "text" else "默认模式")
    task = task_manager.create_task("BATCH_CLEAN", f"批量AI数据清洗 ({mode_label})", total, req.product_ids)
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
                    "takealot_description": prod.takealot_description,
                    "raw_images": prod.raw_images,
                    "cover_image": prod.raw_images
                }, target_brand=prod.makro_brand or "Beishi", clean_mode=clean_mode)

                prod.makro_title = cleaned.get("makro_title", prod.takealot_title)
                raw_seo_kw = cleaned.get("seo_keywords") or []
                if isinstance(raw_seo_kw, list) and raw_seo_kw:
                    prod.seo_keywords = json.dumps([str(x).strip() for x in raw_seo_kw if str(x).strip()][:6], ensure_ascii=False)

                raw_desc = cleaned.get("description", prod.takealot_description)
                prod.makro_description = "\n".join(str(x) for x in raw_desc) if isinstance(raw_desc, list) else (str(raw_desc) if raw_desc else None)
                
                from ..services.vertical_service import VerticalService
                raw_v = cleaned.get("vertical", "")
                resolved_batch_v, _ = VerticalService.resolve_vertical(raw_v)
                if resolved_batch_v == "bath_towel" and "towel" not in (prod.takealot_title or "").lower():
                    resolved_batch_v = VerticalService.predict_vertical(
                        title=prod.takealot_title,
                        category=prod.takealot_category or "",
                        specs=combined_specs,
                        description=prod.takealot_description or ""
                    )
                prod.makro_vertical = resolved_batch_v

                attrs = cleaned.get("attributes", {})
                catalog_attrs = {}
                for k, val in attrs.items():
                    if k in LISTING_ONLY_ATTRS:
                        continue  # 排除纯 Listing 级属性，严防混入 Catalog 导致 412
                    qualifier = None
                    if k in ["width", "length"]:
                        qualifier = "cm"
                    catalog_attrs[k] = [{"value": str(val), "qualifier": qualifier}]

                # 同步更新商品主属性 (排除非服装下的假数据)
                if cleaned.get("size"):
                    prod.size = str(cleaned.get("size"))
                elif resolved_batch_v != "costume_wear" and prod.size == "均码":
                    prod.size = None

                if cleaned.get("colour"):
                    prod.colour = str(cleaned.get("colour"))
                elif resolved_batch_v != "costume_wear" and prod.colour == "多色":
                    prod.colour = None

                if cleaned.get("pack_of"):
                    prod.pack_of = str(cleaned.get("pack_of"))

                clean_c = prod.colour or var_attrs.get("colour")
                if clean_c and str(clean_c) != "多色":
                    catalog_attrs["colour"] = [{"value": str(clean_c), "qualifier": None}]
                    catalog_attrs["brand_colour"] = [{"value": str(prod.brand_colour or clean_c), "qualifier": None}]

                clean_s = prod.size or var_attrs.get("size")
                if clean_s and (str(clean_s) != "均码" or resolved_batch_v == "costume_wear"):
                    catalog_attrs["size"] = [{"value": str(clean_s), "qualifier": None}]

                clean_p = prod.pack_of or var_attrs.get("pack_of")
                if clean_p:
                    catalog_attrs["pack_of"] = [{"value": str(clean_p), "qualifier": None}]

                cap = var_attrs.get("capacity") or var_attrs.get("storage_capacity")
                if cap:
                    catalog_attrs["storage_capacity"] = [{"value": str(cap), "qualifier": None}]

                target_b = prod.makro_brand or "Beishi"
                clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', prod.makro_title or "", flags=re.I)
                clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
                catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{prod.id}")[:250], "qualifier": None}]
                prod.clean_mode = cleaned.get("clean_mode", clean_mode or "text")
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
    concurrency_limit = max(1, min(req.concurrency or 20, 50))
    task = task_manager.create_task("BATCH_COMPLIANCE", f"批量合规与侵权排查 ({concurrency_limit}线程并发)", total, req.product_ids)
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
                }, check_image=True, check_ai_title=True)

                prod.compliance_status = comp_res.get("compliance_status", "SAFE")
                prod.compliance_details = json.dumps(comp_res, ensure_ascii=False)
                local_db.commit()
                return pid, True, None, p_title
            except Exception as e:
                return pid, False, f"商品 {pid} 合规检测异常: {str(e)}", ""
            finally:
                local_db.close()

        max_workers = min(concurrency_limit, max(1, total))
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
        tm.finish_task(tid, status=status, message=f"批量合规排查完成: 成功 {succ} 件, 失败 {fail} 件 ({concurrency_limit}线程)")

    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total,
        "concurrency": concurrency_limit,
        "message": f"已在后台启动批量合规排查 (共 {total} 件商品，{concurrency_limit} 线程受控并发)"
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
    }, check_image=True, check_ai_title=True)

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

@router.post("/arbitrate-compliance/{product_id}", summary="人工终审仲裁双 AI 合规分歧并沉淀语料日志")
def arbitrate_compliance(
    product_id: int,
    req: ArbitrateComplianceRequest,
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    human = req.human_verdict.upper()
    if human not in ["SAFE", "RISK", "PROHIBITED"]:
        raise HTTPException(status_code=400, detail="裁决状态必须为 SAFE, RISK 或 PROHIBITED")

    # 解析现有会审细节
    details = {}
    if product.compliance_details:
        try:
            details = json.loads(product.compliance_details)
        except Exception:
            details = {}

    qwen_verdict = details.get("qwen_verdict", {})
    deepseek_verdict = details.get("deepseek_verdict", {})
    qwen_status = qwen_verdict.get("overall_risk", "SAFE")
    deepseek_status = deepseek_verdict.get("overall_risk", "SAFE")
    has_dual = details.get("dual_ai_mode", False)

    # 智能归因分析
    if has_dual:
        if qwen_status == human and deepseek_status == human:
            attribution = "CONSENSUS_AFFIRMED"
        elif qwen_status != human and deepseek_status == human:
            attribution = "QWEN_FALSE_POSITIVE" if (qwen_status in ["RISK", "PROHIBITED"] and human == "SAFE") else "QWEN_FALSE_NEGATIVE"
        elif deepseek_status != human and qwen_status == human:
            attribution = "DEEPSEEK_FALSE_POSITIVE" if (deepseek_status in ["RISK", "PROHIBITED"] and human == "SAFE") else "DEEPSEEK_FALSE_NEGATIVE"
        else:
            attribution = "BOTH_MISJUDGED"
    else:
        if qwen_status == human:
            attribution = "QWEN_AFFIRMED"
        else:
            attribution = "QWEN_FALSE_POSITIVE" if (qwen_status in ["RISK", "PROHIBITED"] and human == "SAFE") else "QWEN_FALSE_NEGATIVE"

    # 获取首图 URL
    first_img_url = None
    if product.raw_images:
        try:
            imgs = json.loads(product.raw_images) if isinstance(product.raw_images, str) else product.raw_images
            if isinstance(imgs, list) and len(imgs) > 0 and isinstance(imgs[0], str):
                first_img_url = imgs[0]
        except Exception:
            first_img_url = None

    # 更新商品合规状态与细节快照
    product.compliance_status = human
    details["compliance_status"] = human
    details["is_disputed"] = False
    details["human_arbitration"] = {
        "human_verdict": human,
        "notes": req.human_notes or "",
        "error_attribution": attribution,
        "arbitrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    product.compliance_details = json.dumps(details, ensure_ascii=False)

    # 记录持久化仲裁日志与提示词样本
    arbitration_log = ComplianceArbitrationLog(
        product_id=product.id,
        takealot_title=product.takealot_title,
        makro_title=product.makro_title,
        brand=product.makro_brand or product.takealot_brand or "Beishi",
        image_url=first_img_url,
        qwen_verdict=json.dumps(qwen_verdict, ensure_ascii=False) if qwen_verdict else None,
        deepseek_verdict=json.dumps(deepseek_verdict, ensure_ascii=False) if deepseek_verdict else None,
        qwen_status=qwen_status,
        deepseek_status=deepseek_status,
        human_verdict=human,
        error_attribution=attribution,
        dispute_keywords=json.dumps(details.get("brand_info", {}).get("detected_brands", []), ensure_ascii=False),
        human_notes=req.human_notes,
        created_at=datetime.now(),
        arbitrated_at=datetime.now()
    )
    db.add(arbitration_log)
    db.commit()
    db.refresh(product)

    # 记录操作审计日志
    record_audit_log(
        task_type="ARBITRATION",
        status="SUCCESS",
        message=f"商品 ID {product.id} 人工终审完成: 裁定为 [{human}], 归因标注=[{attribution}]",
        product_id=product.id,
        detail_logs={
            "human_verdict": human,
            "attribution": attribution,
            "qwen_status": qwen_status,
            "deepseek_status": deepseek_status,
            "notes": req.human_notes
        },
        db=db
    )

    return {
        "success": True,
        "product_id": product.id,
        "compliance_status": product.compliance_status,
        "error_attribution": attribution,
        "arbitration_log_id": arbitration_log.id,
        "compliance_details": details
    }

@router.get("/compliance-logs", summary="获取双 AI 分歧仲裁与提示词优化语料日志")
def get_compliance_logs(
    attribution: Optional[str] = Query(None, description="按归因过滤: 如 QWEN_FALSE_POSITIVE"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    query = db.query(ComplianceArbitrationLog)
    if attribution:
        query = query.filter(ComplianceArbitrationLog.error_attribution == attribution)
    
    logs = query.order_by(ComplianceArbitrationLog.id.desc()).limit(limit).all()

    # 汇总统计
    all_logs = db.query(ComplianceArbitrationLog).all()
    stats = {
        "total": len(all_logs),
        "qwen_false_positive": sum(1 for l in all_logs if l.error_attribution == "QWEN_FALSE_POSITIVE"),
        "qwen_false_negative": sum(1 for l in all_logs if l.error_attribution == "QWEN_FALSE_NEGATIVE"),
        "deepseek_false_positive": sum(1 for l in all_logs if l.error_attribution == "DEEPSEEK_FALSE_POSITIVE"),
        "deepseek_false_negative": sum(1 for l in all_logs if l.error_attribution == "DEEPSEEK_FALSE_NEGATIVE"),
        "both_misjudged": sum(1 for l in all_logs if l.error_attribution == "BOTH_MISJUDGED"),
        "consensus_affirmed": sum(1 for l in all_logs if l.error_attribution in ["CONSENSUS_AFFIRMED", "QWEN_AFFIRMED"])
    }

    result_items = []
    for l in logs:
        result_items.append({
            "id": l.id,
            "product_id": l.product_id,
            "title": l.makro_title or l.takealot_title,
            "brand": l.brand,
            "image_url": l.image_url,
            "qwen_status": l.qwen_status,
            "deepseek_status": l.deepseek_status,
            "human_verdict": l.human_verdict,
            "error_attribution": l.error_attribution,
            "dispute_keywords": json.loads(l.dispute_keywords) if l.dispute_keywords else [],
            "human_notes": l.human_notes,
            "arbitrated_at": l.arbitrated_at.strftime("%Y-%m-%d %H:%M:%S") if l.arbitrated_at else ""
        })

    return {
        "stats": stats,
        "total": len(result_items),
        "logs": result_items
    }

@router.get("/verticals", summary="获取系统支持的全部 Makro 垂直类目")
def get_supported_verticals():
    from ..services.vertical_service import VerticalService
    return {
        "supported_verticals": VerticalService.list_verticals()
    }

