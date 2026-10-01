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
from ..services.ai_cleaner_service import AICleanerService, truncate_title_safely, format_title_with_specs, clean_spec_value
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
from ..models.user import User
from ..utils.auth import get_optional_current_user
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
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    u_id = current_user.id if current_user else product.user_id
    op_name = (current_user.nickname or current_user.username) if current_user else None

    mode_label = "图文多模态" if mode == "vision" else "纯文本"
    task = TaskLog(
        product_id=product.id,
        task_type="CLEAN",
        status="RUNNING",
        user_id=u_id,
        operator_name=op_name,
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
        from ..services.translation_service import TranslationService
        product.takealot_title_zh = cleaned.get("takealot_title_zh") or TranslationService.translate_title(product.takealot_title, db=db)
        product.makro_title_zh = cleaned.get("makro_title_zh") or TranslationService.translate_title(product.makro_title, db=db)
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

        target_b = product.makro_brand or "Beishi"
        # 风格 B: 确保 makro_title 正确融合 (Color, Size) 规格
        clean_c = product.colour or var_attrs.get("colour")
        clean_s = product.size or var_attrs.get("size")
        product.makro_title = format_title_with_specs(
            product.makro_title,
            brand=target_b,
            color=clean_c,
            size=clean_s,
            max_len=ai_service.seo_title_max_len,
            vertical=resolved_v
        )

        # 选项 1: 将去除品牌名后的完整标题写入 model_number 与 model_name (放宽至 120 字符)
        clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', product.makro_title or "", flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{product.id}")[:250], "qualifier": None}]
        catalog_attrs["model_name"] = [{"value": truncate_title_safely(clean_mn, 120) or f"STD-{product.id}", "qualifier": None}]

        product.clean_mode = cleaned.get("clean_mode", mode or "text")
        product.makro_catalog_attributes = json.dumps(catalog_attrs)

        if cleaned.get("brand_nature"):
            comp_d = json.loads(product.compliance_details) if product.compliance_details else {}
            comp_d["brand_nature"] = cleaned.get("brand_nature")
            comp_d["target_compatible_brand"] = cleaned.get("target_compatible_brand")
            product.compliance_details = json.dumps(comp_d, ensure_ascii=False)

        # 阶段 1 本地契约就地预检 (Pre-flight Check)
        mandatories = cleaned.get("mandatory_names") or []
        missing_mandatories = [m for m in mandatories if m not in catalog_attrs and m not in LISTING_ONLY_ATTRS]
        if missing_mandatories:
            product.makro_submit_error = f"⚠️ 缺少必填属性: {', '.join(missing_mandatories[:3])}"
        else:
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

@router.post("/batch-clean", summary="批量执行 AI 数据清洗 (后台异步多线程任务，支持同SPU变体协同与软预检)")
def batch_clean_products(
    req: BatchCleanRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    if not req.product_ids:
        return {"total": 0, "success": 0, "failed": 0, "errors": [], "message": "未选择商品"}

    u_id = current_user.id if current_user else None
    op_name = (current_user.nickname or current_user.username) if current_user else None

    ai_service = AICleanerService.from_db(db)
    total = len(req.product_ids)
    clean_mode = req.clean_mode
    mode_label = "图文多模态" if clean_mode == "vision" else ("纯文本" if clean_mode == "text" else "默认模式")
    task = task_manager.create_task("BATCH_CLEAN", f"批量AI数据清洗 ({mode_label})", total, req.product_ids)
    task_id = task["id"]


    # 阶段 2: 提取商品母体标识 (takealot_id 或 group_code) 实现 SPU 变体协同清洗
    prods_meta = db.query(Product.id, Product.takealot_id, Product.group_code).filter(Product.id.in_(req.product_ids)).all()
    spu_groups = {}  # spu_key -> list of pids
    for pid, tid, gcode in prods_meta:
        spu_key = tid or gcode or f"standalone_{pid}"
        spu_groups.setdefault(spu_key, []).append(pid)

    def _worker(tm: TaskManager, tid: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..database import SessionLocal
        from ..services.vertical_service import VerticalService
        from ..services.ai_cleaner_service import truncate_title_safely

        def _clean_product_entity(prod: Product, cleaned: dict, applied_mode: str):
            """统一将结构化清洗产物赋给 Product 模型并执行本地契约就地预检"""
            target_b = prod.makro_brand or "Beishi"
            prod.makro_title = cleaned.get("makro_title", prod.takealot_title)
            from ..services.translation_service import TranslationService
            prod.takealot_title_zh = cleaned.get("takealot_title_zh") or TranslationService.translate_title(prod.takealot_title)
            prod.makro_title_zh = cleaned.get("makro_title_zh") or TranslationService.translate_title(prod.makro_title)
            raw_seo_kw = cleaned.get("seo_keywords") or []
            if isinstance(raw_seo_kw, list) and raw_seo_kw:
                prod.seo_keywords = json.dumps([str(x).strip() for x in raw_seo_kw if str(x).strip()][:6], ensure_ascii=False)

            raw_desc = cleaned.get("description", prod.takealot_description)
            prod.makro_description = "\n".join(str(x) for x in raw_desc) if isinstance(raw_desc, list) else (str(raw_desc) if raw_desc else None)

            raw_v = cleaned.get("vertical", "")
            resolved_batch_v, _ = VerticalService.resolve_vertical(raw_v)
            if resolved_batch_v == "bath_towel" and "towel" not in (prod.takealot_title or "").lower():
                specs_dict = json.loads(prod.takealot_specs) if prod.takealot_specs else {}
                resolved_batch_v = VerticalService.predict_vertical(
                    title=prod.takealot_title,
                    category=prod.takealot_category or "",
                    specs=specs_dict,
                    description=prod.takealot_description or ""
                )
            prod.makro_vertical = resolved_batch_v

            attrs = dict(cleaned.get("attributes", {}))
            catalog_attrs = {}
            for k, val in attrs.items():
                if k in LISTING_ONLY_ATTRS:
                    continue
                qualifier = None
                if k in ["width", "length"]:
                    qualifier = "cm"
                catalog_attrs[k] = [{"value": str(val), "qualifier": qualifier}]

            var_attrs = json.loads(prod.variant_attributes) if prod.variant_attributes else {}
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

            clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', prod.makro_title or "", flags=re.I)
            clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
            catalog_attrs["model_number"] = [{"value": (f"{clean_mn[:230]}-{prod.id}")[:250], "qualifier": None}]
            catalog_attrs["model_name"] = [{"value": truncate_title_safely(clean_mn, 120) or f"STD-{prod.id}", "qualifier": None}]
            prod.clean_mode = applied_mode
            prod.makro_catalog_attributes = json.dumps(catalog_attrs)

            if cleaned.get("brand_nature"):
                comp_d = json.loads(prod.compliance_details) if prod.compliance_details else {}
                comp_d["brand_nature"] = cleaned.get("brand_nature")
                comp_d["target_compatible_brand"] = cleaned.get("target_compatible_brand")
                prod.compliance_details = json.dumps(comp_d, ensure_ascii=False)

            # 阶段 1 本地契约就地预检 (Pre-flight Check)
            mandatories = cleaned.get("mandatory_names") or []
            missing_mandatories = [m for m in mandatories if m not in catalog_attrs and m not in LISTING_ONLY_ATTRS]
            if missing_mandatories:
                prod.makro_submit_error = f"⚠️ 缺少必填属性: {', '.join(missing_mandatories[:3])}"
            else:
                prod.makro_submit_error = None

            prod.status = "CLEANED"

        def _do_spu_group(skey: str, pids: list):
            if tm.is_cancelled(tid):
                return [(p, False, "任务已取消", "") for p in pids]

            group_results = []
            anchor_pid = pids[0]
            anchor_cleaned = None

            # 1. 运行 Anchor 首变体全量清洗
            local_db = SessionLocal()
            try:
                anchor_prod = local_db.query(Product).filter(Product.id == anchor_pid).first()
                if not anchor_prod:
                    return [(p, False, f"商品 {p} 不存在", "") for p in pids]

                anchor_title = anchor_prod.takealot_title
                specs = json.loads(anchor_prod.takealot_specs) if anchor_prod.takealot_specs else {}
                var_attrs = json.loads(anchor_prod.variant_attributes) if anchor_prod.variant_attributes else {}
                combined_specs = {**specs, **var_attrs}

                anchor_cleaned = ai_service.clean_product_data({
                    "takealot_title": anchor_prod.takealot_title,
                    "takealot_brand": anchor_prod.takealot_brand,
                    "takealot_category": anchor_prod.takealot_category,
                    "takealot_specs": combined_specs,
                    "takealot_description": anchor_prod.takealot_description,
                    "raw_images": anchor_prod.raw_images,
                    "cover_image": anchor_prod.raw_images
                }, target_brand=anchor_prod.makro_brand or "Beishi", clean_mode=clean_mode)

                _clean_product_entity(anchor_prod, anchor_cleaned, anchor_cleaned.get("clean_mode", clean_mode or "text"))
                local_db.commit()
                group_results.append((anchor_pid, True, None, anchor_title))
            except Exception as e:
                group_results.append((anchor_pid, False, f"商品 {anchor_pid} 清洗失败: {str(e)}", ""))
            finally:
                local_db.close()

            # 2. 对同组后续兄弟变体进行 SPU 协同轻量衍生注入 (大幅节省 Token 并保证类目一致)
            if len(pids) > 1 and anchor_cleaned:
                for sib_pid in pids[1:]:
                    if tm.is_cancelled(tid):
                        group_results.append((sib_pid, False, "任务已取消", ""))
                        continue
                    sib_db = SessionLocal()
                    try:
                        sib_prod = sib_db.query(Product).filter(Product.id == sib_pid).first()
                        if not sib_prod:
                            group_results.append((sib_pid, False, f"变体 {sib_pid} 不存在", ""))
                            continue
                        sib_title = sib_prod.takealot_title

                        # 复用 Anchor 类目与核心 SEO 骨架
                        sib_cleaned = {
                            "vertical": anchor_cleaned.get("vertical"),
                            "brand": anchor_cleaned.get("brand"),
                            "makro_title": anchor_cleaned.get("makro_title"),
                            "seo_keywords": anchor_cleaned.get("seo_keywords"),
                            "description": anchor_cleaned.get("description"),
                            "attributes": dict(anchor_cleaned.get("attributes", {})),
                            "mandatory_names": anchor_cleaned.get("mandatory_names", []),
                            "clean_mode": f"{anchor_cleaned.get('clean_mode', clean_mode or 'text')}_harmonized"
                        }
                        # 变体专属差异微调标题 (风格 B: 括号规格注入)
                        var_attrs = json.loads(sib_prod.variant_attributes) if sib_prod.variant_attributes else {}
                        v_col = sib_prod.colour or var_attrs.get("colour")
                        v_size = sib_prod.size or var_attrs.get("size")
                        base_t = sib_cleaned["makro_title"] or sib_title
                        sib_brand = anchor_cleaned.get("brand") or sib_prod.makro_brand or "Beishi"
                        sib_cleaned["makro_title"] = format_title_with_specs(
                            base_t,
                            brand=sib_brand,
                            color=v_col,
                            size=v_size,
                            max_len=ai_service.seo_title_max_len,
                            vertical=anchor_cleaned.get("vertical") or ""
                        )

                        _clean_product_entity(sib_prod, sib_cleaned, sib_cleaned["clean_mode"])
                        sib_db.commit()
                        group_results.append((sib_pid, True, None, sib_title))
                    except Exception as ex:
                        group_results.append((sib_pid, False, f"变体 {sib_pid} 协同清洗失败: {str(ex)}", ""))
                    finally:
                        sib_db.close()
            elif len(pids) > 1 and not anchor_cleaned:
                # 若 Anchor 偶发异常，后续变体自动回退为独立单品清洗
                for sib_pid in pids[1:]:
                    sib_db = SessionLocal()
                    try:
                        sib_prod = sib_db.query(Product).filter(Product.id == sib_pid).first()
                        if not sib_prod:
                            group_results.append((sib_pid, False, f"商品 {sib_pid} 不存在", ""))
                            continue
                        sib_title = sib_prod.takealot_title
                        specs = json.loads(sib_prod.takealot_specs) if sib_prod.takealot_specs else {}
                        var_attrs = json.loads(sib_prod.variant_attributes) if sib_prod.variant_attributes else {}
                        c_specs = {**specs, **var_attrs}
                        cleaned = ai_service.clean_product_data({
                            "takealot_title": sib_prod.takealot_title,
                            "takealot_brand": sib_prod.takealot_brand,
                            "takealot_category": sib_prod.takealot_category,
                            "takealot_specs": c_specs,
                            "takealot_description": sib_prod.takealot_description,
                            "raw_images": sib_prod.raw_images,
                            "cover_image": sib_prod.raw_images
                        }, target_brand=sib_prod.makro_brand or "Beishi", clean_mode=clean_mode)
                        _clean_product_entity(sib_prod, cleaned, cleaned.get("clean_mode", clean_mode or "text"))
                        sib_db.commit()
                        group_results.append((sib_pid, True, None, sib_title))
                    except Exception as ex:
                        group_results.append((sib_pid, False, f"商品 {sib_pid} 清洗失败: {str(ex)}", ""))
                    finally:
                        sib_db.close()

            return group_results

        # 阶段 1: 稳健提升并发 (纯文本模式扩至 12，图文多模态模式维持在 10 线程受控并发)
        is_vision = (clean_mode == "vision")
        worker_limit = 10 if is_vision else 12
        max_workers = min(worker_limit, max(1, len(spu_groups)))
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_do_spu_group, skey, p_list): skey for skey, p_list in spu_groups.items()}
            for fut in as_completed(futures):
                if tm.is_cancelled(tid):
                    break
                group_res = fut.result()
                for pid, ok, err, title in group_res:
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
        msg = f"批量AI清洗完成: 成功 {succ} 件, 失败 {fail} 件"
        tm.finish_task(tid, status=status, message=msg)
        record_audit_log(
            task_type="BATCH_CLEAN",
            status=status,
            message=msg,
            detail_logs={"total": total, "success": succ, "failed": fail, "product_ids": req.product_ids[:50]},
            user_id=u_id,
            operator_name=op_name
        )


    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total,
        "message": f"已在后台启动批量AI清洗 (共 {total} 件商品)"
    }

