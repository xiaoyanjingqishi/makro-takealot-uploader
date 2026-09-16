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

        from .vertical_service import VerticalService
        predicted_vertical = VerticalService.predict_vertical(
            title=req.takealot_title,
            category=req.takealot_category or "",
            specs=req.takealot_specs,
            description=req.takealot_description or ""
        )

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
                
                var_title = v.variant_title
                if not var_title:
                    tags = []
                    if v.colour and v.colour != "多色":
                        tags.append(v.colour)
                    if v.size and v.size != "均码":
                        tags.append(v.size)
                    if v.variant_attributes:
                        cap = v.variant_attributes.get("capacity") or v.variant_attributes.get("storage_capacity")
                        if cap and cap not in tags:
                            tags.append(cap)
                    if tags:
                        var_title = f"{req.takealot_title} - {' - '.join(tags)}"
                    else:
                        var_title = req.takealot_title

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
                    brand_colour=v.brand_colour or v.colour or "多色",
                    pack_of=v.pack_of or "1",
                    status="PENDING_CLEAN",
                    compliance_status="PENDING_CHECK",
                    compliance_details=None,
                    makro_vertical=predicted_vertical,
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
                makro_vertical=predicted_vertical,
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
        通过 Takealot 官方原生 REST API 接口获取商品全量数据（支持单/多维变体如 颜色+尺码 笛卡尔积全量解析与专属图组隔离）
        """
        import re
        import requests
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter
        from concurrent.futures import ThreadPoolExecutor
        from ..schemas.product import VariantCreate

        m = re.search(r'PLID(\d+)', str(plid_or_url), re.IGNORECASE)
        if not m:
            m = re.search(r'(\d+)', str(plid_or_url))
        if not m:
            raise ValueError(f"无法识别有效的 PLID: {plid_or_url}")
        plid = m.group(1)

        session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
            'Origin': 'https://www.takealot.com',
            'Referer': 'https://www.takealot.com/',
            'Accept': 'application/json'
        }

        api_url = f"https://api.takealot.com/rest/v-1-11-0/product-details/PLID{plid}"
        resp = session.get(api_url, headers=headers, timeout=20)
        if resp.status_code != 200:
            raise ValueError(f"Takealot 官方 API 请求失败 (HTTP {resp.status_code}): {resp.text[:200]}")

        data = resp.json()

        # 核心基础信息
        title = data.get('title') or (data.get('core') or {}).get('title') or f"Takealot Product {plid}"
        brand = (data.get('brand') or {}).get('name') or "Generic"
        
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

        # 主图列表
        raw_images = []
        for img in (data.get('gallery') or {}).get('images') or []:
            hd = img.replace('{size}', 'pdpxl') if '{size}' in img else img
            if hd not in raw_images:
                raw_images.append(hd)

        # 变体全维度解析 (支持单维变体、二维 颜色+尺寸、多维 颜色+容量+尺寸等笛卡尔积组合解析)
        variants = []
        v_selectors = (data.get('variants') or {}).get('selectors') or []

        def _normalize_key(k_name: str) -> str:
            k_l = (k_name or '').lower()
            if any(x in k_l for x in ['colour', 'color', '颜色']):
                return 'colour'
            if any(x in k_l for x in ['size', '尺码', '尺寸', 'bra_size', 'dimension']):
                return 'size'
            if any(x in k_l for x in ['capacity', 'storage', 'memory', 'ram', 'rom', '容量', '内存']):
                return 'capacity'
            if any(x in k_l for x in ['pack', 'count', '包装', '件数']):
                return 'pack_of'
            return k_name or 'option'

        combinations_to_fetch = []

        # 通用 N 级变体树展开引擎 (支持 1级单变量、2级双变量、3级及以上多维度任意层级组合)
        num_levels = len(v_selectors)
        current_nodes = []

        if num_levels == 1:
            sel = v_selectors[0]
            sel_title = sel.get('title') or 'Option'
            for opt in sel.get('options', []):
                val = opt.get('value')
                v_name = val.get('name') if isinstance(val, dict) else (str(val) if val is not None else opt.get('id', ''))
                href = opt.get('href')
                if href and not href.startswith('http'):
                    href = f"https://api.takealot.com{href}"
                current_nodes.append({
                    'href': href,
                    'attrs': {sel_title: v_name},
                    'fallback_gallery': raw_images[:5],
                    'opt': opt
                })
        elif num_levels >= 2:
            # 第一层级（主维度，如颜色/主型号）初始化
            sel0 = v_selectors[0]
            s0_title = sel0.get('title') or 'Option1'
            for opt in sel0.get('options', []):
                val = opt.get('value')
                name = val.get('name') if isinstance(val, dict) else (str(val) if val is not None else opt.get('id', ''))
                href = opt.get('href')
                if href and not href.startswith('http'):
                    href = f"https://api.takealot.com{href}"
                current_nodes.append({
                    'href': href,
                    'attrs': {s0_title: name},
                    'fallback_gallery': raw_images[:5],
                    'level': 0,
                    'opt': opt
                })

            def _fetch_node_data(href):
                if not href:
                    return None
                try:
                    r = session.get(href, headers=headers, timeout=20)
                    if r.status_code == 200:
                        return r.json()
                except Exception as e:
                    logger.warning(f"获取变体层级数据异常 {href}: {e}")
                return None

            # 逐层向下递归展开（支持 2 级、3 级、4 级等多维任意层级嵌套）
            for lvl in range(1, num_levels):
                next_nodes = []

                def _expand_branch(node):
                    sub_data = _fetch_node_data(node['href'])
                    node_gallery = list(node['fallback_gallery'])
                    if sub_data:
                        for img in (sub_data.get('gallery') or {}).get('images') or []:
                            hd = img.replace('{size}', 'pdpxl') if '{size}' in img else img
                            if hd not in node_gallery:
                                node_gallery.append(hd)

                    sub_selectors = (sub_data.get('variants') or {}).get('selectors') if sub_data else None
                    if sub_selectors and len(sub_selectors) > lvl:
                        next_sel = sub_selectors[lvl]
                    else:
                        next_sel = v_selectors[lvl]

                    n_title = next_sel.get('title') or f"Option{lvl+1}"
                    children = []
                    for opt in next_sel.get('options', []):
                        val = opt.get('value')
                        name = val.get('name') if isinstance(val, dict) else (str(val) if val is not None else opt.get('id', ''))
                        combo_href = opt.get('href')
                        if combo_href and not combo_href.startswith('http'):
                            combo_href = f"https://api.takealot.com{combo_href}"
                        child_attrs = dict(node['attrs'])
                        child_attrs[n_title] = name
                        children.append({
                            'href': combo_href,
                            'attrs': child_attrs,
                            'fallback_gallery': node_gallery,
                            'level': lvl,
                            'opt': opt
                        })
                    return children

                with ThreadPoolExecutor(max_workers=10) as ex:
                    results = list(ex.map(_expand_branch, current_nodes))
                for res in results:
                    next_nodes.extend(res)
                current_nodes = next_nodes

        combinations_to_fetch = current_nodes

        if combinations_to_fetch:
            def _fetch_combo_detail(combo):
                href = combo.get('href')
                if not href:
                    return (combo, None)
                try:
                    r = session.get(href, headers=headers, timeout=20)
                    if r.status_code == 200:
                        return (combo, r.json())
                except Exception as e:
                    logger.warning(f"获取变体组合详情 {href} 异常: {e}")
                return (combo, None)

            with ThreadPoolExecutor(max_workers=12) as executor:
                combo_details = list(executor.map(_fetch_combo_detail, combinations_to_fetch))

            for combo, c_data in combo_details:
                raw_attrs = combo.get('attrs', {})
                var_images = list(combo.get('fallback_gallery') or [])
                var_price = price
                var_tsin = None
                var_title = ""
                var_specs = {}
                var_barcode = None

                if c_data:
                    # 组合相册提取
                    for img in (c_data.get('gallery') or {}).get('images') or []:
                        hd = img.replace('{size}', 'pdpxl') if '{size}' in img else img
                        if hd not in var_images:
                            var_images.append(hd)

                    sub_bb = c_data.get('buybox') or {}
                    var_tsin = sub_bb.get('tsin')
                    sub_items = sub_bb.get('items') or []
                    if sub_items:
                        var_price = float(sub_items[0].get('price') or sub_items[0].get('listing_price') or var_price)

                    var_title = c_data.get('title') or (c_data.get('core') or {}).get('title') or ""

                    for it in (c_data.get('product_information') or {}).get('items') or []:
                        dn = it.get('display_name')
                        dt = it.get('displayable_text')
                        if dn and dt:
                            clean_dt = dt.replace('\\(', '(').replace('\\)', ')')
                            var_specs[dn] = clean_dt
                            if it.get('item_type') == 'barcode' or 'barcode' in dn.lower():
                                var_barcode = clean_dt

                if not var_images:
                    var_images = raw_images[:5]

                # 规范化与提取变体全部变量 (Colour + Size + Capacity + Pack of 等)
                var_attrs = {}
                for k, v in raw_attrs.items():
                    norm_k = _normalize_key(k)
                    var_attrs[norm_k] = str(v).strip()

                # 从子规格 specs 中补全未匹配的变量
                for sk, sv in var_specs.items():
                    norm_k = _normalize_key(sk)
                    if norm_k in ['colour', 'size', 'capacity', 'pack_of'] and norm_k not in var_attrs:
                        var_attrs[norm_k] = str(sv).strip()

                final_size = var_attrs.get('size') or var_attrs.get('capacity') or "均码"
                final_colour = var_attrs.get('colour') or "多色"
                final_pack = var_attrs.get('pack_of') or "1"

                # 变体唯一标识
                opt_obj = combo.get('opt') or {}
                opt_id = opt_obj.get('id') or (f"{final_colour}_{final_size}".replace(' ', '_'))
                variant_id_str = str(var_tsin or opt_id)

                variants.append(VariantCreate(
                    takealot_variant_id=variant_id_str,
                    variant_title=var_title,
                    variant_attributes=var_attrs,
                    specs=var_specs,
                    barcode=var_barcode,
                    size=final_size,
                    colour=final_colour,
                    brand_colour=final_colour,
                    pack_of=final_pack,
                    takealot_price=var_price,
                    images=var_images
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
    def fetch_and_save_by_plid(cls, plid_or_url: str, db: Session) -> List[Product]:
        req = cls.fetch_product_by_plid(plid_or_url)
        return cls.save_collected_product(db, req)
