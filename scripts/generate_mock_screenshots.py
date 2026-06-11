from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"

BG = (239, 230, 211)
PANEL = (250, 244, 231)
CARD = (255, 252, 245)
CARD_SOFT = (252, 247, 236)
LINE = (224, 212, 190)
TEXT = (31, 42, 51)
MUTED = (91, 104, 112)
BLUE = (49, 121, 174)
SAGE = (128, 151, 110)
SAND = (210, 184, 135)


MOCK_GOALS = [
    {
        "project": "演示项目体验优化",
        "title": "整理本周产品反馈并形成行动清单",
        "date": "2026-06-10",
        "minutes": "75 分钟",
        "difficulty": "难度 3/5",
        "tag": "product",
        "criteria": [
            "汇总 8 条示例反馈，归类为体验、稳定性、文案",
            "标出 3 个高优先级改进点，并写清判断理由",
            "整理一页下周可执行行动清单",
        ],
        "minimum": "至少完成反馈清单，并选出 1 个最值得先做的改进点。",
        "history": "继续完成「演示项目体验优化」｜未完成目标：整理本周产品反馈并形成行动清单",
        "version": "v1",
    },
    {
        "project": "文档与交付",
        "title": "更新演示项目 README 的本地运行说明",
        "date": "2026-06-10",
        "minutes": "45 分钟",
        "difficulty": "难度 2/5",
        "tag": "documentation",
        "criteria": [
            "补齐 mock 模式启动步骤",
            "检查截图说明是否使用演示数据",
            "把常见失败原因整理为 3 条排查提示",
        ],
        "minimum": "README 能让新用户在 mock 模式下打开页面。",
        "history": "已完成「文档与交付」｜产出：README 运行说明与排查提示",
        "version": "v2",
    },
    {
        "project": "日程提醒流程",
        "title": "检查提醒卡片在空数据状态下的展示",
        "date": "2026-06-10",
        "minutes": "40 分钟",
        "difficulty": "难度 2/5",
        "tag": "qa",
        "criteria": [
            "列出空目标、无历史、无周报三种状态",
            "为每种状态写一句清楚的引导文案",
            "记录一个需要后续验证的边界场景",
        ],
        "minimum": "完成空状态清单和一条后续验证记录。",
        "history": "已完成「日程提醒流程」｜产出：空状态清单与引导文案",
        "version": "v1",
    },
    {
        "project": "每周复盘模板",
        "title": "草拟一份周五复盘的问题清单",
        "date": "2026-06-10",
        "minutes": "35 分钟",
        "difficulty": "难度 2/5",
        "tag": "weekly",
        "criteria": [
            "写出本周完成、阻塞、下周重点三组问题",
            "删掉泛泛的问题，只保留可回答项",
            "保存为一页模板草稿",
        ],
        "minimum": "完成 6 个可回答的问题。",
        "history": "继续完成「每周复盘模板」｜未完成目标：草拟一份周五复盘的问题清单",
        "version": "v1",
    },
    {
        "project": "小功能验收",
        "title": "为 check-in 表单补一组手动验收记录",
        "date": "2026-06-10",
        "minutes": "50 分钟",
        "difficulty": "难度 3/5",
        "tag": "testing",
        "criteria": [
            "覆盖完成、未完成、部分完成三种提交",
            "记录每种提交后的页面反馈",
            "整理一个待优化的小问题",
        ],
        "minimum": "完成 3 条手动验收记录。",
        "history": "已完成「小功能验收」｜产出：check-in 表单手动验收记录",
        "version": "v1",
    },
]


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def canvas(size: tuple[int, int]) -> Image.Image:
    img = Image.new("RGBA", size, BG)
    layer = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    for y in range(size[1]):
        mix = y / max(size[1] - 1, 1)
        color = tuple(round((250, 244, 232)[i] * (1 - mix) + BG[i] * mix) for i in range(3))
        draw.line((0, y, size[0], y), fill=(*color, 255))
    img.alpha_composite(layer)
    return img


