import os
import time
import json
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple
from ..config import settings
from ..models.setting import SystemSetting
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class MakroClient:
    """
    Makro (Flipkart SaaS) 卖家中心逆向协议驱动客户端
    """
    BASE_URL = "https://seller.makro.co.za"

    def __init__(self, seller_id: str = None, csrf_token: str = None, cookie: str = None):
        self.seller_id = seller_id or settings.DEFAULT_SELLER_ID
        self.csrf_token = csrf_token or settings.DEFAULT_FK_CSRF_TOKEN
        self.cookie = cookie or ""
        
        self.session = requests.Session()
        self._setup_headers()

    @classmethod
    def from_db(cls, db: Session):
        """从数据库读取最新的店铺配置和凭据实例化"""
        s_seller = db.query(SystemSetting).filter(SystemSetting.key == "seller_id").first()
        s_csrf = db.query(SystemSetting).filter(SystemSetting.key == "fk_csrf_token").first()
        s_cookie = db.query(SystemSetting).filter(SystemSetting.key == "cookie").first()

        seller_id = s_seller.value if s_seller and s_seller.value else settings.DEFAULT_SELLER_ID
        csrf_token = s_csrf.value if s_csrf and s_csrf.value else settings.DEFAULT_FK_CSRF_TOKEN
        cookie = s_cookie.value if s_cookie and s_cookie.value else ""
        return cls(seller_id=seller_id, csrf_token=csrf_token, cookie=cookie)

    def _setup_headers(self):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/index.html",
            "Sec-Ch-Ua": '"Chromium";v="152", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "fk-csrf-token": self.csrf_token
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        self.session.headers.update(headers)

    def get_vertical_definition(self, vertical: str = "bath_towel") -> Dict[str, Any]:
        """获取类目完整属性定义与枚举限制"""
        url = f"{self.BASE_URL}/napi/createProductV2/verticalDefinition?verticals={vertical}&sellerId={self.seller_id}"
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def get_variant_definition(self, vertical: str = "bath_towel") -> Dict[str, Any]:
        """获取类目的变体维度定义 (如 size, brand_colour, pack_of)"""
        url = f"{self.BASE_URL}/napi/createProductV2/v1/fetch-variant-definition?vertical={vertical}"
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def check_brand_approval(self, vertical: str, brand: str) -> bool:
        """检查品牌是否在允许售卖列表"""
        url = f"{self.BASE_URL}/napi/regulation/approvalStatus?vertical={vertical}&brand={brand}&sellerId={self.seller_id}"
        resp = self.session.get(url, timeout=15)
        if resp.status_code == 200:
            return True
        return False

    def create_draft(self, vertical: str = "bath_towel", brand: str = "Beishi", vid: Optional[str] = None) -> Dict[str, Any]:
        """
        步骤 1: 初始化 Listing 草稿
        返回包含 requestId, txnId, reqId, version 等信息的字典
        """
        from .vertical_service import VerticalService
        valid_vertical, resolved_vid = VerticalService.resolve_vertical(vertical)
        actual_vid = vid or resolved_vid
        actual_vertical = valid_vertical

        url = f"{self.BASE_URL}/napi/createProductV2/create?vid={actual_vid}"
        payload = {
            "vertical": actual_vertical,
            "listingRequestEntity": {"listingAttributes": {}},
            "catalogRequestEntity": {
                "catalogAttributes": {
                    "brand": [{"value": brand, "qualifier": None}]
                },
                "images": {}
            },
            "context": "PRODUCT_LISTING_CREATION"
        }
        resp = self.session.post(url, json=payload, timeout=45)
        resp.raise_for_status()
        return resp.json()

    def get_vertical_definition(self, vertical: str) -> dict:
        """获取垂直类目的官方属性定义 (包含必填项与允许值)"""
        url = f"{self.BASE_URL}/napi/createProductV2/verticalDefinition?verticals={vertical}&sellerId={self.seller_id}"
        resp = self.session.get(url, timeout=45)
        resp.raise_for_status()
        return resp.json()

    def upload_image(self, file_bytes: bytes, filename: str, vertical: str, request_id: str) -> Optional[str]:
        """
        步骤 2: 将图片上传至 Makro 官方静态 CDN
        返回资产 URL，如: https://www.makro.co.za/asset/cms/...
        """
        url = f"{self.BASE_URL}/napi/scf/uploadImage?vertical={vertical}&requestId={request_id}"
        
        # 临时移除 application/json 请求头，以便 requests 自动设置带 boundary 的 multipart/form-data
        headers = dict(self.session.headers)
        headers.pop("Content-Type", None)
        
        files = {
            "file": (filename, file_bytes, "image/jpeg")
        }
        
        resp = requests.post(url, headers=headers, files=files, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        
        content_list = data.get("contentDataList", [])
        if content_list and len(content_list) > 0:
            return content_list[0].get("path")
        return None

    def upload_image_from_url(self, image_url: str, vertical: str, request_id: str) -> Optional[str]:
        """下载 Takealot 远程原图并直接中转上传至 Makro CDN"""
        try:
            download_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
            }
            img_resp = requests.get(image_url, headers=download_headers, timeout=30)
            img_resp.raise_for_status()
            
            filename = os.path.basename(image_url.split("?")[0]) or f"img_{int(time.time()*1000)}.jpg"
            if not filename.endswith((".jpg", ".jpeg", ".png")):
                filename += ".jpg"
                
            return self.upload_image(img_resp.content, filename, vertical, request_id)
        except Exception as e:
            logger.error(f"下载或上传图片失败: {image_url}, 错误: {e}")
            return None

    def fetch_title_preview(self, vertical: str, product_attributes: Dict[str, Any]) -> str:
        """生成平台标准标题预览"""
        url = f"{self.BASE_URL}/napi/createProductV2/fetch-product-title-preview"
        payload = {
            "vertical": vertical,
            "productAttributes": product_attributes
        }
        try:
            resp = self.session.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("title", "")
        except Exception as e:
            logger.warning(f"获取标题预览失败: {e}")
        return ""

    def submit_product(self, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
        """
        步骤 3: 提交最终上品数据审核
        返回: (is_success, error_details, response_message)
        """
        url = f"{self.BASE_URL}/napi/createProductV2/submit"
        resp = self.session.post(url, json=payload, timeout=45)
        
        if resp.status_code != 200:
            return False, {}, f"HTTP {resp.status_code}: {resp.text}"

        res_data = resp.json()
        error_details = res_data.get("errorDetails", {})
        
        global_errs = error_details.get("globalErrors", [])
        catalog_errs = error_details.get("catalogErrors", {})
        listing_errs = error_details.get("listingErrors", {})
        
        manual_val_errs = catalog_errs.get("manualValidationErrors", {}).get("globalErrors", [])
        system_val_errs = catalog_errs.get("systemValidationErrors", {}).get("globalErrors", [])
        listing_val_errs = listing_errs.get("systemValidationErrors", {}).get("globalErrors", [])

        all_errors = global_errs + manual_val_errs + system_val_errs + listing_val_errs
        
        if len(all_errors) == 0:
            return True, error_details, "发布成功，已提交平台审核 (QC_PENDING)"
        else:
            err_msg = "; ".join([str(e) for e in all_errors])
            return False, error_details, f"提交包含校验错误: {err_msg}"
