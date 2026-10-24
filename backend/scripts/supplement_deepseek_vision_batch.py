import sys
import os
import time
import json
import argparse
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("backend"))

from backend.app.database import SessionLocal
from backend.app.models.makro_piggyback import MakroPiggybackItem
from backend.app.services.compliance_service import ComplianceService
from backend.app.services.makro_piggyback_service import MakroPiggybackService

def main():
    parser = argparse.ArgumentParser(description="批量补齐待处理池合规商品的 DeepSeek 视觉审查")
    parser.add_argument("--workers", type=int, default=15, help="并发线程数 (默认 15)")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条 (默认全部)")
    parser.add_argument("--dry-run", action="store_true", help="演练模式 (不落库更新)")
    args = parser.parse_args()

    print("==================================================================")
    print("  🐬 Makro 跟品待处理池 DeepSeek 视觉增量补齐引擎")
    print(f"  并发线程: {args.workers} | 演练模式: {args.dry_run}")
    print("==================================================================")

    init_db = SessionLocal()
    staging_query = init_db.query(MakroPiggybackItem).filter(
        (MakroPiggybackItem.is_abandoned == False) | (MakroPiggybackItem.is_abandoned == None),
        MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"]),
        MakroPiggybackItem.compliance_status == "SAFE"
    ).order_by(MakroPiggybackItem.id.asc())

    all_items = staging_query.all()
    target_ids = []

    for it in all_items:
        if not it.compliance_details:
            target_ids.append(it.id)
            continue
        try:
            det = json.loads(it.compliance_details) if isinstance(it.compliance_details, str) else it.compliance_details
            ds_img = det.get("deepseek_verdict", {}).get("image_audit", {})
            if (not ds_img.get("tested", False)) or ("跳过" in str(ds_img.get("summary", ""))) or ("暂未开放" in str(ds_img.get("summary", ""))):
                target_ids.append(it.id)
        except Exception:
            target_ids.append(it.id)

    total_count = len(target_ids)
    if args.limit and args.limit > 0:
        target_ids = target_ids[:args.limit]
        print(f"[*] 筛选出待补齐商品: {total_count} 件，按限制仅处理前 {len(target_ids)} 件")
    else:
        print(f"[*] 筛选出待补齐商品: {total_count} 件 (100% 待处理池跳过商品)")

    if not target_ids:
        print("[+] 待处理池中没有需要补齐 DeepSeek 视觉的合规商品！")
        init_db.close()
        return

    # 初始化全局共享的 ComplianceService 实例
    cs = ComplianceService.from_db(init_db, ignore_dual_toggle=True)
    init_db.close()

    total_to_process = len(target_ids)
    processed_count = 0
    safe_count = 0
    risk_intercepted_count = 0
    prohibited_intercepted_count = 0
    error_count = 0
    lock = threading.Lock()
    start_time = time.time()

    intercepted_records = []

    def process_one(item_id: int):
        nonlocal processed_count, safe_count, risk_intercepted_count, prohibited_intercepted_count, error_count
        thread_db = SessionLocal()
        max_retries = 3
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                it = thread_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
                if not it:
                    return

                if args.dry_run:
                    # 演练模式不提交事务
                    res = MakroPiggybackService.supplement_deepseek_vision_for_item(it, thread_db, compliance_service=cs)
                    thread_db.rollback()
                else:
                    res = MakroPiggybackService.supplement_deepseek_vision_for_item(it, thread_db, compliance_service=cs)

                status = res.get("compliance_status", "SAFE")
                ds_verdict = res.get("deepseek_verdict", {}).get("image_audit", {})
                detected_logos = ds_verdict.get("logo_names", [])
                prohibited_types = ds_verdict.get("prohibited_types", [])

                with lock:
                    processed_count += 1
                    if status == "SAFE":
                        safe_count += 1
                    elif status == "PROHIBITED":
                        prohibited_intercepted_count += 1
                        intercepted_records.append({
                            "id": it.id,
                            "sku": it.seller_sku,
                            "title": it.title,
                            "status": status,
                            "logos": detected_logos,
                            "prohibited": prohibited_types,
                            "summary": ds_verdict.get("summary", "")
                        })
                        print(f"🚫 [红线拦截 PROHIBITED] ID: {it.id} | SKU: {it.seller_sku} | Logo: {detected_logos} | 禁运: {prohibited_types}")
                    else:
                        risk_intercepted_count += 1
                        intercepted_records.append({
                            "id": it.id,
                            "sku": it.seller_sku,
                            "title": it.title,
                            "status": status,
                            "logos": detected_logos,
                            "prohibited": prohibited_types,
                            "summary": ds_verdict.get("summary", "")
                        })
                        print(f"⚠️ [黄线拦截 RISK] ID: {it.id} | SKU: {it.seller_sku} | 摘要: {ds_verdict.get('summary', '')}")

                    # 周期性日志汇报
                    if processed_count % 20 == 0 or processed_count == total_to_process:
                        elapsed = time.time() - start_time
                        speed = processed_count / elapsed if elapsed > 0 else 0
                        pct = (processed_count / total_to_process) * 100
                        print(f"[{time.strftime('%H:%M:%S')}] 进度: {processed_count}/{total_to_process} ({pct:.1f}%) | "
                              f"SAFE: {safe_count} | 拦截: {risk_intercepted_count + prohibited_intercepted_count} | "
                              f"速度: {speed:.1f} 件/秒")

                return
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                is_transient = any(k in err_str for k in ["429", "rate limit", "timeout", "connection", "reset", "502", "503", "504", "locked"])
                if attempt < max_retries and is_transient:
                    backoff = (0.5 * (2 ** attempt)) + random.uniform(0.1, 0.4)
                    time.sleep(backoff)
                    continue
                break
            finally:
                thread_db.close()

        with lock:
            processed_count += 1
            error_count += 1
            print(f"❌ [调用异常] ID: {item_id} 异常: {last_error}")

    print(f"[*] 启动 {args.workers} 线程池开始增量发包...")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_one, iid) for iid in target_ids]
        for f in as_completed(futures):
            pass

    total_time = time.time() - start_time
    print("\n==================================================================")
    print("  🏁 DeepSeek 视觉补齐检测执行完成报告")
    print("==================================================================")
    print(f"  总耗时:          {total_time:.2f} 秒 ({total_time / 60:.2f} 分钟)")
    print(f"  平均处理速率:    {total_to_process / total_time:.2f} 件/秒")
    print(f"  目标待补齐商品:  {total_to_process} 件")
    print(f"  成功处理完成:    {processed_count - error_count} 件")
    print(f"  ✓ 保持 SAFE 合规: {safe_count} 件")
    print(f"  🚫 识别风险拦截:  {risk_intercepted_count + prohibited_intercepted_count} 件 (PROHIBITED: {prohibited_intercepted_count}, RISK: {risk_intercepted_count})")
    print(f"  ❌ 异常失败件数:  {error_count} 件")
    print("==================================================================")

    if intercepted_records:
        print("\n[!] 补齐检测新识别出的侵权/违禁拦截明细 (前 10 条):")
        for rec in intercepted_records[:10]:
            print(f"- ID: {rec['id']} | [{rec['status']}] SKU: {rec['sku']} | 检出: {rec.get('logos') or rec.get('prohibited') or rec.get('summary')}")
            print(f"  标题: {rec['title'][:60]}")

if __name__ == "__main__":
    main()