def shadowed_round(
    img: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    shadow: int = 0,
) -> None:
    if shadow:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(layer)
        sd.rounded_rectangle((box[0], box[1] + 8, box[2], box[3] + 8), radius=radius, fill=(81, 66, 42, shadow))
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(16)))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline or fill, width=1)


def wrap_with_readme_shadow(
    source: Image.Image,
    radius: int = 18,
    padding: int = 18,
    blur: int = 10,
    alpha: int = 44,
    offset: tuple[int, int] = (0, 6),
) -> Image.Image:
    source = source.convert("RGBA")
    width, height = source.size
    wrapped = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))

    shadow = Image.new("RGBA", wrapped.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (
            padding + offset[0],
            padding + offset[1],
            padding + offset[0] + width,
            padding + offset[1] + height,
        ),
        radius=radius,
        fill=(73, 61, 42, alpha),
    )
    wrapped.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))

    mask = Image.new("L", source.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    wrapped.paste(source, (padding, padding), mask)
    return wrapped


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    size: int,
    color: tuple[int, int, int] = TEXT,
    bold: bool = False,
) -> None:
    draw.text(xy, text, font=get_font(size, bold), fill=color)


def text_size(draw: ImageDraw.ImageDraw, text: str, size: int, bold: bool = False) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=get_font(size, bold))
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_by_width(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = False) -> list[str]:
    font = get_font(size, bold)
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    max_width: int,
    size: int,
    color: tuple[int, int, int] = TEXT,
    bold: bool = False,
    line_gap: int = 8,
    max_lines: int | None = None,
) -> int:
    y = xy[1]
    lines = wrap_by_width(draw, text, max_width, size, bold)
    if max_lines is not None:
        lines = lines[:max_lines]
    line_h = size + line_gap
    for line in lines:
        draw.text((xy[0], y), line, font=get_font(size, bold), fill=color)
        y += line_h
    return y


def badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int = 13, fill: tuple[int, int, int] = (244, 241, 234)) -> int:
    pad_x, pad_y = 14, 7
    w, h = text_size(draw, text, size)
    x, y = xy
    draw.rounded_rectangle((x, y, x + w + pad_x * 2, y + h + pad_y * 2), radius=18, fill=fill)
    draw.text((x + pad_x, y + pad_y - 1), text, font=get_font(size), fill=TEXT)
    return x + w + pad_x * 2 + 10


def nav(img: Image.Image, selected: str = "today") -> None:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    sidebar_w = 246 if w >= 1200 else 220
    draw.rounded_rectangle((18, 18, w - 18, h - 18), radius=13, fill=PANEL, outline=LINE)
    draw.rectangle((18, 18, sidebar_w, h - 18), fill=(247, 240, 224))
    draw.line((sidebar_w, 18, sidebar_w, h - 18), fill=LINE, width=1)

    x = 40
    draw.rounded_rectangle((x, 43, sidebar_w - 22, 92), radius=8, fill=(235, 233, 218))
    draw.ellipse((x + 12, 54, x + 44, 86), fill=(247, 250, 248), outline=BLUE, width=2)
    draw_text(draw, (x + 20, 62), "DP", 10, BLUE, True)
    draw_text(draw, (x + 58, 59), "DayPilot", 16, BLUE, True)

    items = [("today", "□", "今日"), ("history", "◇", "历史"), ("weekly", "⌁", "周报"), ("career", "✦", "职业规划")]
    y = 112
    for key, icon, label in items:
        is_selected = key == selected
        if is_selected:
            draw.rounded_rectangle((x, y, sidebar_w - 22, y + 44), radius=8, fill=(235, 233, 218))
        draw_text(draw, (x + 18, y + 12), icon, 17, BLUE if is_selected else TEXT, True)
        draw_text(draw, (x + 48, y + 13), label, 15, BLUE if is_selected else TEXT, True if is_selected else False)
        y += 54

    action_y = y + 10
    draw.rounded_rectangle((x, action_y, sidebar_w - 22, action_y + 38), radius=8, fill=(250, 246, 236), outline=LINE)
    draw_text(draw, (x + 58, action_y + 11), "项目更新", 13, TEXT)

    note_y = h - 118
    draw.rounded_rectangle((x, note_y, sidebar_w - 22, note_y + 80), radius=8, fill=(252, 248, 239))
    draw_text(draw, (x + 14, note_y + 17), "日用节奏", 14, TEXT, True)
    draw_text(draw, (x + 14, note_y + 42), "小目标，留下可检查成果。", 13, TEXT)


