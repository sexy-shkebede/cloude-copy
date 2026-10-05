"""Карточки для сообщений: стартовый баннер, инструкции к фото, итог анализа, баланс."""
from __future__ import annotations

import datetime as dt
import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw

from analyzer.frontal import CANON_SIZE, FrontalResult
from analyzer.scoring import Report

from .base import (
    BG, CYAN, GOLD, MAGENTA, MUTED, TEXT, VIOLET, Neon, bgr_to_pil, font, gradient, pill,
    radial_glow, score_color, star, text_size, to_jpeg, wrap,
)
from .overlays import CANON_IPD
from .shapes import (
    GROUPS_CLOSED, GROUPS_OPEN, MEAN_SHAPE, PROFILE_BACK, PROFILE_EAR, PROFILE_EYE, PROFILE_FRONT, PROFILE_JAW,
    PROFILE_POINTS, catmull_rom, mesh_edges,
)


def _background(size: tuple[int, int], accent=MAGENTA, accent2=CYAN) -> Image.Image:
    w, h = size
    img = gradient(size, (18, 12, 42), (6, 20, 40), angle=60)
    img = radial_glow(img, (w * 0.12, h * 0.1), max(w, h) * 0.55, accent, 0.28)
    img = radial_glow(img, (w * 0.9, h * 0.85), max(w, h) * 0.6, accent2, 0.22)
    d = ImageDraw.Draw(img, "RGBA")
    step = 48
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 10))
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 10))
    return img


def _draw_mesh_face(n: Neon, center: tuple[float, float], ipd: float, guides: bool = True) -> np.ndarray:
    p = MEAN_SHAPE * ipd + np.array(center, np.float32)
    for a, b in mesh_edges(p):
        n.line([p[a], p[b]], CYAN, 1.2, 70)
    for g in GROUPS_OPEN:
        n.line(p[g], CYAN, 2.6)
    for g in GROUPS_CLOSED:
        n.line(p[g], CYAN, 2.6, closed=True)
    if guides:
        cx = center[0]
        brow_y, sn_y, me_y = p[17:27, 1].mean(), p[33, 1], p[8, 1]
        hair_y = brow_y - (sn_y - brow_y)
        for y in (hair_y, brow_y, sn_y, me_y):
            n.dashed((cx - 1.6 * ipd, y), (cx + 1.6 * ipd, y), GOLD, 2, 12, 8, 220)
        n.dashed((cx, hair_y - 0.4 * ipd), (cx, me_y + 0.4 * ipd), MAGENTA, 2.2, 12, 8, 230)
    for q in p:
        n.dot(q, 2.8, (255, 255, 255))
    return p


def _corners(d: ImageDraw.ImageDraw, box, color, length: int = 46, width: int = 5) -> None:
    x0, y0, x1, y1 = box
    for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([(x, y), (x + dx * length, y)], fill=color, width=width)
        d.line([(x, y), (x, y + dy * length)], fill=color, width=width)


def _bullets(d: ImageDraw.ImageDraw, xy, items: list[str], f, max_w: int, gap: int = 18, color=CYAN) -> int:
    x, y = xy
    for it in items:
        lines = wrap(d, it, f, max_w - 34)
        d.ellipse([x, y + 9, x + 14, y + 23], fill=color)
        for ln in lines:
            d.text((x + 32, y), ln, font=f, fill=TEXT)
            y += text_size(d, "Ay", f)[1] + 10
        y += gap
    return y


@lru_cache(maxsize=4)
def render_banner(brand: str, price: int) -> bytes:
    w, h = 1280, 720
    img = _background((w, h))
    n = Neon((w, h))
    _draw_mesh_face(n, (950, 300), 118)
    img = n.render_onto(img, glow=7)
    d = ImageDraw.Draw(img, "RGBA")
    # сканирующая полоса
    for i in range(40):
        d.line([(700, 420 + i), (1220, 420 + i)], fill=(*CYAN, int(40 * (1 - i / 40))))
    _corners(d, (720, 40, 1190, 690), (*CYAN, 200))
    d.text((70, 92), brand.upper(), font=font(96, "display"), fill=TEXT)
    d.text((74, 206), "ИИ-анализ внешности", font=font(40, "bold"), fill=CYAN)
    y = _bullets(d, (76, 300), ["68 точек разметки лица", "Профиль под углом 90°", "Баллы по каждой части лица", "Итоговая оценка и советы"],
                 font(32, "semibold"), 580, gap=10)
    box = pill(d, (74, y + 30), f"1 оценка = {price}      ", font(34, "bold"), fg=BG, bg=GOLD, pad=(26, 14))
    star(d, (box[2] - 38, (box[1] + box[3]) / 2), 17, BG)
    return to_jpeg(img)


