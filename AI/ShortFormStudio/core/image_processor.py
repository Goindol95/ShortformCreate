import os
from typing import Tuple, Optional
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageFont

FONT_PATH = "C:\\Windows\\Fonts\\malgun.ttf"
BOLD_FONT_PATH = "C:\\Windows\\Fonts\\malgunbd.ttf"

def get_font(size: int, bold: bool = True):
    font_file = BOLD_FONT_PATH if bold and os.path.exists(BOLD_FONT_PATH) else FONT_PATH
    if os.path.exists(font_file):
        try:
            return ImageFont.truetype(font_file, size)
        except Exception:
            pass
    return ImageFont.load_default()

def apply_image_adjustments(
    image: Image.Image,
    rotation: int = 0,
    flip_h: bool = False,
    brightness: float = 1.0,
    contrast: float = 1.0
) -> Image.Image:
    """Applies rotation, horizontal flip, brightness and contrast."""
    img = image.copy()
    
    if rotation in (90, 180, 270):
        # PIL rotate is counter-clockwise, transpose is clockwise
        if rotation == 90:
            img = img.transpose(Image.Transpose.ROTATE_270)
        elif rotation == 180:
            img = img.transpose(Image.Transpose.ROTATE_180)
        elif rotation == 270:
            img = img.transpose(Image.Transpose.ROTATE_90)
            
    if flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        
    if brightness != 1.0:
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(brightness)
        
    if contrast != 1.0:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(contrast)
        
    return img

def resize_to_shortform(
    image: Image.Image,
    target_size: Tuple[int, int] = (1080, 1920),
    mode: str = "blur",  # "blur", "crop", "fit"
    bg_color: Tuple[int, int, int] = (0, 0, 0)
) -> Image.Image:
    """
    Resizes image to target vertical short-form dimensions (e.g. 1080x1920).
    Modes:
      - 'blur': Blurred background filling the remaining area.
      - 'crop': Center cropped to 9:16 aspect ratio.
      - 'fit': Preserved aspect ratio with solid color padding (letterbox).
    """
    tw, th = target_size
    img = image.convert("RGB")
    ow, oh = img.size
    
    if mode == "crop":
        target_ratio = tw / th
        orig_ratio = ow / oh
        
        if orig_ratio > target_ratio:
            # Image is wider: crop width
            new_crop_w = int(oh * target_ratio)
            left = (ow - new_crop_w) // 2
            box = (left, 0, left + new_crop_w, oh)
        else:
            # Image is taller: crop height
            new_crop_h = int(ow / target_ratio)
            top = (oh - new_crop_h) // 2
            box = (0, top, ow, top + new_crop_h)
            
        cropped = img.crop(box)
        return cropped.resize((tw, th), Image.Resampling.LANCZOS)
        
    elif mode == "fit":
        # Letterbox with solid color
        scale = min(tw / ow, th / oh)
        nw, nh = max(1, int(ow * scale)), max(1, int(oh * scale))
        resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
        
        canvas = Image.new("RGB", (tw, th), color=bg_color)
        pos_x = (tw - nw) // 2
        pos_y = (th - nh) // 2
        canvas.paste(resized, (pos_x, pos_y))
        return canvas
        
    else:  # "blur"
        # Background: fill & blur
        bg_scale = max(tw / ow, th / oh)
        bw, bh = max(tw, int(ow * bg_scale)), max(th, int(oh * bg_scale))
        bg_img = img.resize((bw, bh), Image.Resampling.BILINEAR)
        
        # Center crop background to target size
        bg_left = (bw - tw) // 2
        bg_top = (bh - th) // 2
        bg_canvas = bg_img.crop((bg_left, bg_top, bg_left + tw, bg_top + th))
        # Blur and slightly darken background
        blur_rad = max(6, int(35 * (tw / 1080.0)))
        bg_canvas = bg_canvas.filter(ImageFilter.GaussianBlur(radius=blur_rad))
        bg_canvas = ImageEnhance.Brightness(bg_canvas).enhance(0.75)
        
        # Foreground: scale to fit
        scale = min(tw / ow, th / oh)
        nw, nh = max(1, int(ow * scale)), max(1, int(oh * scale))
        fg_resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
        
        pos_x = (tw - nw) // 2
        pos_y = (th - nh) // 2
        bg_canvas.paste(fg_resized, (pos_x, pos_y))
        return bg_canvas

