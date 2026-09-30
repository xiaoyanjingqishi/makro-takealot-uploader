import re
import json
import logging
import requests
from typing import Optional, Dict, Any, Tuple
from .translation_service import TranslationService

logger = logging.getLogger(__name__)

class MakroScraperService:
    """
    负责解析 Makro 商品链接 / FSN 编号，并抓取官方商品元数据、前台在售价格与竞争卖家情报
    """

    @classmethod
    def extract_identifiers(cls, input_str: str) -> Tuple[Optional[str], Optional[str]]:
        """
        智能解析输入文本 (可能是 URL 或纯 FSN / Item ID)，同时提取 16位 FSN (pid) 与 Item ID (itm...)
        返回 (fsn, item_id)
        
        常见形态:
          1. 前台标准带参 URL:
             https://www.makro.co.za/.../p/itmdda5c11c09523?pid=GSPHPVTNMFHDAWV4&lid=...
             -> fsn: GSPHPVTNMFHDAWV4, item_id: itmdda5c11c09523
          2. 纯 FSN:
             GSPHPVTNMFHDAWV4
             -> fsn: GSPHPVTNMFHDAWV4, item_id: None
          3. 纯 Item ID 或无 pid 的 URL:
             https://www.makro.co.za/p/itmdda5c11c09523
             -> fsn: None, item_id: itmdda5c11c09523
        """
        if not input_str:
            return None, None
        raw = input_str.strip()

        fsn = None
        item_id = None

        # 1. 优先提取 URL query 参数中的 pid / fsn / fsnSearch / productId (最高优先级)
        q_match = re.search(r'[?&](?:pid|fsn|fsnSearch|productId)=([A-Za-z0-9]{12,20})', raw, re.I)
        if q_match:
            fsn = q_match.group(1).upper()

        # 2. 从 URL 路径 /p/{ID} 中提取
        p_match = re.search(r'/p/([a-zA-Z0-9]+)', raw, re.I)
        if p_match:
            seg = p_match.group(1)
            if seg.lower().startswith("itm"):
                item_id = seg
            elif not fsn and len(seg) == 16:
                fsn = seg.upper()

        # 3. 纯 16 位输入判断
        if not fsn and re.fullmatch(r'[A-Za-z0-9]{16}', raw):
            if raw.lower().startswith("itm"):
                item_id = raw
            else:
                fsn = raw.upper()

        # 4. 任意文本中截取独立的 16 位大写英数代码 (非 itm 开头)
        if not fsn:
            any_match = re.search(r'\b([A-Z0-9]{16})\b', raw)
            if any_match and not any_match.group(1).lower().startswith("itm"):
                fsn = any_match.group(1).upper()

        return fsn, item_id

    @classmethod
    def extract_fsn(cls, input_str: str) -> Optional[str]:
        """向后兼容提取 16 位 FSN"""
        fsn, _ = cls.extract_identifiers(input_str)
        return fsn

    @staticmethod
    def format_canonical_makro_url(fsn: str, item_id: Optional[str] = None) -> str:
        """
        构造官方标准且可直达的 Makro 买家端商品链接
        格式: https://www.makro.co.za/-/p/{item_id or fsn}?pid={fsn}
        """
        target_id = item_id if item_id else fsn
        return f"https://www.makro.co.za/-/p/{target_id}?pid={fsn}"

    @classmethod
    def fetch_product_by_fsn_from_seller_api(
        cls,
        fsn: str,
        store: Any
    ) -> Optional[Dict[str, Any]]:
        """
        通过 Makro 卖家官方网关 searchProduct 接口高速获取商品官方建档参数
        GET /napi/listing/searchProduct?fsnSearch={FSN}&sellerId={seller_id}
        """
        url = f"https://seller.makro.co.za/napi/listing/searchProduct?fsnSearch={fsn}&sellerId={store.seller_id}"
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://seller.makro.co.za",
            "referer": "https://seller.makro.co.za/index.html",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest"
        }
        if store.fk_csrf_token:
            headers["fk-csrf-token"] = store.fk_csrf_token.strip()
        if store.cookie:
            headers["cookie"] = store.cookie.strip()
        if store.default_location_id:
            headers["x-location-id"] = store.default_location_id.strip()

        try:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                product_list = data.get("result", {}).get("productList", [])
                if product_list:
                    p = product_list[0]
                    detail = p.get("detail", {})
                    images = p.get("imagePaths", {})
                    # 选取最高分辨率图片
                    best_img = images.get("275x275") or images.get("200x200") or images.get("125x125") or images.get("100x100")
                    if best_img:
                        # 兼容带有固定规格子路径的放大替换
                        best_img = re.sub(r'/(?:100x100|125x125|200x200|275x275)/', '/original/', best_img)

                    title = p.get("title") or detail.get("Model Number") or fsn
                    title_zh = TranslationService.translate_title(title)

                    return {
                        "fsn": p.get("entityId", fsn),
                        "title": title,
                        "title_zh": title_zh,
                        "brand": detail.get("Brand") or store.default_brand or "Generic",
                        "vertical": p.get("vertical", "general"),
                        "image_url": best_img or "",
                        "model_number": detail.get("Model Number"),
                        "barcode": detail.get("Barcode") or detail.get("EAN"),
                        "already_selling": p.get("alreadySelling", False),
                        "detail": detail
                    }
            else:
                logger.warning(f"Makro searchProduct 接口返回状态码 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"调用 Makro searchProduct 异常: {e}")
        return None

    @classmethod
    def scrape_buyer_frontend(cls, url_or_fsn: str) -> Dict[str, Any]:
        """
        抓取 Makro 前台买家商城 (makro.co.za) 详情页获取当前售价、MRP 与竞争情报
        """
        fsn, item_id = cls.extract_identifiers(url_or_fsn)
        target_url = url_or_fsn if url_or_fsn.startswith("http") else (cls.format_canonical_makro_url(fsn, item_id) if fsn else "")
        if not target_url:
            return {"price": 0.0, "mrp": 0.0, "seller_name": "", "seller_count": 1, "image_url": "", "url": ""}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }

        price = 0.0
        mrp = 0.0
        seller_name = ""
        seller_count = 1
        image_url = ""

        try:
            resp = requests.get(target_url, headers=headers, timeout=12)
            if resp.status_code == 200:
                html = resp.text

                # 1. 深度解析 window.__INITIAL_STATE__
                state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>', html, re.S)
                if state_m:
                    try:
                        st = json.loads(state_m.group(1))
                        ctx = st.get("pageDataV4", {}).get("page", {}).get("pageData", {}).get("pageContext", {})
                        if ctx:
                            pricing = ctx.get("pricing", {})
                            if pricing:
                                if pricing.get("finalPrice") and isinstance(pricing["finalPrice"].get("value"), (int, float)):
                                    price = float(pricing["finalPrice"]["value"])
                                elif pricing.get("fsp"):
                                    fsp = float(pricing["fsp"])
                                    price = fsp / 100.0 if fsp > 5000 else fsp
                                
                                for p_item in pricing.get("prices", []):
                                    if p_item.get("priceType") == "MRP" and isinstance(p_item.get("value"), (int, float)):
                                        mrp = float(p_item["value"])
                            
                            tracking = ctx.get("trackingDataV2", {})
                            if tracking:
                                seller_name = tracking.get("sellerName", "")
                                seller_count = tracking.get("sellerCount", 1)

                            if ctx.get("imageUrl"):
                                image_url = ctx["imageUrl"].replace("{@width}", "400").replace("{@height}", "400").replace("{@quality}", "80")
                    except Exception as parse_e:
                        logger.debug(f"解析 __INITIAL_STATE__ 失败: {parse_e}")

                # 2. 尝试从 LD-JSON 结构化数据补充
                if price <= 0 or not image_url:
                    ld_json_matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
                    for block in ld_json_matches:
                        try:
                            d = json.loads(block.strip())
                            if isinstance(d, list):
                                d = next((x for x in d if x.get("@type") == "Product"), {})
                            if isinstance(d, dict) and d.get("@type") == "Product":
                                if not image_url and d.get("image"):
                                    image_url = d["image"]
                                offers = d.get("offers", {})
                                if isinstance(offers, list) and offers:
                                    offers = offers[0]
                                if isinstance(offers, dict) and "price" in offers and price <= 0:
                                    price = float(offers["price"])
                                    mrp = float(offers.get("highPrice", price * 1.5))
                                    break
                        except Exception:
                            pass

                # 3. 正则匹配页面价格兜底
                if price <= 0:
                    price_match = re.search(r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)
                    if price_match:
                        price = float(price_match.group(1))

                if mrp <= 0 and price > 0:
                    mrp = round(price * 1.5, 2)
        except Exception as e:
            logger.warning(f"抓取 Makro 前台价格异常: {e}")

        return {
            "price": price,
            "mrp": mrp,
            "seller_name": seller_name,
            "seller_count": seller_count,
            "image_url": image_url,
            "url": target_url
        }

    @classmethod
    def resolve_piggyback_product(
        cls,
        url_or_fsn: str,
        store: Any,
        client_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        综合解析入口：整合来自浏览器扩展的直传精确数据、官方卖家网关 searchProduct 与前台抓取
        """
        fsn, item_id = cls.extract_identifiers(url_or_fsn)
        
        # 允许优先采用客户端传递的真实 item_id 与 fsn
        if client_data:
            if not fsn and client_data.get("fsn"):
                fsn = str(client_data["fsn"]).strip().upper()
            if not item_id and client_data.get("item_id"):
                item_id = str(client_data["item_id"]).strip()

        if not fsn:
            raise ValueError(f"无法从输入中提取合法的 16 位 Makro FSN 编号 (例如 GSPHPVTNMFHDAWV4): '{url_or_fsn}'")

        # 1. 优先调用 searchProduct 官方网关获取官方类目、标题、品牌与图片
        official = cls.fetch_product_by_fsn_from_seller_api(fsn, store)

        # 2. 结合前台数据 (若客户端已直传，则直接采纳客户端数据，彻底避开服务端受限风控)
        client_price = float(client_data.get("price") or 0.0) if client_data else 0.0
        client_mrp = float(client_data.get("mrp") or 0.0) if client_data else 0.0
        client_img = (client_data.get("image_url") or "").strip() if client_data else ""
        client_seller = (client_data.get("seller_name") or "").strip() if client_data else ""
        client_seller_count = int(client_data.get("seller_count") or 1) if client_data else 1
        client_title = (client_data.get("title") or "").strip() if client_data else ""

        frontend_info = {}
        if client_price <= 0:
            frontend_info = cls.scrape_buyer_frontend(url_or_fsn)

        # 整合数据
        title = (official.get("title") if official else "") or client_title or f"Makro Product {fsn}"
        brand = (official.get("brand") if official else "") or "Generic"
        vertical = (official.get("vertical") if official else "") or "general"
        image_url = (official.get("image_url") if official else "") or client_img or frontend_info.get("image_url", "")
        model_number = (official.get("model_number") if official else "")
        barcode = (official.get("barcode") if official else "")
        title_zh = official.get("title_zh") if official else TranslationService.translate_title(title)

        price = client_price if client_price > 0 else (frontend_info.get("price") or 0.0)
        mrp = client_mrp if client_mrp > 0 else (frontend_info.get("mrp") or (round(price * 1.5, 2) if price > 0 else 0.0))
        seller_name = client_seller or frontend_info.get("seller_name", "")
        seller_count = client_seller_count if client_seller_count > 1 else (frontend_info.get("seller_count") or 1)

        makro_url = cls.format_canonical_makro_url(fsn, item_id)

        return {
            "makro_product_id": fsn,
            "item_id": item_id,
            "makro_url": makro_url,
            "title": title,
            "title_zh": title_zh,
            "brand": brand,
            "vertical": vertical,
            "image_url": image_url,
            "model_number": model_number,
            "barcode": barcode,
            "original_price": price,
            "original_mrp": mrp,
            "original_seller": seller_name,
            "seller_count": seller_count
        }