def header(draw: ImageDraw.ImageDraw, left: int, right: int) -> None:
    draw_text(draw, (left, 48), "DAYPILOT", 11, BLUE, True)
    draw_text(draw, (left, 76), "日用工作台", 27, TEXT, True)
    draw_text(draw, (right - 360, 56), "四小时有效工作时间，别把长期愿望压成一天任务。", 14, TEXT)


def section_shell(img: Image.Image, x0: int, y0: int, x1: int, y1: int, kicker: str, title: str) -> None:
    draw = ImageDraw.Draw(img)
    shadowed_round(img, (x0, y0, x1, y1), 10, (250, 244, 231), None, 0)
    draw_text(draw, (x0 + 24, y0 + 28), kicker, 11, BLUE, True)
    draw_text(draw, (x0 + 24, y0 + 55), title, 23, TEXT, True)
    draw.rounded_rectangle((x1 - 85, y0 + 25, x1 - 24, y0 + 63), radius=8, fill=(252, 247, 236), outline=LINE)
    draw_text(draw, (x1 - 67, y0 + 35), "刷新", 13, TEXT)


def draw_goal_card(
    img: Image.Image,
    box: tuple[int, int, int, int],
    goal: dict[str, object],
    interactive: bool,
) -> None:
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    compact = (y1 - y0) < 660
    shadowed_round(img, box, 8, (255, 253, 246), None, 12 if interactive else 0)
    draw.rectangle((x0, y0 + 16, x0 + 4, y1 - 16), fill=(103, 169, 207))

    pad = 18
    content_x0 = x0 + pad
    content_x1 = x1 - pad
    cursor_y = y0 + pad

    project_font = 14 if compact else 15
    draw_text(draw, (content_x0, cursor_y + 2), str(goal["project"]), project_font, TEXT, True)
    chips = [str(goal["date"]), str(goal["minutes"]), str(goal["difficulty"]), str(goal["tag"])]
    badge_x = content_x1 - (328 if compact else 370)
    for value in chips:
        badge_x = badge(draw, (badge_x, cursor_y - 2), value, 11 if compact else 12)
    cursor_y += 48 if compact else 54

    title_h = 74 if compact else 86
    draw.rounded_rectangle((content_x0, cursor_y, content_x1, cursor_y + title_h), radius=8, fill=(255, 250, 239))
    draw_text(draw, (content_x0 + 16, cursor_y + 13), "今日目标", 11, BLUE, True)
    draw_wrapped(
        draw,
        (content_x0 + 16, cursor_y + 38),
        str(goal["title"]),
        content_x1 - content_x0 - 32,
        16 if compact else 18,
        TEXT,
        True,
        line_gap=6,
        max_lines=2,
    )
    cursor_y += title_h + 14

    detail_h = 132 if compact else 156
    gap = 12
    min_w = 250 if compact else 280
    criteria_w = content_x1 - content_x0 - gap - min_w
    criteria_box = (content_x0, cursor_y, content_x0 + criteria_w, cursor_y + detail_h)
    minimum_box = (content_x0 + criteria_w + gap, cursor_y, content_x1, cursor_y + detail_h)
    for detail_box in (criteria_box, minimum_box):
        draw.rounded_rectangle(detail_box, radius=8, fill=(255, 250, 239))

    draw_text(draw, (criteria_box[0] + 14, criteria_box[1] + 13), "完成标准", 14, TEXT, True)
    for i, item in enumerate(goal["criteria"]):  # type: ignore[index]
        draw_wrapped(
            draw,
            (criteria_box[0] + 14, criteria_box[1] + 40 + i * (27 if compact else 30)),
            f"{i + 1}. {item}",
            criteria_box[2] - criteria_box[0] - 28,
            12 if compact else 13,
            TEXT,
            line_gap=5,
            max_lines=1,
        )
    draw_text(draw, (minimum_box[0] + 14, minimum_box[1] + 13), "最低成果", 14, TEXT, True)
    draw_wrapped(
        draw,
        (minimum_box[0] + 14, minimum_box[1] + 40),
        str(goal["minimum"]),
        minimum_box[2] - minimum_box[0] - 28,
        12 if compact else 13,
        TEXT,
        line_gap=5,
        max_lines=3,
    )
    cursor_y += detail_h + 14

    action_h = max(196 if compact else 270, y1 - cursor_y - pad)
    form_w = (content_x1 - content_x0 - gap) // 2
    feedback_box = (content_x0, cursor_y, content_x0 + form_w, min(cursor_y + action_h, y1 - pad))
    checkin_box = (content_x0 + form_w + gap, cursor_y, content_x1, min(cursor_y + action_h, y1 - pad))
    draw_compact_feedback(draw, feedback_box, compact)
    draw_compact_checkin(draw, checkin_box, compact)


