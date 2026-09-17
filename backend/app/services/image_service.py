import io
import logging
from typing import Tuple, Optional
from PIL import Image, ImageOps, ImageFilter

logger = logging.getLogger(__name__)

def enhance_image_for_makro(
    image_bytes: bytes,
    min_resolution: int = 800,
    min_coverage: int = 350,
    max_dimension: int = 2500
) -> bytes:
    """
    针对 Makro / Flipkart 官方商品图规范执行全自动分辨率超分与画框合规优化：
    
    Makro 平台硬性要求：
    1. 整体分辨率不得低于 300x300 (建议 800x800 以上以激活前台放大镜体验)；
    2. 图像主体覆盖范围（排除空白区域）不得低于 240x240；
    
    本函数处理流程：
    1. 自动转换 CMYK/RGBA/带透明度图至标准 RGB 纯白背景；
    2. 纠正 EXIF 拍摄方向旋转；
    3. 智能检测主体非白底有效覆盖区域 (Bounding Box)；
    4. 若分辨率低于 300x300 或主体小于 240x240，执行无损超分缩放 (Lanczos 重采样)；
    5. 若属极端长条图 (如 88x436) 或边缘留白严重，自适应置入电商标准 1:1 白底画布；
    6. 施加自适应轻度 USM 锐化以保持边缘清晰；
    7. 输出经过优化的高品质 JPEG 图像二进制数据。
    """
    if not image_bytes:
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)

        # 1. 规范化颜色模式为 RGB (透明通道合成至纯白背景)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            mask = img.split()[-1] if "A" in img.mode else None
            bg.paste(img, (0, 0), mask=mask)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        orig_w, orig_h = img.size

        # 2. 探测真实商品主体的有效边界 (除去近白色背景)
        gray = img.convert("L")
        # 像素亮度低于 245 视为有效主体前景
        thresh = gray.point(lambda p: 255 if p < 245 else 0)
        bbox = thresh.getbbox()

        has_white_border = False
        if bbox:
            item_w = bbox[2] - bbox[0]
            item_h = bbox[3] - bbox[1]
            if item_w < orig_w * 0.96 or item_h < orig_h * 0.96:
                has_white_border = True
        else:
            bbox = (0, 0, orig_w, orig_h)
            item_w, item_h = orig_w, orig_h

        # 3. 判定是否需要增强
        # Makro 硬性红线: 整体尺寸 < 300x300 或 主体覆盖 < 240x240
        is_violating_makro = (orig_w < 300 or orig_h < 300 or item_w < 240 or item_h < 240)
        # 为保证高品质刊登与悬停放大，若短边小于 600 或长边小于 800 也一并增强
        is_suboptimal = (min(orig_w, orig_h) < 600 or max(orig_w, orig_h) < min_resolution)

        if not (is_violating_makro or is_suboptimal):
            # 已经符合高画质标准，直接以高质量 JPEG 导出返回
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=95, optimize=True)
            return out.getvalue()

        logger.info(
            f"检测到低分辨率/低覆盖图片 ({orig_w}x{orig_h}, 主体约 {item_w}x{item_h})，启动自动合规超分处理..."
        )

        # 4. 执行针对性超分
        # 场景 A: 存在空白边框且主体较小，或者属于极端长宽比
        aspect_ratio = float(max(orig_w, orig_h)) / max(1, min(orig_w, orig_h))
        
        if (has_white_border and (item_w < min_coverage or item_h < min_coverage or is_violating_makro)) or aspect_ratio > 2.5:
            # 裁剪主体并保留适量安全内边距
            pad = max(8, int(min(item_w, item_h) * 0.05))
            x1 = max(0, bbox[0] - pad)
            y1 = max(0, bbox[1] - pad)
            x2 = min(orig_w, bbox[2] + pad)
            y2 = min(orig_h, bbox[3] + pad)
            cropped_item = img.crop((x1, y1, x2, y2))
            cw, ch = cropped_item.size

            # 计算主体放大系数：确保宽和高均 >= min_coverage (默认 350 > 240)，且长边尽量达到 800~1200
            scale_coverage_w = float(min_coverage) / max(1, cw)
            scale_coverage_h = float(min_coverage) / max(1, ch)
            scale_main = float(min_resolution * 0.88) / max(cw, ch)
            scale = max(scale_coverage_w, scale_coverage_h, scale_main, 1.0)

            # 限制上限防止过大
            if max(cw * scale, ch * scale) > max_dimension:
                scale = float(max_dimension) / max(cw, ch)

            new_cw = max(min_coverage, int(round(cw * scale)))
            new_ch = max(min_coverage, int(round(ch * scale)))
            scaled_item = cropped_item.resize((new_cw, new_ch), Image.Resampling.LANCZOS)

            # 置入 1:1 电商标准纯白底画布中
            canvas_side = max(min_resolution, max(new_cw, new_ch) + 80)
            canvas = Image.new("RGB", (canvas_side, canvas_side), (255, 255, 255))
            pos_x = (canvas_side - new_cw) // 2
            pos_y = (canvas_side - new_ch) // 2
            canvas.paste(scaled_item, (pos_x, pos_y))
            result_img = canvas
        else:
            # 场景 B: 实景照片或全出血图，整体等比例超分
            scale_min = float(min_coverage) / max(1, min(orig_w, orig_h))
            scale_main = float(min_resolution) / max(1, max(orig_w, orig_h))
            scale = max(scale_min, scale_main, 1.0)

            if max(orig_w * scale, orig_h * scale) > max_dimension:
                scale = float(max_dimension) / max(orig_w, orig_h)

            new_w = max(min_coverage, int(round(orig_w * scale)))
            new_h = max(min_coverage, int(round(orig_h * scale)))
            result_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 5. 轻度 USM 锐化增强纹理与文字边缘，防止插值柔化
        result_img = result_img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=115, threshold=3))

        out = io.BytesIO()
        result_img.save(out, format="JPEG", quality=95, optimize=True)
        final_bytes = out.getvalue()

        logger.info(
            f"图片超分完成: 原始 {orig_w}x{orig_h} -> 增强后 {result_img.size[0]}x{result_img.size[1]} (字节数: {len(final_bytes)} B)"
        )
        return final_bytes

    except Exception as e:
        logger.warning(f"图片超分增强过程发生异常，回退使用原图: {e}")
        return image_bytes