@lru_cache(maxsize=2)
def render_step_front() -> bytes:
    w, h = 1280, 720
    img = _background((w, h), accent=CYAN, accent2=VIOLET)
    n = Neon((w, h))
    _draw_mesh_face(n, (960, 300), 128)
    img = n.render_onto(img, glow=6)
    d = ImageDraw.Draw(img, "RGBA")
    _corners(d, (740, 40, 1180, 680), (*GOLD, 230))
    pill(d, (70, 70), "ШАГ 1 / 2", font(28, "bold"), fg=BG, bg=CYAN, pad=(18, 8))
    d.text((70, 132), "ФОТО АНФАС", font=font(80, "display"), fill=TEXT)
    _bullets(
        d, (74, 262),
        ["Смотри прямо в камеру", "Камера на уровне глаз, ~50 см", "Ровный свет, без фильтров", "Открой лоб, сними очки",
         "Нейтральное лицо, рот закрыт"],
        font(31, "semibold"), 600, gap=8, color=GOLD,
    )
    return to_jpeg(img)


@lru_cache(maxsize=2)
def render_step_profile() -> bytes:
    w, h = 1280, 720
    img = _background((w, h), accent=VIOLET, accent2=MAGENTA)
    n = Neon((w, h))
    s, ox, oy = 520, 860, 70
    off = np.array([ox, oy], np.float32)
    n.line(catmull_rom(PROFILE_BACK) * s + off, CYAN, 2.4, 140)
    n.line(catmull_rom(PROFILE_JAW) * s + off, CYAN, 1.8, 90)
    n.line(catmull_rom(PROFILE_FRONT) * s + off, CYAN, 3.6)
    ex, ey, erx, ery = PROFILE_EAR
    n.draw.ellipse([(ox + (ex - erx) * s) * n.ss, (oy + (ey - ery) * s) * n.ss, (ox + (ex + erx) * s) * n.ss,
                    (oy + (ey + ery) * s) * n.ss], outline=(*CYAN, 150), width=int(2.4 * n.ss))
    n.line([np.array(q) * s + off for q in PROFILE_EYE], CYAN, 3.2, 220)
    pts = {k: np.array(v, np.float32) * s + off for k, v in PROFILE_POINTS.items()}
    e = pts["Pog"] - pts["Prn"]
    n.dashed(pts["Prn"] - e * 0.25, pts["Pog"] + e * 0.35, GOLD, 2.4)
    n.line([pts["G"], pts["Sn"], pts["Pog"]], MAGENTA, 2.2, 220)
    for q in pts.values():
        n.dot(q, 5, (255, 255, 255))
    # стрелка поворота головы
    c, r = (ox + 0.08 * s, oy + 0.5 * s), 0.6 * s
    n.arc(c, r, 262, 322, GOLD, 4, 230)
    a = math.radians(322)
    tip = np.array([c[0] + r * math.cos(a), c[1] + r * math.sin(a)])
    tangent = np.array([-math.sin(a), math.cos(a)])
    normal = np.array([math.cos(a), math.sin(a)])
    n.line([tip - tangent * 22 + normal * 12, tip, tip - tangent * 22 - normal * 12], GOLD, 4, 230)
    img = n.render_onto(img, glow=6)
    d = ImageDraw.Draw(img, "RGBA")
    am = math.radians(292)
    pill(d, (c[0] + (r + 34) * math.cos(am), max(30, c[1] + (r + 34) * math.sin(am))), "90°", font(34, "display"),
         fg=BG, bg=GOLD, pad=(14, 6), anchor="mm")
    pill(d, (70, 70), "ШАГ 2 / 2", font(28, "bold"), fg=BG, bg=MAGENTA, pad=(18, 8))
    d.text((70, 132), "ПРОФИЛЬ 90°", font=font(80, "display"), fill=TEXT)
    _bullets(
        d, (74, 262),
        ["Повернись строго боком", "Камера на уровне носа", "Однотонный фон за лицом", "Волосы за ухо, лоб открыт",
         "Голову не наклоняй"],
        font(31, "semibold"), 560, gap=8, color=MAGENTA,
    )
    return to_jpeg(img)


def _avatar(front: FrontalResult, size: int) -> Image.Image:
    f = float(np.clip(front.src_ipd / CANON_IPD, 1.6, 2.4))
    M = front.to_canon * f
    W, H = int(CANON_SIZE[0] * f), int(CANON_SIZE[1] * f)
    warped = cv2.warpAffine(front.image, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    p = front.points * f
    ipd = front.m["ipd"] * f
    cx = float(p[[36, 39, 42, 45, 30, 8], 0].mean())
    cy = float((p[36:48, 1].mean() + p[8, 1]) / 2 - 0.15 * ipd)
    half = 1.75 * ipd
    x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
    crop = warped[y0 : int(min(H, cy + half)), x0 : int(min(W, cx + half))]
    im = bgr_to_pil(crop).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 3, size * 3], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask.resize((size, size), Image.LANCZOS))
    return out


def render_result_card(report: Report, front: FrontalResult, brand: str) -> bytes:
    w, h = 1080, 1350
    col = score_color(report.total)
    img = gradient((w, h), (14, 14, 34), (26, 10, 44), angle=90)
    img = radial_glow(img, (260, 360), 520, col, 0.25)
    img = radial_glow(img, (w, h), 700, VIOLET, 0.18)
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, w, 54):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 8))
    for y in range(0, h, 54):
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 8))

    d.text((64, 52), brand.upper(), font=font(26, "bold"), fill=CYAN)
    d.text((64, 88), "РЕЗУЛЬТАТ АНАЛИЗА", font=font(54, "display"), fill=TEXT)

    # аватар с неоновым кольцом
    size, ax, ay = 300, 64, 190
    n = Neon((w, h))
    n.ring((ax + size / 2, ay + size / 2), size / 2 + 12, col, 7)
    angle = 360 * report.total / 10
    n.arc((ax + size / 2, ay + size / 2), size / 2 + 28, -90, -90 + angle, col, 6)
    img = n.render_onto(img, glow=9)
    avatar = _avatar(front, size)
    img.paste(avatar, (ax, ay), avatar)
    d = ImageDraw.Draw(img, "RGBA")

    tx = ax + size + 80
    d.text((tx, 200), "ИТОГОВЫЙ БАЛЛ", font=font(26, "bold"), fill=MUTED)
    big = font(170, "display")
    score_text = f"{report.total:.1f}"
    d.text((tx - 6, 230), score_text, font=big, fill=col)
    sw, _ = text_size(d, score_text, big)
    d.text((tx + sw + 6, 330), "/10", font=font(52, "display"), fill=MUTED)
    ty = 440
    for ln in wrap(d, report.tier, font(32, "bold"), w - tx - 50):
        d.text((tx, ty), ln, font=font(32, "bold"), fill=TEXT)
        ty += 42
    d.text((tx, ty + 6), f"Форма лица: {report.face_shape}", font=font(26, "semibold"), fill=MUTED)

    # строки по частям лица
    y = 580
    row_h = min(74, int(620 / max(1, len(report.parts))))
    ft, fsc = font(30, "semibold"), font(30, "bold")
    for part in report.parts:
        c = score_color(part.score)
        d.ellipse([66, y + 10, 84, y + 28], fill=c)
        d.text((102, y + 2), part.title, font=ft, fill=TEXT)
        st = f"{part.score:.1f}"
        tw, _ = text_size(d, st, fsc)
        d.text((w - 64 - tw, y + 2), st, font=fsc, fill=c)
        by = y + 46
        d.rounded_rectangle([102, by, w - 64, by + 12], radius=6, fill=(255, 255, 255, 28))
        fill_w = (w - 64 - 102) * max(0.03, part.score / 10)
        bar = gradient((int(fill_w), 12), tuple(int(v * 0.6) for v in c), c, angle=0)
        m = Image.new("L", bar.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, bar.size[0] - 1, 11], radius=6, fill=255)
        img.paste(bar, (102, by), m)
        y += row_h

    d.text((64, h - 70), "Геометрический анализ пропорций • развлекательный формат", font=font(21, "semibold"), fill=MUTED)
    date = dt.datetime.now().strftime("%d.%m.%Y")
    dw, _ = text_size(d, date, font(21, "semibold"))
    d.text((w - 64 - dw, h - 70), date, font=font(21, "semibold"), fill=MUTED)
    return to_jpeg(img, 92)


def render_balance(balance: int, price: int, unlimited: bool = False) -> bytes:
    w, h = 1280, 560
    img = _background((w, h), accent=GOLD, accent2=VIOLET)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((70, 60), "ТВОЙ БАЛАНС", font=font(40, "display"), fill=MUTED)
    value = "∞" if unlimited else str(balance)
    big = font(200, "display")
    d.text((64, 110), value, font=big if not unlimited else font(200, "bold"), fill=GOLD)
    vw, _ = text_size(d, value, big)
    d.text((90 + vw, 250), _plural(balance, ("оценка", "оценки", "оценок")) if not unlimited else "оценок",
           font=font(48, "bold"), fill=TEXT)
    box = pill(d, (70, 420), f"1 оценка = {price}      ", font(34, "bold"), fg=BG, bg=GOLD, pad=(26, 14))
    star(d, (box[2] - 38, (box[1] + box[3]) / 2), 17, BG)
    # декоративные звёзды
    for cx, cy, r, a in ((1010, 200, 110, 255), (1170, 120, 46, 200), (1150, 390, 64, 220), (880, 420, 34, 170)):
        star(d, (cx, cy), r, (*GOLD, a))
    return to_jpeg(img)


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


plural = _plural
