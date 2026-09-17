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

        is_apparel = (
            predicted_vertical == "costume_wear"
            or any(x in (req.takealot_category or "").lower() for x in ["clothing", "apparel", "wear", "fashion", "bra", "underwear"])
        )
        def_size = "均码" if is_apparel else None
        def_colour = "多色" if is_apparel else None

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
                    size=v.size or def_size,
                    colour=v.colour or def_colour,
                    brand_colour=v.brand_colour or v.colour or def_colour,
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
                size=def_size,
                colour=def_colour,
                brand_colour=def_colour,
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
    def fetch_product_by_plid(cls, plid_or_url: str, custom_url: Optional[str] = None) -> TakealotCollectRequest:
        """
        通过 Takealot 官方原生 REST API 接口获取商品全量数据（支持单/多维变体如 颜色+尺码 笛卡尔积全量解析与专属图组隔离）
        """
        import re
        import requests
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter
        from concurrent.futures import ThreadPoolExecutor
        from ..schemas.product import VariantCreate

        plid_str = str(plid_or_url).strip()
        is_explicit_tsin = bool(re.search(r'TSIN(\d+)|[?&]tsin=(\d+)', plid_str, re.IGNORECASE))

        m = re.search(r'PLID(\d+)', plid_str, re.IGNORECASE)
        if not m:
            m = re.search(r'TSIN(\d+)', plid_str, re.IGNORECASE)
        if not m:
            m = re.search(r'[?&](?:plid|tsin)=(\d+)', plid_str, re.IGNORECASE)
        if not m:
            m = re.search(r'takealot\.com.*?/(\d{7,10})', plid_str, re.IGNORECASE)
        if not m:
            m = re.search(r'(\d{7,10})', plid_str)
        if not m:
            m = re.search(r'(\d+)', plid_str)
        if not m:
            raise ValueError(f"无法识别有效的商品编号或网址: {plid_or_url}")
        target_id = m.group(1)

        resolved_url = custom_url if (custom_url and custom_url.startswith("http")) else (
            plid_str if plid_str.startswith("http") else f"https://www.takealot.com/x/PLID{target_id}"
        )

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

        # 智能双模接口端点支持 (PLID 与 TSIN 互为容灾与回退)
        # 1. Takealot 的商品既有母商品编号 (PLID)，也有变体/单品唯一编号 (TSIN, 类似 ASIN)
        # 2. 如果输入显式指明 TSIN 或在 PLID 下 404，自动无缝尝试 TSIN 模式拉取全量数据
        if is_explicit_tsin:
            api_endpoints = [
                f"https://api.takealot.com/rest/v-1-19-0/product-details/TSIN{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/TSIN{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/TSIN{target_id}",
                f"https://api.takealot.com/rest/v-1-19-0/product-details/PLID{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/PLID{target_id}?platform=desktop",
            ]
        else:
            api_endpoints = [
                f"https://api.takealot.com/rest/v-1-19-0/product-details/PLID{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/PLID{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/PLID{target_id}",
                f"https://api.takealot.com/rest/v-1-19-0/product-details/TSIN{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/TSIN{target_id}?platform=desktop",
                f"https://api.takealot.com/rest/v-1-11-0/product-details/TSIN{target_id}",
            ]

        resp = None
        last_err = None
        for endpoint in api_endpoints:
            try:
                r = session.get(endpoint, headers=headers, timeout=15)
                if r.status_code == 200:
                    resp = r
                    break
                elif r.status_code == 404:
                    last_err = "404"
                else:
                    last_err = f"HTTP {r.status_code}"
            except Exception as e:
                last_err = str(e)

        if resp is None or resp.status_code != 200:
            if last_err == "404":
                raise ValueError(
                    f"Takealot 官方未找到该商品 (HTTP 404: 资源不存在)。\n"
                    f"目标编号【{target_id}】（已自动尝试 PLID 与 TSIN 双模匹配）在平台不存在或已被下架删除。\n"
                    f"请核对输入的编号或商品网址，或在 Takealot 网站上确认该商品是否仍在线售卖。\n"
                    f"(建议：在 Chrome 浏览器中打开该商品页面，点击页面右下角的一键采集或插件面板采集，可防止手输错误)"
                )
            raise ValueError(f"Takealot 官方 API 请求失败 ({last_err})")

        data = resp.json()

        # 核心基础信息
        title = data.get('title') or (data.get('core') or {}).get('title') or f"Takealot Product {target_id}"
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

        # 辅助高清图片提取器
        def _extract_hd_images(img_list):
            res = []
            for img in (img_list or []):
                hd = img.replace('{size}', 'pdpxl') if '{size}' in img else img
                if hd not in res:
                    res.append(hd)
            return res

        if num_levels == 1:
            sel = v_selectors[0]
            sel_title = sel.get('title') or 'Option'
            for opt in sel.get('options', []):
                val = opt.get('value')
                v_name = val.get('name') if isinstance(val, dict) else (str(val) if val is not None else opt.get('id', ''))
                href = opt.get('href')
                if href and not href.startswith('http'):
                    href = f"https://api.takealot.com{href}"
                opt_imgs = _extract_hd_images(opt.get('image'))
                current_nodes.append({
                    'href': href,
                    'attrs': {sel_title: v_name},
                    'branch_gallery': opt_imgs,
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
                opt_imgs = _extract_hd_images(opt.get('image'))
                current_nodes.append({
                    'href': href,
                    'attrs': {s0_title: name},
                    'branch_gallery': opt_imgs,
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
                    node_gallery = []
                    if sub_data:
                        sub_imgs = _extract_hd_images((sub_data.get('gallery') or {}).get('images'))
                        if sub_imgs:
                            node_gallery = sub_imgs
                    if not node_gallery:
                        node_gallery = list(node.get('branch_gallery') or [])

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
                        opt_imgs = _extract_hd_images(opt.get('image'))
                        child_gallery = opt_imgs if opt_imgs else list(node_gallery)
                        child_attrs = dict(node['attrs'])
                        child_attrs[n_title] = name
                        children.append({
                            'href': combo_href,
                            'attrs': child_attrs,
                            'branch_gallery': child_gallery,
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
                combo_imgs = []
                var_price = price
                var_tsin = None
                var_title = ""
                var_specs = {}
                var_barcode = None

                if c_data:
                    # 组合相册提取
                    combo_imgs = _extract_hd_images((c_data.get('gallery') or {}).get('images'))

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

                # 变体相册隔离：严格优先使用变体专有相册 -> 颜色分支相册 -> 兜底母体相册，杜绝首图串色
                if combo_imgs:
                    var_images = list(combo_imgs)
                    if len(var_images) == 1 and combo.get('branch_gallery'):
                        for bi in combo.get('branch_gallery'):
                            if bi not in var_images:
                                var_images.append(bi)
                elif combo.get('branch_gallery'):
                    var_images = list(combo.get('branch_gallery'))
                else:
                    var_images = list(raw_images)

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

        # 从官方响应中提取权威母商品 PLID
        buybox = data.get('buybox') or {}
        real_plid_num = buybox.get('plid') or (data.get('core') or {}).get('id')
        canonical_plid = str(real_plid_num) if real_plid_num else target_id
        canonical_url = data.get('desktop_href') or resolved_url or f"https://www.takealot.com/x/PLID{canonical_plid}"

        return TakealotCollectRequest(
            takealot_id=f"PLID{canonical_plid}",
            takealot_url=canonical_url,
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
    def fetch_and_save_by_plid(cls, plid_or_url: str, db: Session, custom_url: Optional[str] = None) -> List[Product]:
        req = cls.fetch_product_by_plid(plid_or_url, custom_url=custom_url)
        return cls.save_collected_product(db, req)
