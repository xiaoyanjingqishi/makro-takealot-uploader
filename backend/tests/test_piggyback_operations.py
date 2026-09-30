import os
import sys
import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 确保 backend 目录在 sys.path 中
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import Base
from app.models.store import Store
from app.models.makro_piggyback import MakroPiggybackItem
from app.schemas.piggyback import BatchSetFloorRequest, CheckExistenceRequest
from app.api.piggyback import (
    get_piggyback_kpi_stats,
    batch_set_floor,
    check_piggyback_existence,
    list_piggyback_items
)

class TestPiggybackOperations(unittest.TestCase):
    def setUp(self):
        # 使用独立的内存数据库测试
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # 初始化测试店铺
        self.store = Store(
            name="测试主力店铺",
            seller_id="seller_test_123",
            fk_csrf_token="csrf_123",
            cookie="cookie_123",
            is_active=True,
            is_default=True
        )
        self.db.add(self.store)
        self.db.commit()
        self.db.refresh(self.store)

        # 构造多条不同状态与战况的跟品记录
        # 1. 待处理池品 (STAGING)
        self.item_staging = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="FSN_STAGING_1",
            seller_sku="GP_STAGING_1",
            title="待处理测试品 1",
            original_price=100.0,
            target_price=99.0,
            min_price_floor=0.0,
            status="PENDING",
            buybox_status="UNKNOWN"
        )
        # 2. 在售且占位赢得 Buybox (ACTIVE, WINNING)
        self.item_winning = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="FSN_WINNING_2",
            seller_sku="GP_WINNING_2",
            title="在售抢车成功品 2",
            original_price=200.0,
            target_price=190.0,
            min_price_floor=150.0,
            status="ACTIVE",
            buybox_status="WINNING"
        )
        # 3. 在售但被竞对压价失守 (ACTIVE, LOSING)
        self.item_losing = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="FSN_LOSING_3",
            seller_sku="GP_LOSING_3",
            title="在售被压价品 3",
            original_price=300.0,
            target_price=299.0,
            last_competitor_price=280.0,
            min_price_floor=250.0,
            status="ACTIVE",
            buybox_status="LOSING"
        )
        # 4. 在售且触及保本底线坚守中 (ACTIVE, FLOOR_HIT)
        self.item_floor = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="FSN_FLOOR_4",
            seller_sku="GP_FLOOR_4",
            title="在售触底保本品 4",
            original_price=400.0,
            target_price=320.0,
            last_competitor_price=300.0,
            min_price_floor=320.0,
            status="ACTIVE",
            buybox_status="FLOOR_HIT"
        )
        # 5. 在售但未设保本底价 (ACTIVE, MISSING_FLOOR)
        self.item_missing_floor = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="FSN_NO_FLOOR_5",
            seller_sku="GP_NO_FLOOR_5",
            title="在售缺底价高危品 5",
            original_price=500.0,
            target_price=490.0,
            min_price_floor=0.0, # 缺失底价
            status="ACTIVE",
            buybox_status="WINNING"
        )
        # 6. 异常拦截品 (FAILED / PROHIBITED)
        self.item_failed = MakroPiggybackItem(
            store_id=self.store.id,
            makro_product_id="FSN_FAILED_6",
            seller_sku="GP_FAILED_6",
            title="挂靠失败品 6",
            original_price=150.0,
            target_price=140.0,
            min_price_floor=100.0,
            status="FAILED",
            compliance_status="SAFE"
        )

        self.db.add_all([
            self.item_staging,
            self.item_winning,
            self.item_losing,
            self.item_floor,
            self.item_missing_floor,
            self.item_failed
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_kpi_stats_calculation(self):
        """测试运营驾驶舱 6 大 KPI 战况统计指标准确性"""
        kpi = get_piggyback_kpi_stats(store_id=self.store.id, current_user=None, db=self.db)
        
        self.assertEqual(kpi["total_count"], 6)
        self.assertEqual(kpi["active_count"], 4) # winning, losing, floor, missing_floor
        self.assertEqual(kpi["winning_count"], 2) # winning + missing_floor
        self.assertEqual(kpi["losing_count"], 1) # losing
        self.assertEqual(kpi["floor_hit_count"], 1) # floor
        self.assertEqual(kpi["missing_floor_count"], 1) # missing_floor (floor=0)
        self.assertEqual(kpi["staging_count"], 1) # staging
        self.assertEqual(kpi["blocked_count"], 1) # failed

    def test_batch_set_floor_percent_mode(self):
        """测试按比例公式批量设置保本底价 (如原价 70%)"""
        req = BatchSetFloorRequest(
            ids=[self.item_staging.id, self.item_missing_floor.id],
            mode="PERCENT",
            value=70.0,
            auto_enable_reprice=True
        )
        res = batch_set_floor(req=req, current_user=None, db=self.db)
        self.assertTrue(res["success"])
        self.assertEqual(res["updated_count"], 2)

        # 刷新数据库验证
        self.db.refresh(self.item_staging)
        self.db.refresh(self.item_missing_floor)

        # 100 * 70% = 70.0
        self.assertEqual(self.item_staging.min_price_floor, 70.0)
        self.assertTrue(self.item_staging.auto_reprice)

        # 500 * 70% = 350.0
        self.assertEqual(self.item_missing_floor.min_price_floor, 350.0)
        self.assertTrue(self.item_missing_floor.auto_reprice)

    def test_batch_set_floor_offset_mode(self):
        """测试按固定差额公式批量设置保本底价 (如原价 - R50)"""
        req = BatchSetFloorRequest(
            ids=[self.item_losing.id],
            mode="OFFSET",
            value=50.0,
            auto_enable_reprice=True
        )
        res = batch_set_floor(req=req, current_user=None, db=self.db)
        self.assertTrue(res["success"])
        self.db.refresh(self.item_losing)
        # 300 - 50 = 250.0
        self.assertEqual(self.item_losing.min_price_floor, 250.0)

    def test_check_existence_deduplication(self):
        """测试插件前台排重核验接口"""
        req = CheckExistenceRequest(
            fsns=["FSN_WINNING_2", "FSN_NON_EXISTENT_999", "FSN_FAILED_6"]
        )
        res = check_piggyback_existence(req=req, db=self.db)
        exists = res["exists"]
        self.assertIn("FSN_WINNING_2", exists)
        self.assertEqual(exists["FSN_WINNING_2"]["seller_sku"], "GP_WINNING_2")
        self.assertEqual(exists["FSN_WINNING_2"]["status"], "ACTIVE")
        self.assertEqual(exists["FSN_WINNING_2"]["buybox_status"], "WINNING")

        self.assertIn("FSN_FAILED_6", exists)
        self.assertEqual(exists["FSN_FAILED_6"]["status"], "FAILED")

        self.assertNotIn("FSN_NON_EXISTENT_999", exists)

    def test_stage_and_buybox_filters(self):
        """测试漏斗阶段与 Buybox 战况列表筛选联动"""
        # 1. 筛选待处理池 (STAGING)
        res_staging = list_piggyback_items(page=1, page_size=20, stage="STAGING", current_user=None, db=self.db)
        self.assertEqual(res_staging["total"], 1)
        self.assertEqual(res_staging["items"][0]["makro_product_id"], "FSN_STAGING_1")

        # 2. 筛选在售丢车需调价品 (LOSING)
        res_losing = list_piggyback_items(page=1, page_size=20, stage="ACTIVE_MONITOR", buybox_status="LOSING", current_user=None, db=self.db)
        self.assertEqual(res_losing["total"], 1)
        self.assertEqual(res_losing["items"][0]["makro_product_id"], "FSN_LOSING_3")

        # 3. 筛选在售缺底价高危品 (MISSING_FLOOR)
        res_missing = list_piggyback_items(page=1, page_size=20, stage="ACTIVE_MONITOR", buybox_status="MISSING_FLOOR", current_user=None, db=self.db)
        self.assertEqual(res_missing["total"], 1)
        self.assertEqual(res_missing["items"][0]["makro_product_id"], "FSN_NO_FLOOR_5")

if __name__ == "__main__":
    unittest.main()
