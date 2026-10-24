# -*- coding: utf-8 -*-
"""
Qwen-VL 视觉质检存量异常靶向自愈脚本
仅对历史因 404/模型下线导致视觉审查跳过的商品执行单模态 Qwen3-VL 看图补检
复用已有的文本与 DeepSeek 审计结论，双向回写 makro_piggyback_items 与 store_product_reprices
"""

import sys
import os
import json
import time
import base64
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 将 backend 路径加入 sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import SessionLocal
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.store_product_reprice import StoreProductReprice
from app.services.compliance_service import ComplianceService, IMAGE_CACHE_DIR, _vision_image_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("VisionHealer")

def fetch_and_cache_image(img_url: str):
    """提取首图并转换为 base64 data URI (优先命中全局 .image_cache)"""
    if not img_url or not isinstance(img_url, str) or not img_url.startswith("http"):
        return None, "无效图片URL"

    clean_url = img_url.strip()
    if "{size}" in clean_url:
        clean_url = clean_url.replace("{size}", "pdpxl")

    import hashlib
    url_hash = hashlib.md5(clean_url.encode("utf-8")).hexdigest()
    cache_path = os.path.join(IMAGE_CACHE_DIR, f"{url_hash}.img")
    ct_path = os.path.join(IMAGE_CACHE_DIR, f"{url_hash}.ct")

    img_bytes = None
    mime = "image/jpeg"

    # 1. 命中缓存
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        try:
            with open(cache_path, "rb") as f:
                img_bytes = f.read()
            if os.path.exists(ct_path):
                with open(ct_path, "r", encoding="utf-8") as f:
                    mime = f.read().strip() or "image/jpeg"
        except Exception:
            img_bytes = None

    # 2. 网络抓取
    if not img_bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.makro.co.za/" if "makro.co.za" in clean_url else "https://www.takealot.com/"
        }
        for attempt in range(3):
            try:
                resp = _vision_image_session.get(clean_url, headers=headers, timeout=12)
                if resp.status_code == 200 and resp.content:
                    img_bytes = resp.content
                    ct = resp.headers.get("content-type", "").split(";")[0].strip()
                    if ct and ct.startswith("image/"):
                        mime = ct
                    elif img_bytes.startswith(b'\x89PNG'):
                        mime = "image/png"
                    elif img_bytes.startswith(b'RIFF') and b'WEBP' in img_bytes[:16]:
                        mime = "image/webp"
                    else:
                        mime = "image/jpeg"
                    try:
                        with open(cache_path, "wb") as f:
                            f.write(img_bytes)
                        with open(ct_path, "w", encoding="utf-8") as f:
                            f.write(mime)
                    except Exception:
                        pass
                    break
                else:
                    if attempt < 2:
                        time.sleep(0.3 * (attempt + 1))
            except Exception:
                if attempt < 2:
                    time.sleep(0.3 * (attempt + 1))

    if not img_bytes:
        return None, "图片下载失败"

    b64_data = base64.b64encode(img_bytes).decode("utf-8")
    data_uri = f"data:{mime};base64,{b64_data}"
    return data_uri, None