def create_text_overlay(
    target_size: Tuple[int, int],
    text: str,
    position: str = "bottom",  # "top", "center", "bottom"
    font_size: int = 56,
    text_color: str = "#FFFFFF",
    bg_style: str = "dark_box",  # "dark_box", "yellow_box", "outline", "none"
    font_bold: bool = True
) -> Image.Image:
    """
    Creates an RGBA transparent image of target_size with styled text overlay.
    Can be pasted directly on PIL image or overlayed onto video frames/ffmpeg.
    """
    tw, th = target_size
    overlay = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    if not text.strip():
        return overlay

    scale_factor = tw / 1080.0
    eff_font_size = max(11, int(font_size * scale_factor))
    font = get_font(eff_font_size, bold=font_bold)
    draw = ImageDraw.Draw(overlay)
    
    # Calculate bounding box for multiline text
    lines = text.strip().splitlines()
    line_bboxes = []
    total_h = 0
    max_w = 0
    line_spacing = max(2, int(eff_font_size * 0.3))
    
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        line_bboxes.append((line, lw, lh))
        max_w = max(max_w, lw)
        total_h += lh
    total_h += (len(lines) - 1) * line_spacing
    
    # Determine Y position
    padding_y = max(6, int(20 * scale_factor))
    padding_x = max(10, int(35 * scale_factor))
    radius = max(5, int(18 * scale_factor))
    box_w = min(tw - int(60 * scale_factor), max_w + padding_x * 2)
    box_h = total_h + padding_y * 2
    
    if position == "top":
        base_y = int(th * 0.12)
    elif position == "center":
        base_y = (th - box_h) // 2
    else:  # "bottom"
        base_y = int(th * 0.75)
        
    box_x = (tw - box_w) // 2
    
    # Draw Background Pill / Box
    if bg_style == "dark_box":
        draw.rounded_rectangle(
            [box_x, base_y, box_x + box_w, base_y + box_h],
            radius=radius,
            fill=(0, 0, 0, 180)
        )
    elif bg_style == "yellow_box":
        draw.rounded_rectangle(
            [box_x, base_y, box_x + box_w, base_y + box_h],
            radius=radius,
            fill=(255, 215, 0, 230)
        )
    elif bg_style == "white_box":
        draw.rounded_rectangle(
            [box_x, base_y, box_x + box_w, base_y + box_h],
            radius=radius,
            fill=(255, 255, 255, 230)
        )
        
    # Draw Lines of Text
    cur_y = base_y + padding_y
    for line, lw, lh in line_bboxes:
        line_x = (tw - lw) // 2
        
        if bg_style == "outline":
            # Draw strong outline
            stroke_width = max(2, font_size // 14)
            draw.text(
                (line_x, cur_y),
                line,
                font=font,
                fill=text_color,
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 255)
            )
        else:
            fill_color = "#000000" if bg_style in ("yellow_box", "white_box") and text_color == "#FFFFFF" else text_color
            draw.text(
                (line_x, cur_y),
                line,
                font=font,
                fill=fill_color
            )
            
        cur_y += lh + line_spacing
        
    return overlay

def process_single_image(
    input_path: str,
    output_path: str,
    target_size: Tuple[int, int] = (1080, 1920),
    resize_mode: str = "blur",
    rotation: int = 0,
    flip_h: bool = False,
    brightness: float = 1.0,
    contrast: float = 1.0,
    text: str = "",
    text_position: str = "bottom",
    font_size: int = 56,
    text_color: str = "#FFFFFF",
    bg_style: str = "dark_box"
) -> bool:
    """Full image processing and export pipeline."""
    try:
        with Image.open(input_path) as orig:
            # Step 1: Adjustments (rotate, flip, brightness, contrast)
            adj = apply_image_adjustments(orig, rotation, flip_h, brightness, contrast)
            # Step 2: 9:16 Resize
            short_img = resize_to_shortform(adj, target_size, resize_mode)
            # Step 3: Text Overlay
            if text.strip():
                txt_layer = create_text_overlay(
                    target_size=target_size,
                    text=text,
                    position=text_position,
                    font_size=font_size,
                    text_color=text_color,
                    bg_style=bg_style
                )
                short_img.paste(txt_layer, (0, 0), txt_layer)
            
            # Step 4: Save
            ext = os.path.splitext(output_path)[1].lower()
            if ext in ('.jpg', '.jpeg'):
                short_img.convert("RGB").save(output_path, "JPEG", quality=95)
            else:
                short_img.save(output_path, "PNG")
            return True
    except Exception as e:
        print(f"Error processing image: {e}")
        return False
