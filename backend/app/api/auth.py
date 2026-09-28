from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.user import User
from ..models.store import Store
from ..utils.auth import (
    verify_password,
    hash_password,
    create_access_token,
    get_current_user,
    get_user_authorized_stores
)

router = APIRouter(prefix="/auth", tags=["用户登录与认证"])

class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@router.post("/login", summary="用户账号密码登录")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    username = req.username.strip()
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名或密码错误"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账号已被禁用，请联系管理员"
        )

    # 签发 JWT
    token = create_access_token({
        "user_id": user.id,
        "username": user.username,
        "role": user.role
    })

    # 查询用户被授权的所有店铺
    stores = get_user_authorized_stores(user, db)
    store_list = [{
        "id": s.id,
        "name": s.name,
        "seller_id": s.seller_id,
        "default_brand": s.default_brand or "Beishi",
        "default_location_id": s.default_location_id,
        "is_default": s.is_default
    } for s in stores]

    return {
        "success": True,
        "token": token,
        "access_token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname or user.username,
            "role": user.role
        },
        "authorized_stores": store_list,
        "message": f"欢迎回来，{user.nickname or user.username}"
    }

@router.get("/me", summary="获取当前登录用户详情与被授权店铺")
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stores = get_user_authorized_stores(current_user, db)
    store_list = [{
        "id": s.id,
        "name": s.name,
        "seller_id": s.seller_id,
        "default_brand": s.default_brand or "Beishi",
        "default_location_id": s.default_location_id,
        "is_default": s.is_default
    } for s in stores]

    return {
        "id": current_user.id,
        "username": current_user.username,
        "nickname": current_user.nickname or current_user.username,
        "role": current_user.role,
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "nickname": current_user.nickname or current_user.username,
            "role": current_user.role
        },
        "authorized_stores": store_list
    }

@router.post("/change-password", summary="修改个人登录密码")
def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于 6 位")

    current_user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"success": True, "message": "密码修改成功，请妥善保管新密码"}

@router.post("/logout", summary="退出登录")
def logout():
    return {"success": True, "message": "已安全退出"}
