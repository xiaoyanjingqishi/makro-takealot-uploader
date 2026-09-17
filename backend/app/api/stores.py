import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from ..database import get_db
from ..models.store import Store, ProductStoreListing
from ..schemas.store import StoreCreate, StoreUpdate, StoreResponse
from ..services.makro_client import MakroClient
from ..services.audit_logger import record_audit_log

router = APIRouter(prefix="/stores", tags=["多店铺管理"])
logger = logging.getLogger(__name__)

def _format_store(store: Store, db: Session) -> dict:
    cnt = db.query(func.count(ProductStoreListing.id)).filter(ProductStoreListing.store_id == store.id).scalar() or 0
    has_cookie = bool(store.cookie and len(store.cookie.strip()) > 10)
    cookie_prev = None
    if has_cookie:
        c = store.cookie.strip()
        cookie_prev = f"{c[:12]}...{c[-10:]}" if len(c) > 25 else c[:20]

    return {
        "id": store.id,
        "name": store.name,
        "seller_id": store.seller_id,
        "fk_csrf_token": store.fk_csrf_token,
        "cookie": store.cookie,
        "default_brand": store.default_brand or "Beishi",
        "is_active": store.is_active,
        "is_default": store.is_default,
        "notes": store.notes,
        "has_cookie": has_cookie,
        "cookie_preview": cookie_prev,
        "listings_count": cnt,
        "created_at": store.created_at,
        "updated_at": store.updated_at
    }

@router.get("", summary="获取所有店铺列表")
def list_stores(db: Session = Depends(get_db)):
    stores = db.query(Store).order_by(Store.is_default.desc(), Store.id.asc()).all()
    return [_format_store(s, db) for s in stores]

@router.post("", summary="添加新店铺")
def create_store(req: StoreCreate, db: Session = Depends(get_db)):
    # 检查是否有同名店铺
    existing = db.query(Store).filter(Store.name == req.name.strip()).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"已存在同名店铺: {req.name}")

    # 如果是第一个店铺或明确指定为默认店铺
    total_stores = db.query(Store).count()
    is_default = req.is_default or (total_stores == 0)

    if is_default:
        db.query(Store).update({Store.is_default: False})

    store = Store(
        name=req.name.strip(),
        seller_id=req.seller_id.strip(),
        fk_csrf_token=req.fk_csrf_token.strip() if req.fk_csrf_token else None,
        cookie=req.cookie.strip() if req.cookie else None,
        default_brand=req.default_brand.strip() if req.default_brand else "Beishi",
        is_active=req.is_active if req.is_active is not None else True,
        is_default=is_default,
        notes=req.notes.strip() if req.notes else None
    )
    db.add(store)
    db.commit()
    db.refresh(store)

    record_audit_log(
        task_type="STORE_CREATE",
        status="SUCCESS",
        message=f"添加新店铺: {store.name} (SellerID: {store.seller_id})",
        detail_logs={"store_id": store.id, "name": store.name, "seller_id": store.seller_id},
        db=db
    )

    return _format_store(store, db)

@router.put("/{store_id}", summary="修改店铺配置")
def update_store(store_id: int, req: StoreUpdate, db: Session = Depends(get_db)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    if req.name is not None:
        name_clean = req.name.strip()
        same_name = db.query(Store).filter(Store.name == name_clean, Store.id != store_id).first()
        if same_name:
            raise HTTPException(status_code=400, detail=f"已存在同名店铺: {name_clean}")
        store.name = name_clean

    if req.seller_id is not None:
        store.seller_id = req.seller_id.strip()
    if req.fk_csrf_token is not None:
        store.fk_csrf_token = req.fk_csrf_token.strip() if req.fk_csrf_token else None
    if req.cookie is not None:
        store.cookie = req.cookie.strip() if req.cookie else None
    if req.default_brand is not None:
        store.default_brand = req.default_brand.strip() or "Beishi"
    if req.is_active is not None:
        store.is_active = req.is_active
    if req.notes is not None:
        store.notes = req.notes.strip() if req.notes else None

    if req.is_default is True:
        db.query(Store).filter(Store.id != store_id).update({Store.is_default: False})
        store.is_default = True

    db.commit()
    db.refresh(store)

    record_audit_log(
        task_type="STORE_UPDATE",
        status="SUCCESS",
        message=f"更新店铺配置: {store.name} (ID: #{store.id})",
        detail_logs={"store_id": store.id, "name": store.name},
        db=db
    )

    return _format_store(store, db)

@router.delete("/{store_id}", summary="删除店铺")
def delete_store(store_id: int, db: Session = Depends(get_db)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    total_stores = db.query(Store).count()
    if total_stores <= 1:
        raise HTTPException(status_code=400, detail="系统必须保留至少一个店铺，无法删除唯一店铺！")

    was_default = store.is_default
    store_name = store.name

    db.delete(store)
    db.commit()

    if was_default:
        another = db.query(Store).filter(Store.is_active == True).first() or db.query(Store).first()
        if another:
            another.is_default = True
            db.commit()

    record_audit_log(
        task_type="STORE_DELETE",
        status="SUCCESS",
        message=f"删除店铺: {store_name} (ID: #{store_id})",
        detail_logs={"store_id": store_id, "name": store_name},
        db=db
    )

    return {"message": f"店铺 {store_name} 已成功删除"}

@router.post("/{store_id}/set-default", summary="设置默认店铺")
def set_default_store(store_id: int, db: Session = Depends(get_db)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    db.query(Store).update({Store.is_default: False})
    store.is_default = True
    store.is_active = True
    db.commit()
    return {"message": f"已将【{store.name}】设置为默认店铺"}

@router.post("/{store_id}/test", summary="测试店铺凭据连通性")
def test_store_connection(store_id: int, db: Session = Depends(get_db)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    if not store.cookie:
        return {"success": False, "message": "该店铺尚未配置 Cookie 登录态凭据！"}

    try:
        client = MakroClient.from_store(store)
        # 发起轻量级类目属性查询测试
        res = client.get_vertical_definition("bath_towel")
        if res and ("attributes" in res or "vertical" in res or isinstance(res, dict)):
            return {
                "success": True,
                "message": f"店铺【{store.name}】凭据有效，Makro 接口连通正常！"
            }
        return {"success": True, "message": f"店铺【{store.name}】测试响应正常！"}
    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "403" in err_str or "login" in err_str.lower():
            return {"success": False, "message": f"登录态已过期或无效 (HTTP 401/403)，请更新 Cookie！"}
        return {"success": False, "message": f"接口请求异常: {err_str}"}

@router.post("/{store_id}/sync-credentials", summary="同步凭据到指定店铺")
def sync_store_credentials(store_id: int, payload: dict, db: Session = Depends(get_db)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    if payload.get("seller_id"):
        store.seller_id = payload["seller_id"].strip()
    if payload.get("fk_csrf_token"):
        store.fk_csrf_token = payload["fk_csrf_token"].strip()
    if payload.get("cookie"):
        store.cookie = payload["cookie"].strip()

    db.commit()
    return {"message": f"店铺【{store.name}】凭据同步成功"}
