import json
import uuid
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from ..models.product import Product, ProductVariant
from ..schemas.product import TakealotCollectRequest
from .pricing_service import calculate_prices
from .ai_cleaner_service import AICleanerService
from ..config import settings

logger = logging.getLogger(__name__)

class TakealotService:
    @staticmethod
    def save_collected_product(db: Session, req: TakealotCollectRequest) -> List[Product]:
        """
        接收 Takealot 采集数据并扁平化入库：
        每个变体均作为一条独立的 Product 记录生成入库，拥有独立的标题、专属图组、价格与规格，清洗/上品全流程相互独立。
        """
        created_products = []
        root_plid = req.takealot_id or "PLID_UNKNOWN"

        # 如果已有相同 group_code 或 takealot_id 的旧数据，安全级联清理以支持重新采集刷新
        from ..models.task import TaskLog
        existing_pids = [p[0] for p in db.query(Product.id).filter((Product.group_code == root_plid) | (Product.takealot_id == root_plid)).all()]
        if existing_pids:
            db.query(ProductVariant).filter(ProductVariant.product_id.in_(existing_pids)).delete(synchronize_session=False)
            db.query(TaskLog).filter(TaskLog.product_id.in_(existing_pids)).delete(synchronize_session=False)
            db.query(Product).filter(Product.id.in_(existing_pids)).delete(synchronize_session=False)
            db.commit()

        if req.variants and len(req.variants) > 0:
            for idx, v in enumerate(req.variants):
                v_price = v.takealot_price if v.takealot_price > 0 else req.takealot_price
                v_selling, v_mrp = calculate_prices(v_price, db)
                sku_id = v.sku_id or f"SKU-{uuid.uuid4().hex[:8].upper()}"
                var_takealot_id = f"{root_plid}-{v.takealot_variant_id}" if v.takealot_variant_id else f"{root_plid}-{idx+1}"
                
                var_title = v.variant_title or req.takealot_title
                if not v.variant_title and v.colour and v.colour != "多色":
                    var_title = f"{req.takealot_title} - {v.colour}"
                
                var_images = v.images if v.images else req.raw_images
                
                combined_specs = dict(req.takealot_specs or {})
                if v.specs:
                    combined_specs.update(v.specs)

                product = Product(
                    takealot_id=var_takealot_id,
                    group_code=root_plid,
                    takealot_url=req.takealot_url,
                    takealot_title=var_title,
                    takealot_price=v_price,
                    takealot_brand=req.takealot_brand,
                    takealot_category=req.takealot_category,
                    takealot_description=req.takealot_description,
                    takealot_specs=json.dumps(combined_specs, ensure_ascii=False),
                    raw_images=json.dumps(var_images),
                    sku_id=sku_id,
                    barcode=v.barcode,
                    variant_attributes=json.dumps(v.variant_attributes, ensure_ascii=False) if v.variant_attributes else "{}",
                    size=v.size or "均码",
                    colour=v.colour or "多色",
                    brand_colour=v.brand_colour or "多色",
                    pack_of=v.pack_of or "1",
                    status="PENDING_CLEAN",
                    compliance_status="PENDING_CHECK",
                    compliance_details=None,
                    makro_vertical="bath_towel",
                    makro_brand=settings.DEFAULT_BRAND,
                    makro_selling_price=v_selling,
                    makro_mrp=v_mrp
                )
                db.add(product)
                created_products.append(product)
        else:
            # 单品无变体
            sku_id = f"SKU-{uuid.uuid4().hex[:8].upper()}"
            selling_price, mrp = calculate_prices(req.takealot_price, db)
            product = Product(
                takealot_id=root_plid,
                group_code=root_plid,
                takealot_url=req.takealot_url,
                takealot_title=req.takealot_title,
                takealot_price=req.takealot_price,
                takealot_brand=req.takealot_brand,
                takealot_category=req.takealot_category,
                takealot_description=req.takealot_description,
                takealot_specs=json.dumps(req.takealot_specs or {}, ensure_ascii=False),
                raw_images=json.dumps(req.raw_images or []),
                sku_id=sku_id,
                barcode=None,
                variant_attributes="{}",
                size="均码",
                colour="多色",
                brand_colour="多色",
                pack_of="1",
                status="PENDING_CLEAN",
                compliance_status="PENDING_CHECK",
                compliance_details=None,
                makro_vertical="bath_towel",
                makro_brand=settings.DEFAULT_BRAND,
                makro_selling_price=selling_price,
                makro_mrp=mrp
            )
            db.add(product)
            created_products.append(product)

        db.commit()
        for p in created_products:
            db.refresh(p)

        return created_products

    @classmethod
    def fetch_product_by_plid(cls, plid_or_url: str) -> TakealotCollectRequest:
        """
        通过 Takealot 官方原生 REST API 接口获取商品全量数据（含多变体独立专属图组）
        """
        import re
        import requests
        from concurrent.futures import ThreadPoolExecutor
        from ..schemas.product import VariantCreate

        m = re.search(r'PLID(\d+)', str(plid_or_url), re.IGNORECASE)
        if not m:
            m = re.search(r'(\d+)', str(plid_or_url))
        if not m:
            raise ValueError(f"无法识别有效的 PLID: {plid_or_url}")
        plid = m.group(1)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
            'Origin': 'https://www.takealot.com',
            'Referer': 'https://www.takealot.com/',
            'Accept': 'application/json'
        }

        url = f"https://api.takealot.com/rest/v-1-19-0/product-details/PLID{plid}?platform=desktop"
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            raise RuntimeError(f"Takealot API 请求失败 HTTP {resp.status_code}")
        data = resp.json()

        title = data.get('title') or (data.get('core') or {}).get('title') or ""
        brand = (data.get('core') or {}).get('brand') or (data.get('brand') or {}).get('name') or "Generic"

        # 价格
        buybox = data.get('buybox') or {}
        items = buybox.get('items') or []
        price = 0.0
        if items:
            price = float(items[0].get('price') or items[0].get('listing_price') or 0.0)

        # 类目面包屑
        cat_items = (data.get('breadcrumbs') or {}).get('items') or []
        category = " > ".join([x.get('name') for x in cat_items if x.get('name')])

        # 描述
        desc_obj = data.get('description')
        if isinstance(desc_obj, dict):
            raw_desc = desc_obj.get('html') or desc_obj.get('text') or ""
        else:
            raw_desc = str(desc_obj) if desc_obj else ""
        description = re.sub(r'<[^>]+>', ' ', raw_desc)
        description = re.sub(r'\s+', ' ', description).strip()

        # 规格参数
        specs = {}
        for it in (data.get('product_information') or {}).get('items') or []:
            name = it.get('display_name')
            text = it.get('displayable_text')
            if name and text:
                specs[name] = text.replace('\\(', '(').replace('\\)', ')')

        # 主图列表 (替换为 pdpxl 1200x1200 超高清)
        raw_images = []
        for img in (data.get('gallery') or {}).get('images') or []:
            hd = img.replace('{size}', 'pdpxl') if '{size}' in img else img
            if hd not in raw_images:
                raw_images.append(hd)

        # 变体解析 (并发抓取变体专属独立图组与价格)
        variants = []
        v_selectors = (data.get('variants') or {}).get('selectors') or []

        def _fetch_sub(opt_tuple):
            opt, sel_title, sel_type = opt_tuple
            sub_href = opt.get('href')
            if not sub_href:
                return None
            if not sub_href.startswith('http'):
                sub_href = f"https://api.takealot.com{sub_href}"
            try:
                r = requests.get(sub_href, headers=headers, timeout=10)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
            return None

        if v_selectors:
            options_to_fetch = []
            for sel in v_selectors:
                sel_title = sel.get('title') or ''
                sel_type = sel.get('selector_type', '').lower()
                for opt in sel.get('options', []):
                    options_to_fetch.append((opt, sel_title, sel_type))

            with ThreadPoolExecutor(max_workers=5) as executor:
                sub_results = list(executor.map(_fetch_sub, options_to_fetch))

            for (opt, sel_title, sel_type), sub_data in zip(options_to_fetch, sub_results):
                val = opt.get('value')
                v_name = val.get('name') if isinstance(val, dict) else (str(val) if val else opt.get('id', ''))

                var_images = []
                var_price = price
                var_tsin = None
                var_title = ""
                var_specs = {}
                var_barcode = None
                var_attrs = {}

                if sub_data:
                    for img in (sub_data.get('gallery') or {}).get('images') or []:
                        hd = img.replace('{size}', 'pdpxl') if '{size}' in img else img
                        if hd not in var_images:
                            var_images.append(hd)
                    sub_bb = sub_data.get('buybox') or {}
                    var_tsin = sub_bb.get('tsin')
                    sub_items = sub_bb.get('items') or []
                    if sub_items:
                        var_price = float(sub_items[0].get('price') or sub_items[0].get('listing_price') or var_price)

                    var_title = sub_data.get('title') or (sub_data.get('core') or {}).get('title') or ""
                    for it in (sub_data.get('product_information') or {}).get('items') or []:
                        dn = it.get('display_name')
                        dt = it.get('displayable_text')
                        if dn and dt:
                            clean_dt = dt.replace('\\(', '(').replace('\\)', ')')
                            var_specs[dn] = clean_dt
                            if it.get('item_type') == 'barcode' or 'barcode' in dn.lower():
                                var_barcode = clean_dt

                if not var_images:
                    opt_imgs = opt.get('image') or []
                    var_images = [img.replace('{size}', 'pdpxl') if '{size}' in img else img for img in opt_imgs]
                if not var_images:
                    var_images = raw_images[:5]

                # 提取异构参数: 容量(1TB/2TB/512GB), 包装(60 Pack), 尺码(XL/110cm), 颜色等
                sel_type_l = (sel_type or '').lower()
                sel_title_l = (sel_title or '').lower()

                if any(k in sel_type_l or k in sel_title_l for k in ['capacity', 'storage', 'memory', 'ram', 'rom', '容量', '内存']):
                    var_attrs['capacity'] = v_name
                elif any(k in sel_type_l or k in sel_title_l for k in ['pack', 'count', '包装', '件数']):
                    var_attrs['pack_of'] = v_name
                elif any(k in sel_type_l or k in sel_title_l for k in ['size', 'length', 'dimension', '尺码', '尺寸', '长度']):
                    var_attrs['size'] = v_name
                elif any(k in sel_type_l or k in sel_title_l for k in ['colour', 'color', '颜色']):
                    var_attrs['colour'] = v_name
                else:
                    var_attrs[sel_title or 'option'] = v_name

                # 从 var_specs (子规格) 中进一步提取并补全关键参数
                for sk, sv in var_specs.items():
                    sk_l = sk.lower()
                    if ('storage' in sk_l or 'capacity' in sk_l or 'memory' in sk_l) and 'capacity' not in var_attrs:
                        var_attrs['capacity'] = sv
                    elif ('colour' in sk_l or 'color' in sk_l) and 'colour' not in var_attrs:
                        var_attrs['colour'] = sv
                    elif ('size' in sk_l or 'dimension' in sk_l) and 'size' not in var_attrs:
                        var_attrs['size'] = sv

                final_size = var_attrs.get('size') or var_attrs.get('capacity') or "均码"
                final_colour = var_attrs.get('colour') or "多色"
                final_pack = var_attrs.get('pack_of') or "1"

                variants.append(VariantCreate(
                    takealot_variant_id=str(var_tsin or opt.get('id')),
                    variant_title=var_title,
                    variant_attributes=var_attrs,
                    specs=var_specs,
                    barcode=var_barcode,
                    size=final_size,
                    colour=final_colour,
                    brand_colour=final_colour,
                    pack_of=final_pack,
                    takealot_price=var_price,
                    images=var_images # 变体独立图组
                ))

        return TakealotCollectRequest(
            takealot_id=f"PLID{plid}",
            takealot_url=f"https://www.takealot.com/x/PLID{plid}",
            takealot_title=title,
            takealot_price=price,
            takealot_brand=brand,
            takealot_category=category,
            takealot_description=description,
            takealot_specs=specs,
            raw_images=raw_images,
            variants=variants
        )

    @classmethod
    def fetch_and_save_by_plid(cls, plid_or_url: str, db: Session) -> Product:
        req = cls.fetch_product_by_plid(plid_or_url)
        return cls.save_collected_product(db, req)
