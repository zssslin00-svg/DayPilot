from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"
LOGO_PATH = ASSET_DIR / "daypilot-logo.png"
BANNER_PATH = ASSET_DIR / "daypilot-hero-banner.png"

IVORY = (248, 244, 234)
CREAM = (238, 226, 204)
SAND = (209, 186, 145)
GOLD = (206, 165, 82)
SAGE = (132, 151, 112)
BLUE = (74, 112, 139)
INK = (70, 78, 77)
WHITE = (255, 255, 255)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_vertical_gradient(img: Image.Image, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    draw = ImageDraw.Draw(img)
    width, height = img.size
    for y in range(height):
        mix = y / max(height - 1, 1)
        color = tuple(round(top[i] * (1 - mix) + bottom[i] * mix) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)


def ellipse_shadow(base: Image.Image, box: tuple[int, int, int, int], blur: int, alpha: int) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse(box, fill=(71, 61, 43, alpha))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def rounded_shadow(
    base: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    blur: int,
    alpha: int,
    offset: tuple[int, int] = (0, 12),
) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shifted = (box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1])
    draw.rounded_rectangle(shifted, radius=radius, fill=(84, 70, 45, alpha))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def line_with_glow(
    base: Image.Image,
    points: list[tuple[float, float]],
    color: tuple[int, int, int],
    width: int = 3,
    glow: int = 12,
    alpha: int = 170,
) -> None:
    glow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.line(points, fill=(*color, alpha), width=width + 6, joint="curve")
    base.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(glow)))
    draw = ImageDraw.Draw(base)
    draw.line(points, fill=(*color, 210), width=width, joint="curve")


def draw_check(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: tuple[int, int, int], width: int) -> None:
    pts = [(x, y + size * 0.52), (x + size * 0.35, y + size * 0.84), (x + size, y)]
    draw.line(pts, fill=color, width=width, joint="curve")


def draw_ai_core(base: Image.Image, cx: int, cy: int, r: int, scale: float = 1.0) -> None:
    draw = ImageDraw.Draw(base)
    ellipse_shadow(base, (cx - r + 10, cy - r + 22, cx + r + 10, cy + r + 22), int(14 * scale), 60)

    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i, alpha in enumerate([52, 38, 26]):
        grow = int(r * (0.34 + i * 0.22))
        gd.ellipse((cx - r - grow, cy - r - grow, cx + r + grow, cy + r + grow), fill=(151, 205, 236, alpha))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(int(14 * scale))))

    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(250, 248, 241, 255), outline=(231, 218, 195, 255), width=max(2, int(2 * scale)))
    draw.ellipse((cx - r + int(12 * scale), cy - r + int(12 * scale), cx + r - int(12 * scale), cy + r - int(12 * scale)), fill=(233, 241, 236, 255), outline=(188, 205, 189, 255), width=max(2, int(2 * scale)))

    for angle in range(0, 360, 45):
        a = math.radians(angle)
        start = (cx + math.cos(a) * r * 0.18, cy + math.sin(a) * r * 0.18)
        end = (cx + math.cos(a) * r * 0.64, cy + math.sin(a) * r * 0.64)
        draw.line([start, end], fill=(128, 157, 166, 165), width=max(2, int(3 * scale)))
        nx, ny = end
        draw.ellipse(
            (nx - int(5 * scale), ny - int(5 * scale), nx + int(5 * scale), ny + int(5 * scale)),
            fill=(255, 255, 255, 255),
            outline=(105, 141, 158, 210),
            width=max(1, int(1 * scale)),
        )

    pointer = [
        (cx, cy - int(r * 0.72)),
        (cx + int(r * 0.18), cy + int(r * 0.02)),
        (cx, cy + int(r * 0.72)),
        (cx - int(r * 0.18), cy + int(r * 0.02)),
    ]
    draw.polygon(pointer, fill=(84, 127, 151, 255))
    draw.line(pointer + [pointer[0]], fill=(52, 84, 103, 180), width=max(2, int(2 * scale)))
    draw_check(draw, cx - int(r * 0.36), cy + int(r * 0.03), int(r * 0.72), GOLD, max(4, int(7 * scale)))

    label_font = font(max(22, int(r * 0.42)), bold=True)
    text = "AI"
    bbox = draw.textbbox((0, 0), text, font=label_font)
    tx = cx - (bbox[2] - bbox[0]) / 2
    ty = cy - (bbox[3] - bbox[1]) / 2 - int(r * 0.04)
    draw.text((tx + 1, ty + 1), text, font=label_font, fill=(255, 255, 255, 170))
    draw.text((tx, ty), text, font=label_font, fill=(62, 88, 92, 235))


