# -*- coding: utf-8 -*-
"""
订单打包防混标签 PDF 生成服务 (40mm × 30mm 热敏纸规格)
包含:
1. 高对比度 Code128 矢量条形码
2. 订单编号文本 (加粗等宽，便于肉眼核验)
3. 出单时间 (严格格式化为 YYYY-MMDD-HHmm，如 2026-1014-1635)
支持单张 PDF 导出与多单合并连打多页 PDF 导出。
"""

import io
import logging
from typing import List, Optional
from datetime import datetime
import fitz  # PyMuPDF
from barcode import Code128
from barcode.writer import ImageWriter

from ..models.makro_order import MakroOrder

logger = logging.getLogger(__name__)

# 40mm × 30mm 转换为 PDF 点数 (1 inch = 25.4mm = 72 points)
LABEL_WIDTH_MM = 40.0
LABEL_HEIGHT_MM = 30.0
LABEL_WIDTH_PT = LABEL_WIDTH_MM * 72.0 / 25.4   # ~113.39 pt
LABEL_HEIGHT_PT = LABEL_HEIGHT_MM * 72.0 / 25.4  # ~85.04 pt


class OrderLabelService:

    @classmethod
    def _format_order_time(cls, dt: Optional[datetime]) -> str:
        """
        格式化出单时间为 2026-1014-1635 形式 (YYYY-MMDD-HHmm)
        """
        if not dt:
            dt = datetime.now()
        return dt.strftime("%Y-%m%d-%H%M")

    @classmethod
    def _generate_barcode_image_bytes(cls, text: str) -> bytes:
        """
        生成高清晰度 Code128 条形码图片二进制数据 (无文字，纯条码)
        """
        clean_text = (text or "").strip()
        code = Code128(clean_text, writer=ImageWriter())
        buf = io.BytesIO()
        code.write(
            buf,
            options={
                "write_text": False,
                "quiet_zone": 1.0,
                "module_height": 14.0,
                "dpi": 300
            }
        )
        return buf.getvalue()

    @classmethod
    def _draw_label_page(cls, doc: fitz.Document, order_id: str, order_date: Optional[datetime]):
        """
        在 PDF 文档中新增一页标准的 40×30mm 打包标签
        """
        page = doc.new_page(width=LABEL_WIDTH_PT, height=LABEL_HEIGHT_PT)

        # 1. 顶部绘制高对比度 Code128 条形码 (Y: 4 ~ 45 pt)
        try:
            img_bytes = cls._generate_barcode_image_bytes(order_id)
            barcode_rect = fitz.Rect(4, 4, LABEL_WIDTH_PT - 4, 45)
            page.insert_image(barcode_rect, stream=img_bytes)
        except Exception as e:
            logger.error(f"绘制订单条形码异常 [{order_id}]: {e}")

        # 2. 中间绘制订单号大字 (Y: 47 ~ 63 pt，字体大小自适应)
        order_text_clean = (order_id or "").strip()
        text_rect = fitz.Rect(2, 47, LABEL_WIDTH_PT - 2, 63)
        font_size = 7.5 if len(order_text_clean) > 18 else 8.5
        page.insert_textbox(
            text_rect,
            order_text_clean,
            fontsize=font_size,
            fontname="helv",
            align=fitz.TEXT_ALIGN_CENTER
        )

        # 3. 底部绘制出单时间 (Y: 64 ~ 81 pt，格式: 2026-1014-1635)
        dt_str = cls._format_order_time(order_date)
        dt_rect = fitz.Rect(2, 64, LABEL_WIDTH_PT - 2, 81)
        page.insert_textbox(
            dt_rect,
            dt_str,
            fontsize=8.5,
            fontname="helv",
            align=fitz.TEXT_ALIGN_CENTER
        )

    @classmethod
    def generate_single_label_pdf(cls, order: MakroOrder) -> bytes:
        """
        生成单笔订单的 40×30mm PDF 标签
        """
        doc = fitz.open()
        cls._draw_label_page(
            doc=doc,
            order_id=order.order_id,
            order_date=order.order_date or order.created_at
        )
        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes

    @classmethod
    def generate_batch_labels_pdf(cls, orders: List[MakroOrder]) -> bytes:
        """
        批量生成多笔订单的 40×30mm PDF 标签 (多页合并在一个文件内，适配连续打印)
        """
        doc = fitz.open()
        for order in orders:
            cls._draw_label_page(
                doc=doc,
                order_id=order.order_id,
                order_date=order.order_date or order.created_at
            )
        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes
