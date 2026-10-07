"""Картинки-шапки для топов и кланов в том же «огненном» стиле, что и баннер.

Списки мест идут текстом в подписи: имена и названия кланов бывают с эмодзи и любыми алфавитами,
которых нет в шрифте, поэтому на картинке только статичный заголовок и рисунок.
"""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from .base import AMBER, BG, FLAME, GOLD, MUTED, ORANGE, TEXT, Neon, font, pill, star, to_jpeg
from .cards import _background, _corners, _draw_mesh_face

W, H = 1280, 560

HEADERS = {
    # вид: (метка на плашке, заголовок, подзаголовок)
    "top_rating": ("ТОП 10", "ПО РЕЙТИНГУ", "Лучшая оценка каждого участника"),
    "top_balance": ("ТОП 10", "ПО БАЛАНСУ", "Больше всего оценок на счету"),
    "top_clans": ("ТОП 10", "КЛАНОВ", "Сумма рейтингов всех участников"),
    "clans": ("ВМЕСТЕ", "КЛАНЫ", "Создай свой или вступи в любой"),
}


def _glow_text(img: Image.Image, xy, text: str, f, fill, glow=(255, 122, 26), radius: int = 14) -> Image.Image:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=f, fill=(*glow, 200))
    blurred = layer.filter(ImageFilter.GaussianBlur(radius))
    out = Image.alpha_composite(img.convert("RGBA"), blurred).convert("RGB")
    ImageDraw.Draw(out).text(xy, text, font=f, fill=fill)
    return out


def _podium(n: Neon, d_after: list, cx: float, base_y: float, unit: float) -> None:
    """Пьедестал 2-1-3: неоновые контуры, цифры дорисовываются после свечения."""
    blocks = ((-1, 2, 0.62), (0, 1, 1.0), (1, 3, 0.42))
    for off, place, k in blocks:
        x0 = cx + off * unit * 1.05 - unit / 2
        x1 = x0 + unit
        y1 = base_y
        y0 = base_y - unit * 1.55 * k
        col = GOLD if place == 1 else (AMBER if place == 2 else ORANGE)
        n.line([(x0, y1), (x0, y0), (x1, y0), (x1, y1)], col, 4)
        n.line([(x0 + 10, y0 + 10), (x1 - 10, y0 + 10)], col, 1.6, 120)
        d_after.append((place, (x0 + x1) / 2, y0 + (y1 - y0) / 2 + 6, col))
    n.line([(cx - unit * 2.0, base_y), (cx + unit * 2.0, base_y)], ORANGE, 3, 200)


def _crown(n: Neon, c: tuple[float, float], s: float, color) -> None:
    x, y = c
    pts = [(x - s, y + s * 0.55), (x - s, y - s * 0.35), (x - s * 0.5, y + s * 0.05), (x, y - s * 0.6),
           (x + s * 0.5, y + s * 0.05), (x + s, y - s * 0.35), (x + s, y + s * 0.55)]
    n.line(pts, color, 4, closed=True)
    for px, py in ((x - s, y - s * 0.35), (x, y - s * 0.6), (x + s, y - s * 0.35)):
        n.dot((px, py - 9), 6, color)


def _shield_path(cx: float, top: float, w: float, h: float, steps: int = 24) -> list[tuple[float, float]]:
    """Контур щита: плечи сверху, плавные бока к острому низу."""
    half = w / 2
    pts = [(cx - half, top + h * 0.08), (cx - half * 0.55, top), (cx, top + h * 0.05), (cx + half * 0.55, top),
           (cx + half, top + h * 0.08)]
    for i in range(1, steps + 1):  # правый бок
        t = i / steps
        pts.append((cx + half * (1 - t ** 1.8), top + h * 0.08 + (h * 0.92) * (1 - (1 - t) ** 1.25)))
    for i in range(steps - 1, 0, -1):  # левый бок обратно вверх
        t = i / steps
        pts.append((cx - half * (1 - t ** 1.8), top + h * 0.08 + (h * 0.92) * (1 - (1 - t) ** 1.25)))
    return pts


def _shield(n: Neon, cx: float, top: float, w: float, h: float, color, width: float = 4.5) -> None:
    n.line(_shield_path(cx, top, w, h), color, width, closed=True)


def _stars_art(img: Image.Image, center: tuple[float, float]) -> Image.Image:
    cx, cy = center
    items = ((cx, cy, 120, 255), (cx + 170, cy - 110, 52, 215), (cx + 150, cy + 130, 70, 235),
             (cx - 160, cy + 120, 40, 190), (cx - 150, cy - 120, 30, 170))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(layer)
    for x, y, r, a in items:
        star(sd, (x, y), r * 1.08, (*AMBER, a))
    glow = layer.filter(ImageFilter.GaussianBlur(18))
    img = Image.alpha_composite(Image.alpha_composite(img.convert("RGBA"), glow), glow).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    for x, y, r, a in items:
        star(d, (x, y), r, (*GOLD, a))
    return img


@lru_cache(maxsize=8)
def render_header(kind: str) -> bytes:
    """kind: top_rating | top_balance | top_clans | clans."""
    tag, title, subtitle = HEADERS[kind]
    seed = {"top_rating": 11, "top_balance": 12, "top_clans": 13, "clans": 14}[kind]
    img = _background((W, H), seed=seed, glow_at=(0.78, 0.45))
    n = Neon((W, H))
    labels: list = []
    art_cx = 990
    if kind == "top_rating":
        _podium(n, labels, art_cx, 500, 120)
        _draw_mesh_face(n, (art_cx, 205), 58, guides=False)
        _crown(n, (art_cx, 62), 30, GOLD)
    elif kind == "top_clans":
        _podium(n, labels, art_cx, 500, 120)
        _shield(n, art_cx, 100, 150, 190, GOLD)
        _shield(n, art_cx, 126, 104, 138, ORANGE, 2.4)
    elif kind == "clans":
        for dx, s, col in ((-150, 0.72, AMBER), (150, 0.72, AMBER), (0, 1.0, GOLD)):
            w, h = 210 * s, 270 * s
            _shield(n, art_cx + dx, 280 - h / 2 + (0 if dx == 0 else 40), w, h, col)
        _shield(n, art_cx, 175, 140, 180, ORANGE, 2.4)
    if kind != "top_balance":
        img = n.render_onto(img, glow=8)
    else:
        img = _stars_art(img, (art_cx, 280))
    d = ImageDraw.Draw(img, "RGBA")
    if kind in ("top_rating", "top_clans"):
        _corners(d, (800, 30, 1190, 530), (*ORANGE, 210), length=40, width=4)
    for place, x, y, col in labels:
        f = font(54 if place == 1 else 44, "display")
        d.text((x, y), str(place), font=f, fill=col, anchor="mm")
    if kind == "clans":
        star(d, (art_cx, 262), 36, GOLD)
    if kind == "top_clans":
        star(d, (art_cx, 190), 30, GOLD)

    pill(d, (70, 70), tag, font(30, "bold"), fg=BG, bg=ORANGE, pad=(20, 9))
    img = _glow_text(img, (66, 140), title, font(92, "display"), TEXT, glow=FLAME, radius=16)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((72, 262), subtitle, font=font(34, "semibold"), fill=GOLD)
    d.line([(72, 330), (420, 330)], fill=(*ORANGE, 150), width=3)
    d.text((72, 470), "I WANNA MOG YOU", font=font(26, "bold"), fill=MUTED)
    return to_jpeg(img)

