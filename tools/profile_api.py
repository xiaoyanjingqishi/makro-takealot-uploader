import time
import requests
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = "http://127.0.0.1:8001"

def profile_api():
    # Login to get token
    session = requests.Session()
    # Try logging in as admin or default user
    login_url = f"{BASE_URL}/api/auth/token"
    # Let's inspect users to get test credentials or check headers
    from backend.app.database import SessionLocal
    from backend.app.models.user import User
    from backend.app.utils.auth import create_access_token
    db = SessionLocal()
    admin_user = db.query(User).filter(User.role == "ADMIN").first() or db.query(User).first()
    if not admin_user:
        print("No user found!")
        return
    token = create_access_token(data={"user_id": admin_user.id, "username": admin_user.username})
    headers = {"Authorization": f"Bearer {token}"}
    db.close()

    endpoints = [
        ("KPI: Piggyback KPI Stats", "/api/piggyback/kpi-stats"),
        ("KPI: Piggyback KPI with store_id=3", "/api/piggyback/kpi-stats?store_id=3"),
        ("List: Piggyback Items (Page 1, 30)", "/api/piggyback/items?page=1&page_size=30"),
        ("List: Piggyback Items store=3, stage=ALL", "/api/piggyback/items?page=1&page_size=30&store_id=3&stage=ALL"),
        ("List: Piggyback Items store=3, stage=PUBLISHED", "/api/piggyback/items?page=1&page_size=30&store_id=3&stage=PUBLISHED"),
        ("List: Piggyback Items store=3, search=sensor", "/api/piggyback/items?page=1&page_size=30&store_id=3&search=sensor"),
        ("Reprice: KPI Stats", "/api/reprice/kpi-stats?store_id=3"),
        ("Reprice: List (Page 1, 30)", "/api/reprice/list?page=1&page_size=30&store_id=3"),
        ("Store Products: List (Page 1, 30)", "/api/makro/store-products?page=1&page_size=30&store_id=3"),
        ("Portal: Store Listings DB", "/api/makro-portal/listings-db?store_id=3&page_no=1&page_size=30"),
        ("Static: HTML Home", "/"),
        ("Static: JS Piggyback Module", "/static/js/modules/piggyback.js"),
    ]

    print(f"{'Endpoint Description':<40} | {'Status':<6} | {'Time (ms)':<10} | {'Size (KB)':<10}")
    print("-" * 75)

    for desc, path in endpoints:
        url = f"{BASE_URL}{path}"
        start = time.perf_counter()
        try:
            resp = session.get(url, headers=headers, timeout=15)
            duration = (time.perf_counter() - start) * 1000
            size_kb = len(resp.content) / 1024
            print(f"{desc:<40} | {resp.status_code:<6} | {duration:>8.2f}ms | {size_kb:>8.2f}KB")
        except Exception as e:
            print(f"{desc:<40} | ERROR  | {str(e)[:30]}")

if __name__ == "__main__":
    profile_api()
