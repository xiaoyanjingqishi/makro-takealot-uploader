import sys
import os
from pathlib import Path

# 添加 backend 到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health():
    resp = client.get("/")
    assert resp.status_code == 200
    print("Health check OK:", resp.json())

def test_settings():
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    print("Settings OK, seller_id:", data.get("seller_id"), "markup_ratio:", data.get("markup_ratio"))

def test_product_lifecycle():
    # 1. 模拟插件发送商品采集请求
    collect_payload = {
        "takealot_id": "TSIN-12345678",
        "takealot_url": "https://www.takealot.com/sample-bath-towel/PLID12345",
        "takealot_title": "Luxury 100% Microfiber Quick Dry Bath Towel (Multicolor)",
        "takealot_price": 150.0,
        "takealot_brand": "Generic Brand",
        "takealot_category": "Home & Kitchen / Bathroom / Towels",
        "takealot_description": "Super absorbent microfiber bath towel, lightweight and durable.",
        "takealot_specs": {
            "Material": "Microfiber",
            "Dimensions": "70cm x 140cm",
            "Pack Size": "1"
        },
        "raw_images": [
            "https://media.takealot.com/covers_tsins/sample1.jpg",
            "https://media.takealot.com/covers_tsins/sample2.jpg"
        ],
        "variants": []
    }
    
    resp = client.post("/api/products/collect", json=collect_payload)
    assert resp.status_code == 200
    product = resp.json()
    print("Product created OK, ID:", product.get("id"))
    print("  Status:", product.get("status"))
    print("  Makro Title:", product.get("makro_title"))
    print("  Selling Price (Takealot 150 * 1.35 + 20):", product.get("makro_selling_price"))
    print("  MRP (Selling * 1.5):", product.get("makro_mrp"))
    assert product.get("makro_selling_price") == 222  # 150*1.35 + 20 = 222.5 -> 222 (bankers rounding)
    assert product.get("makro_mrp") == 333           # 222 * 1.5 = 333.0

    # 2. 验证生成给 Makro 的 Payload 结构
    resp_payload = client.get(f"/api/makro/build-payload/{product['id']}")
    assert resp_payload.status_code == 200
    makro_payload = resp_payload.json()
    print("Makro submit payload built successfully!")
    print("  Context:", makro_payload.get("context"))
    print("  Vertical:", makro_payload.get("vertical"))
    print("  Brand in Catalog:", makro_payload["catalogRequestEntity"]["catalogAttributes"]["brand"])
    print("  Selling Price in Listing:", makro_payload["listingRequestEntity"]["listingAttributes"]["flipkart_selling_price"])
    print("  Package Dimensions:", makro_payload["listingRequestEntity"]["packages"])

if __name__ == "__main__":
    test_health()
    test_settings()
    test_product_lifecycle()
    print("\nALL BACKEND TESTS PASSED!")
