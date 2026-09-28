import re
import logging
from typing import List, Dict, Any, Tuple
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models.product import Product
from .ai_cleaner_service import reconstruct_accessory_title, sanitize_accessory_core_name, truncate_title_safely
from .compliance_service import FAMOUS_BRANDS, PROTECTED_ENTERTAINMENT_IPS

logger = logging.getLogger(__name__)

COMPATIBILITY_PATTERNS = [
    r'\bcompatible\s+with\b',
    r'\bcompatible\s+for\b',
    r'\bsuitable\s+for\b',
    r'\breplacement\s+for\b',
    r'\bdesigned\s+for\b',
]

def needs_accessory_title_fix(makro_title: str) -> bool:
    """
    检查标题是否存在【在兼容词 (Compatible with) 之前出现第三方大牌商标/专有型号词】的高危侵权模式
    """
    if not makro_title:
        return False

    t_lower = makro_title.lower()
    
    # 查找兼容句式起点
    m_comp = re.search(r'\b(compatible\s+with|compatible\s+for|suitable\s+for|replacement\s+for|designed\s+for)\b', t_lower)
    if not m_comp:
        return False

    prefix = t_lower[:m_comp.start()].strip()
    
    # 检查 prefix 是否包含任何第三方品牌/型号
    for b in FAMOUS_BRANDS:
        if re.search(rf'\b{re.escape(b)}\b', prefix):
            return True

    for ip in PROTECTED_ENTERTAINMENT_IPS:
        if re.search(rf'\b{re.escape(ip)}\b', prefix):
            return True

    # 常见专有名词特征
    extra_keywords = ["airtag", "ps5", "ps4", "playstation", "magsafe", "joy-con", "joycon", "pulse 3d", "dyson"]
    for kw in extra_keywords:
        if re.search(rf'\b{re.escape(kw)}\b', prefix):
            return True

    return False

def fix_product_titles(db: Session, dry_run: bool = False) -> List[Dict[str, Any]]:
    """
    全量扫描并修复数据库中在 Compatible with 前残留第三方品牌/专有名词的商品标题
    """
    products = db.query(Product).filter(Product.makro_title.isnot(None)).all()
    fixed_records = []

    for p in products:
        makro_title = p.makro_title or ""
        if not needs_accessory_title_fix(makro_title):
            continue

        raw_title = p.takealot_title or ""
        brand = p.makro_brand or p.takealot_brand or "Beishi"
        vert = p.makro_vertical or ""

        new_title = reconstruct_accessory_title(
            makro_title=makro_title,
            raw_title=raw_title,
            target_brand=brand,
            nature="COMPATIBLE_ACCESSORY",
            vertical=vert
        )

        if new_title and new_title != makro_title:
            fixed_records.append({
                "id": p.id,
                "old_title": makro_title,
                "new_title": new_title,
                "vertical": vert
            })
            if not dry_run:
                p.makro_title = new_title
                p.title = new_title
                # 更新 model_number (去品牌前缀)
                clean_mn = re.sub(rf'^\s*{re.escape(brand)}\s*[-_:]*\s*', '', new_title, flags=re.I)
                clean_mn = re.sub(rf'\b{re.escape(brand)}\b', '', clean_mn, flags=re.I).strip(' -_,:;')
                if hasattr(p, 'model_number') and p.model_number:
                    p.model_number = clean_mn[:250]
                if hasattr(p, 'model_name') and p.model_name:
                    p.model_name = truncate_title_safely(clean_mn, 120)

    if not dry_run and fixed_records:
        db.commit()

    return fixed_records

if __name__ == "__main__":
    db = SessionLocal()
    print(">>> 正在扫描并检测数据库中的配件兼容标题侵权风险...")
    fixed = fix_product_titles(db, dry_run=False)
    print(f">>> 扫描完成！共修复 {len(fixed)} 个受影响商品：")
    for item in fixed:
        print(f"\n[ID {item['id']}] ({item['vertical']})")
        print(f"  旧标题: {item['old_title']}")
        print(f"  新标题: {item['new_title']}")
    db.close()
