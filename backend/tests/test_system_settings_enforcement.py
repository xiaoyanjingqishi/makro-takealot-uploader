# -*- coding: utf-8 -*-
"""
自动化验证脚本：
1. piggyback_default_strategy 默认跟价公式动态读取与生效
2. piggyback_default_inventory 默认上架库存动态读取与生效
3. auto_translate_on_collect 采集自动大模型翻译开关控制
4. custom_category_synonyms 类目同义词服务冷启动自动加载
"""

import sys
import os
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 确保 backend 在 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models.setting import SystemSetting
from app.models.store import Store
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.product import Product
from app.schemas.piggyback import CollectPiggybackRequest
from app.api.piggyback import collect_single_piggyback
from app.services.takealot_service import TakealotService
from app.schemas.product import TakealotCollectRequest
from app.services.vertical_service import VerticalSemanticRetriever, VerticalService

def run_tests():
    db = SessionLocal()
    print("=" * 70)
    print("🚀 开始执行系统设置落实与业务生效自动化回归测试")
    print("=" * 70)

    try:
        store = db.query(Store).filter(Store.is_active == True).first()
        if not store:
            store = Store(name="测试运营店铺", seller_id="TEST_SELLER_SETTINGS", is_active=True)
            db.add(store)
            db.commit()
            db.refresh(store)

        # 清理旧的遗留测试数据 (级联清理 variants 与 task logs)
        from app.models.product import ProductVariant
        from app.models.task import TaskLog
        db.query(MakroPiggybackItem).filter(MakroPiggybackItem.makro_product_id.like("TESTFSN%")).delete(synchronize_session=False)
        old_pids = [p[0] for p in db.query(Product.id).filter(Product.takealot_id.like("PLID_TEST_%")).all()]
        if old_pids:
            db.query(ProductVariant).filter(ProductVariant.product_id.in_(old_pids)).delete(synchronize_session=False)
            db.query(TaskLog).filter(TaskLog.product_id.in_(old_pids)).delete(synchronize_session=False)
            db.query(Product).filter(Product.id.in_(old_pids)).delete(synchronize_session=False)
        db.commit()

        # -------------------------------------------------------------
        # 测试 1: piggyback_default_strategy 默认跟价公式动态生效
        # -------------------------------------------------------------
        print("\n--- [TEST 1] piggyback_default_strategy 默认跟价公式动态生效 ---")
        strat_setting = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_default_strategy").first()
        if not strat_setting:
            strat_setting = SystemSetting(key="piggyback_default_strategy", value="PERCENT_2")
            db.add(strat_setting)
        else:
            strat_setting.value = "PERCENT_2"
        db.commit()

        # 采集单个商品，请求中不传 price_strategy
        import time
        ts = int(time.time())
        test_fsn_1 = f"TESTFSNSTRAT_{ts}"
        req1 = CollectPiggybackRequest(
            url_or_fsn=test_fsn_1,
            store_id=store.id
        )
        res1 = collect_single_piggyback(req=req1, current_user=None, db=db)
        item1 = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.makro_product_id == test_fsn_1).first()
        assert item1 is not None, "跟品未成功创建"
        assert item1.price_strategy == "PERCENT_2", f"跟价公式未采用系统设置，实际为: {item1.price_strategy}"
        print(f"  ✅ 1.1 跟品入库默认公式成功读取系统设置: price_strategy = '{item1.price_strategy}'")

        # -------------------------------------------------------------
        # 测试 2: piggyback_default_inventory 默认库存动态生效
        # -------------------------------------------------------------
        print("\n--- [TEST 2] piggyback_default_inventory 默认库存动态生效 ---")
        inv_setting = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_default_inventory").first()
        if not inv_setting:
            inv_setting = SystemSetting(key="piggyback_default_inventory", value="888")
            db.add(inv_setting)
        else:
            inv_setting.value = "888"
        db.commit()

        test_fsn_2 = f"TESTFSNINV_{ts}"
        req2 = CollectPiggybackRequest(
            url_or_fsn=test_fsn_2,
            store_id=store.id
        )
        res2 = collect_single_piggyback(req=req2, current_user=None, db=db)
        item2 = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.makro_product_id == test_fsn_2).first()
        assert item2 is not None, "跟品未成功创建"
        assert item2.inventory == 888, f"跟品库存未采用系统设置，实际为: {item2.inventory}"
        print(f"  ✅ 2.1 跟品入库默认库存成功读取系统设置: inventory = {item2.inventory}")

        # -------------------------------------------------------------
        # 测试 3: auto_translate_on_collect 采集自动大模型翻译开关控制
        # -------------------------------------------------------------
        print("\n--- [TEST 3] auto_translate_on_collect 采集自动翻译开关控制 ---")
        tr_setting = db.query(SystemSetting).filter(SystemSetting.key == "auto_translate_on_collect").first()
        if not tr_setting:
            tr_setting = SystemSetting(key="auto_translate_on_collect", value="false")
            db.add(tr_setting)

        # 3.1 当 auto_translate_on_collect 为 false 时，不调用大模型，takealot_title_zh 为空
        tr_setting.value = "false"
        db.commit()

        req_takealot_off = TakealotCollectRequest(
            takealot_id="PLID_TEST_OFF_01",
            takealot_url="https://www.takealot.com/test-off",
            takealot_title="Stainless Steel Kitchen Knife Set",
            takealot_price=299.0
        )
        prods_off = TakealotService.save_collected_product(db=db, req=req_takealot_off)
        assert len(prods_off) > 0
        assert prods_off[0].takealot_title_zh is None or prods_off[0].takealot_title_zh == "", f"关闭自动翻译时应当不翻译，实际: {prods_off[0].takealot_title_zh}"
        print("  ✅ 3.1 自动翻译开关关闭 (False): 入库英文标题，未调用 AI 翻译 (takealot_title_zh 为空，零额度消耗)")

        # 3.2 当 auto_translate_on_collect 为 true 时，自动调用翻译生成中文标题
        tr_setting.value = "true"
        db.commit()

        req_takealot_on = TakealotCollectRequest(
            takealot_id="PLID_TEST_ON_02",
            takealot_url="https://www.takealot.com/test-on",
            takealot_title="Stainless Steel Kitchen Knife Set",
            takealot_price=299.0
        )
        prods_on = TakealotService.save_collected_product(db=db, req=req_takealot_on)
        assert len(prods_on) > 0
        assert prods_on[0].takealot_title_zh is not None and len(prods_on[0].takealot_title_zh) > 0, "开启自动翻译时未能生成中文标题"
        print(f"  ✅ 3.2 自动翻译开关开启 (True): 成功自动生成中文标题: '{prods_on[0].takealot_title_zh}'")

        # -------------------------------------------------------------
        # 测试 4: custom_category_synonyms 服务冷启动自动从数据库加载
        # -------------------------------------------------------------
        print("\n--- [TEST 4] custom_category_synonyms 服务冷启动自动从数据库加载 ---")
        syn_setting = db.query(SystemSetting).filter(SystemSetting.key == "custom_category_synonyms").first()
        custom_syn_json = json.dumps({"card_holder": ["rfid wallet clip test token"]})
        if not syn_setting:
            syn_setting = SystemSetting(key="custom_category_synonyms", value=custom_syn_json)
            db.add(syn_setting)
        else:
            syn_setting.value = custom_syn_json
        db.commit()

        # 模拟服务重启 (重置单例并重新初始化)
        VerticalSemanticRetriever._instance = None
        new_retriever = VerticalSemanticRetriever.get_instance()
        
        # 检索自定义词 "rfid wallet clip test token"
        matched_category = new_retriever.search_relevant_verticals("rfid wallet clip test token", top_k=1)
        assert len(matched_category) > 0, "未能从冷启动倒排索引中检索到类目"
        assert matched_category[0] == "card_holder", f"冷启动自定义同义词未命中期望类目 card_holder，实际命中: {matched_category[0]}"
        print(f"  ✅ 4.1 冷启动倒排索引自动加载成功: 输入 'rfid wallet clip test token' 精准命中类目 -> '{matched_category[0]}'")

        # -------------------------------------------------------------
        # 清理测试临时数据与恢复设置
        # -------------------------------------------------------------
        print("\n--- [CLEANUP] 清理测试数据并恢复设置默认值 ---")
        strat_setting.value = "MINUS_15"
        inv_setting.value = "500"
        tr_setting.value = "false"
        syn_setting.value = "{}"
        
        if item1: db.delete(item1)
        if item2: db.delete(item2)
        test_pids = [p.id for p in prods_off + prods_on if p.id]
        if test_pids:
            db.query(ProductVariant).filter(ProductVariant.product_id.in_(test_pids)).delete(synchronize_session=False)
            db.query(TaskLog).filter(TaskLog.product_id.in_(test_pids)).delete(synchronize_session=False)
            db.query(Product).filter(Product.id.in_(test_pids)).delete(synchronize_session=False)
        db.commit()

        # 再次重置同义词索引
        VerticalSemanticRetriever._instance = None
        VerticalSemanticRetriever.get_instance()
        print("✅ 测试临时数据清理完毕，系统设置恢复默认状态")

        print("\n" + "=" * 70)
        print("🎉 全部 4 项系统设置落实与业务生效自动化测试 100% 验证通过！")
        print("=" * 70)
        return True

    except Exception as e:
        db.rollback()
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