def draw_small_token(base: Image.Image, cx: int, cy: int, r: int, kind: str) -> None:
    draw = ImageDraw.Draw(base)
    ellipse_shadow(base, (cx - r + 4, cy - r + 8, cx + r + 4, cy + r + 8), max(6, r // 6), 44)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(252, 247, 236, 255), outline=(226, 211, 184, 255), width=max(2, r // 18))
    draw.ellipse((cx - r + 8, cy - r + 8, cx + r - 8, cy + r - 8), outline=(255, 255, 255, 230), width=max(2, r // 20))

    if kind == "calendar":
        w, h = int(r * 1.05), int(r * 0.95)
        x0, y0 = cx - w // 2, cy - h // 2
        draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=r // 7, fill=(244, 239, 226), outline=(200, 183, 153), width=2)
        draw.rounded_rectangle((x0, y0, x0 + w, y0 + h // 4), radius=r // 7, fill=BLUE)
        for dx in (-w // 4, w // 4):
            draw.rounded_rectangle((cx + dx - 4, y0 - 8, cx + dx + 4, y0 + 10), radius=3, fill=GOLD)
        for row in range(3):
            for col in range(3):
                px = x0 + int(w * (0.25 + col * 0.25))
                py = y0 + int(h * (0.43 + row * 0.2))
                draw.rounded_rectangle((px - 5, py - 5, px + 5, py + 5), radius=2, fill=(211, 196, 166))
        draw.ellipse((cx + r // 4, cy + r // 5, cx + r // 2, cy + r // 2), fill=SAGE)
        draw_check(draw, cx + r // 3, cy + r // 4, r // 5, WHITE, 3)
    elif kind == "target":
        for i, color in enumerate([SAGE, (238, 233, 219), SAGE, (248, 244, 234)]):
            rr = int(r * (0.68 - i * 0.16))
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=color)
        draw.line((cx - r // 2, cy + r // 2, cx + r // 2, cy - r // 2), fill=GOLD, width=max(3, r // 10))
        draw.polygon([(cx + r // 2, cy - r // 2), (cx + r // 2, cy - r // 5), (cx + r * 3 // 4, cy - r * 3 // 4)], fill=GOLD)
    elif kind == "checklist":
        for i, color in enumerate([SAGE, SAGE, BLUE]):
            yy = cy - r // 3 + i * r // 3
            draw.ellipse((cx - r // 2, yy - 6, cx - r // 2 + 12, yy + 6), fill=color)
            draw.line((cx - r // 5, yy, cx + r // 2, yy), fill=(160, 139, 105), width=2)
        draw.ellipse((cx + r // 4, cy + r // 4, cx + r * 3 // 5, cy + r * 3 // 5), fill=BLUE)
        draw_check(draw, cx + r // 3, cy + r // 3, r // 5, WHITE, 3)
    elif kind == "compass":
        for angle, color in [(270, BLUE), (90, (220, 207, 181)), (0, (186, 202, 191)), (180, (186, 202, 191))]:
            a = math.radians(angle)
            p1 = (cx + math.cos(a) * r * 0.75, cy + math.sin(a) * r * 0.75)
            p2 = (cx + math.cos(a + 0.45) * r * 0.16, cy + math.sin(a + 0.45) * r * 0.16)
            p3 = (cx + math.cos(a - 0.45) * r * 0.16, cy + math.sin(a - 0.45) * r * 0.16)
            draw.polygon([p1, p2, p3], fill=color)
        draw.ellipse((cx - r // 6, cy - r // 6, cx + r // 6, cy + r // 6), fill=(255, 250, 239), outline=SAND, width=3)
    elif kind == "home":
        roof = [(cx - r // 2, cy), (cx, cy - r // 2), (cx + r // 2, cy)]
        draw.polygon(roof, fill=SAGE)
        draw.rounded_rectangle((cx - r // 3, cy, cx + r // 3, cy + r // 2), radius=5, fill=(143, 160, 117), outline=(102, 122, 91), width=2)
        draw.rounded_rectangle((cx - r // 10, cy + r // 6, cx + r // 10, cy + r // 2), radius=3, fill=CREAM)


def create_logo() -> None:
    size = 1024
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw_vertical_gradient(img, (255, 254, 250), (247, 241, 229))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    ellipse_shadow(img, (86, 78, 938, 946), 34, 52)
    draw.ellipse((70, 54, 954, 938), fill=(246, 236, 216, 255), outline=(228, 208, 172, 255), width=5)
    draw.ellipse((104, 92, 920, 908), fill=(250, 243, 229, 255), outline=(255, 255, 255, 190), width=3)

    orbit = [(cx, cy - 318), (cx + 260, cy - 126), (cx + 228, cy + 212), (cx, cy + 318), (cx - 228, cy + 212), (cx - 260, cy - 126), (cx, cy - 318)]
    line_with_glow(img, orbit, (174, 193, 191), width=4, glow=14, alpha=92)
    for x, y in orbit[:-1]:
        draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(251, 247, 237), outline=(204, 185, 151), width=3)

    draw_small_token(img, 312, 254, 92, "compass")
    draw_small_token(img, 714, 246, 86, "calendar")
    draw_small_token(img, 312, 704, 88, "target")
    draw_small_token(img, 724, 698, 82, "checklist")
    draw_small_token(img, 512, 810, 68, "home")
    draw_ai_core(img, cx, cy, 136, scale=2.0)

    for x, y, r in [(404, 426, 8), (612, 392, 7), (574, 594, 8), (452, 602, 6), (512, 360, 6)]:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 240), outline=(125, 178, 207, 190), width=2)
    for p1, p2 in [((404, 426), (512, 360)), ((512, 360), (612, 392)), ((612, 392), (574, 594)), ((574, 594), (452, 602)), ((452, 602), (404, 426))]:
        draw.line([p1, p2], fill=(103, 149, 166, 105), width=3)

    img = img.resize((512, 512), Image.Resampling.LANCZOS).convert("RGB")
    img.save(LOGO_PATH, quality=94, optimize=True)


def draw_card(base: Image.Image, box: tuple[int, int, int, int], radius: int = 34) -> None:
    draw = ImageDraw.Draw(base)
    rounded_shadow(base, box, radius, blur=22, alpha=38, offset=(0, 16))
    draw.rounded_rectangle(box, radius=radius, fill=(252, 247, 236, 236), outline=(255, 255, 255, 230), width=3)


def create_banner() -> None:
    width, height = 1600, 900
    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw_vertical_gradient(img, (255, 249, 236), (238, 228, 207))
    draw = ImageDraw.Draw(img)

    def leaf_cluster(origin: tuple[int, int], flip: int = 1) -> None:
        ox, oy = origin
        stem = [(ox, oy + 130), (ox + flip * 34, oy + 72), (ox + flip * 74, oy + 18)]
        draw.line(stem, fill=(126, 145, 103, 120), width=5)
        for i, (dx, dy, angle) in enumerate([(12, 96, -30), (38, 66, 18), (66, 36, -22), (92, 12, 18)]):
            cx = ox + flip * dx
            cy = oy + dy
            rx, ry = 24 - i * 2, 44 - i * 5
            leaf = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(leaf)
            ld.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(132, 151, 112, 82))
            leaf = leaf.rotate(angle * flip, center=(cx, cy), resample=Image.Resampling.BICUBIC)
            img.alpha_composite(leaf)

    leaf_cluster((42, -6), 1)
    leaf_cluster((1558, -20), -1)
    draw.ellipse((1436, 720, 1588, 872), fill=(214, 185, 139, 46))
    draw.ellipse((-30, 718, 142, 890), fill=(113, 149, 168, 36))

    draw.rounded_rectangle((34, 694, 318, 812), radius=24, fill=(251, 247, 238, 230), outline=(225, 209, 179, 170), width=2)
    draw.line((60, 772, 284, 730), fill=(74, 112, 139, 210), width=7)
    draw.ellipse((101, 748, 151, 798), fill=(245, 238, 222), outline=(207, 181, 132), width=3)
    draw.ellipse((1328, 654, 1554, 880), fill=(252, 246, 235), outline=(224, 204, 172), width=4)
    draw.ellipse((1365, 690, 1516, 840), fill=(159, 102, 55, 155))

    center = (800, 470)
    draw.ellipse((632, 514, 968, 602), fill=(95, 78, 48, 30))
    draw.ellipse((650, 502, 950, 590), fill=(236, 224, 202), outline=(210, 188, 149), width=3)
    line_with_glow(img, [(800, 168), (800, 470), (800, 710)], (126, 178, 205), width=5, glow=18, alpha=120)
    line_with_glow(img, [(330, 250), (800, 470), (1270, 250)], (126, 178, 205), width=4, glow=18, alpha=106)
    line_with_glow(img, [(292, 540), (800, 470), (1295, 545)], (126, 178, 205), width=4, glow=18, alpha=100)

    draw_card(img, (258, 126, 628, 284))
    draw_card(img, (1010, 126, 1378, 292))
    draw_card(img, (154, 392, 488, 552))
    draw_card(img, (1118, 390, 1470, 564))
    draw_card(img, (300, 646, 636, 804))
    draw_card(img, (1004, 642, 1330, 802))

    draw_small_token(img, 370, 205, 56, "target")
    for i, color in enumerate([BLUE, SAGE, SAND, SAGE]):
        y = 170 + i * 28
        draw.ellipse((470, y - 7, 484, y + 7), fill=color)
        draw.line((504, y, 592, y), fill=(166, 145, 110), width=3)
        draw_check(draw, 598, y - 10, 20, color, 4)

    chart = [(1068, 238), (1114, 218), (1160, 202), (1208, 168), (1254, 184), (1314, 148)]
    draw.line(chart, fill=SAGE, width=5, joint="curve")
    for x, y in chart:
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=SAGE)
    for i, h in enumerate([46, 68, 96, 128]):
        x = 1080 + i * 58
        color = [SAGE, (182, 185, 137), BLUE, SAND][i]
        draw.rounded_rectangle((x, 252 - h, x + 28, 252), radius=4, fill=color)

    draw_small_token(img, 235, 472, 54, "compass")
    for i, color in enumerate([SAGE, (198, 202, 187), BLUE]):
        x = 328 + i * 60
        y = 475 - (i % 2) * 34
        draw.ellipse((x - 16, y - 16, x + 16, y + 16), fill=color)
        if i:
            draw.line((x - 60, y + (34 if i % 2 else -34), x - 16, y), fill=(139, 155, 145), width=3)

    draw.ellipse((1180, 438, 1296, 554), fill=BLUE)
    draw.pieslice((1180, 438, 1296, 554), 300, 90, fill=SAND)
    draw.pieslice((1180, 438, 1296, 554), 90, 170, fill=SAGE)
    draw.ellipse((1218, 476, 1258, 516), fill=(252, 247, 236))
    for i, color in enumerate([BLUE, SAGE, SAND]):
        yy = 430 + i * 36
        draw.ellipse((1334, yy - 8, 1350, yy + 8), fill=color)
        draw.line((1370, yy, 1440, yy), fill=(166, 145, 110), width=3)

    draw_small_token(img, 390, 716, 54, "home")
    draw.line((462, 690, 594, 690), fill=(166, 145, 110), width=3)
    draw.line((462, 728, 590, 728), fill=(166, 145, 110), width=3)
    draw.line((462, 766, 560, 766), fill=(166, 145, 110), width=3)
    for i, color in enumerate([SAGE, BLUE, SAND]):
        draw.ellipse((430, 683 + i * 38, 448, 701 + i * 38), fill=color)

    for row in range(4):
        for col in range(6):
            x = 1130 + col * 36
            y = 680 + row * 28
            draw.rounded_rectangle((x, y, x + 24, y + 18), radius=4, fill=(235, 226, 207), outline=(207, 190, 158))
    for x, y, color in [(1166, 736, BLUE), (1240, 708, SAND), (1280, 764, SAGE)]:
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=color)
    draw.ellipse((1264, 732, 1324, 792), fill=SAGE)
    draw_check(draw, 1280, 748, 32, WHITE, 6)

    draw_ai_core(img, center[0], center[1], 116, scale=1.7)
    for angle in range(0, 360, 30):
        a = math.radians(angle)
        x = center[0] + math.cos(a) * 190
        y = center[1] + math.sin(a) * 145
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 235), outline=(126, 178, 205, 160), width=2)

    title_font = font(42, bold=True)
    subtitle_font = font(22)
    draw.text((678, 78), "AI", font=title_font, fill=(62, 88, 92, 130))
    draw.text((740, 88), "daily planning intelligence", font=subtitle_font, fill=(93, 109, 101, 110))

    img = img.convert("RGB")
    img.save(BANNER_PATH, quality=92, optimize=True)


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    create_logo()
    create_banner()
    print(f"wrote {LOGO_PATH}")
    print(f"wrote {BANNER_PATH}")


if __name__ == "__main__":
    main()