def heal_single_item(item_id: int, compliance_svc: ComplianceService):
    """对单个商品执行 Qwen-VL 视觉靶向自愈补检"""
    db = SessionLocal()
    try:
        item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
        if not item or not item.compliance_details:
            return False, "未找到商品或质检记录为空"

        try:
            details = json.loads(item.compliance_details)
        except Exception as e:
            return False, f"JSON解析失败: {e}"

        img_url = item.image_url or details.get("image_inspection", {}).get("image_url")
        if not img_url:
            return False, "无商品主图"

        # 抓取并转换图片
        data_uri, err = fetch_and_cache_image(img_url)
        if not data_uri:
            # 记录首图下载失败
            details["image_inspection"]["download_error"] = True
            details["image_inspection"]["summary"] = f"首图下载失败: {err}"
            item.compliance_details = json.dumps(details, ensure_ascii=False)
            db.commit()
            return False, f"首图下载异常: {err}"

        # 构造 prompt
        known_brands = details.get("brand_breakdown", {}).get("all_brands", [])
        vision_prompt = compliance_svc._build_vision_prompt(known_brands, is_piggyback=True)

        # 专属调用 qwen3-vl-plus
        qwen_img_res = compliance_svc._invoke_vision_model(
            client=compliance_svc.qwen_client,
            model="qwen3-vl-plus",
            ai_name="Qwen-VL",
            prompt=vision_prompt,
            data_uri=data_uri,
            image_url=img_url
        )

        if not qwen_img_res.get("tested"):
            return False, f"Qwen-VL 调用未完成: {qwen_img_res.get('summary')}"

        # ---------------------------------------------------------
        # 增量注入与合并
        # ---------------------------------------------------------
        # 1. 更新 qwen_verdict 快照
        if "qwen_verdict" in details:
            details["qwen_verdict"]["tested"] = True
            details["qwen_verdict"]["image_audit"] = qwen_img_res
            qwen_text_level = details["qwen_verdict"].get("title_audit", {}).get("risk_level", "SAFE")
            qwen_img_level = qwen_img_res.get("risk_level", "SAFE")
            from app.constants.brands import get_higher_risk
            details["qwen_verdict"]["overall_risk"] = get_higher_risk(qwen_text_level, qwen_img_level)

        # 2. 提取并保留 DeepSeek 视觉原有结论
        ds_summary = ""
        old_sum = details.get("image_inspection", {}).get("summary", "")
        if "DeepSeek:" in old_sum:
            ds_summary = old_sum.split("DeepSeek:", 1)[1].strip()
        elif "deepseek_verdict" in details:
            ds_summary = details["deepseek_verdict"].get("image_audit", {}).get("summary", "")

        qwen_summary_text = qwen_img_res.get("summary", "纯中性产品图无品牌Logo及禁运品")
        combined_summary = f"千问: {qwen_summary_text} | DeepSeek: {ds_summary or '视觉未检出异常'}"

        # 3. 更新 image_inspection
        details["image_inspection"]["tested"] = True
        details["image_inspection"]["download_error"] = False
        details["image_inspection"]["has_brand_logo"] = bool(qwen_img_res.get("has_brand_logo") or details.get("image_inspection", {}).get("has_brand_logo", False))
        details["image_inspection"]["is_prohibited"] = bool(qwen_img_res.get("is_transport_prohibited") or details.get("image_inspection", {}).get("is_prohibited", False))
        details["image_inspection"]["logo_names"] = list(dict.fromkeys(qwen_img_res.get("logo_names", []) + details.get("image_inspection", {}).get("logo_names", [])))
        details["image_inspection"]["summary"] = combined_summary

        # 4. 更新 vision_breakdown
        if "vision_breakdown" not in details:
            details["vision_breakdown"] = {}
        vb = details["vision_breakdown"]
        vb["tested"] = True
        vb["qwen_logos"] = qwen_img_res.get("logo_names", [])
        vb["qwen_prohibs"] = qwen_img_res.get("prohibited_types", [])
        vb["qwen_risk"] = qwen_img_res.get("risk_level", "SAFE")
        vb["all_logos"] = list(dict.fromkeys(vb.get("qwen_logos", []) + vb.get("deepseek_logos", [])))
        vb["all_prohibs"] = list(dict.fromkeys(vb.get("qwen_prohibs", []) + vb.get("deepseek_prohibs", [])))

        # 5. 重新校定综合评级
        if qwen_img_res.get("risk_level") == "PROHIBITED" or vb.get("all_prohibs"):
            new_status = "PROHIBITED"
        elif qwen_img_res.get("risk_level") == "RISK" and item.compliance_status != "PROHIBITED":
            new_status = "RISK"
        else:
            new_status = item.compliance_status or "SAFE"

        details["compliance_status"] = new_status
        item.compliance_status = new_status
        item.compliance_details = json.dumps(details, ensure_ascii=False)
        db.commit()

        # 6. 双向同步更新 store_product_reprices
        reps = db.query(StoreProductReprice).filter(
            (StoreProductReprice.source_piggyback_id == item.id) |
            (StoreProductReprice.makro_product_id == item.makro_product_id)
        ).all()
        for rp in reps:
            rp.compliance_status = new_status
            rp.compliance_details = item.compliance_details
            rp.last_compliance_check_at = datetime.now()
            if new_status == "PROHIBITED":
                rp.monitor_status = "BLOCKED"
                rp.auto_reprice = False
            elif new_status == "SAFE" and rp.monitor_status == "BLOCKED":
                rp.monitor_status = "ACTIVE"
                rp.auto_reprice = True
        if reps:
            db.commit()

        return True, f"成功 (状态: {new_status})"

    except Exception as ex:
        db.rollback()
        return False, str(ex)
    finally:
        db.close()


