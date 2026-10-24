import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 北京时间 (UTC+8) 与 印度标准时间 (IST, UTC+5:30)
BEIJING_TZ = timezone(timedelta(hours=8))
IST_TZ = timezone(timedelta(hours=5, minutes=30))


def parse_to_beijing_time(dt_val: Optional[any]) -> Optional[datetime]:
    """
    将 Makro/Flipkart 平台抓取的时间字符串或 datetime 规整并准确换算为北京时间 (UTC+8:00) 的无时区 datetime 对象。
    
    规则：
    1. 若包含时区偏移 (如 "+05:30", "Z", "+00:00")，转换为 UTC+8 并剥离 tzinfo；
    2. 若不含时区标识，由于 Makro 卖家后端为 Flipkart 架构 (默认时区为 IST, UTC+5:30)，
       自动视作 IST 时区并换算至北京时间 (+2.5 小时)；
    3. 异常时安全回退，绝不导致业务崩溃。
    """
    if not dt_val:
        return None
    
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is not None:
            return dt_val.astimezone(BEIJING_TZ).replace(tzinfo=None)
        # 若传入原生 datetime，视情况假定其已由调用方处理或保留
        return dt_val

    s = str(dt_val).strip()
    if not s or s.lower() in ("null", "none"):
        return None

    try:
        # 处理常见 ISO 格式尾部的 Z
        clean_s = s
        if clean_s.endswith("Z") or clean_s.endswith("z"):
            clean_s = clean_s[:-1] + "+00:00"

        # 尝试标准 ISO 解析
        dt = datetime.fromisoformat(clean_s)
        if dt.tzinfo is not None:
            return dt.astimezone(BEIJING_TZ).replace(tzinfo=None)
        else:
            # 缺失时区标识，默认按印度标准时间 (+05:30) 注入并换算为北京时间
            return dt.replace(tzinfo=IST_TZ).astimezone(BEIJING_TZ).replace(tzinfo=None)
    except Exception as e:
        logger.debug(f"标准 ISO 解析失败 ({s}), 尝试备用正则解析: {e}")

    # 备用容错解析：剥离毫秒及非数字字符
    try:
        # 去掉可能的时区和微秒后缀进行简单解析
        part = s.split(".")[0].replace("T", " ")
        if len(part) == 19:
            base_dt = datetime.strptime(part, "%Y-%m-%d %H:%M:%S")
            # 如果原始字符串明确含有 +05:30
            if "+05:30" in s:
                return (base_dt.replace(tzinfo=IST_TZ).astimezone(BEIJING_TZ)).replace(tzinfo=None)
            elif "+08:00" in s:
                return base_dt
            else:
                # 默认按 IST 增加 2小时30分
                return base_dt + timedelta(hours=2, minutes=30)
    except Exception as ex:
        logger.warning(f"parse_to_beijing_time 备用解析亦失败 ({s}): {ex}")

    return None


def format_beijing_time(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    将 datetime 格式化为北京时间显示字符串
    """
    if not dt:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(BEIJING_TZ).replace(tzinfo=None)
    return dt.strftime(fmt)
