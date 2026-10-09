import logging
import time
import random
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from ..models.makro_piggyback import MakroPiggybackItem
from ..models.makro_reprice_log import MakroRepriceLog
from ..models.store import Store
from .makro_scraper_service import MakroScraperService
from .makro_piggyback_service import MakroPiggybackService, MAKRO_HOST
from .proxy_service import ProxyPoolService

logger = logging.getLogger(__name__)

class AutoRepriceService:
    """
    Makro 智能自动跟价引擎
    核心能力：
      1. 巡检前台实时在售价格与 Buybox 归属
      2. 己方胜出保护 (WINNING_HOLD): 己方占位绝不降价内卷
      3. 保本底线熔断 (REACHED_FLOOR): 竞对恶意低价跌破成本时锁死保本价
      4. 自动比价 (UNDER_CUT): 比竞对低 R1.00 或 2% 抢占购物车
      5. 官方 API 极速下发与操作全量审计追踪
    """

    @classmethod
    def reprice_single_item(
        cls,
        item: MakroPiggybackItem,
        db: Session,
        force: bool = False,
        allow_inspect_only: bool = False,
        proxy: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        对单件在售跟品执行一次自动跟价巡检与调价
        """
        store = item.store
        if not store:
            store = db.query(Store).filter(Store.id == item.store_id).first()

        if not store or not store.seller_id or not store.fk_csrf_token or not store.cookie:
            msg = f"跟品商品 [{item.seller_sku}] 关联店铺凭据不完整，跳过跟价"
            logger.warning(msg)
            return {"status": "FAILED", "reason": msg, "item_id": item.id}

        # 检查是否启用了自动跟价 (force 参数可强制单次触发调价，allow_inspect_only 允许未开启自动跟价时仅做买家前台巡检和 Buybox 归属分析，不主动调价)
        if not force and not item.auto_reprice and not allow_inspect_only:
            return {"status": "SKIPPED", "reason": "商品未开启自动跟价开关", "item_id": item.id}

        # 1. 获取买家前台最新在售与竞争情报 (遇反爬自动秒切代理)
        target_ref = item.makro_url or MakroScraperService.format_canonical_makro_url(item.makro_product_id, item.item_id)
        scraped = MakroScraperService.scrape_buyer_frontend(target_ref, proxy=proxy)
        
        comp_price = scraped.get("price", 0.0)
        comp_mrp = scraped.get("mrp", 0.0)
        comp_seller = (scraped.get("seller_name") or "").strip()
        comp_seller_id = (scraped.get("seller_id") or "").strip()
        seller_count = scraped.get("seller_count", 1)

        old_selling_price = float(item.target_price or 0.0)
        min_floor = float(item.min_price_floor or 0.0)
        strategy = item.price_strategy or "MINUS_1"

        action = "NO_CHANGE"
        reason = ""
        new_price = old_selling_price

        # 2. 判断当前 Buybox 归属 (全矩阵多店铺协同防内卷识别)
        all_active_stores = db.query(Store).filter(Store.is_active == True).all()

        is_own_current_store = False
        is_matrix_sister_store = False
        winning_store_name = None

        # 核心优先：通过官方唯一 sellerId 严格精准匹配
        if comp_seller_id:
            for s in all_active_stores:
                if s.seller_id and s.seller_id.strip().lower() == comp_seller_id.lower():
                    if s.id == store.id:
                        is_own_current_store = True
                        winning_store_name = s.name
                    else:
                        is_matrix_sister_store = True
                        winning_store_name = s.name
                    break

        # 备选辅助：若无 sellerId，则通过前台卖家别名/默认品牌/店名模糊比对
        if not is_own_current_store and not is_matrix_sister_store and comp_seller:
            comp_seller_clean = comp_seller.strip().lower()
            for s in all_active_stores:
                identifiers = []
                if s.name:
                    identifiers.append(s.name.strip().lower())
                if s.default_brand:
                    identifiers.append(s.default_brand.strip().lower())
                if s.seller_id:
                    identifiers.append(s.seller_id.strip().lower())

                if any(ident in comp_seller_clean or comp_seller_clean in ident for ident in identifiers if ident):
                    if s.id == store.id:
                        is_own_current_store = True
                        winning_store_name = s.name
                    else:
                        is_matrix_sister_store = True
                        winning_store_name = s.name
                    break

        # 辅助多店铺同品跨店关联校验：
        # 仅当前台未能解析出 seller_name (comp_seller 为空) 时，
        # 才尝试本地同矩阵其他启用的店铺挂靠同款 FSN 的价格特征进行兜底推导。
        # 若 comp_seller 已明确识别且不属于本店或任何友军，坚决确认为外部竞争对手！
        if not comp_seller and not is_own_current_store and not is_matrix_sister_store and comp_price > 0:
            sister_items = db.query(MakroPiggybackItem).filter(
                MakroPiggybackItem.makro_product_id == item.makro_product_id,
                MakroPiggybackItem.id != item.id,
                MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"])
            ).all()
            for s_it in sister_items:
                if s_it.target_price and abs(float(s_it.target_price) - comp_price) < 0.05:
                    is_matrix_sister_store = True
                    s_name = s_it.store.name if s_it.store else f"店铺#{s_it.store_id}"
                    winning_store_name = f"{s_name} (同款在售)"
                    break

        is_own_any_store = is_own_current_store or is_matrix_sister_store

        # ★★★ 核心修复：更新竞品情报时的身份保护 ★★★
        # 无论谁赢车，在售商家数量与划线 MRP 始终同步
        item.seller_count = seller_count
        if comp_mrp > 0:
            item.original_mrp = comp_mrp

        if is_own_any_store:
            # 本店或矩阵友军占位赢车：严禁把本店出价/店名覆盖为外部原网基准价和原卖家！
            # 外部原价与原卖家保持历史真实竞对记录，若从未记录过才兜底补齐
            if (item.original_price or 0.0) <= 0 and comp_price > 0:
                item.original_price = comp_price
        else:
            # 外部真实竞对占位：正常同步最新竞对价格与卖家名称
            if comp_price > 0:
                item.original_price = comp_price
                item.last_competitor_price = comp_price
            if comp_seller:
                item.original_seller = comp_seller

        is_blocked = scraped.get("blocked", False)

        if comp_price <= 0:
            if is_blocked:
                # 明确标记为反爬阻断，坚决不冒进调价，绝不假冒正常成功
                action = "BLOCKED"
                reason = f"前台反爬瞬时阻断防护：未能获取竞对有效报价，安全维持现价 R{old_selling_price}"
                new_price = old_selling_price
            else:
                action = "FAILED"
                reason = "未能获取到前台有效竞对售价"
        elif is_own_current_store:
            # 本店铺自己已经赢得黄金购物车！坚决不自我压价！
            action = "WINNING_HOLD"
            reason = f"当前店铺已抢占黄金购物车 (Buybox: {comp_seller or store.name})，保持现价 R{old_selling_price}，不自我压价"
            new_price = old_selling_price
        elif is_matrix_sister_store:
            # 矩阵内兄弟店铺已经赢得黄金购物车！坚决不自相残杀，不压价内卷！
            action = "WINNING_HOLD"
            reason = f"矩阵友军店铺 [{winning_store_name or comp_seller}] 已占位黄金购物车，为避免内部互相压价削减利润，本店铺维持现价 R{old_selling_price}，不内卷跟价"
            new_price = old_selling_price
        else:
            # 外部竞对占位
            if not item.auto_reprice and not force:
                action = "INSPECTED"
                new_price = old_selling_price
                reason = f"买家前台巡检更新 (未开启自动跟价): 竞对 ({comp_seller or '无'}) 报价 R{comp_price}, 在售商家: {seller_count}"
            else:
                # 开启自动跟价，根据该商品的跟价公式策略计算抢流出价
                calc_p = MakroPiggybackService.eval_price_by_strategy(
                    base_price=comp_price,
                    strategy=strategy,
                    min_floor=0.0  # 先算裸价，以精确识别是否击穿保本线
                )

                # 保本底线防穿保护
                if min_floor > 0 and calc_p < min_floor:
                    new_price = min_floor
                    action = "REACHED_FLOOR"
                    reason = f"竞对 ({comp_seller}) 报价 R{comp_price} 过低，按公式算价 R{calc_p} 已击穿保本底线 R{min_floor}，触发锁定防护"
                elif abs(calc_p - old_selling_price) < 0.01:
                    new_price = old_selling_price
                    action = "NO_CHANGE"
                    reason = f"计算跟价 R{calc_p} (策略: {strategy}) 与当前本店售价一致，无需重复调价"
                else:
                    new_price = calc_p
                    action = "UNDER_CUT"
                    reason = f"竞对 ({comp_seller}) 报价 R{comp_price}，按公式 [{strategy}] 下调至 R{new_price} 抢占购物车"

        # 5. 若价格发生实质变动且开启跟价，向 Makro 官方 API 提交更新
        if action in ["UNDER_CUT", "REACHED_FLOOR"] and abs(new_price - old_selling_price) >= 0.01:
            if not item.auto_reprice and not force:
                logger.info(f"商品 [{item.seller_sku}] 未开启自动跟价，跳过官方 API 调价推送")
            else:
                try:
                    cls._push_price_to_makro(item, store, new_price)
                    item.target_price = new_price
                    if item.target_mrp and item.target_mrp < new_price:
                        item.target_mrp = round(new_price * 1.5, 2)
                except Exception as api_err:
                    logger.error(f"调用 Makro API 更新价格失败 [{item.seller_sku}]: {api_err}")
                    action = "FAILED"
                    reason = f"调价计算成功但推送官方失败: {str(api_err)}"

        # 6. 计算最终 Buybox 归属状态 (实事求是反映买家前台真实权属)
        if comp_price <= 0:
            # 抓取未果，保留原有状态
            buybox_status = item.buybox_status or "UNKNOWN"
        elif is_own_current_store:
            # 本店明确占有黄金购物车
            buybox_status = "WINNING" if seller_count > 1 else "NO_COMPETITOR"
        elif is_matrix_sister_store:
            # 矩阵友军兄弟店铺占有黄金购物车
            buybox_status = "WINNING"
        elif comp_seller and not is_own_any_store:
            # 外部真实竞对占位 (例如 pumu222 占车)
            if action == "REACHED_FLOOR" or (min_floor > 0 and (item.target_price or 0.0) <= min_floor):
                buybox_status = "FLOOR_HIT"
            else:
                buybox_status = "LOSING"
        elif seller_count <= 1 and (is_own_any_store or not comp_seller):
            buybox_status = "NO_COMPETITOR"
        elif action == "REACHED_FLOOR":
            buybox_status = "FLOOR_HIT"
        elif comp_price < (item.target_price or 0.0):
            buybox_status = "LOSING"
        else:
            buybox_status = "UNKNOWN"

        item.buybox_status = buybox_status
        if not is_own_any_store and comp_price > 0:
            item.last_competitor_price = comp_price
        elif is_own_any_store and (not item.last_competitor_price or item.last_competitor_price <= 0) and (item.original_price or 0.0) > 0:
            item.last_competitor_price = item.original_price

        # 7. 更新商品记录状态与审计日志
        item.last_reprice_at = datetime.now()
        item.last_reprice_result = f"{action}: {reason[:120]}"

        # 竞对卖家审计文案：清晰区分真实外部竞对与本店/友军占位或反爬阻断
        logged_seller = comp_seller or "未知/无竞对"
        if action == "BLOCKED":
            logged_seller = "阻断未获取"
        elif is_own_current_store:
            logged_seller = f"{comp_seller or store.name} (本店抢占)"
        elif is_matrix_sister_store:
            logged_seller = f"{winning_store_name or comp_seller} (友军抢占)"

        log_entry = MakroRepriceLog(
            piggyback_id=item.id,
            store_id=store.id,
            seller_sku=item.seller_sku,
            makro_product_id=item.makro_product_id,
            competitor_seller=logged_seller,
            competitor_price=comp_price if action != "BLOCKED" else 0.0,
            old_price=old_selling_price,
            new_price=new_price if action in ["UNDER_CUT", "REACHED_FLOOR"] else old_selling_price,
            action=action,
            reason=reason
        )
        db.add(log_entry)
        db.commit()
        db.refresh(item)

        final_status = "BLOCKED" if action == "BLOCKED" else ("SUCCESS" if action != "FAILED" else "FAILED")

        return {
            "status": final_status,
            "action": action,
            "seller_sku": item.seller_sku,
            "makro_product_id": item.makro_product_id,
            "competitor_seller": "阻断未获取" if action == "BLOCKED" else comp_seller,
            "competitor_price": None if action == "BLOCKED" else comp_price,
            "old_price": old_selling_price,
            "new_price": item.target_price,
            "reason": reason,
            "blocked": is_blocked
        }

    @classmethod
    def _push_price_to_makro(cls, item: MakroPiggybackItem, store: Store, new_price: float):
        """
        调用 Makro 官方 create-update-listings 接口即时更新 Listing 售价
        """
        url = f"{MAKRO_HOST}/napi/listing/create-update-listings?sellerId={store.seller_id}"
        headers = MakroPiggybackService._build_headers(store)

        safe_mrp = max(float(item.target_mrp or 0.0), float(new_price) * 1.5, float(new_price) + 10.0)
        item.target_mrp = round(safe_mrp, 2)
        ssp_val = str(int(new_price)) if float(new_price).is_integer() else str(round(new_price, 2))
        mrp_val = str(int(safe_mrp)) if float(safe_mrp).is_integer() else str(round(safe_mrp, 2))
        lead_time = str(item.lead_time_days or 14)
        pkg_len = str(item.length or 15.0)
        pkg_brd = str(item.breadth or 10.0)
        pkg_hgt = str(item.height or 5.0)
        pkg_wgt = str(item.weight or 0.5)

        payload = {
            "bulkRequests": [
                {
                    "attributeValues": {
                        "sku_id": [{"value": item.seller_sku, "qualifier": ""}],
                        "listing_status": [{"value": "ACTIVE", "qualifier": ""}],
                        "mrp": [{"value": mrp_val, "qualifier": "INR"}],
                        "flipkart_selling_price": [{"value": ssp_val, "qualifier": "INR"}],
                        "service_profile": [{"value": "NON_FBF", "qualifier": ""}],
                        "shipping_days": [{"value": lead_time, "qualifier": "DAY"}],
                        "forbid_shipping": [{"qualifier": "", "value": "none"}],
                        "country_of_origin": [{"value": "CN", "qualifier": ""}],
                        "manufacturer_details": [{"value": "General", "qualifier": ""}],
                        "packer_details": [{"value": store.default_brand or "Generic", "qualifier": ""}]
                    },
                    "context": {
                        "ignore_warnings": False
                    },
                    "productId": item.makro_product_id,
                    "skuId": item.seller_sku,
                    "packages": [
                        {
                            "id": {"value": "packages-0"},
                            "length": {"value": pkg_len, "qualifier": "CM"},
                            "breadth": {"value": pkg_brd, "qualifier": "CM"},
                            "height": {"value": pkg_hgt, "qualifier": "CM"},
                            "weight": {"value": pkg_wgt, "qualifier": "KG"},
                            "sku_id": {"value": item.seller_sku, "qualifier": ""}
                        }
                    ]
                }
            ],
            "sellerId": store.seller_id
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            raise Exception(f"Makro 调价接口 HTTP {resp.status_code}: {resp.text[:200]}")

        res_json = resp.json()
        bulk_res = res_json.get("result", {}).get("bulkResponse", [])
        if not bulk_res:
            raise Exception(f"Makro 调价未返回有效 bulkResponse: {res_json}")
        
        single_res = bulk_res[0]
        status = single_res.get("status")
        if status not in ["created", "updated", "success"]:
            errors = single_res.get("globalErrors", []) or single_res.get("attributeErrors", {})
            raise Exception(f"官方更新失败 (状态: {status}): {errors}")

    @classmethod
    def run_reprice_for_store(cls, store: Store, db: Session, max_workers: int = 100) -> Dict[str, Any]:
        """
        为指定店铺中所有在售已激活且开启自动跟价的跟品执行超高并发智能跟价巡检 (100线程多路复用，直通携趣动态代理池)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..database import SessionLocal

        items = db.query(MakroPiggybackItem).filter(
            MakroPiggybackItem.store_id == store.id,
            MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]),
            MakroPiggybackItem.auto_reprice == True,
            MakroPiggybackItem.makro_product_id.notlike("TEST_FSN_%")
        ).all()

        total = len(items)
        if total == 0:
            return {
                "total_items": 0,
                "success_count": 0,
                "undercut_count": 0,
                "winning_hold_count": 0,
                "floor_count": 0,
                "failed_count": 0,
                "details": []
            }

        item_ids = [it.id for it in items]
        success_count = 0
        blocked_count = 0
        undercut_count = 0
        winning_hold_count = 0
        floor_count = 0
        failed_count = 0
        details = []

        # 优先读取系统设置中管理员配置的巡检线程数
        sys_concurrency = max_workers
        try:
            from ..models.setting import SystemSetting
            setting_item = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_cruise_concurrency").first()
            if setting_item and setting_item.value:
                sys_concurrency = int(setting_item.value)
        except Exception:
            pass

        actual_workers = max(1, min(sys_concurrency, total, 30))
        logger.info(f"店铺 [{store.name}] 启动平稳安全自动跟价巡航：共 {total} 件商品，工作线程数: {actual_workers}")

        def _cruise_worker(iid: int) -> Dict[str, Any]:
            time.sleep(random.uniform(0.8, 1.8))
            worker_db = SessionLocal()
            try:
                target_item = worker_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == iid).first()
                if not target_item:
                    return {"status": "FAILED", "reason": "商品不存在", "item_id": iid}
                return cls.reprice_single_item(target_item, worker_db, proxy=None)
            except Exception as w_err:
                return {"status": "FAILED", "reason": str(w_err), "item_id": iid}
            finally:
                worker_db.close()

        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            future_map = {executor.submit(_cruise_worker, iid): iid for iid in item_ids}
            for fut in as_completed(future_map):
                try:
                    res = fut.result()
                    action = res.get("action")
                    if action == "UNDER_CUT":
                        undercut_count += 1
                    elif action == "WINNING_HOLD":
                        winning_hold_count += 1
                    elif action == "REACHED_FLOOR":
                        floor_count += 1
                    elif action == "BLOCKED":
                        blocked_count += 1
                    elif action == "FAILED":
                        failed_count += 1
                    
                    if res.get("status") == "SUCCESS":
                        success_count += 1
                    details.append(res)
                except Exception as ex:
                    logger.error(f"并发巡检线程执行异常: {ex}")
                    failed_count += 1

        logger.info(f"店铺 [{store.name}] 巡航巡检完成: 处理 {total} 件，成功 {success_count} 件 (降价跟进: {undercut_count}, 保持胜出: {winning_hold_count}, 触底保本: {floor_count}, 阻断: {blocked_count}, 失败: {failed_count})")

        return {
            "total_items": total,
            "success_count": success_count,
            "blocked_count": blocked_count,
            "undercut_count": undercut_count,
            "winning_hold_count": winning_hold_count,
            "floor_count": floor_count,
            "failed_count": failed_count,
            "details": details
        }

    @classmethod
    def run_full_cruise_task(
        cls,
        tm: Any,
        task_id: str,
        item_ids: List[int],
        concurrency: int = 100
    ):
        """
        全量立刻巡检巡航后台执行器 (集成 TaskManager，支持多线程受控平稳巡航、防页面刷新、真实阻断统计与取消)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        from ..database import SessionLocal

        total = len(item_ids)
        if total == 0:
            tm.finish_task(task_id, status="SUCCESS", message="未找到符合巡检条件的在售跟品商品", success_count=0, fail_count=0, blocked_count=0)
            return

        done_count = 0
        undercut_count = 0
        winning_count = 0
        floor_count = 0
        inspected_count = 0
        failed_count = 0
        no_change_count = 0
        blocked_count = 0
        lock = threading.Lock()

        def _cruise_one(iid: int):
            nonlocal done_count, undercut_count, winning_count, floor_count, inspected_count, failed_count, no_change_count, blocked_count
            if tm.is_cancelled(task_id):
                return

            time.sleep(random.uniform(0.8, 1.8))
            worker_db = SessionLocal()
            try:
                target_item = worker_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == iid).first()
                if not target_item:
                    with lock:
                        done_count += 1
                        failed_count += 1
                        tm.update_progress(task_id, current=done_count, fail_inc=1, error=f"ID {iid} 商品不存在")
                    return

                res = cls.reprice_single_item(target_item, worker_db, force=False, allow_inspect_only=True, proxy=None)
                action = res.get("action", "")
                status = res.get("status", "")

                with lock:
                    done_count += 1
                    succ_inc = 0
                    fail_inc = 0
                    blocked_inc = 0

                    if action == "UNDER_CUT":
                        undercut_count += 1
                    elif action == "WINNING_HOLD":
                        winning_count += 1
                    elif action == "REACHED_FLOOR":
                        floor_count += 1
                    elif action == "INSPECTED":
                        inspected_count += 1
                    elif action == "NO_CHANGE":
                        no_change_count += 1
                    elif action == "BLOCKED":
                        blocked_count += 1
                        blocked_inc = 1

                    if status == "SUCCESS":
                        succ_inc = 1
                    elif status == "BLOCKED" or action == "BLOCKED":
                        # 反爬阻断明确统计，严禁归入成功！
                        pass
                    else:
                        fail_inc = 1
                        failed_count += 1

                    sku_display = target_item.seller_sku or f"Item#{iid}"
                    action_display = {
                        "UNDER_CUT": f"抢流降价至 R{res.get('new_price')}",
                        "WINNING_HOLD": "胜出保持现价",
                        "REACHED_FLOOR": f"触底锁死 R{res.get('new_price')}",
                        "INSPECTED": f"巡检归属 [{target_item.buybox_status}]",
                        "NO_CHANGE": "价格一致无需调整",
                        "BLOCKED": "🛡️反爬阻断防护(维持现价)",
                        "FAILED": f"失败: {res.get('reason', '')[:30]}"
                    }.get(action, action or "完成")

                    tm.update_progress(
                        task_id=task_id,
                        current=done_count,
                        current_title=f"{sku_display}: {action_display}",
                        success_inc=succ_inc,
                        fail_inc=fail_inc,
                        blocked_inc=blocked_inc
                    )
            except Exception as ex:
                logger.error(f"巡检单品 [ID: {iid}] 发生异常: {ex}", exc_info=True)
                with lock:
                    done_count += 1
                    failed_count += 1
                    tm.update_progress(task_id, current=done_count, fail_inc=1, error=str(ex))
            finally:
                worker_db.close()

        actual_workers = max(1, min(concurrency, total, 30))
        logger.info(f"全量巡检巡航任务 [{task_id}] 启动: 目标 {total} 件，并发线程数: {actual_workers}")

        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            futures = [executor.submit(_cruise_one, iid) for iid in item_ids]
            for fut in as_completed(futures):
                if tm.is_cancelled(task_id):
                    break
                try:
                    fut.result()
                except Exception:
                    pass

        if tm.is_cancelled(task_id):
            tm.finish_task(task_id, status="CANCELLED", message="任务已被用户取消")
            return

        success_total = done_count - failed_count - blocked_count
        summary_msg = f"全量巡检巡航完成！共处理 {done_count}/{total} 件 (降价抢流: {undercut_count}, 胜出保持: {winning_count}, 触底保本: {floor_count}, 巡检更新: {inspected_count}, 维持不变: {no_change_count}, 🛡️反爬阻断: {blocked_count}, 失败: {failed_count})"
        logger.info(f"全量巡检巡航任务 [{task_id}] 执行完毕: {summary_msg}")
        tm.finish_task(
            task_id=task_id,
            status="SUCCESS",
            message=summary_msg,
            success_count=success_total,
            fail_count=failed_count,
            blocked_count=blocked_count
        )

