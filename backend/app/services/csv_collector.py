import os
import re
import csv
import io
import time
import logging
from typing import List, Dict, Any, Optional, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models.product import Product
from .takealot_service import TakealotService
from .task_manager import task_manager
from .audit_logger import record_audit_log

logger = logging.getLogger("csv_collector")

def parse_plids_from_content(content_or_path: Union[str, bytes]) -> List[Dict[str, str]]:
    """
    智能解析 CSV 或纯文本内容/文件中的 PLID 和 TSIN。
    支持：
    1. CSV 表格格式（自动识别 PLID, TSIN, 代表性TSIN, URL 等表头）
    2. 纯文本逐行 PLID / 链接格式
    3. 自由文本中正则提取 8~10 位数字编号
    返回去重后的列表: [{"plid": "100779861", "tsin": "102455384"}, ...]
    """
    raw_text = ""
    if isinstance(content_or_path, bytes):
        for enc in ['utf-8-sig', 'utf-8', 'gbk', 'latin-1']:
            try:
                raw_text = content_or_path.decode(enc)
                break
            except Exception:
                continue
    elif isinstance(content_or_path, str):
        if os.path.isfile(content_or_path):
            with open(content_or_path, mode='rb') as f:
                raw_bytes = f.read()
            for enc in ['utf-8-sig', 'utf-8', 'gbk', 'latin-1']:
                try:
                    raw_text = raw_bytes.decode(enc)
                    break
                except Exception:
                    continue
        else:
            raw_text = content_or_path

    if not raw_text.strip():
        return []

    results = []
    seen_plids = set()

    # 1. 尝试以 CSV DictReader 解析
    try:
        f_csv = io.StringIO(raw_text.strip())
        reader = csv.DictReader(f_csv)
        if reader.fieldnames:
            plid_col = None
            tsin_col = None
            url_col = None

            for col in reader.fieldnames:
                col_clean = str(col).strip().upper()
                if not plid_col and any(k == col_clean or k in col_clean for k in ['PLID', '商品编号', '母商品']):
                    # 避免把数据行如 "PLID100779861" 误当成列名
                    if not re.search(r'\d{6,}', col_clean):
                        plid_col = col
                if not tsin_col and any(k == col_clean or k in col_clean for k in ['TSIN', '代表性TSIN', 'SKU_ID', '变体编号']):
                    if not re.search(r'\d{6,}', col_clean):
                        tsin_col = col
                if not url_col and any(k == col_clean or k in col_clean for k in ['URL', '链接', 'LINK', 'HREF']):
                    if not col_clean.startswith('HTTP'):
                        url_col = col

            if plid_col or url_col:
                for row in reader:
                    val = str(row.get(plid_col, '') if plid_col else '').strip()
                    tsin_val = str(row.get(tsin_col, '') if tsin_col else '').strip()
                    url_val = str(row.get(url_col, '') if url_col else '').strip()

                    # 提取纯数字 PLID
                    m_plid = re.search(r'(?:PLID)?(\d{7,10})', val, re.I)
                    if not m_plid and url_val:
                        m_plid = re.search(r'(?:PLID)?(\d{7,10})', url_val, re.I)

                    if m_plid:
                        p_num = m_plid.group(1)
                        if p_num not in seen_plids:
                            seen_plids.add(p_num)
                            # 提取纯数字 TSIN
                            m_tsin = re.search(r'(?:TSIN)?(\d{7,10})', tsin_val, re.I)
                            t_num = m_tsin.group(1) if m_tsin else ""
                            results.append({"plid": p_num, "tsin": t_num})
    except Exception as e:
        logger.debug(f"CSV 解析跳过或格式不规则: {e}")

    # 2. 如果 CSV 识别不足，按逐行与正则兜底
    if not results:
        lines = raw_text.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            # 匹配 7~10 位数字
            matches = re.findall(r'(?:PLID|TSIN)?(\d{7,10})', line_str, re.I)
            if matches:
                p_num = matches[0]
                if p_num not in seen_plids:
                    seen_plids.add(p_num)
                    t_num = matches[1] if len(matches) > 1 else ""
                    results.append({"plid": p_num, "tsin": t_num})

    return results