def draw_compact_feedback(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], compact: bool) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=8, fill=(255, 252, 244), outline=None)
    draw_text(draw, (x0 + 14, y0 + 12), "反馈修正", 14, TEXT, True)
    button_h = 38
    textarea_top = y0 + 42
    textarea_bottom = max(textarea_top + 70, y1 - button_h - 26)
    draw.rounded_rectangle((x0 + 14, textarea_top, x1 - 14, textarea_bottom), radius=7, fill=(255, 253, 246), outline=LINE)
    draw.rounded_rectangle((x0 + 14, y1 - button_h - 14, x1 - 14, y1 - 14), radius=8, fill=BLUE)
    draw_text(draw, (x0 + (x1 - x0) // 2 - 58, y1 - button_h - 3), "修正该项目目标", 12 if compact else 13, (255, 255, 255), True)


def draw_compact_checkin(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], compact: bool) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=8, fill=(255, 252, 244), outline=None)
    draw_text(draw, (x0 + 14, y0 + 12), "Check-in", 14, TEXT, True)
    draw_text(draw, (x0 + 14, y0 + 39), "完成状态", 11, MUTED)

    inner_x0 = x0 + 14
    inner_x1 = x1 - 14
    toggle_y = y0 + 59
    toggle_h = 34 if compact else 38
    draw.rounded_rectangle((inner_x0, toggle_y, inner_x1, toggle_y + toggle_h), radius=8, fill=(255, 253, 246), outline=LINE)
    draw.rounded_rectangle((inner_x0, toggle_y, inner_x0 + (inner_x1 - inner_x0) // 2, toggle_y + toggle_h), radius=8, fill=BLUE)
    draw_text(draw, (inner_x0 + (inner_x1 - inner_x0) // 4 - 16, toggle_y + 8), "完成", 12, (255, 255, 255), True)
    draw_text(draw, (inner_x0 + (inner_x1 - inner_x0) * 3 // 4 - 22, toggle_y + 8), "未完成", 12, TEXT, True)

    note_y = toggle_y + toggle_h + 18
    draw_text(draw, (inner_x0, note_y), "完成说明（可空）", 11, MUTED)
    note_h = 50 if compact else 62
    draw.rounded_rectangle((inner_x0, note_y + 20, inner_x1, note_y + 20 + note_h), radius=7, fill=(255, 253, 246), outline=LINE)

    if not compact:
        difficulty_y = note_y + note_h + 34
        draw_text(draw, (inner_x0, difficulty_y), "主观难度", 11, MUTED)
        cells_y = difficulty_y + 22
        cell_w = (inner_x1 - inner_x0) // 5
        for i in range(5):
            fill = BLUE if i == 2 else (255, 253, 246)
            draw.rectangle((inner_x0 + i * cell_w, cells_y, inner_x0 + (i + 1) * cell_w, cells_y + 34), fill=fill, outline=LINE)
            draw_text(draw, (inner_x0 + i * cell_w + cell_w // 2 - 4, cells_y + 8), str(i + 1), 12, (255, 255, 255) if i == 2 else TEXT, True)

    button_h = 38
    draw.rounded_rectangle((inner_x0, y1 - button_h - 14, inner_x1, y1 - 14), radius=8, fill=BLUE)
    draw_text(draw, (x0 + (x1 - x0) // 2 - 70, y1 - button_h - 3), "保存该项目 check-in", 12 if compact else 13, (255, 255, 255), True)


def today(size: tuple[int, int], path: Path) -> None:
    img = canvas(size)
    draw = ImageDraw.Draw(img)
    nav(img, "today")
    sidebar_w = 246 if size[0] >= 1200 else 220
    left = sidebar_w + 32
    right = size[0] - 52
    header(draw, left, right)
    shell_y = 136
    section_shell(img, left, shell_y, right, size[1] - 46, "TODAY", "今日目标")

    outer = (left + 24, shell_y + 96, right - 24, size[1] - 72)
    shadowed_round(img, outer, 8, CARD, None, 10)
    inner = (outer[0] + 19, outer[1] + 19, outer[2] - 19, outer[3] - 19)
    draw_goal_card(img, inner, MOCK_GOALS[0], True)

    wrap_with_readme_shadow(img).save(path, optimize=True)


def history(size: tuple[int, int], path: Path) -> None:
    img = canvas(size)
    draw = ImageDraw.Draw(img)
    nav(img, "history")
    sidebar_w = 246 if size[0] >= 1200 else 220
    left = sidebar_w + 32
    right = size[0] - 52
    header(draw, left, right)
    shell_y = 136
    section_shell(img, left, shell_y, right, size[1] - 46, "HISTORY", "最近记录")

    list_x0 = left + 24
    list_y0 = shell_y + 96
    list_x1 = right - 24
    list_y1 = size[1] - 72
    shadowed_round(img, (list_x0, list_y0, list_x1, list_y1), 8, CARD, None, 0)
    y = list_y0 + 25
    draw.rounded_rectangle((list_x0 + 18, y - 6, list_x0 + 174, y + 28), radius=18, fill=(245, 242, 235))
    draw_text(draw, (list_x0 + 30, y), "2026-06-10  ·  5 条记录", 14, TEXT, True)
    y += 50

    available_w = list_x1 - list_x0 - 72
    row_h = max(132, (list_y1 - y - 16) // 5)
    for goal in MOCK_GOALS:
        row_top = y
        badge(draw, (list_x0 + 18, row_top), str(goal["project"]), 13)
        draw.rounded_rectangle((list_x1 - 54, row_top + 2, list_x1 - 22, row_top + 34), radius=16, fill=(244, 241, 234))
        draw_text(draw, (list_x1 - 45, row_top + 9), str(goal["version"]), 12, TEXT)
        draw_wrapped(draw, (list_x0 + 18, row_top + 48), str(goal["history"]), available_w, 15, TEXT, True, max_lines=2)
        draw_text(draw, (list_x0 + 18, row_top + 95), "mock check-in：已记录演示完成情况。", 13, MUTED)
        y += row_h
        if y < list_y1 - 20:
            draw.line((list_x0 + 18, y - 16, list_x1 - 18, y - 16), fill=LINE)

    wrap_with_readme_shadow(img).save(path, optimize=True)


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    today((1265, 1140), ASSET_DIR / "daypilot-screenshot-today.png")
    history((1265, 1133), ASSET_DIR / "daypilot-screenshot-history.png")
    today((1280, 900), ASSET_DIR / "daypilot-today-desktop.png")
    history((1100, 1150), ASSET_DIR / "daypilot-history-desktop.png")
    for name in [
        "daypilot-screenshot-today.png",
        "daypilot-screenshot-history.png",
        "daypilot-today-desktop.png",
        "daypilot-history-desktop.png",
    ]:
        print(f"wrote {ASSET_DIR / name}")


if __name__ == "__main__":
    main()
