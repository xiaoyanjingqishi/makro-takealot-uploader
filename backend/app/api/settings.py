from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from ..models.setting import SystemSetting
from ..models.user import User
from ..schemas.setting import SystemSettingsSchema, CostPricingCalculateRequest
from ..config import settings
from ..utils.auth import get_current_admin, get_optional_current_user
from ..services.auto_login_scheduler import auto_login_scheduler

router = APIRouter(prefix="/settings", tags=["系统配置"])

@router.get("", response_model=SystemSettingsSchema, summary="获取所有系统配置与定价规则")
def get_settings(
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    all_settings = db.query(SystemSetting).all()
    setting_dict = {s.key: s.value for s in all_settings}

    def _get_float(key: str, default: float) -> float:
        try:
            return float(setting_dict.get(key, default))
        except (ValueError, TypeError):
            return default

    def _get_int(key: str, default: int) -> int:
        try:
            return int(setting_dict.get(key, default))
        except (ValueError, TypeError):
            return default

    def _get_str(key: str, default: str) -> str:
        val = setting_dict.get(key)
        return val if val is not None else default

    def _get_bool(key: str, default: bool) -> bool:
        val = setting_dict.get(key)
        if val is None:
            return default
        return str(val).lower() in ["true", "1", "yes", "y"]

    is_admin = bool(current_user and current_user.role == "ADMIN")

    qwen_k = _get_str("qwen_api_key", settings.QWEN_API_KEY)
    deepseek_k = _get_str("deepseek_api_key", settings.DEEPSEEK_API_KEY)
    jev_k = _get_str("jev_api_key", getattr(settings, "JEV_API_KEY", ""))
    ck = _get_str("cookie", "")
    csrf = _get_str("fk_csrf_token", settings.DEFAULT_FK_CSRF_TOKEN)

    # 普通员工访问时强制对高危密钥与 Cookie 做脱敏遮罩，严防凭据泄露
    if not is_admin:
        qwen_k = "******" if qwen_k else ""
        deepseek_k = "******" if deepseek_k else ""
        jev_k = "******" if jev_k else ""
        ck = "******" if ck else ""
        csrf = "******" if csrf else ""

    return SystemSettingsSchema(
        markup_ratio=_get_float("markup_ratio", settings.DEFAULT_MARKUP_RATIO),
        fixed_markup=_get_float("fixed_markup", settings.DEFAULT_FIXED_MARKUP),
        mrp_ratio=_get_float("mrp_ratio", settings.DEFAULT_MRP_RATIO),
        publish_concurrency=_get_int("publish_concurrency", getattr(settings, "DEFAULT_PUBLISH_CONCURRENCY", 2)),
        auto_login_check_enabled=_get_bool("auto_login_check_enabled", True),
        auto_login_check_interval_hours=_get_float("auto_login_check_interval_hours", 21.0),
        seller_id=_get_str("seller_id", settings.DEFAULT_SELLER_ID),
        fk_csrf_token=csrf,
        cookie=ck,
        default_brand=_get_str("default_brand", settings.DEFAULT_BRAND),
        shipping_days=_get_str("shipping_days", settings.DEFAULT_SHIPPING_DAYS),
        country_of_origin=_get_str("country_of_origin", settings.DEFAULT_COUNTRY_OF_ORIGIN),
        manufacturer_details=_get_str("manufacturer_details", settings.DEFAULT_MANUFACTURER),
        packer_details=_get_str("packer_details", settings.DEFAULT_PACKER),
        default_pkg_length=_get_str("default_pkg_length", settings.DEFAULT_PKG_LENGTH),
        default_pkg_breadth=_get_str("default_pkg_breadth", settings.DEFAULT_PKG_BREADTH),
        default_pkg_height=_get_str("default_pkg_height", settings.DEFAULT_PKG_HEIGHT),
        default_pkg_weight=_get_str("default_pkg_weight", settings.DEFAULT_PKG_WEIGHT),
        ai_provider=_get_str("ai_provider", settings.AI_PROVIDER),
        qwen_api_key=qwen_k,
        qwen_base_url=_get_str("qwen_base_url", settings.QWEN_BASE_URL),
        qwen_model=_get_str("qwen_model", settings.QWEN_MODEL),
        deepseek_api_key=deepseek_k,
        deepseek_base_url=_get_str("deepseek_base_url", settings.DEEPSEEK_BASE_URL),
        deepseek_model=_get_str("deepseek_model", settings.DEEPSEEK_MODEL),
        deepseek_vision_model=_get_str("deepseek_vision_model", getattr(settings, "DEEPSEEK_VISION_MODEL", "deepseek-flash")),
        seo_title_enabled=_get_bool("seo_title_enabled", getattr(settings, "DEFAULT_SEO_TITLE_ENABLED", True)),
        seo_title_max_len=_get_int("seo_title_max_len", getattr(settings, "DEFAULT_SEO_TITLE_MAX_LEN", 120)),
        cleaner_mode=_get_str("cleaner_mode", getattr(settings, "DEFAULT_CLEANER_MODE", "text")),
        qwen_vision_model=_get_str("qwen_vision_model", getattr(settings, "DEFAULT_QWEN_VISION_MODEL", "qwen-vl-plus")),
        custom_category_synonyms=_get_str("custom_category_synonyms", "{}"),
        jev_api_key=jev_k,
        jev_base_url=_get_str("jev_base_url", getattr(settings, "JEV_BASE_URL", "https://api.typesafe.ai")),
        jev_model=_get_str("jev_model", getattr(settings, "JEV_MODEL", "jev-latest")),
        jev_enabled=_get_bool("jev_enabled", getattr(settings, "JEV_ENABLED", True)),
    )

@router.post("", summary="保存或更新系统配置 (仅限系统管理员)")
def save_settings(
    req: SystemSettingsSchema,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    data = req.dict()
    for key, value in data.items():
        val_str = str(value) if value is not None else ""
        item = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if item:
            item.value = val_str
        else:
            db.add(SystemSetting(key=key, value=val_str))

    db.commit()

    # 热重载自动保活定时调度器周期配置
    auto_login_scheduler.set_interval_hours(
        hours=req.auto_login_check_interval_hours,
        enabled=req.auto_login_check_enabled
    )

    # 热重载自定义类目同义词到倒排索引
    from ..services.vertical_service import VerticalSemanticRetriever
    VerticalSemanticRetriever.sync_custom_synonyms_from_db(db)

    from ..services.audit_logger import record_audit_log
    record_audit_log(
        task_type="SETTINGS_UPDATE",
        status="SUCCESS",
        message=f"管理员【{current_admin.username}】更新系统全局配置 (保活周期: {req.auto_login_check_interval_hours}h, 启用状态={req.auto_login_check_enabled})",
        detail_logs={"updated_keys": list(data.keys()), "admin": current_admin.username},
        db=db
    )
    return {"message": "配置更新成功"}

@router.get("/network-info", summary="获取宿主机局域网访问地址与网络配置")
def get_network_config():
    from ..services.lan_proxy import get_network_info
    info = get_network_info()
    primary_ip = info.get("primary_ip", "127.0.0.1")
    proxy_port = info.get("proxy_port", 80)
    backend_port = info.get("backend_port", 8001)

    lan_url = f"http://{primary_ip}" if proxy_port == 80 else f"http://{primary_ip}:{proxy_port}"
    lan_direct_url = f"http://{primary_ip}:{backend_port}"
    localhost_url = f"http://localhost:{backend_port}"

    return {
        **info,
        "lan_url": lan_url,
        "lan_direct_url": lan_direct_url,
        "localhost_url": localhost_url
    }

@router.post("/test-jev", summary="测试 Jev (TypeSafe AI) 决策模型连通性与响应时延")
def test_jev_connection(db: Session = Depends(get_db)):
    from ..services.jev_service import JevService
    res = JevService.test_connectivity(db=db)
    return res


@router.post("/calculate-cost-price", summary="1688全链路跨境成本与精准定价计算器")
def calculate_cost_price(req: CostPricingCalculateRequest):
    from ..services.pricing_service import calculate_1688_pricing
    return calculate_1688_pricing(
        purchase_price_cny=req.purchase_price_cny,
        length_cm=req.length_cm or 0.0,
        width_cm=req.width_cm or 0.0,
        height_cm=req.height_cm or 0.0,
        actual_weight_kg=req.actual_weight_kg or 0.0,
        domestic_freight_cny=req.domestic_freight_cny if req.domestic_freight_cny is not None else 8.0,
        first_leg_rate_cny=req.first_leg_rate_cny if req.first_leg_rate_cny is not None else 95.0,
        volumetric_divisor=req.volumetric_divisor if req.volumetric_divisor is not None else 6000.0,
        last_leg_base_zar=req.last_leg_base_zar if req.last_leg_base_zar is not None else 70.0,
        last_leg_vat_rate=req.last_leg_vat_rate if req.last_leg_vat_rate is not None else 0.15,
        exchange_rate=req.exchange_rate if req.exchange_rate is not None else 0.40,
        commission_rate=req.commission_rate if req.commission_rate is not None else 0.15,
        commission_vat_rate=req.commission_vat_rate if req.commission_vat_rate is not None else 0.15,
        target_margin=req.target_margin if req.target_margin is not None else 0.30
    )



