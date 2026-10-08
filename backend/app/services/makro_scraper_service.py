import re
import json
import logging
import requests
import threading
from typing import Optional, Dict, Any, Tuple
from .translation_service import TranslationService

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = requests
    HAS_CURL_CFFI = False

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
            resp = requests.get(url, headers=headers, timeout=20, proxies={"http": None, "https": None})
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

    _buyer_local = threading.local()

    @classmethod
    def get_buyer_session(cls) -> Any:
        session = getattr(cls._buyer_local, "session", None)
        if session is None:
            if HAS_CURL_CFFI:
                session = cffi_requests.Session(impersonate="chrome124")
            else:
                s = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                session = s
            cls._buyer_local.session = session
        return session

    @classmethod
    def reset_buyer_session(cls) -> None:
        cls._buyer_local.session = None

    @classmethod
    def scrape_buyer_frontend(cls, url_or_fsn: str) -> Dict[str, Any]:
        """
        强化抓取 Makro 前台买家商城 (makro.co.za) 详情页获取当前售价、MRP 与竞争情报
        具备 Chrome 124 浏览器指纹模拟 (curl_cffi)、国内清洁 IP 强制直连、长连接复用与深度多层级价格解析。
        """
        fsn, item_id = cls.extract_identifiers(url_or_fsn)
        
        candidate_urls = []
        if url_or_fsn.startswith("http"):
            candidate_urls.append(url_or_fsn)
        if fsn:
            canonical = cls.format_canonical_makro_url(fsn, item_id)
            if canonical not in candidate_urls:
                candidate_urls.append(canonical)
        if not candidate_urls:
            return {"price": 0.0, "mrp": 0.0, "seller_name": "", "seller_count": 0, "image_url": "", "title": "", "brand": "", "vertical": "", "item_id": "", "url": "", "blocked": False}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1"
        }

        price = 0.0
        mrp = 0.0
        seller_name = ""
        seller_count = 1
        image_url = ""
        title = ""
        brand = ""
        vertical = ""
        scraped_item_id = item_id or ""
        chosen_url = candidate_urls[0]
        blocked = False

        session = cls.get_buyer_session()

        for cur_url in candidate_urls:
            chosen_url = cur_url
            try:
                # 强化反爬：强制直连国内清洁 IP (proxies={"http": "", "https": ""})，绕过污染海外代理
                # 同时使用 curl_cffi 模拟 Chrome 124 真实 TLS (JA3/JA4) 与 HTTP/2
                if HAS_CURL_CFFI:
                    resp = session.get(
                        cur_url,
                        headers=headers,
                        timeout=15,
                        allow_redirects=True,
                        proxies={"http": "", "https": ""},
                        impersonate="chrome124"
                    )
                else:
                    resp = session.get(
                        cur_url,
                        headers=headers,
                        timeout=15,
                        allow_redirects=True,
                        proxies={"http": None, "https": None}
                    )

                if resp.status_code != 200:
                    continue

                html = resp.text
                if "/blocked" in resp.url or "<title>Are you a human?</title>" in html:
                    logger.warning(f"Makro 前台反爬阻断拦截: {cur_url}")
                    blocked = True
                    cls.reset_buyer_session()
                    # 遭遇 IP 级反爬阻断时即刻止损，禁止在同一 IP 上无谓轮询其他候选链接
                    break

                # 1. 深度解析 window.__INITIAL_STATE__
                state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>', html, re.S)
                if state_m:
                    try:
                        st = json.loads(state_m.group(1))
                        pageDataV4 = st.get("pageDataV4", {})
                        page = pageDataV4.get("page", {})
                        pageData = page.get("pageData", {})
                        ctx = pageData.get("pageContext", {}) or {}

                        if ctx.get("itemId"):
                            scraped_item_id = str(ctx["itemId"]).lower()
                        if ctx.get("titles"):
                            title = ctx["titles"].get("title") or ctx["titles"].get("subtitle") or ""
                        if ctx.get("imageUrl"):
                            image_url = ctx["imageUrl"].replace("{@width}", "400").replace("{@height}", "400").replace("{@quality}", "80")

                        # 竞争情报与在售状态
                        tracking = ctx.get("trackingDataV2", {}) or {}
                        if tracking:
                            seller_count = int(tracking.get("sellerCount") if tracking.get("sellerCount") is not None else 1)
                            seller_name = tracking.get("sellerName") or ("暂无在售卖家" if seller_count == 0 else "")
                            if tracking.get("brand"):
                                brand = tracking.get("brand")
                            if tracking.get("vertical"):
                                vertical = tracking.get("vertical")

                        # 1.1 从 ctx.pricing 提取价格
                        pricing = ctx.get("pricing")
                        if isinstance(pricing, dict):
                            final_p = pricing.get("finalPrice", {}) or {}
                            if final_p.get("decimalValue"):
                                try:
                                    clean_dec = re.sub(r'[^0-9.]', '', str(final_p["decimalValue"]).strip())
                                    if clean_dec:
                                        price = float(clean_dec)
                                except Exception:
                                    pass
                            if price <= 0 and isinstance(final_p.get("value"), (int, float)):
                                val = float(final_p["value"])
                                if val >= 5000 and pricing.get("fsp") == val:
                                    val = val / 100.0
                                price = val
                            elif price <= 0 and pricing.get("fsp"):
                                fsp = float(pricing["fsp"])
                                price = fsp / 100.0 if fsp >= 5000 else fsp

                            for p_item in pricing.get("prices", []):
                                ptype = p_item.get("priceType")
                                dec_val = p_item.get("decimalValue")
                                p_val = p_item.get("value")
                                if ptype == "MRP":
                                    if dec_val:
                                        try:
                                            clean_mrp = re.sub(r'[^0-9.]', '', str(dec_val).strip())
                                            if clean_mrp:
                                                mrp = float(clean_mrp)
                                        except Exception:
                                            pass
                                    if mrp <= 0 and isinstance(p_val, (int, float)):
                                        m_val = float(p_val)
                                        if m_val >= 5000 and pricing.get("mrp") == m_val:
                                            m_val = m_val / 100.0
                                        mrp = m_val
                                elif ptype in ["FSP", "SPECIAL_PRICE"] and price <= 0:
                                    if dec_val:
                                        try:
                                            clean_sp = re.sub(r'[^0-9.]', '', str(dec_val).strip())
                                            if clean_sp:
                                                price = float(clean_sp)
                                        except Exception:
                                            pass
                                    if price <= 0 and isinstance(p_val, (int, float)):
                                        price = float(p_val)

                        # 1.2 若价格仍为0，检查 ctx.minPrice
                        if price <= 0:
                            min_p = ctx.get("minPrice")
                            if isinstance(min_p, dict):
                                if min_p.get("decimalValue"):
                                    try:
                                        clean_mp = re.sub(r'[^0-9.]', '', str(min_p["decimalValue"]).strip())
                                        if clean_mp:
                                            price = float(clean_mp)
                                    except Exception:
                                        pass
                                if price <= 0 and isinstance(min_p.get("value"), (int, float)):
                                    val = float(min_p["value"])
                                    price = val / 100.0 if val >= 5000 else val

                        # 1.3 从 Redux slots 插件数据中查找变体专属价格
                        if price <= 0 and page.get("data"):
                            slots_data = page.get("data", {})
                            for s_key, widgets in slots_data.items():
                                if not isinstance(widgets, list):
                                    continue
                                for w in widgets:
                                    w_val = w.get("widget", {}).get("data", {})
                                    # 检查 COMPOSED_SWATCH
                                    swatch_prods = w_val.get("swatchComponent", {}).get("value", {}).get("products", {})
                                    if isinstance(swatch_prods, dict) and fsn in swatch_prods:
                                        target_pdata = swatch_prods[fsn]
                                        p_pricing = target_pdata.get("pricing", {})
                                        if isinstance(p_pricing, dict):
                                            f_p = p_pricing.get("finalPrice", {})
                                            if f_p.get("decimalValue"):
                                                price = float(re.sub(r'[^0-9.]', '', str(f_p["decimalValue"])))
                                            elif isinstance(f_p.get("value"), (int, float)):
                                                price = float(f_p["value"])
                                            if mrp <= 0 and p_pricing.get("mrp"):
                                                mrp = float(p_pricing["mrp"])
                                                if mrp >= 5000:
                                                    mrp = mrp / 100.0

                    except Exception as parse_e:
                        logger.debug(f"解析 Makro __INITIAL_STATE__ 异常: {parse_e}")

                # 2. 从 LD-JSON 结构化数据补充
                if price <= 0 or not image_url or not title:
                    ld_json_matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
                    for block in ld_json_matches:
                        try:
                            d = json.loads(block.strip())
                            if isinstance(d, list):
                                d = next((x for x in d if isinstance(x, dict) and x.get("@type") == "Product"), {})
                            if isinstance(d, dict) and d.get("@type") == "Product":
                                if not title and d.get("name"):
                                    title = str(d["name"]).strip()
                                if not brand and d.get("brand"):
                                    b_val = d["brand"]
                                    brand = b_val.get("name", "") if isinstance(b_val, dict) else str(b_val)
                                if not image_url and d.get("image"):
                                    image_url = str(d["image"]).strip()
                                offers = d.get("offers", {})
                                if isinstance(offers, list) and offers:
                                    offers = offers[0]
                                if isinstance(offers, dict) and offers.get("price") and price <= 0:
                                    price = float(str(offers["price"]).replace(",", ""))
                                    if mrp <= 0 and offers.get("highPrice"):
                                        mrp = float(str(offers["highPrice"]).replace(",", ""))
                        except Exception:
                            pass

                # 3. HTML 正则保底提取
                if price <= 0:
                    dec_m = re.search(r'"finalPrice"\s*:\s*\{[^}]*"decimalValue"\s*:\s*"([0-9.]+)"', html)
                    if dec_m:
                        price = float(dec_m.group(1))
                if price <= 0:
                    fsp_m = re.search(r'"fsp"\s*:\s*([0-9]+)', html)
                    if fsp_m:
                        val = float(fsp_m.group(1))
                        price = val / 100.0 if val >= 5000 else val

                if mrp <= 0 and price > 0:
                    mrp_m = re.search(r'"mrp"\s*:\s*([0-9]+)', html)
                    if mrp_m:
                        m_val = float(mrp_m.group(1))
                        mrp = m_val / 100.0 if m_val >= 5000 else m_val

                # 4. 价格单位归一化防踩坑 (分转元)
                if price >= 5000 and (price % 100 == 0):
                    price = round(price / 100.0, 2)
                if mrp >= 5000 and (mrp % 100 == 0):
                    mrp = round(mrp / 100.0, 2)

                if mrp <= 0 and price > 0:
                    mrp = round(price * 1.5, 2)

                # 若已成功获取到价格，跳出候选重试
                if price > 0:
                    break

            except Exception as req_err:
                logger.warning(f"请求 Makro 前台链接 {cur_url} 异常: {req_err}")
                cls.reset_buyer_session()

        return {
            "price": price,
            "mrp": mrp,
            "seller_name": seller_name,
            "seller_count": seller_count,
            "image_url": image_url,
            "title": title,
            "brand": brand,
            "vertical": vertical,
            "item_id": scraped_item_id,
            "url": chosen_url,
            "blocked": blocked
        }

    @classmethod
    def resolve_piggyback_product(
        cls,
        url_or_fsn: str,
        store: Any,
        client_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        综合解析入口：由后端直接访问 Makro 获取官方权威数据与前台在售数据
        """
        fsn, item_id = cls.extract_identifiers(url_or_fsn)
        
        # 提取客户端传递的辅助标识 (fsn / item_id / variant_name)
        if client_data:
            if not fsn and client_data.get("fsn"):
                fsn = str(client_data["fsn"]).strip().upper()
            if not item_id and client_data.get("item_id"):
                item_id = str(client_data["item_id"]).strip()

        if not fsn:
            raise ValueError(f"无法从输入中提取合法的 16 位 Makro FSN 编号 (例如 GSPHPVTNMFHDAWV4): '{url_or_fsn}'")

        # 1. 尝试调用 searchProduct 官方卖家网关获取官方类目、标题、品牌与高精图片
        official = cls.fetch_product_by_fsn_from_seller_api(fsn, store)

        # 2. 无论客户端传递何种参数，后端始终直接访问 Makro 前台获取买家真实售价、MRP 与 Buybox 情报
        canonical_target = cls.format_canonical_makro_url(fsn, item_id)
        frontend_info = cls.scrape_buyer_frontend(canonical_target)
        if (frontend_info.get("price") or 0.0) <= 0 and url_or_fsn.startswith("http") and url_or_fsn != canonical_target:
            # 备用：尝试原链接直接抓取
            alt_info = cls.scrape_buyer_frontend(url_or_fsn)
            if (alt_info.get("price") or 0.0) > 0:
                frontend_info = alt_info

        # 3. 整合权威数据
        final_item_id = item_id or frontend_info.get("item_id") or ""
        title = (official.get("title") if official else "") or frontend_info.get("title") or f"Makro Product {fsn}"
        brand = (official.get("brand") if official else "") or frontend_info.get("brand") or getattr(store, "default_brand", "Generic") or "Generic"
        vertical = (official.get("vertical") if official else "") or frontend_info.get("vertical") or "general"
        image_url = (official.get("image_url") if official else "") or frontend_info.get("image_url") or ""
        model_number = (official.get("model_number") if official else "")
        barcode = (official.get("barcode") if official else "")
        title_zh = official.get("title_zh") if official else TranslationService.translate_title(title)

        price = float(frontend_info.get("price") or 0.0)
        mrp = float(frontend_info.get("mrp") or (round(price * 1.5, 2) if price > 0 else 0.0))
        if price > 0 and mrp < price:
            mrp = round(price * 1.5, 2)
            
        raw_seller_count = frontend_info.get("seller_count")
        seller_count = int(raw_seller_count) if raw_seller_count is not None else 1
        seller_name = frontend_info.get("seller_name") or ("暂无在售卖家" if seller_count == 0 else "")

        makro_url = cls.format_canonical_makro_url(fsn, final_item_id)

        return {
            "makro_product_id": fsn,
            "item_id": final_item_id,
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
