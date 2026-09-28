from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.user import User, UserStore
from ..models.store import Store
from ..utils.auth import hash_password, get_current_admin

router = APIRouter(prefix="/users", tags=["用户与权限管理(仅限管理员)"])

class UserCreateRequest(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None
    role: Optional[str] = "OPERATOR"  # "ADMIN" 或 "OPERATOR"
    store_ids: Optional[List[int]] = []

class UserUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = None
    store_ids: Optional[List[int]] = None

class UserStoresRequest(BaseModel):
    store_ids: List[int]

@router.get("/operators", summary="获取激活状态的操作员工列表 (供插件与筛选下拉使用)")
def list_operators(db: Session = Depends(get_db)):
    users = db.query(User).filter(User.is_active == True).order_by(User.id.asc()).all()
    return {
        "operators": [
            {
                "id": u.id,
                "username": u.username,
                "nickname": u.nickname or u.username,
                "role": u.role
            }
            for u in users
        ]
    }

@router.get("", summary="获取全部用户列表及店铺授权")
def list_users(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id.asc()).all()
    all_stores = db.query(Store).order_by(Store.id.asc()).all()
    
    result = []
    for u in users:
        # 查询该用户被授权的所有店铺 ID
        assigned_store_ids = [us.store_id for us in db.query(UserStore).filter(UserStore.user_id == u.id).all()]
        result.append({
            "id": u.id,
            "username": u.username,
            "nickname": u.nickname or u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "",
            "store_ids": assigned_store_ids
        })

    stores_meta = [{"id": s.id, "name": s.name, "seller_id": s.seller_id, "default_brand": s.default_brand} for s in all_stores]

    return {
        "users": result,
        "all_stores": stores_meta
    }

@router.post("", summary="创建新用户")
def create_user(
    req: UserCreateRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    username = req.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于 6 位")

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"用户名 '{username}' 已存在，请更换")

    user = User(
        username=username,
        password_hash=hash_password(req.password),
        role=req.role if req.role in ["ADMIN", "OPERATOR"] else "OPERATOR",
        nickname=req.nickname or username,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 绑定初始店铺
    if req.store_ids:
        for sid in req.store_ids:
            db.add(UserStore(user_id=user.id, store_id=sid))
        db.commit()

    return {
        "success": True,
        "user_id": user.id,
        "message": f"用户 '{user.username}' 创建成功"
    }

@router.put("/{user_id}", summary="修改用户信息与权限")
def update_user(
    user_id: int,
    req: UserUpdateRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if req.nickname is not None:
        user.nickname = req.nickname
    if req.role is not None and req.role in ["ADMIN", "OPERATOR"]:
        user.role = req.role
    if req.is_active is not None:
        # 防止管理员将自己禁用
        if user.id == admin.id and not req.is_active:
            raise HTTPException(status_code=400, detail="不可禁用当前登录的管理员账号")
        user.is_active = req.is_active
    if req.new_password:
        if len(req.new_password) < 6:
            raise HTTPException(status_code=400, detail="新密码不能少于 6 位")
        user.password_hash = hash_password(req.new_password)

    # 更新店铺授权矩阵
    if req.store_ids is not None:
        db.query(UserStore).filter(UserStore.user_id == user.id).delete()
        for sid in req.store_ids:
            db.add(UserStore(user_id=user.id, store_id=sid))

    db.commit()
    return {"success": True, "message": f"用户 '{user.username}' 信息更新成功"}

@router.delete("/{user_id}", summary="删除用户")
def delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不可删除当前登录的管理员账号")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    db.query(UserStore).filter(UserStore.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return {"success": True, "message": f"用户 '{user.username}' 已删除"}

@router.post("/{user_id}/stores", summary="批量更新该用户的店铺授权矩阵")
def update_user_stores(
    user_id: int,
    req: UserStoresRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    db.query(UserStore).filter(UserStore.user_id == user.id).delete()
    for sid in req.store_ids:
        db.add(UserStore(user_id=user.id, store_id=sid))
    db.commit()

    return {"success": True, "message": f"用户 '{user.username}' 的店铺授权已更新"}
