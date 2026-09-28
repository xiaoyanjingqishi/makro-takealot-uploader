import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from ..database import get_db
from ..models.store import Store, ProductStoreListing
from ..models.user import User
from ..schemas.store import (
    StoreCreate, StoreUpdate, StoreResponse,
    AutoLoginRequest, SendOtpRequest, VerifyOtpRequest, TestEmailRequest
)
from ..services.makro_client import MakroClient
from ..services.makro_auth_service import MakroAuthService
from ..services.email_otp_service import EmailOtpService
from ..services.audit_logger import record_audit_log
from ..utils.auth import get_current_user, get_current_admin, get_user_authorized_stores

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
        "login_email": store.login_email,
        "login_password": store.login_password,
        "has_login_password": bool(store.login_password and len(store.login_password) > 0),
        "imap_server": store.imap_server,
        "imap_port": store.imap_port or 993,
        "imap_user": store.imap_user,
        "imap_password": store.imap_password,
        "has_imap_password": bool(store.imap_password and len(store.imap_password) > 0),
        "last_auto_login_at": store.last_auto_login_at,
        "last_auto_login_status": store.last_auto_login_status,
        "has_cookie": has_cookie,
        "cookie_preview": cookie_prev,
        "listings_count": cnt,
        "created_at": store.created_at,
        "updated_at": store.updated_at
    }

@router.get("", summary="获取店铺列表 (按用户权限过滤)")
def list_stores(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role == "ADMIN":
        stores = db.query(Store).order_by(Store.is_default.desc(), Store.id.asc()).all()
    else:
        stores = get_user_authorized_stores(current_user, db)
    return [_format_store(s, db) for s in stores]

@router.post("", summary="添加新店铺 (仅管理员)")
def create_store(
    req: StoreCreate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
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
        notes=req.notes.strip() if req.notes else None,
        login_email=req.login_email.strip() if req.login_email else None,
        login_password=req.login_password.strip() if req.login_password else None,
        imap_server=req.imap_server.strip() if req.imap_server else None,
        imap_port=req.imap_port or 993,
        imap_user=req.imap_user.strip() if req.imap_user else None,
        imap_password=req.imap_password.strip() if req.imap_password else None
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

@router.put("/{store_id}", summary="修改店铺配置 (仅管理员)")
def update_store(
    store_id: int,
    req: StoreUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
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
    if req.login_email is not None:
        store.login_email = req.login_email.strip() if req.login_email else None
    if req.login_password is not None and req.login_password.strip():
        store.login_password = req.login_password.strip()
    if req.imap_server is not None:
        store.imap_server = req.imap_server.strip() if req.imap_server else None
    if req.imap_port is not None:
        store.imap_port = req.imap_port or 993
    if req.imap_user is not None:
        store.imap_user = req.imap_user.strip() if req.imap_user else None
    if req.imap_password is not None and req.imap_password.strip():
        store.imap_password = req.imap_password.strip()

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

@router.delete("/{store_id}", summary="删除店铺 (仅管理员)")
def delete_store(
    store_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
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

@router.post("/{store_id}/set-default", summary="设置默认店铺 (仅管理员)")
def set_default_store(
    store_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
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

@router.post("/test-email", summary="测试邮箱 IMAP 连接与授权码有效性")
def test_email_account(req: TestEmailRequest):
    res = EmailOtpService.test_connection(
        email_address=req.email,
        password=req.password,
        server=req.imap_server,
        port=req.imap_port or 993
    )
    return res

@router.post("/{store_id}/auto-login", summary="全自动登录指定店铺 (自动发码+多邮箱自动提取OTP+回写凭据)")
def auto_login_store(
    store_id: int,
    req: Optional[AutoLoginRequest] = None,
    db: Session = Depends(get_db)
):
    override_u = req.username if req else None
    override_p = req.password if req else None
    override_ip = req.imap_password if req else None
    override_is = req.imap_server if req else None
    override_ipt = req.imap_port if req else None
    override_iu = req.imap_user if req else None
    max_wait = (req.max_wait_seconds if req and req.max_wait_seconds else 60)

    res = MakroAuthService.run_full_auto_login(
        store_id=store_id,
        db=db,
        override_username=override_u,
        override_password=override_p,
        override_imap_password=override_ip,
        override_imap_server=override_is,
        override_imap_port=override_ipt,
        override_imap_user=override_iu,
        max_wait_seconds=max_wait
    )
    if not res.get("success") and not res.get("need_manual_otp"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@router.post("/{store_id}/send-login-otp", summary="为店铺发起登录请求 (获取 session_id 并触发邮箱验证码)")
def send_store_login_otp(
    store_id: int,
    req: Optional[SendOtpRequest] = None,
    db: Session = Depends(get_db)
):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    username = (req.username if req and req.username else None) or store.login_email
    password = (req.password if req and req.password else None) or store.login_password

    if not username or not password:
        raise HTTPException(status_code=400, detail="请提供 Makro 登录邮箱和密码")

    res = MakroAuthService.send_login_request(username=username, password=password, store_id=store_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@router.post("/{store_id}/verify-login-otp", summary="为店铺提交验证码完成登录并自动更新凭据")
def verify_store_login_otp(
    store_id: int,
    req: VerifyOtpRequest,
    db: Session = Depends(get_db)
):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺未找到")

    ok, creds, msg = MakroAuthService.verify_otp(session_id=req.session_id, otp=req.otp)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    seller_id = creds.get("seller_id") or store.seller_id
    csrf_token = creds.get("fk_csrf_token")
    cookie_str = creds.get("cookie")

    MakroAuthService.sync_to_store_db(
        store_id=store.id,
        seller_id=seller_id,
        csrf_token=csrf_token,
        cookie=cookie_str,
        db=db,
        login_email=creds.get("email")
    )

    return {
        "success": True,
        "message": f"店铺【{store.name}】验证码核验成功，凭据已自动同步！",
        "store": _format_store(store, db),
        "credentials": creds
    }

@router.post("/quick-login-send-otp", summary="独立自动登录第1步：发送账号密码触发验证码")
def quick_login_send_otp(req: SendOtpRequest):
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="请填写 Makro 登录邮箱和密码")

    res = MakroAuthService.send_login_request(username=req.username, password=req.password)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@router.post("/quick-login-verify-otp", summary="独立自动登录第2步：提交验证码提取凭据")
def quick_login_verify_otp(req: VerifyOtpRequest):
    ok, creds, msg = MakroAuthService.verify_otp(session_id=req.session_id, otp=req.otp)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {
        "success": True,
        "message": "登录成功，已取得凭据",
        "credentials": creds
    }

@router.post("/trigger-auto-login-all", summary="立即触发所有店铺全自动登录与凭据保活 (仅管理员)")
def trigger_auto_login_all_stores(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    from ..services.auto_login_scheduler import auto_login_scheduler
    res = auto_login_scheduler.trigger_all_stores()
    return {
        "success": True,
        "message": f"全店铺自动登录保活轮询已执行完成 (共检测 {res.get('total', 0)} 个店铺)",
        "data": res
    }
