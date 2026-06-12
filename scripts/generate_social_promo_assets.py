from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"
EXPORT_DIR = ROOT / "exports" / "social"
BACKGROUND_PATH = EXPORT_DIR / "daypilot-social-bg-ai.png"
POSTER_PATH = EXPORT_DIR / "daypilot-xiaohongshu-poster.png"

GENERATED_BG_SOURCE = Path(
    r"C:\Users\lin\.codex\generated_images\019eb614-c7d7-7fe0-b0f6-f24cd2570e56"
    r"\ig_04a1c0eca97653f9016a2a84a4fe508193ae640d8eab0b8893.png"
)

LOGO_PATH = ASSET_DIR / "daypilot-logo.png"
TODAY_SCREENSHOT_PATH = ASSET_DIR / "daypilot-today-desktop.png"

W, H = 1080, 1350
INK = (31, 42, 51)
MUTED = (86, 99, 105)
BLUE = (39, 128, 184)
TEAL = (38, 136, 127)
AMBER = (219, 151, 59)
CREAM = (255, 250, 239)
PANEL = (255, 252, 246)


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = {
        "bold": [
            "C:/Windows/Fonts/Noto Sans SC Bold (TrueType).otf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ],
        "medium": [
            "C:/Windows/Fonts/Noto Sans SC Medium (TrueType).otf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ],
        "regular": [
            "C:/Windows/Fonts/Noto Sans SC (TrueType).otf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ],
    }
    for candidate in candidates.get(weight, candidates["regular"]):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def cover_resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    img = img.convert("RGBA")
    scale = max(size[0] / img.width, size[1] / img.height)
    resized = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def add_shadow(
    base: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    blur: int,
    alpha: int,
    offset: tuple[int, int] = (0, 14),
) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shifted = (box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1])
    draw.rounded_rectangle(shifted, radius=radius, fill=(33, 30, 24, alpha))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def text_size(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=text_font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    draw.text(xy, text, font=text_font, fill=fill)


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    max_width: int,
    line_gap: int,
) -> int:
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if text_size(draw, candidate, text_font)[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)

    y = xy[1]
    for line in lines:
        draw_text(draw, (xy[0], y), line, text_font, fill)
        y += text_size(draw, line, text_font)[1] + line_gap
    return y


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], label: str, accent: tuple[int, int, int]) -> int:
    f = font(27, "medium")
    tw, th = text_size(draw, label, f)
    x, y = xy
    box = (x, y, x + tw + 44, y + 54)
    draw.rounded_rectangle(box, radius=27, fill=(255, 255, 255, 226), outline=(*accent, 120), width=2)
    draw.ellipse((x + 18, y + 19, x + 34, y + 35), fill=accent)
    draw_text(draw, (x + 44, y + 10), label, f, INK)
    return box[2]


def paste_rounded(base: Image.Image, img: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    target_size = (box[2] - box[0], box[3] - box[1])
    image = cover_resize(img, target_size)
    base.paste(image, (box[0], box[1]), rounded_mask(target_size, radius))


def trim_dark_border(img: Image.Image, threshold: int = 24, padding: int = 4) -> Image.Image:
    gray = img.convert("L")
    mask = gray.point(lambda px: 255 if px > threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return img
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(img.width, bbox[2] + padding)
    bottom = min(img.height, bbox[3] + padding)
    return img.crop((left, top, right, bottom))


def draw_poster() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if GENERATED_BG_SOURCE.exists() and not BACKGROUND_PATH.exists():
        shutil.copy2(GENERATED_BG_SOURCE, BACKGROUND_PATH)

    if BACKGROUND_PATH.exists():
        bg = cover_resize(Image.open(BACKGROUND_PATH), (W, H))
    else:
        bg = Image.new("RGBA", (W, H), (247, 240, 226, 255))
        d = ImageDraw.Draw(bg)
        for y in range(H):
            mix = y / H
            color = (
                round(252 * (1 - mix) + 232 * mix),
                round(247 * (1 - mix) + 241 * mix),
                round(236 * (1 - mix) + 233 * mix),
                255,
            )
            d.line((0, y, W, y), fill=color)

    base = bg.convert("RGBA")
    wash = Image.new("RGBA", (W, H), (255, 249, 237, 108))
    base.alpha_composite(wash)
    top_fade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fade_draw = ImageDraw.Draw(top_fade)
    for y in range(0, 720):
        alpha = max(0, 170 - round(y * 0.18))
        fade_draw.line((0, y, W, y), fill=(255, 252, 244, alpha))
    base.alpha_composite(top_fade)

    draw = ImageDraw.Draw(base)

    logo = Image.open(LOGO_PATH).convert("RGBA").resize((92, 92), Image.Resampling.LANCZOS)
    draw.rounded_rectangle((70, 68, 422, 132), radius=32, fill=(255, 255, 255, 216), outline=(224, 210, 183, 160), width=2)
    base.alpha_composite(logo, (82, 54))
    draw_text(draw, (178, 82), "DayPilot", font(31, "bold"), BLUE)
    draw_text(draw, (178, 115), "AI 日用工作台", font(19, "medium"), MUTED)

    draw_text(draw, (72, 202), "别再把愿望", font(74, "bold"), INK)
    draw_text(draw, (72, 286), "塞进一天", font(74, "bold"), INK)
    draw_text(draw, (72, 382), "每天只定一个", font(48, "medium"), (38, 72, 84))
    draw_text(draw, (72, 443), "真正能交付的小目标", font(54, "bold"), TEAL)

    y = draw_wrapped(
        draw,
        (74, 532),
        "读取长期方向、项目进度和当天精力，把今天压成一件能推进、能检查、能复盘的事。",
        font(31, "regular"),
        (58, 68, 72),
        760,
        12,
    )

    px = 74
    py = y + 32
    px = pill(draw, (px, py), "今日目标", BLUE) + 18
    px = pill(draw, (px, py), "反馈修正", TEAL) + 18
    pill(draw, (px, py), "周五复盘", AMBER)

    card = (62, 762, 1018, 1272)
    add_shadow(base, card, radius=42, blur=28, alpha=62, offset=(0, 18))
    draw.rounded_rectangle(card, radius=42, fill=(*PANEL, 246), outline=(255, 255, 255, 230), width=3)

    screen = trim_dark_border(Image.open(TODAY_SCREENSHOT_PATH).convert("RGBA"))
    screen_box = (92, 800, 988, 1216)
    paste_rounded(base, screen, screen_box, radius=28)
    draw.rounded_rectangle(screen_box, radius=28, outline=(255, 255, 255, 210), width=3)

    bottom_y = 1241
    draw.rounded_rectangle((91, bottom_y, 989, bottom_y + 63), radius=31, fill=(27, 38, 44, 226))
    draw_text(draw, (126, bottom_y + 17), "本地优先", font(24, "medium"), (255, 253, 246))
    draw.ellipse((256, bottom_y + 25, 268, bottom_y + 37), fill=AMBER)
    draw_text(draw, (292, bottom_y + 17), "SOUL.md 记住上下文", font(24, "medium"), (255, 253, 246))
    draw.ellipse((595, bottom_y + 25, 607, bottom_y + 37), fill=TEAL)
    draw_text(draw, (631, bottom_y + 17), "目标 / check-in / 周报闭环", font(24, "medium"), (255, 253, 246))

    base.convert("RGB").save(POSTER_PATH, quality=96, optimize=True)
    print(POSTER_PATH)


if __name__ == "__main__":
    draw_poster()
