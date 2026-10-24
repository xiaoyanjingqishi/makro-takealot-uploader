# -*- coding: utf-8 -*-
import sys
import os
import time
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import SessionLocal
from app.models.product import Product
from app.services.compliance_service import ComplianceService, IMAGE_CACHE_DIR

db = SessionLocal()
cs = ComplianceService.from_db(db)

print("=== 1. 系统与模型配置验证 ===")
print(f"通义千问文本: {cs.qwen_model}, 视觉: {cs.qwen_vision_model}")
print(f"DeepSeek 文本: {cs.deepseek_model}, 视觉: {cs.deepseek_vision_model}")
print(f"强制双模型会审: {cs.is_dual_ai_mode}")

# 从数据库取真实商品 4310 (Memory Foam Chair Cushion)
p_4310 = db.query(Product).filter(Product.id == 4310).first()
sample_1 = {
    "title": p_4310.takealot_title,
    "makro_title": p_4310.makro_title or p_4310.takealot_title,
    "makro_brand": p_4310.makro_brand or "Generic",
    "category": p_4310.takealot_category or "Home & Kitchen",
    "raw_images": p_4310.raw_images,
    "is_piggyback": True,
    "original_seller": "Home Decor Store"
}

# 彻底清理该图可能存在的旧 verdict 缓存以测试全量首次审核
clean_url = json.loads(p_4310.raw_images)[0]
import hashlib
cache_key = hashlib.md5(clean_url.encode("utf-8")).hexdigest()
verdict_file = os.path.join(IMAGE_CACHE_DIR, f"{cache_key}.verdict.json")
if os.path.exists(verdict_file):
    os.remove(verdict_file)

print(f"\n=== 2. 执行真实商品 4310 首次全量双模型审核 ===")
print(f"商品标题: {sample_1['title']}")
print(f"首图 URL: {clean_url}")
t0 = time.time()
res_1 = cs.check_product(sample_1, check_ai_title=True, check_image=True)
elapsed_1 = time.time() - t0

print(f"\n[首次审核耗时]: {elapsed_1:.2f}s")
print(f"[最终合规评级]: {res_1.get('compliance_status')}")
print(f"[共识比例]: {res_1.get('consensus_ratio')}")
print(f"[裁决摘要]: {res_1.get('reconciliation_summary')}")
print(f"[Qwen 文本审查]: {res_1.get('qwen_verdict', {}).get('title_check', {}).get('risk_level')}")
print(f"[DeepSeek 文本审查]: {res_1.get('deepseek_verdict', {}).get('title_check', {}).get('risk_level')}")
print(f"[Qwen 视觉审查]: {res_1.get('qwen_verdict', {}).get('image_check', {}).get('risk_level')}")
print(f"[DeepSeek 视觉审查]: {res_1.get('deepseek_verdict', {}).get('image_check', {}).get('risk_level')}")

print("\n=== 3. 验证首图视觉审查持久化缓存 (.verdict.json) ===")
print(f"缓存文件存在: {os.path.exists(verdict_file)}")
t1 = time.time()
res_1_cached = cs.check_product(sample_1, check_ai_title=False, check_image=True)
elapsed_cache = time.time() - t1
print(f"[二次图审耗时 (命中缓存)]: {elapsed_cache:.4f}s (Token消耗: 0)")
print(f"[二次图审结论]: {res_1_cached.get('compliance_status')}")

print("\n=== 4. 验证文本 PROHIBITED 短路熔断 ===")
# 假冒大牌/禁运品用例
sample_prohibited = {
    "title": "Brand New Apple Airpods Pro 2nd Gen Wireless Bluetooth Earphones with Active Noise Cancellation",
    "makro_title": "Apple Airpods Pro 2nd Gen Wireless Bluetooth Earphones",
    "makro_brand": "Apple",
    "category": "Electronics",
    "raw_images": p_4310.raw_images,
    "is_piggyback": True
}
t2 = time.time()
res_prohib = cs.check_product(sample_prohibited, check_ai_title=True, check_image=True)
elapsed_2 = time.time() - t2
print(f"[短路熔断耗时]: {elapsed_2:.2f}s")
print(f"[短路审查最终评级]: {res_prohib.get('compliance_status')}")
print(f"[Qwen 视觉摘要]: {res_prohib.get('qwen_verdict', {}).get('image_check', {}).get('summary')}")
print(f"[DeepSeek 视觉摘要]: {res_prohib.get('deepseek_verdict', {}).get('image_check', {}).get('summary')}")
