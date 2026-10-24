# -*- coding: utf-8 -*-
import sys
import os
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import SessionLocal
from app.models.makro_piggyback import MakroPiggybackItem
from app.services.compliance_service import ComplianceService

db = SessionLocal()
item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == 29356).first()
if not item:
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.makro_product_id == 'DTCHNFQUG3JQDPZG').first()

print(f"Testing Item ID: {item.id}")
print(f"Title: {item.title}")
print(f"Brand: {item.brand}")
print(f"Image: {item.image_url}")

product_data = {
    'title': item.title,
    'makro_title': item.title,
    'brand': item.brand,
    'category': item.vertical or 'Computer Cables',
    'image_url': item.image_url,
    'is_piggyback': True,
    'original_seller': item.original_seller or ''
}

cs = ComplianceService.from_db(db)
res = cs.check_product(product_data)

print("\n" + "="*50)
print(f"=== OVERALL COMPLIANCE STATUS: {res.get('compliance_status')} ===")
print(f"Summary: {res.get('reconciliation_summary')}")
print("="*50)

print("\n--- DEEPSEEK VERDICT ---")
print(json.dumps(res.get('deepseek_verdict'), ensure_ascii=False, indent=2))

print("\n--- QWEN VERDICT ---")
print(json.dumps(res.get('qwen_verdict'), ensure_ascii=False, indent=2))
