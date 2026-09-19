from pydantic_settings import BaseSettings
from typing import Optional
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "Makro-Takealot 智能搬品系统"
    API_V1_STR: str = "/api"
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/makro_app.db"

    # 默认定价策略规则
    DEFAULT_MARKUP_RATIO: float = 1.35      # 售价加价比例
    DEFAULT_FIXED_MARKUP: float = 20.0      # 固定加价金额 (ZAR)
    DEFAULT_MRP_RATIO: float = 1.5          # 划线原价倍率 (MRP = 售价 * 1.5)

    # Makro 默认销售与履约配置
    DEFAULT_SELLER_ID: str = "cb80491bf0a34dc5"
    DEFAULT_FK_CSRF_TOKEN: str = "FbvXzXEP-45o5eUtkeX8Wo6LCq9GBWDg9Rcg"
    DEFAULT_BRAND: str = "Beishi"
    DEFAULT_SHIPPING_DAYS: str = "15"
    DEFAULT_COUNTRY_OF_ORIGIN: str = "CN"
    DEFAULT_MANUFACTURER: str = "Beijing"
    DEFAULT_PACKER: str = "BeiShi"
    DEFAULT_SERVICE_PROFILE: str = "NON_FBF"

    # 默认包装尺寸 (cm / kg)
    DEFAULT_PKG_LENGTH: str = "20"
    DEFAULT_PKG_BREADTH: str = "15"
    DEFAULT_PKG_HEIGHT: str = "5"
    DEFAULT_PKG_WEIGHT: str = "0.5"

    # AI 大模型配置 (支持 通义千问 Qwen 与 DeepSeek)
    AI_PROVIDER: str = "qwen"  # 'qwen' or 'deepseek'
    
    # 通义千问 (DashScope OpenAI 兼容接口)
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"
    
    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # 上品执行模式: 'backend' (后端发包) 或 'extension' (插件在浏览器上下文代发)
    UPLOAD_MODE: str = "backend"

    # 默认批量上品并发线程数 (推荐 2~3，支持店铺间并发)
    DEFAULT_PUBLISH_CONCURRENCY: int = 2

    # 标题 SEO 关键词搜索意图拓展配置
    DEFAULT_SEO_TITLE_ENABLED: bool = True
    DEFAULT_SEO_TITLE_MAX_LEN: int = 120

    # AI 清洗双模式配置: 'text' (纯文本快速清洗) 或 'vision' (图文多模态首图深度校准)
    DEFAULT_CLEANER_MODE: str = "text"
    DEFAULT_QWEN_VISION_MODEL: str = "qwen-vl-plus"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