def batch_collect_plids(
    plid_items: List[Dict[str, str]],
    db: Session,
    user_id: Optional[int] = None,
    skip_existing: bool = True,
    max_workers: int = 6,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    全自动批量拉取并入库 Takealot 商品：
    - 多线程并发拉取官方 API 物料（防阻塞）
    - 串行写入 SQLite 数据库（防 database is locked 竞争）
    - 智能去重保护（默认跳过线上已提交/已清洗商品）
    - 自动关联变体、预测类目与计算加价
    """
    total = len(plid_items)
    if total == 0:
        return {
            "total": 0,
            "success_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "variant_count": 0,
            "items": [],
            "failures": []
        }

    # 1. 预查已存在商品 (去重保护)
    to_fetch_items = []
    skipped_items = []

    if skip_existing:
        all_plids = [item["plid"] for item in plid_items]
        lookup_codes = [f"PLID{p}" for p in all_plids] + all_plids
        existing_records = db.query(Product.group_code, Product.takealot_id, Product.status).filter(
            (Product.group_code.in_(lookup_codes)) | (Product.takealot_id.in_(lookup_codes))
        ).all()
        
        existing_set = set()
        for r in existing_records:
            g = (r[0] or "").replace("PLID", "").strip()
            t = (r[1] or "").replace("PLID", "").strip()
            if g:
                existing_set.add(g)
            if t:
                existing_set.add(t)

        for item in plid_items:
            if item["plid"] in existing_set:
                skipped_items.append(item["plid"])
            else:
                to_fetch_items.append(item)
    else:
        to_fetch_items = list(plid_items)

    logger.info(f"批量采集任务启动: 总计 {total} 项, 已存在跳过 {len(skipped_items)} 项, 待拉取 {len(to_fetch_items)} 项")

    # 2. 线程池并发抓取 API 物料
    def _fetch_single(item: Dict[str, str]):
        p_id = item["plid"]
        t_id = item.get("tsin", "")
        err_msg = None
        try:
            req = TakealotService.fetch_product_by_plid(p_id)
            return {"plid": p_id, "req": req, "error": None}
        except Exception as e1:
            if t_id and t_id != p_id:
                try:
                    req = TakealotService.fetch_product_by_plid(f"TSIN{t_id}")
                    return {"plid": p_id, "req": req, "error": None, "fallback": "TSIN"}
                except Exception as e2:
                    err_msg = f"PLID: {str(e1)[:50]} | TSIN: {str(e2)[:50]}"
            else:
                err_msg = str(e1)[:100]
        return {"plid": p_id, "req": None, "error": err_msg}

    # 3. 执行并发拉取并有序写入数据库
    success_products = []
    failures = []
    processed_count = len(skipped_items)

    if task_id:
        task_manager.update_progress(
            task_id,
            current=processed_count,
            current_title=f"跳过已存在 {len(skipped_items)} 项，正在并发抓取新商品...",
            success_inc=0
        )

    if to_fetch_items:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_item = {executor.submit(_fetch_single, item): item for item in to_fetch_items}
            for fut in as_completed(future_to_item):
                if task_id and task_manager.is_cancelled(task_id):
                    logger.warning(f"任务 {task_id} 收到取消请求，停止后续入库")
                    break

                fetch_res = fut.result()
                p_id = fetch_res["plid"]
                req = fetch_res["req"]
                err = fetch_res["error"]
                processed_count += 1

                if req:
                    try:
                        # 串行写入保证 SQLite 无竞争
                        created = TakealotService.save_collected_product(db, req, user_id=user_id)
                        var_cnt = len(created) if isinstance(created, list) else 1
                        first_p = created[0] if isinstance(created, list) else created
                        success_products.append({
                            "plid": p_id,
                            "title": req.takealot_title,
                            "variants_count": var_cnt,
                            "price": req.takealot_price,
                            "id": first_p.id if first_p else None
                        })
                        if task_id:
                            task_manager.update_progress(
                                task_id,
                                current=processed_count,
                                current_title=f"已入库: {req.takealot_title[:35]} ({var_cnt}变体)",
                                success_inc=1
                            )
                    except Exception as save_err:
                        logger.error(f"保存商品 PLID{p_id} 异常: {save_err}")
                        failures.append({"plid": p_id, "error": f"保存入库失败: {str(save_err)[:100]}"})
                        if task_id:
                            task_manager.update_progress(
                                task_id,
                                current=processed_count,
                                current_title=f"入库失败: PLID{p_id}",
                                fail_inc=1,
                                error=f"PLID{p_id}: {str(save_err)[:100]}"
                            )
                else:
                    failures.append({"plid": p_id, "error": err or "API未返回有效物料"})
                    if task_id:
                        task_manager.update_progress(
                            task_id,
                            current=processed_count,
                            current_title=f"拉取失败: PLID{p_id}",
                            fail_inc=1,
                            error=f"PLID{p_id}: {err}"
                        )

    total_variants_saved = sum(p["variants_count"] for p in success_products)
    
    # 记录系统审计操作日志
    record_audit_log(
        task_type="COLLECT",
        status="SUCCESS" if success_products else ("FAILED" if failures else "INFO"),
        message=f"CSV批量采集完成: 成功 {len(success_products)} 个商品 (共 {total_variants_saved} 独立变体), 跳过已有 {len(skipped_items)} 项, 失败 {len(failures)} 项",
        detail_logs={
            "total_input": total,
            "success_plids": len(success_products),
            "total_variants": total_variants_saved,
            "skipped_plids": len(skipped_items),
            "failed_plids": len(failures),
            "failures": failures[:20],
            "user_id": user_id
        },
        db=db
    )

    return {
        "total": total,
        "success_count": len(success_products),
        "skipped_count": len(skipped_items),
        "failed_count": len(failures),
        "variant_count": total_variants_saved,
        "items": success_products,
        "skipped_items": skipped_items,
        "failures": failures
    }

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    csv_file = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Administrator\WorkBuddy\简单takealot自动跟价\exports\8店_有出单商品_PLID明细.csv"
    print("=" * 65)
    print(f"Takealot CSV 批量采集工具")
    print(f"目标文件: {csv_file}")
    print("=" * 65)
    
    parsed = parse_plids_from_content(csv_file)
    print(f">>> 成功从文件中解析出 {len(parsed)} 个唯一 PLID")
    
    db = SessionLocal()
    t0 = time.time()
    res = batch_collect_plids(parsed, db=db, skip_existing=True, max_workers=6)
    dur = time.time() - t0
    
    print("\n" + "=" * 65)
    print(f"采集结果汇报 (总耗时: {dur:.1f} 秒):")
    print(f"  - 总识别商品: {res['total']}")
    print(f"  - 跳过已存在: {res['skipped_count']} (保护已刊登数据)")
    print(f"  - 成功新采集: {res['success_count']} 个母商品")
    print(f"  - 生成新变体: {res['variant_count']} 个独立待清洗商品 (PENDING_CLEAN)")
    print(f"  - 采集失败数: {res['failed_count']}")
    if res['failures']:
        print("  - 失败明细:")
        for f in res['failures']:
            print(f"    * PLID {f['plid']}: {f['error']}")
    print("=" * 65)
    db.close()
