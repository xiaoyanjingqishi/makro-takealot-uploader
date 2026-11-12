import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import json
from datetime import datetime
from app.database import SessionLocal
from app.models.makro_piggyback import MakroPiggybackItem

def run_fix():
    # 1. Load scan_results.json
    scan_path = r'C:\Users\Administrator\.gemini\antigravity\brain\1b88da0d-0d5d-4299-9d2f-13765f0636fc\scratch\scan_results.json'

    with open(scan_path, 'r', encoding='utf-8') as f:
        scan_data = json.load(f)

    anomalies = scan_data.get('piggyback_items', [])
    anomaly_ids = [it['id'] for it in anomalies]
    print(f"Total anomaly IDs loaded: {len(anomaly_ids)}")

    db = SessionLocal()
    # Query all matching anomaly items
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(anomaly_ids)).all()
    print(f"Total anomaly records in DB: {len(items)}")

    # Strict separation:
    # Listed: status in ['ACTIVE', 'PUBLISHED'] or makro_listing_id is not None
    # Unlisted: status in ['PENDING', 'FAILED'] and makro_listing_id is None
    listed_items = []
    unlisted_items = []

    for it in items:
        if it.makro_listing_id or it.status in ['ACTIVE', 'PUBLISHED']:
            listed_items.append(it)
        else:
            unlisted_items.append(it)

    print(f"\n[Separation Check]")
    print(f"  Listed items to PRESERVE (Untouched): {len(listed_items)}")
    print(f"  Unlisted items to FIX (Correction): {len(unlisted_items)}")

    # Perform fix on unlisted items
    fixed_count = 0
    fixed_details = []

    for it in unlisted_items:
        old_status = it.compliance_status
        cd = {}
        if it.compliance_details:
            try:
                cd = json.loads(it.compliance_details)
            except Exception:
                cd = {}

        # Update JSON
        cd["compliance_status"] = "PROHIBITED"
        cd["is_disputed"] = False
        cd["is_white_label"] = False
        cd["reconciliation_summary"] = "【视觉红线一票拦截】首图实物印有品牌商号文字或航空禁运物，已由合规风控引擎阻断跟品挂靠"

        # Update DB Model
        it.compliance_status = "PROHIBITED"
        it.compliance_details = json.dumps(cd, ensure_ascii=False)
        it.is_abandoned = True
        it.abandoned_reason = "视觉红线违规：实物印有私牌/禁运物"
        it.abandoned_at = datetime.now()

        fixed_count += 1
        fixed_details.append({
            "id": it.id,
            "sku": it.seller_sku,
            "fsn": it.makro_product_id,
            "brand": it.brand,
            "title": it.title[:40],
            "old_status": old_status,
            "new_status": it.compliance_status
        })

    db.commit()
    print(f"\n[Execution Complete]")
    print(f"Successfully fixed {fixed_count} unlisted items to PROHIBITED (Abandoned to prevent piggyback)!")
    print(f"Preserved 100% of {len(listed_items)} listed items without any change.")

    # Print a few samples of fixed items
    print("\nSample of Fixed Items:")
    for fd in fixed_details[:10]:
        t_clean = fd['title'].encode('ascii', 'ignore').decode('ascii')
        print(f"  ID: {fd['id']} | SKU: {fd['sku']} | {fd['old_status']} -> {fd['new_status']} | Brand: {fd['brand']} | {t_clean}")

    # Specifically verify SKU GP2611111805294F76
    target = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.seller_sku == 'GP2611111805294F76').first()
    if target:
        print(f"\n[Target SKU Verification]")
        print(f"  SKU: {target.seller_sku}")
        print(f"  compliance_status: {target.compliance_status}")
        print(f"  is_abandoned: {target.is_abandoned}")
        print(f"  abandoned_reason: {target.abandoned_reason}")

if __name__ == '__main__':
    run_fix()
