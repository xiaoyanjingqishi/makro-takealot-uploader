import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.utils.auth import create_access_token
import requests

BASE_URL = "http://127.0.0.1:8001"

def run_tests():
    print("=== 开始端到端自动化验证 ===")

    # 1. 签发有效 admin JWT token
    token = create_access_token({"user_id": 1, "username": "admin", "role": "ADMIN"})
    headers = {"Authorization": f"Bearer {token}"}

    # 2. 获取店铺列表
    store_resp = requests.get(f"{BASE_URL}/api/stores", headers=headers)
    stores = store_resp.json() if store_resp.status_code == 200 else []
    if not stores:
        print("未获取到店铺列表，退出")
        return
    target_store = next((s for s in stores if s["id"] == 3), stores[0])
    store_id = target_store["id"]
    print(f"测试店铺 ID: {store_id} ({target_store.get('name')})")

    # 3. 测试 /api/reprice/list 默认拉取与 KPI
    list_resp = requests.get(f"{BASE_URL}/api/reprice/list?store_id={store_id}&page=1&page_size=20", headers=headers)
    assert list_resp.status_code == 200, f"List API 失败: {list_resp.text}"
    list_data = list_resp.json()
    kpi = list_data.get("kpi", {})
    total = list_data.get("total", 0)
    print(f"大盘总在售监控数: {total}, KPI: {kpi}")
    assert "safe_count" in kpi, "KPI 缺少 safe_count"
    assert "risk_count" in kpi, "KPI 缺少 risk_count"
    assert "missing_floor_count" in kpi, "KPI 缺少 missing_floor_count"

    # 验证列表返回的所有商品 listing internal_state 是否均为 ACTIVE
    items = list_data.get("items", [])
    print(f"第 1 页返回 {len(items)} 条商品")
    if items:
        first_item = items[0]
        assert "compliance_details" in first_item, "item 必须包含 compliance_details"
        print(f"首个商品: SKU={first_item['seller_sku']}, 来源={first_item['source_type']}, 合规状态={first_item['compliance_status']}")

    # 4. 测试“缺少底价”筛选过滤
    missing_floor_cnt = kpi.get("missing_floor_count", 0)
    print(f"KPI 统计缺底价数: {missing_floor_cnt}")
    filter_resp = requests.get(f"{BASE_URL}/api/reprice/list?store_id={store_id}&buybox_status=MISSING_FLOOR&page=1&page_size=20", headers=headers)
    assert filter_resp.status_code == 200, f"缺底价筛选失败: {filter_resp.text}"
    filter_data = filter_resp.json()
    filter_total = filter_data.get("total", 0)
    print(f"缺底价筛选匹配数据量: {filter_total} (预期等于 KPI {missing_floor_cnt})")
    assert filter_total == missing_floor_cnt, f"缺底价筛选返回数 ({filter_total}) 与 KPI ({missing_floor_cnt}) 不一致！"

    # 5. 测试合规状态筛选 (SAFE, RISK, PROHIBITED)
    safe_cnt = kpi.get("safe_count", 0)
    safe_resp = requests.get(f"{BASE_URL}/api/reprice/list?store_id={store_id}&compliance_status=SAFE&page=1&page_size=20", headers=headers)
    assert safe_resp.status_code == 200
    print(f"合规 SAFE 筛选匹配数据量: {safe_resp.json().get('total')} (预期等于 KPI safe_count: {safe_cnt})")

    # 6. 测试双向同步裁决 (StoreProductReprice -> MakroPiggybackItem)
    # 找一个跟品来源的商品
    piggyback_reprice_item = next((it for it in items if it.get("source_type") == "PIGGYBACK"), None)
    if not piggyback_reprice_item and items:
        piggyback_reprice_item = items[0]

    if piggyback_reprice_item:
        item_id = piggyback_reprice_item["id"]
        print(f"测试裁决商品 ID {item_id} (SKU: {piggyback_reprice_item['seller_sku']})...")

        # 裁定为 SAFE
        arb_resp = requests.post(
            f"{BASE_URL}/api/reprice/arbitrate/{item_id}",
            json={"human_verdict": "SAFE", "human_notes": "自动化测试: 卖家自造白牌豁免"},
            headers=headers
        )
        assert arb_resp.status_code == 200, f"裁决失败: {arb_resp.text}"
        arb_data = arb_resp.json()
        print(f"裁定为 SAFE 成功: {arb_data['compliance_status']}, 同步跟品={arb_data.get('synced_to_piggyback')}")
        assert arb_data["compliance_status"] == "SAFE"

        # 裁定为 PROHIBITED (验证侵权熔断)
        arb_resp2 = requests.post(
            f"{BASE_URL}/api/reprice/arbitrate/{item_id}",
            json={"human_verdict": "PROHIBITED", "human_notes": "自动化测试: 红线侵权禁售"},
            headers=headers
        )
        assert arb_resp2.status_code == 200
        arb_data2 = arb_resp2.json()
        print(f"裁定为 PROHIBITED 成功: 状态={arb_data2['compliance_status']}, 监控状态={arb_data2['monitor_status']}, 自动跟价={arb_data2['auto_reprice']}")
        assert arb_data2["compliance_status"] == "PROHIBITED"
        assert arb_data2["monitor_status"] == "BLOCKED"
        assert arb_data2["auto_reprice"] is False

        # 恢复裁定为 SAFE
        arb_resp3 = requests.post(
            f"{BASE_URL}/api/reprice/arbitrate/{item_id}",
            json={"human_verdict": "SAFE", "human_notes": "自动化测试: 恢复合规"},
            headers=headers
        )
        assert arb_resp3.status_code == 200
        print(f"恢复为 SAFE 成功: 状态={arb_resp3.json()['compliance_status']}, 监控状态={arb_resp3.json()['monitor_status']}, 自动跟价={arb_resp3.json()['auto_reprice']}")
        assert arb_resp3.json()["compliance_status"] == "SAFE"
        assert arb_resp3.json()["monitor_status"] == "ACTIVE"
        assert arb_resp3.json()["auto_reprice"] is True

    # 7. 测试反向同步：跟品挂靠表裁定 -> 同步至 StoreProductReprice 监控表
    print("测试反向同步: 从跟品池 ID 6 提交裁定，验证同步至 StoreProductReprice ID 1...")
    pb_arb_resp = requests.post(
        f"{BASE_URL}/api/piggyback/arbitrate/6",
        json={"human_verdict": "SAFE", "human_notes": "自动化测试: 跟品反向同步至跟价"},
        headers=headers
    )
    assert pb_arb_resp.status_code == 200, f"跟品裁定失败: {pb_arb_resp.text}"
    print(f"跟品裁定成功: {pb_arb_resp.json()['compliance_status']}")

    # 验证 StoreProductReprice ID 1 是否被同步更新为 SAFE
    rep_check = requests.get(f"{BASE_URL}/api/reprice/list?store_id={store_id}&search=GP261003235420BCD2", headers=headers)
    assert rep_check.status_code == 200
    matched = rep_check.json().get("items", [])
    assert len(matched) > 0, "未搜到关联跟价商品"
    print(f"反向同步验证成功: 跟价商品 SKU={matched[0]['seller_sku']}, 合规状态={matched[0]['compliance_status']}")
    assert matched[0]["compliance_status"] == "SAFE"

    print("=== 所有端到端测试全部通过！ ===")

if __name__ == "__main__":
    run_tests()
