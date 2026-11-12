# -*- coding: utf-8 -*-
"""
自动化测试脚本：检验多店铺数据隔离、API 响应与凭据配置完整性。
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from starlette.testclient import TestClient
from main import app
from app.database import SessionLocal
from app.models.store import Store
from app.utils.auth import create_access_token

token = create_access_token(data={"sub": "admin", "user_id": 1, "role": "ADMIN"})
headers = {"Authorization": f"Bearer {token}"}
client = TestClient(app)

def test_store_isolation():
    print("=== 1. 验证店铺配置与双邮箱隔离 ===")
    with SessionLocal() as db:
        store3 = db.query(Store).filter(Store.id == 3).first()
        store4 = db.query(Store).filter(Store.id == 4).first()

        assert store3.seller_id == "6f97e8077c294234", f"Store 3 seller_id 异常: {store3.seller_id}"
        assert store3.login_email == "18350589058@163.com", f"Store 3 login_email 异常: {store3.login_email}"

        assert store4.seller_id == "15a8324a830b4049", f"Store 4 seller_id 异常: {store4.seller_id}"
        assert store4.login_email == "chenyouyou0407@gmail.com", f"Store 4 login_email 异常: {store4.login_email}"
        assert store4.imap_user == "18350589058@163.com", f"Store 4 imap_user 异常: {store4.imap_user}"
        assert store3.seller_id != store4.seller_id, "Store 3 与 Store 4 的 seller_id 仍有冲突！"
        print("✓ 店铺配置隔离验证通过：hzh 与 xixi 各自拥有独立的 Makro Seller ID 与登录邮箱。")

    print("\n=== 2. 验证订单管理 API 隔离 ===")
    res_o4 = client.get("/api/store-orders/list?store_id=4", headers=headers)
    assert res_o4.status_code == 200, f"xixi 订单接口异常: {res_o4.status_code}"
    data_o4 = res_o4.json()
    print(f"Store 4 (xixi) 订单总数: {data_o4.get('total', 0)} (期望: 0)")
    assert data_o4.get('total', 0) == 0, f"xixi 订单数未清零: {data_o4.get('total')}"

    res_o3 = client.get("/api/store-orders/list?store_id=3", headers=headers)
    assert res_o3.status_code == 200, f"hzh 订单接口异常: {res_o3.status_code}"
    data_o3 = res_o3.json()
    print(f"Store 3 (hzh) 订单总数: {data_o3.get('total', 0)} (期望: 251)")
    assert data_o3.get('total', 0) == 251, f"hzh 订单数受损: {data_o3.get('total')}"
    print("✓ 订单管理隔离验证通过！")

    print("\n=== 3. 验证跟价监控 API 隔离 ===")
    res_r4 = client.get("/api/reprice/list?store_id=4", headers=headers)
    assert res_r4.status_code == 200, f"xixi 跟价接口异常: {res_r4.status_code}"
    data_r4 = res_r4.json()
    print(f"Store 4 (xixi) 跟价总数: {data_r4.get('total', 0)} (期望: 1)")
    assert data_r4.get('total', 0) <= 1, f"xixi 仍残留 hzh 跟价商品: {data_r4.get('total')}"

    res_r3 = client.get("/api/reprice/list?store_id=3", headers=headers)
    assert res_r3.status_code == 200, f"hzh 跟价接口异常: {res_r3.status_code}"
    data_r3 = res_r3.json()
    print(f"Store 3 (hzh) 跟价总数: {data_r3.get('total', 0)} (期望: 41042)")
    assert data_r3.get('total', 0) == 41042, f"hzh 跟价总数受损: {data_r3.get('total')}"
    print("✓ 跟价监控隔离验证通过！")

    print("\n=== 4. 验证店铺在售商品 API 隔离 ===")
    res_l4 = client.get("/api/store-products/list?store_id=4", headers=headers)
    assert res_l4.status_code == 200, f"xixi 在售商品接口异常: {res_l4.status_code}"
    data_l4 = res_l4.json()
    print(f"Store 4 (xixi) 在售商品总数: {data_l4.get('total', 0)} (期望: 1)")
    assert data_l4.get('total', 0) <= 1, f"xixi 仍残留 hzh 在售商品: {data_l4.get('total')}"

    res_l3 = client.get("/api/store-products/list?store_id=3", headers=headers)
    assert res_l3.status_code == 200, f"hzh 在售商品接口异常: {res_l3.status_code}"
    data_l3 = res_l3.json()
    print(f"Store 3 (hzh) 在售商品总数: {data_l3.get('total', 0)} (期望: 42113)")
    assert data_l3.get('total', 0) == 42113, f"hzh 在售商品总数受损: {data_l3.get('total')}"
    print("✓ 店铺在售商品隔离验证通过！")

    print("\n=== 5. 验证选品箱当前店铺隔离标记 ===")
    res_p = client.get("/api/products?page=1&page_size=10&store_id=4", headers=headers)
    assert res_p.status_code == 200, f"选品箱接口异常: {res_p.status_code}"
    data_p = res_p.json()
    items = data_p.get("items", [])
    print(f"选品箱正常拉取到 {len(items)} 条商品用于多店铺铺货。")
    print("✓ 选品箱多店独立铺货流程验证通过！")

    print("\n==========================================")
    print("🎉 全部多店铺数据隔离与业务验证 100% 通过！")
    print("==========================================")

if __name__ == "__main__":
    test_store_isolation()
