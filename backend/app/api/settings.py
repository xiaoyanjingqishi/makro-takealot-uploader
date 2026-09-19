from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.setting import SystemSetting
from ..schemas.setting import SystemSettingsSchema
from ..config import settings

router = APIRouter(prefix="/settings", tags=["系统配置"])

@router.get("", response_model=SystemSettingsSchema, summary="获取所有系统配置与定价规则")
def get_settings(db: Session = Depends(get_db)):
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

    return SystemSettingsSchema(
        markup_ratio=_get_float("markup_ratio", settings.DEFAULT_MARKUP_RATIO),
        fixed_markup=_get_float("fixed_markup", settings.DEFAULT_FIXED_MARKUP),
        mrp_ratio=_get_float("mrp_ratio", settings.DEFAULT_MRP_RATIO),
        publish_concurrency=_get_int("publish_concurrency", getattr(settings, "DEFAULT_PUBLISH_CONCURRENCY", 2)),
        seller_id=_get_str("seller_id", settings.DEFAULT_SELLER_ID),
        fk_csrf_token=_get_str("fk_csrf_token", settings.DEFAULT_FK_CSRF_TOKEN),
        cookie=_get_str("cookie", ""),
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
        qwen_api_key=_get_str("qwen_api_key", settings.QWEN_API_KEY),
        qwen_base_url=_get_str("qwen_base_url", settings.QWEN_BASE_URL),
        qwen_model=_get_str("qwen_model", settings.QWEN_MODEL),
        deepseek_api_key=_get_str("deepseek_api_key", settings.DEEPSEEK_API_KEY),
        deepseek_base_url=_get_str("deepseek_base_url", settings.DEEPSEEK_BASE_URL),
        deepseek_model=_get_str("deepseek_model", settings.DEEPSEEK_MODEL),
        seo_title_enabled=_get_bool("seo_title_enabled", getattr(settings, "DEFAULT_SEO_TITLE_ENABLED", True)),
        seo_title_max_len=_get_int("seo_title_max_len", getattr(settings, "DEFAULT_SEO_TITLE_MAX_LEN", 120)),
        cleaner_mode=_get_str("cleaner_mode", getattr(settings, "DEFAULT_CLEANER_MODE", "text")),
        qwen_vision_model=_get_str("qwen_vision_model", getattr(settings, "DEFAULT_QWEN_VISION_MODEL", "qwen-vl-plus")),
    )

@router.post("", summary="保存或更新系统配置")
def save_settings(req: SystemSettingsSchema, db: Session = Depends(get_db)):
    data = req.dict()
    for key, value in data.items():
        val_str = str(value) if value is not None else ""
        item = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if item:
            item.value = val_str
        else:
            db.add(SystemSetting(key=key, value=val_str))

    db.commit()
    from ..services.audit_logger import record_audit_log
    record_audit_log(
        task_type="SETTINGS_UPDATE",
        status="SUCCESS",
        message="更新系统全局配置 (包含定价规则、Makro凭据与AI模型配置)",
        detail_logs={"updated_keys": list(data.keys())},
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

