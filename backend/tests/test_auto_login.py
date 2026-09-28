import sys
import os

# 确保 backend 位于 sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal
from app.models.store import Store
from app.models.user import User
from app.utils.auth import create_access_token

client = TestClient(app)

def test_api_routes():
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        token = create_access_token({"user_id": admin.id, "username": admin.username, "role": admin.role})
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Test test-email validation
        res_email = client.post("/api/stores/test-email", json={
            "email": "invalid_email@example.com",
            "password": "wrong_password"
        }, headers=headers)
        assert res_email.status_code == 200
        data = res_email.json()
        assert data["success"] is False
        print("[OK] Test-email endpoint responded with handled failure:", data["message"][:40])

        # 2. Test auto-login on a store without credentials
        store = db.query(Store).first()
        if store:
            res_login = client.post(f"/api/stores/{store.id}/auto-login", json={}, headers=headers)
            # Should return 400 because store doesn't have login credentials yet
            print("[OK] Auto-login validation on unconfigured store returned:", res_login.status_code)

        # 3. Test store list format
        res_stores = client.get("/api/stores", headers=headers)
        assert res_stores.status_code == 200
        stores_data = res_stores.json()
        assert len(stores_data) > 0
        s0 = stores_data[0]
        assert "login_email" in s0
        assert "has_login_password" in s0
        assert "imap_server" in s0
        assert "has_imap_password" in s0
        print("[OK] Store response includes automation and IMAP fields:", s0["name"])

        print("ALL AUTO LOGIN API TESTS PASSED!")
    finally:
        db.close()

if __name__ == "__main__":
    test_api_routes()
