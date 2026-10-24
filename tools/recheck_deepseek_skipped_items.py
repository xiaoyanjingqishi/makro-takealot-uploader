import sys
import os
import json
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from backend.app.database import SessionLocal
from backend.app.models.makro_piggyback import MakroPiggybackItem
from backend.app.services.compliance_service import ComplianceService

def run_recheck(safe_only: bool = True, limit: int = 0, dry_run: bool = False, concurrency: int = 3):
    db = SessionLocal()
    try:
        cs = ComplianceService.from_db(db)
        all_items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.compliance_details.isnot(None)).all()
        
        target_items = []
        for it in all_items:
            try:
                d = json.loads(it.compliance_details)
                s = d.get('deepseek_verdict', {}).get('title_audit', {}).get('summary', '')
                if '402' in s or 'Insufficient' in s:
                    if safe_only and it.compliance_status != 'SAFE':
                        continue
                    target_items.append(it.id)
            except Exception:
                pass
                
        print(f"==================================================")
        print(f"  🔍 DeepSeek 欠费跳过商品全量复查工具")
        print(f"==================================================")
        print(f"模式: {'仅复查 SAFE 放行件' if safe_only else '全量复查 (含PROHIBITED)'}")
        print(f"匹配待复检商品总数: {len(target_items)} 件")
        if limit > 0:
            target_items = target_items[:limit]
            print(f"限制处理条数: {limit} 件")
        print(f"Dry-run 预览模式: {dry_run}")
        print(f"并发数: {concurrency}")
        print(f"--------------------------------------------------\n")

        if not target_items:
            print("无需复检的商品。")
            return

        stats = {
            "total": len(target_items),
            "processed": 0,
            "stayed_safe": 0,
            "downgraded_risk": 0,
            "downgraded_prohibited": 0,
            "errors": 0,
            "downgraded_details": []
        }
        lock = threading.Lock()

        def _worker(item_id: int):
            t_db = SessionLocal()
            try:
                it = t_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
                if not it:
                    return
                
                old_status = it.compliance_status
                pdata = {
                    "id": it.id,
                    "title": it.title,
                    "brand": it.brand,
                    "category": it.vertical,
                    "is_piggyback": True,
                    "images": [it.image_url] if it.image_url else []
                }
                res = cs.check_product(pdata, check_image=bool(it.image_url), check_ai_title=True)
                new_status = res.get("compliance_status", "SAFE")
                
                with lock:
                    stats["processed"] += 1
                    if new_status == old_status:
                        if new_status == "SAFE":
                            stats["stayed_safe"] += 1
                    else:
                        if new_status == "RISK":
                            stats["downgraded_risk"] += 1
                        elif new_status == "PROHIBITED":
                            stats["downgraded_prohibited"] += 1
                        stats["downgraded_details"].append({
                            "id": it.id,
                            "sku": it.seller_sku,
                            "title": it.title[:60],
                            "old_status": old_status,
                            "new_status": new_status,
                            "reasons": res.get("risk_reasons", [])[:2]
                        })
                    
                    if not dry_run:
                        it.compliance_status = new_status
                        it.compliance_details = json.dumps(res, ensure_ascii=False)
                        try:
                            from backend.app.models.store_product_reprice import StoreProductReprice
                            rep = t_db.query(StoreProductReprice).filter(
                                StoreProductReprice.source_piggyback_id == it.id
                            ).first()
                            if rep:
                                rep.compliance_status = new_status
                                if new_status == "PROHIBITED":
                                    rep.monitor_status = "BLOCKED"
                                    rep.last_reprice_result = "BLOCKED: 合规复检判定红线侵权，自动阻断跟价"
                        except Exception:
                            pass
                        t_db.commit()

                    idx = stats["processed"]
                    tot = stats["total"]
                    status_flag = "✅ 维持" if new_status == old_status else f"⚠️ 纠偏->{new_status}"
                    print(f"[{idx}/{tot}] 商品 {it.seller_sku} ({status_flag}) | 原: {old_status} -> 新: {new_status} | {it.title[:35]}")
            except Exception as e:
                with lock:
                    stats["errors"] += 1
                print(f"[ERROR] 复检商品 ID {item_id} 异常: {e}")
            finally:
                t_db.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_worker, iid) for iid in target_items]
            for f in as_completed(futures):
                pass

        # 保存复检报告文件
        report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"recheck_safe_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            with open(report_path, "w", encoding="utf-8") as rf:
                json.dump(stats, rf, ensure_ascii=False, indent=2)
            print(f"\n📄 完整复检报告已保存至: {report_path}")
        except Exception as fe:
            print(f"写入报告文件异常: {fe}")

        print(f"\n==================================================")
        print(f"  🏁 复查执行完毕报告")
        print(f"==================================================")
        print(f"总计复检: {stats['processed']} 件")
        print(f"维持原结论 (SAFE): {stats['stayed_safe']} 件")
        print(f"纠偏为 RISK (黄线风险): {stats['downgraded_risk']} 件")
        print(f"纠偏为 PROHIBITED (红线禁售): {stats['downgraded_prohibited']} 件")
        print(f"执行失败: {stats['errors']} 件")

        if stats["downgraded_details"]:
            print(f"\n被纠偏商品明细 (前 15 件):")
            for d in stats["downgraded_details"][:15]:
                print(f"  - SKU: {d['sku']} (ID: {d['id']}) | {d['old_status']} -> {d['new_status']}")
                print(f"    标题: {d['title']}")
                print(f"    原因: {d['reasons']}")
        print(f"==================================================")

    finally:
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeepSeek 跳过商品全量复查工具")
    parser.add_argument("--all", action="store_true", help="复查所有跳过商品 (默认仅复查 SAFE)")
    parser.add_argument("--limit", type=int, default=0, help="限制处理条数 (0 为不限制)")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写数据库")
    parser.add_argument("--concurrency", type=int, default=6, help="并发线程数 (默认6)")
    args = parser.parse_args()

    run_recheck(
        safe_only=not args.all,
        limit=args.limit,
        dry_run=args.dry_run,
        concurrency=args.concurrency
    )
