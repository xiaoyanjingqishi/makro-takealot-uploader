import unittest
import json
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from backend.app.database import Base
from backend.app.models.user import User
from backend.app.models.store import Store, ProductStoreListing
from backend.app.models.product import Product
from backend.app.models.makro_piggyback import MakroPiggybackItem
from backend.app.models.makro_reprice_log import MakroRepriceLog
from backend.app.services.auto_reprice_service import AutoRepriceService
from backend.app.api.piggyback import list_piggyback_items
from backend.app.api.products import list_products

class TestMatrixRepriceAndFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        self.db = self.Session()
        # 清空表
        for tbl in reversed(Base.metadata.sorted_tables):
            self.db.execute(tbl.delete())
        self.db.commit()

        # 创建测试用户
        self.admin = User(id=1, username="admin_boss", nickname="张总", password_hash="fakehash", role="ADMIN", is_active=True)
        self.op1 = User(id=2, username="op_alice", nickname="小红", password_hash="fakehash", role="OPERATOR", is_active=True)
        self.op2 = User(id=3, username="op_bob", nickname="小明", password_hash="fakehash", role="OPERATOR", is_active=True)
        self.db.add_all([self.admin, self.op1, self.op2])

        # 创建矩阵店铺
        self.store1 = Store(
            id=1, name="南非母婴一号店", seller_id="SELL_MOMI_01", default_brand="MomiBaby",
            fk_csrf_token="fake_csrf_1", cookie="fake_cookie_1", is_active=True
        )
        self.store2 = Store(
            id=2, name="贝诗家居旗舰店", seller_id="SELL_BEISHI_02", default_brand="Beishi",
            fk_csrf_token="fake_csrf_2", cookie="fake_cookie_2", is_active=True
        )
        self.db.add_all([self.store1, self.store2])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_multi_store_friendly_hold_no_undercut(self):
        """
        核心防自残测试：
        当店铺 1 (MomiBaby) 与店铺 2 (Beishi) 挂靠同款商品时，
        若前台 Buybox 占位卖家为店铺 1 (或其品牌 MomiBaby)，
        店铺 2 在巡检时不应下调价格 (-R1.00)，而应触发 WINNING_HOLD，维持原价保护利润。
        """
        fsn = "GSPHTESTMATRIX01"

        # 店铺1 已经上架该品，售价 R100
        item1 = MakroPiggybackItem(
            store_id=self.store1.id, user_id=self.op1.id,
            makro_product_id=fsn, title="智能婴儿恒温奶瓶", seller_sku="MM-BOTTLE-01",
            target_price=100.0, min_price_floor=80.0, auto_reprice=True,
            status="ACTIVE"
        )
        # 店铺2 也跟卖了该品，初始售价 R105，底价 R80
        item2 = MakroPiggybackItem(
            store_id=self.store2.id, user_id=self.op2.id,
            makro_product_id=fsn, title="智能婴儿恒温奶瓶", seller_sku="BS-BOTTLE-02",
            target_price=105.0, min_price_floor=80.0, auto_reprice=True,
            status="ACTIVE"
        )
        self.db.add_all([item1, item2])
        self.db.commit()

        # 模拟前台爬取结果：当前最低价由店铺1持有 (comp_seller='MomiBaby', price=100.0)
        mock_scraped = {
            "price": 100.0,
            "mrp": 150.0,
            "seller_name": "MomiBaby", # 匹配 store1.default_brand
            "seller_count": 2
        }

        with patch("backend.app.services.makro_scraper_service.MakroScraperService.scrape_buyer_frontend", return_value=mock_scraped):
            res = AutoRepriceService.reprice_single_item(item2, self.db, force=True)

        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["action"], "WINNING_HOLD")
        self.assertIn("矩阵友军店铺", res["reason"])
        # 售价应当坚守 105.0 或不低于 100.0，绝不能下调为 99.0 (无压价内卷)
        self.assertEqual(res["new_price"], 105.0)
        self.assertEqual(item2.buybox_status, "WINNING")

    def test_outside_competitor_triggers_undercut(self):
        """
        当占位卖家为外部真实竞对时，系统应正常执行 -R1.00 抢占 Buybox
        """
        fsn = "GSPHTESTOUTSIDE01"
        item = MakroPiggybackItem(
            store_id=self.store1.id, user_id=self.op1.id,
            makro_product_id=fsn, title="户外野营帐篷", seller_sku="CAMP-01",
            target_price=200.0, min_price_floor=150.0, price_strategy="MINUS_1",
            auto_reprice=True, status="ACTIVE"
        )
        self.db.add(item)
        self.db.commit()

        mock_scraped = {
            "price": 180.0,
            "mrp": 250.0,
            "seller_name": "RandomOutdoorCompetitor", # 外部陌生卖家
            "seller_count": 3
        }

        with patch("backend.app.services.makro_scraper_service.MakroScraperService.scrape_buyer_frontend", return_value=mock_scraped):
            with patch("backend.app.services.auto_reprice_service.AutoRepriceService._push_price_to_makro", return_value=True):
                res = AutoRepriceService.reprice_single_item(item, self.db, force=True)

        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["action"], "UNDER_CUT")
        # 180.0 - 1.0 = 179.0
        self.assertEqual(res["new_price"], 179.0)
        self.assertIn("RandomOutdoorCompetitor", res["reason"])

    def test_piggyback_operator_attribution_and_filter(self):
        """
        测试跟品列表的采品人归属与按采品人过滤功能
        """
        it_op1 = MakroPiggybackItem(
            store_id=self.store1.id, user_id=self.op1.id,
            makro_product_id="FSNOP10000000001", title="小红采的品", seller_sku="SKU-OP1-01",
            compliance_status="SAFE", status="PENDING"
        )
        it_op2 = MakroPiggybackItem(
            store_id=self.store1.id, user_id=self.op2.id,
            makro_product_id="FSNOP20000000002", title="小明采的品", seller_sku="SKU-OP2-02",
            compliance_status="PENDING_CHECK", status="PENDING"
        )
        self.db.add_all([it_op1, it_op2])
        self.db.commit()

        # 1. 管理员查询全员跟品，并检查 operator_name 是否正确附带
        res_admin = list_piggyback_items(page=1, page_size=10, current_user=self.admin, db=self.db)
        self.assertEqual(res_admin["total"], 2)
        op_names = [it["operator_name"] for it in res_admin["items"]]
        self.assertIn("小红", op_names)
        self.assertIn("小明", op_names)

        # 2. 管理员按特定员工 user_id=2 过滤
        res_filter_op1 = list_piggyback_items(page=1, page_size=10, user_id=self.op1.id, current_user=self.admin, db=self.db)
        self.assertEqual(res_filter_op1["total"], 1)
        self.assertEqual(res_filter_op1["items"][0]["operator_name"], "小红")

        # 3. 测试待检测独立筛选 compliance_status='PENDING_CHECK'
        res_pending = list_piggyback_items(page=1, page_size=10, compliance_status="PENDING_CHECK", current_user=self.admin, db=self.db)
        self.assertEqual(res_pending["total"], 1)
        self.assertEqual(res_pending["items"][0]["title"], "小明采的品")
        self.assertEqual(res_pending["stats"]["pending_check_count"], 1)

    def test_products_store_publish_status_filter(self):
        """
        测试选品箱按店铺刊登状态筛选 (已上架店铺 / 尚未上架特定店铺)
        """
        # 商品 1: 已刊登到店铺 1 (SUBMITTED)
        p1 = Product(takealot_id="PLID001", takealot_title="蓝牙耳机", status="SUBMITTED", user_id=self.op1.id)
        # 商品 2: 从未刊登过任何店铺
        p2 = Product(takealot_id="PLID002", takealot_title="保暖手套", status="CLEANED", user_id=self.op1.id)
        self.db.add_all([p1, p2])
        self.db.commit()

        sl1 = ProductStoreListing(product_id=p1.id, store_id=self.store1.id, status="SUBMITTED")
        self.db.add(sl1)
        self.db.commit()

        # 1. 筛选已刊登到店铺 1 的商品
        res_pub1 = list_products(store_id=self.store1.id, publish_status="PUBLISHED", current_user=self.admin, db=self.db)
        self.assertEqual(res_pub1["total"], 1)
        self.assertEqual(res_pub1["items"][0]["takealot_title"], "蓝牙耳机")

        # 2. 筛选未刊登到店铺 1 的商品 (用于补发)
        res_unpub1 = list_products(store_id=self.store1.id, publish_status="UNPUBLISHED", current_user=self.admin, db=self.db)
        self.assertEqual(res_unpub1["total"], 1)
        self.assertEqual(res_unpub1["items"][0]["takealot_title"], "保暖手套")

        # 3. 筛选未刊登到店铺 2 的商品 (p1 和 p2 都未上架店铺 2)
        res_unpub2 = list_products(store_id=self.store2.id, publish_status="UNPUBLISHED", current_user=self.admin, db=self.db)
        self.assertEqual(res_unpub2["total"], 2)

        # 4. 筛选全店从未刊登过的商品 (NONE_PUBLISHED)
        res_none_pub = list_products(publish_status="NONE_PUBLISHED", current_user=self.admin, db=self.db)
        self.assertEqual(res_none_pub["total"], 1)
        self.assertEqual(res_none_pub["items"][0]["takealot_title"], "保暖手套")

if __name__ == "__main__":
    unittest.main()
