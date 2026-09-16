import re
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from ..database import get_db
from ..models.product import Product
from ..models.task import TaskLog
from ..schemas.product import BatchCleanRequest, ProductResponse
from ..services.ai_cleaner_service import AICleanerService
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
        message=f"正在对商品 {product.id} 执行 AI 清洗..."
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

        # 确保 model_number 放入去除品牌名后的商品描述，防止触发 Brand name should not be part of model_number 限制
        target_b = product.makro_brand or "Beishi"
        clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', product.makro_title or "", flags=re.I)
        clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
        catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{product.id}")[:250], "qualifier": None}]

        product.makro_catalog_attributes = json.dumps(catalog_attrs)
        product.makro_submit_error = None
        product.status = "CLEANED"
        task.status = "SUCCESS"
        task.message = "AI 清洗完成"
        task.finished_at = datetime.utcnow()
        task.detail_logs = json.dumps(cleaned, ensure_ascii=False)

        db.commit()
        db.refresh(product)
        return _format_product(product)
    except Exception as e:
        task.status = "FAILED"
        task.message = f"清洗失败: {str(e)}"
        task.finished_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/batch-clean", summary="批量执行 AI 数据清洗")
def batch_clean_products(req: BatchCleanRequest, db: Session = Depends(get_db)):
    success_count = 0
    fail_count = 0
    errors = []

    ai_service = AICleanerService.from_db(db)

    for pid in req.product_ids:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            fail_count += 1
            continue

        try:
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

            target_b = product.makro_brand or "Beishi"
            clean_mn = re.sub(rf'^\s*{re.escape(target_b)}\s*[-_:]*\s*', '', product.makro_title or "", flags=re.I)
            clean_mn = re.sub(rf'\b{re.escape(target_b)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
            catalog_attrs["model_number"] = [{"value": (clean_mn or f"STD-{product.id}")[:250], "qualifier": None}]
            product.makro_catalog_attributes = json.dumps(catalog_attrs)
            product.makro_submit_error = None
            product.status = "CLEANED"
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"商品 {pid} 清洗失败: {str(e)}")

    db.commit()
    return {
        "total": len(req.product_ids),
        "success": success_count,
        "failed": fail_count,
        "errors": errors
    }

@router.post("/batch-compliance", summary="批量执行 AI 侵权与违禁品合规检测")
def batch_check_compliance(req: BatchCleanRequest, db: Session = Depends(get_db)):
    success_count = 0
    fail_count = 0
    errors = []

    from ..services.compliance_service import ComplianceService
    cs = ComplianceService.from_db(db)

    for pid in req.product_ids:
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            fail_count += 1
            continue

        try:
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
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"商品 {pid} 合规检测异常: {str(e)}")

    db.commit()
    return {
        "total": len(req.product_ids),
        "success": success_count,
        "failed": fail_count,
        "errors": errors
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

    return {
        "product_id": product.id,
        "compliance_status": product.compliance_status,
        "compliance_details": comp_res
    }