@router.post("/batch-compliance", summary="批量执行 AI 侵权与合规检测 (后台异步多线程任务)")
def batch_check_compliance(
    req: BatchCleanRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    if not req.product_ids:
        return {"total": 0, "success": 0, "failed": 0, "errors": [], "message": "未选择商品"}

    u_id = current_user.id if current_user else None
    op_name = (current_user.nickname or current_user.username) if current_user else None

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
        msg = f"批量合规排查完成: 成功 {succ} 件, 失败 {fail} 件 ({concurrency_limit}线程)"
        tm.finish_task(tid, status=status, message=msg)
        record_audit_log(
            task_type="BATCH_COMPLIANCE",
            status=status,
            message=msg,
            detail_logs={"total": total, "success": succ, "failed": fail, "product_ids": req.product_ids[:50]},
            user_id=u_id,
            operator_name=op_name
        )

    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total,
        "concurrency": concurrency_limit,
        "message": f"已在后台启动批量合规排查 (共 {total} 件商品，{concurrency_limit} 线程受控并发)"
    }

@router.post("/check-compliance/{product_id}", summary="单品独立触发 AI 侵权与违禁品全量检测 (含首图视觉)")
def check_single_compliance(
    product_id: int,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    u_id = current_user.id if current_user else product.user_id
    op_name = (current_user.nickname or current_user.username) if current_user else None

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
        user_id=u_id,
        operator_name=op_name,
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
    current_user: Optional[User] = Depends(get_optional_current_user),
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
    u_id = current_user.id if current_user else product.user_id
    op_name = (current_user.nickname or current_user.username) if current_user else None
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
        user_id=u_id,
        operator_name=op_name,
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

