import re
import json
import logging
import requests
from typing import Optional, Dict, Any, Tuple
from .translation_service import TranslationService

logger = logging.getLogger(__name__)

class MakroScraperService:
    """
    负责解析 Makro 商品链接 / FSN 编号，并抓取官方商品元数据与前台在售价格
    """

    @staticmethod
    def extract_fsn(input_str: str) -> Optional[str]:
        """
        从输入文本 (可能是 URL 或纯 FSN 编号) 中智能提取 16 位 FSN / Product ID
        常见形态:
          1. 纯 FSN: GSPHPVTNMFHDAWV4, PMPHAF9GAFSFWMHM
          2. 前台 URL: https://www.makro.co.za/.../p/GSPHPVTNMFHDAWV4
          3. 参数: ?pid=GSPHPVTNMFHDAWV4 或 &fsn=GSPHPVTNMFHDAWV4
        """
        if not input_str:
            return None
        raw = input_str.strip()

        # 1. 尝试直接正则匹配 16 位大写英数 FSN
        # 常见形态为 16 位大写字母加数字组合
        if re.fullmatch(r'[A-Za-z0-9]{16}', raw):
            return raw.upper()

        # 2. 从 URL /p/{FSN} 中提取
        p_match = re.search(r'/p/([A-Za-z0-9]{16})', raw, re.I)
        if p_match:
            return p_match.group(1).upper()

        # 3. 从 URL query 参数中提取 (pid=..., fsn=..., fsnSearch=...)
        q_match = re.search(r'[?&](?:pid|fsn|fsnSearch|productId)=([A-Za-z0-9]{16})', raw, re.I)
        if q_match:
            return q_match.group(1).upper()

        # 4. 文本内任意 16 位有效标识提取
        any_match = re.search(r'\b([A-Z0-9]{16})\b', raw)
        if any_match:
            return any_match.group(1).upper()

        return None

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
                        # 尝试将 275x275 放大为原图高清链接
                        best_img = re.sub(r'/(?:100x100|125x125|200x200|275x275)/', '/original/', best_img)

                    title = p.get("title") or detail.get("Model Number") or fsn
                    title_zh = TranslationService.translate_title(title)

                    return {
                        "fsn": p.get("entityId", fsn),
                        "title": title,
                        "title_zh": title_zh,
                        "brand": detail.get("Brand") or store.default_brand or "Generic",
                        "vertical": p.get("vertical", "general"),
                        "image_url": best_img,
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
        抓取 Makro 前台买家商城 (makro.co.za) 详情页获取当前售价与 MRP
        """
        fsn = cls.extract_fsn(url_or_fsn)
        target_url = url_or_fsn if url_or_fsn.startswith("http") else (f"https://www.makro.co.za/p/{fsn}" if fsn else "")
        if not target_url:
            return {"price": 0.0, "mrp": 0.0}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }

        price = 0.0
        mrp = 0.0
        try:
            resp = requests.get(target_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                # 尝试从 LD-JSON 结构化数据提取
                ld_json_matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
                for block in ld_json_matches:
                    try:
                        d = json.loads(block.strip())
                        offers = d.get("offers", {})
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict) and "price" in offers:
                            price = float(offers["price"])
                            mrp = float(offers.get("highPrice", price * 1.5))
                            break
                    except Exception:
                        pass

                # 正则匹配页面价格 (如 R 499.00 / R499 / "price": 499)
                if price <= 0:
                    price_match = re.search(r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)
                    if price_match:
                        price = float(price_match.group(1))

                if mrp <= 0 and price > 0:
                    mrp_match = re.search(r'"wasPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)
                    if mrp_match:
                        mrp = float(mrp_match.group(1))
                    else:
                        mrp = round(price * 1.5, 2)
        except Exception as e:
            logger.warning(f"抓取 Makro 前台价格异常: {e}")

        return {"price": price, "mrp": mrp, "url": target_url}

    @classmethod
    def resolve_piggyback_product(
        cls,
        url_or_fsn: str,
        store: Any
    ) -> Dict[str, Any]:
        """
        综合解析入口：优先从官方卖家网关 searchProduct 获取权威参数，结合前台抓取价格
        """
        fsn = cls.extract_fsn(url_or_fsn)
        if not fsn:
            raise ValueError(f"无法从输入中提取合法的 16 位 Makro FSN 编号: '{url_or_fsn}'")

        # 1. 优先调用 searchProduct 官方网关
        official = cls.fetch_product_by_fsn_from_seller_api(fsn, store)

        # 2. 尝试抓取前台价格
        frontend_info = cls.scrape_buyer_frontend(url_or_fsn)

        # 整合数据
        title = (official.get("title") if official else "") or f"Makro Product {fsn}"
        brand = (official.get("brand") if official else "") or "Generic"
        vertical = (official.get("vertical") if official else "") or "general"
        image_url = (official.get("image_url") if official else "")
        model_number = (official.get("model_number") if official else "")
        barcode = (official.get("barcode") if official else "")
        title_zh = official.get("title_zh") if official else TranslationService.translate_title(title)

        price = frontend_info.get("price") or 0.0
        mrp = frontend_info.get("mrp") or (round(price * 1.5, 2) if price > 0 else 0.0)

        makro_url = frontend_info.get("url") or f"https://www.makro.co.za/p/{fsn}"

        return {
            "makro_product_id": fsn,
            "makro_url": makro_url,
            "title": title,
            "title_zh": title_zh,
            "brand": brand,
            "vertical": vertical,
            "image_url": image_url,
            "model_number": model_number,
            "barcode": barcode,
            "original_price": price,
            "original_mrp": mrp
        }
