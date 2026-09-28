from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class StoreBase(BaseModel):
    name: str
    seller_id: str
    fk_csrf_token: Optional[str] = None
    cookie: Optional[str] = None
    default_brand: Optional[str] = "Beishi"
    is_active: Optional[bool] = True
    is_default: Optional[bool] = False
    notes: Optional[str] = None
    login_email: Optional[str] = None
    login_password: Optional[str] = None
    imap_server: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None

class StoreCreate(StoreBase):
    pass

class StoreUpdate(BaseModel):
    name: Optional[str] = None
    seller_id: Optional[str] = None
    fk_csrf_token: Optional[str] = None
    cookie: Optional[str] = None
    default_brand: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    notes: Optional[str] = None
    login_email: Optional[str] = None
    login_password: Optional[str] = None
    imap_server: Optional[str] = None
    imap_port: Optional[int] = None
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None

class StoreResponse(BaseModel):
    id: int
    name: str
    seller_id: str
    fk_csrf_token: Optional[str] = None
    cookie: Optional[str] = None
    default_brand: str
    is_active: bool
    is_default: bool
    notes: Optional[str] = None
    login_email: Optional[str] = None
    imap_server: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_user: Optional[str] = None
    has_login_password: bool = False
    has_imap_password: bool = False
    has_cookie: bool = False
    cookie_preview: Optional[str] = None
    listings_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class AutoLoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    imap_server: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None
    max_wait_seconds: Optional[int] = 60

class SendOtpRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None

class VerifyOtpRequest(BaseModel):
    session_id: str
    otp: str

class TestEmailRequest(BaseModel):
    email: str
    password: str
    imap_server: Optional[str] = None
    imap_port: Optional[int] = 993

class ProductStoreListingItem(BaseModel):
    id: int
    store_id: int
    store_name: str
    brand: Optional[str] = None
    status: str
    makro_sku_id: Optional[str] = None
    makro_request_id: Optional[str] = None
    makro_submit_error: Optional[str] = None
    selling_price: Optional[float] = None
    mrp: Optional[float] = None
    submitted_at: Optional[datetime] = None
