import os
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import Depends, HTTPException, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.user import User, UserStore
from ..models.store import Store

# 安全密钥与 Token 过期时间
SECRET_KEY = os.getenv("APP_SECRET_KEY", "makro-takealot-secret-key-2026-auth-token-super-safe")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 天长效免登

security = HTTPBearer(auto_error=False)

def hash_password(password: str, salt: Optional[str] = None) -> str:
    """使用 PBKDF2-HMAC-SHA256 算法安全哈希密码 (防彩虹表与碰撞)"""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return f"{salt}${key.hex()}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码是否与哈希匹配"""
    try:
        if not hashed_password or "$" not in hashed_password:
            return False
        salt, expected_hex = hashed_password.split("$", 1)
        key = hashlib.pbkdf2_hmac(
            'sha256',
            plain_password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        )
        return hmac.compare_digest(key.hex(), expected_hex)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成签名 JWT 格式 Token"""
    from jose import jwt
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    """解析并校验 JWT Token"""
    from jose import jwt, JWTError
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

def get_current_user(
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    FastAPI 依赖注入：提取并校验当前登录用户
    支持从 Authorization Header (Bearer <token>) 或 Cookie 或 Query 参数获取 Token
    """
    token = None
    if auth and auth.credentials:
        token = auth.credentials
    elif request.headers.get("x-auth-token"):
        token = request.headers.get("x-auth-token")
    elif request.cookies.get("auth_token"):
        token = request.cookies.get("auth_token")

    if not token:
        # 若未登录，抛出 401 提示登录
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录系统",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态已过期或无效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的用户凭证")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员")

    return user

def get_optional_current_user(
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """可选的用户注入：若携带有效 Token 则返回 User，否则返回 None"""
    token = None
    if auth and auth.credentials:
        token = auth.credentials
    elif request.headers.get("x-auth-token"):
        token = request.headers.get("x-auth-token")
    elif request.cookies.get("auth_token"):
        token = request.cookies.get("auth_token")

    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id = payload.get("user_id")
    if not user_id:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user

def get_current_admin(current_user: User = Depends(get_current_user)) -> User:

    """仅限管理员调用的依赖注入"""
    if current_user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作：该功能仅限管理员使用")
    return current_user

def get_user_authorized_stores(user: User, db: Session) -> List[Store]:
    """
    获取指定用户有权操作的全部有效店铺：
    - 管理员 (ADMIN) 拥有全部店铺权限
    - 运营员工 (OPERATOR) 仅拥有被授权的店铺权限
    """
    if user.role == "ADMIN":
        return db.query(Store).filter(Store.is_active == True).order_by(Store.id.asc()).all()
    else:
        return (
            db.query(Store)
            .join(UserStore, UserStore.store_id == Store.id)
            .filter(UserStore.user_id == user.id, Store.is_active == True)
            .order_by(Store.id.asc())
            .all()
        )