def run_healing(target_ids=None, concurrency=20, max_items=None):
    """批量自愈总入口"""
    db = SessionLocal()
    try:
        compliance_svc = ComplianceService.from_db(db)
        if not compliance_svc.has_qwen:
            print("[错误] 未配置 Qwen API Key，无法执行视觉质检自愈！")
            return

        if target_ids is None:
            # 扫描所有 Qwen-VL 跳过异常的商品
            print("正在扫描待自愈商品清单...")
            rows = db.query(MakroPiggybackItem.id, MakroPiggybackItem.compliance_details, MakroPiggybackItem.status).all()
            target_ids = []
            for rid, cdet, st in rows:
                if not cdet:
                    continue
                sum_text = cdet.lower()
                if "qwen" in sum_text and any(k in sum_text for k in ["404", "model not exist", "视觉检测跳过", "视觉检测调用异常", "error code:"]):
                    target_ids.append(rid)

        if max_items:
            target_ids = target_ids[:max_items]

        total = len(target_ids)
        print(f"==================================================")
        print(f"🎯 待自愈商品总数: {total} 件 (并发数: {concurrency})")
        print(f"模型: qwen3-vl-plus")
        print(f"==================================================")

        success_cnt = 0
        fail_cnt = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(heal_single_item, iid, compliance_svc): iid for iid in target_ids}
            done_cnt = 0
            for fut in as_completed(futures):
                iid = futures[fut]
                done_cnt += 1
                try:
                    ok, msg = fut.result()
                    if ok:
                        success_cnt += 1
                    else:
                        fail_cnt += 1
                        logger.warning(f"Item #{iid} 自愈失败: {msg}")
                except Exception as ex:
                    fail_cnt += 1
                    logger.error(f"Item #{iid} 异常: {ex}")

                if done_cnt % 50 == 0 or done_cnt == total:
                    elapsed = time.time() - start_time
                    speed = done_cnt / elapsed if elapsed > 0 else 0
                    print(f"[{done_cnt}/{total}] 进度: {done_cnt/total*100:.1f}% | 成功: {success_cnt} | 失败: {fail_cnt} | 速率: {speed:.1f} 件/秒")

        elapsed_total = time.time() - start_time
        print(f"\n==================================================")
        print(f"🎉 自愈任务执行完毕！")
        print(f"总计: {total} 件 | 成功: {success_cnt} | 失败: {fail_cnt} | 耗时: {elapsed_total:.1f} 秒")
        print(f"==================================================")

    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--canary", action="store_true", help="是否仅执行前20件金丝雀测试")
    parser.add_argument("--limit", type=int, default=None, help="最大处理数量")
    parser.add_argument("--concurrency", type=int, default=20, help="并发线程数")
    args = parser.parse_args()

    max_n = 20 if args.canary else args.limit
    run_healing(concurrency=args.concurrency, max_items=max_n)
