from pydantic import BaseModel
from typing import Optional

class SystemSettingsSchema(BaseModel):
    # 定价公式
    markup_ratio: float = 1.35
    fixed_markup: float = 20.0
    mrp_ratio: float = 1.5

    # 并发与性能调度
    publish_concurrency: int = 2  # 批量上品并发线程数 (推荐 2~3，支持店铺间并发)

    # Makro 凭据与店铺配置
    seller_id: str = "cb80491bf0a34dc5"
    fk_csrf_token: str = "FbvXzXEP-45o5eUtkeX8Wo6LCq9GBWDg9Rcg"
    cookie: Optional[str] = ""
    default_brand: str = "Beishi"
    shipping_days: str = "15"
    country_of_origin: str = "CN"
    manufacturer_details: str = "Beijing"
    packer_details: str = "BeiShi"

    # 包装默认值
    default_pkg_length: str = "20"
    default_pkg_breadth: str = "15"
    default_pkg_height: str = "5"
    default_pkg_weight: str = "0.5"

    # AI 配置
    ai_provider: str = "qwen"
    qwen_api_key: Optional[str] = ""
    qwen_base_url: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: Optional[str] = "qwen-plus"
    
    deepseek_api_key: Optional[str] = ""
    deepseek_base_url: Optional[str] = "https://api.deepseek.com"
    deepseek_model: Optional[str] = "deepseek-flash"
    deepseek_vision_model: Optional[str] = "deepseek-flash"

    # 标题 SEO 关键词扩展增强
    seo_title_enabled: bool = True
    seo_title_max_len: int = 120

    # AI 清洗双模式配置
    cleaner_mode: Optional[str] = "text"  # 'text' 或 'vision'
    qwen_vision_model: Optional[str] = "qwen-vl-plus"  # 'qwen-vl-plus' 或 'qwen-vl-max'

class SyncCredentialsRequest(BaseModel):
    seller_id: Optional[str] = None
    fk_csrf_token: Optional[str] = None
    cookie: Optional[str] = None
