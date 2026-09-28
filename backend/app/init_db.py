import os
import sqlite3
from .database import engine, Base
from .models.user import User, UserStore
from .models.store import Store, ProductStoreListing
from .models.product import Product, ProductVariant
from .models.task import TaskLog
from .models.setting import SystemSetting
from .models.compliance_log import ComplianceArbitrationLog
from .models.makro_listing import MakroListing
from .models.makro_order import MakroOrder
from .utils.auth import hash_password

def init_and_migrate_db():
    print(">>> 正在初始化与迁移数据库结构...")
    # 1. 创建所有声明式模型表
    Base.metadata.create_all(bind=engine)

    # 2. 补丁迁移现有表字段 (SQLite ALTER TABLE 容错)
    db_path = str(engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查 products 是否有 user_id
    cursor.execute("PRAGMA table_info(products)")
    prod_cols = [c[1] for c in cursor.fetchall()]
    if "user_id" not in prod_cols:
        print("  - 给 products 表增补 user_id 字段")
        cursor.execute("ALTER TABLE products ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")

    # 检查 stores 是否有 default_location_id 及自动化登录字段
    cursor.execute("PRAGMA table_info(stores)")
    store_cols = [c[1] for c in cursor.fetchall()]
    if "default_location_id" not in store_cols:
        print("  - 给 stores 表增补 default_location_id 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN default_location_id VARCHAR(100)")
    if "login_email" not in store_cols:
        print("  - 给 stores 表增补 login_email 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN login_email VARCHAR(150)")
    if "login_password" not in store_cols:
        print("  - 给 stores 表增补 login_password 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN login_password VARCHAR(150)")
    if "imap_server" not in store_cols:
        print("  - 给 stores 表增补 imap_server 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN imap_server VARCHAR(100)")
    if "imap_port" not in store_cols:
        print("  - 给 stores 表增补 imap_port 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN imap_port INTEGER DEFAULT 993")
    if "imap_user" not in store_cols:
        print("  - 给 stores 表增补 imap_user 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN imap_user VARCHAR(150)")
    if "imap_password" not in store_cols:
        print("  - 给 stores 表增补 imap_password 字段")
        cursor.execute("ALTER TABLE stores ADD COLUMN imap_password VARCHAR(150)")

    conn.commit()
    conn.close()

    # 3. 初始化默认管理员账号
    from .database import SessionLocal
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            print("  - 创建默认超级管理员: admin (密码: admin123)")
            admin = User(
                username="admin",
                password_hash=hash_password("admin123"),
                role="ADMIN",
                nickname="系统管理员",
                is_active=True
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

        # 4. 将现有店铺全量授权给管理员
        all_stores = db.query(Store).all()
        for s in all_stores:
            exists = db.query(UserStore).filter(UserStore.user_id == admin.id, UserStore.store_id == s.id).first()
            if not exists:
                db.add(UserStore(user_id=admin.id, store_id=s.id))
        db.commit()

        # 5. 迁移历史商品，归属于 admin
        unassigned_prods = db.query(Product).filter(Product.user_id.is_(None)).all()
        if unassigned_prods:
            print(f"  - 将历史 {len(unassigned_prods)} 件选品数据初始化归属于管理员 (ID: {admin.id})")
            for p in unassigned_prods:
                p.user_id = admin.id
            db.commit()

        print(">>> 数据库结构与权限基础数据初始化完成！")
    finally:
        db.close()

if __name__ == "__main__":
    init_and_migrate_db()
