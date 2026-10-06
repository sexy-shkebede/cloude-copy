"""Разлиновка лица поверх фото: анфас (трети, пятые, ось, 68 точек) и профиль (углы, E-линия)."""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

from analyzer.frontal import CANON_SIZE, CANON_TEMPLATE, FrontalResult, _midline_x
from analyzer.profile import ProfileResult
from analyzer.scoring import Report

from .base import (
    AMBER, BG, CREAM, FLAME, GOLD, MUTED, ORANGE, PANEL, TEXT, Neon, bgr_to_pil, darken, font, pill,
    score_color, text_size, to_jpeg, vignette, warm_grade,
)
from .shapes import GROUPS_CLOSED, GROUPS_OPEN, mesh_edges

CANON_IPD = float(np.linalg.norm(CANON_TEMPLATE[0] - CANON_TEMPLATE[1]))
HEADER_H = 110
FOOTER_H = 120


def _frame(photo: Image.Image, title: str, subtitle: str, badge: tuple[str, float] | None,
           legend: list[tuple[tuple, str]]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Добавляет к фото шапку и подвал с легендой."""
    w, h = photo.size
    canvas = Image.new("RGB", (w, h + HEADER_H + FOOTER_H), BG)
    canvas.paste(photo, (0, HEADER_H))
    d = ImageDraw.Draw(canvas, "RGBA")
    # шапка
    d.rectangle([0, 0, w, HEADER_H], fill=PANEL)
    d.rectangle([0, HEADER_H - 3, w, HEADER_H], fill=ORANGE)
    d.text((32, 22), title, font=font(40, "display"), fill=TEXT)
    d.text((34, 72), subtitle, font=font(22, "semibold"), fill=MUTED)
    if badge:
        label, score = badge
        pill(d, (w - 28, HEADER_H / 2), f"{label}  {score:.1f}", font(30, "bold"), fg=BG,
             bg=score_color(score), pad=(20, 10), anchor="rm")
    # подвал
    y0 = HEADER_H + h
    d.rectangle([0, y0, w, y0 + FOOTER_H], fill=PANEL)
    d.rectangle([0, y0, w, y0 + 2], fill=(*ORANGE, 120))
    f = font(21, "semibold")
    x, y = 32, y0 + 24
    for color, text in legend:
        tw, _ = text_size(d, text, f)
        if x + 46 + tw > w - 20:
            x, y = 32, y + 42
        if len(color) == 4 and color[3] == "dashed":  # пунктир — как сама линия на фото
            for sx in (x, x + 14, x + 28):
                d.line([(sx, y + 13), (sx + 8, y + 13)], fill=color[:3], width=5)
        else:
            d.line([(x, y + 13), (x + 34, y + 13)], fill=color, width=5)
        d.text((x + 44, y), text, font=f, fill=TEXT)
        x += 44 + tw + 34
    return canvas, d


def render_front(front: FrontalResult, report: Report) -> bytes:
    f = float(np.clip(front.src_ipd / CANON_IPD, 1.6, 2.4))
    M = front.to_canon * f
    W, H = int(CANON_SIZE[0] * f), int(CANON_SIZE[1] * f)
    warped = cv2.warpAffine(front.image, M, (W, H), flags=cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=BG[::-1])
    p = front.points * f
    m = front.m
    ipd = m["ipd"] * f
    y_brow, y_sn, y_me = m["brow_y"] * f, m["sn_y"] * f, m["menton_y"] * f
    y_hair = front.hairline_y * f if front.hairline_y is not None else None

    top = (y_hair if y_hair is not None else y_brow - 1.3 * ipd) - 0.55 * ipd
    y0, y1 = int(max(0, top)), int(min(H, y_me + 0.75 * ipd))
    photo = bgr_to_pil(warped).crop((0, y0, W, y1))
    photo = vignette(darken(warm_grade(photo, 0.3), 0.3), 0.5)
    p = p - np.array([0, y0], np.float32)
    y_brow, y_sn, y_me = y_brow - y0, y_sn - y0, y_me - y0
    if y_hair is not None:
        y_hair -= y0

    def mid_x(y):  # средняя линия в координатах кадра
        return _midline_x(front.midline, (y + y0) / f) * f

    n = Neon(photo.size)
    xl, xr = float(p[0, 0] - 0.45 * ipd), float(p[16, 0] + 0.45 * ipd)

    # трети лица
    ys = ([y_hair] if y_hair is not None else []) + [y_brow, y_sn, y_me]
    for y in ys:
        n.dashed((xl, y), (xr, y), GOLD, 2.2, 14, 8, 230)
    # пятые: вертикали через края лица и уголки глаз
    for i in (0, 36, 39, 42, 45, 16):
        x = float(p[i, 0])
        n.line([(x, y_brow - 0.3 * ipd), (x, y_sn)], AMBER, 1.4, 130)
    # ось симметрии
    ya, yb = (y_hair if y_hair is not None else y_brow - 1.0 * ipd) - 0.2 * ipd, y_me + 0.35 * ipd
    n.dashed((mid_x(ya), ya), (mid_x(yb), yb), CREAM, 2.4, 12, 8, 235)
    # сетка и контуры
    for a, b in mesh_edges(p):
        n.line([p[a], p[b]], CREAM, 1.0, 50)
    for g in GROUPS_OPEN:
        n.line(p[g], ORANGE, 2.4)
    for g in GROUPS_CLOSED:
        n.line(p[g], ORANGE, 2.4, closed=True)
    # наклон глаз
    for a, b in ((36, 39), (45, 42)):
        pa, pb = p[a], p[b]
        v = pb - pa
        n.line([pa - 0.35 * v, pb + 0.25 * v], GOLD, 2.2, 240)
    # ширина скул и челюсти
    for a, b, col in ((1, 15, FLAME), (4, 12, FLAME)):
        n.line([p[a], p[b]], col, 2.0, 200)
        for q in (p[a], p[b]):
            n.dot(q, 4.5, col)
    for q in p:
        n.dot(q, 2.6, CREAM)
    photo = n.render_onto(photo, glow=5)

    # подписи
    d = ImageDraw.Draw(photo, "RGBA")
    fs = font(20, "bold")
    if y_hair is not None:
        tot = y_me - y_hair
        labels = [(y_hair, y_brow, (y_brow - y_hair) / tot), (y_brow, y_sn, (y_sn - y_brow) / tot), (y_sn, y_me, (y_me - y_sn) / tot)]
    else:
        tot = y_me - y_brow
        labels = [(y_brow, y_sn, (y_sn - y_brow) / tot), (y_sn, y_me, (y_me - y_sn) / tot)]
    bx = min(photo.size[0] - 70, xr + 8)
    for ya_, yb_, share in labels:
        d.line([(bx, ya_ + 4), (bx, yb_ - 4)], fill=(*GOLD, 220), width=3)
        pill(d, (bx + 6, (ya_ + yb_) / 2), f"{share * 100:.0f}%", fs, fg=BG, bg=(*GOLD, 235), pad=(8, 4), anchor="lm",
             clamp=photo.size)
    sym = next((mm for pt in report.parts for mm in pt.metrics if mm.key == "sym_err"), None)
    if sym:
        pill(d, (mid_x(ya), max(8, ya - 6)), f"симметрия {sym.value_text}", fs, fg=TEXT,
             bg=(*PANEL, 220), border=CREAM, pad=(10, 5), anchor="mb", clamp=photo.size)
    tilt = m["canthal"]
    pill(d, (float(p[36, 0] - 0.45 * ipd), float(p[36, 1] - 0.1 * ipd)), f"{tilt:+.1f}°", fs, fg=BG,
         bg=(*GOLD, 235), pad=(8, 4), anchor="rm", clamp=photo.size)
    pill(d, (mid_x(y_me), y_me + 0.14 * ipd), f"челюсть/скулы {m['jaw_cheek']:.2f}", font(18, "bold"),
         fg=BG, bg=(*FLAME, 235), pad=(8, 4), anchor="mt", clamp=photo.size)

    canvas, _ = _frame(
        photo, "РАЗМЕТКА ЛИЦА", "анфас • 68 точек • трети и пятые", ("ИТОГ", report.total),
        [(GOLD, "трети лица"), (AMBER, "правило пятых"), (CREAM, "ось симметрии"), (FLAME, "скулы и челюсть")],
    )
    return to_jpeg(canvas)


def _arc_between(n: Neon, o, a, b, r: float, color) -> float:
    """Рисует дугу внутреннего угла a-o-b и возвращает направление биссектрисы (рад)."""
    a0 = math.degrees(math.atan2(a[1] - o[1], a[0] - o[0]))
    a1 = math.degrees(math.atan2(b[1] - o[1], b[0] - o[0]))
    diff = (a1 - a0) % 360
    if diff <= 180:
        start, sweep = a0, diff
    else:
        start, sweep = a1, 360 - diff
    n.arc(o, r, start, start + sweep, color, 2.6)
    return math.radians(start + sweep / 2)


def render_profile(profile: ProfileResult, report: Report) -> bytes:
    P = {k: np.array(v, np.float32) for k, v in profile.points.items()}
    scale = float(np.linalg.norm(P["N"] - P["Pog"]))
    facing = -1.0 if profile.flipped else 1.0  # +1 — смотрит вправо
    img_h, img_w = profile.image.shape[:2]
    front_x = float(P["Prn"][0])
    xa, xb = front_x - facing * 1.35 * scale, front_x + facing * 0.5 * scale
    x0, x1 = int(max(0, min(xa, xb))), int(min(img_w, max(xa, xb)))
    y0 = int(max(0, P["G"][1] - 0.8 * scale))
    y1 = int(min(img_h, P["Pog"][1] + 0.6 * scale))
    crop = profile.image[y0:y1, x0:x1]
    k = 900.0 / max(crop.shape[1], 1)
    k = min(k, 1300.0 / max(crop.shape[0], 1))
    crop = cv2.resize(crop, (int(crop.shape[1] * k), int(crop.shape[0] * k)), interpolation=cv2.INTER_CUBIC)
    photo = vignette(darken(warm_grade(bgr_to_pil(crop), 0.3), 0.28), 0.5)

    def t(q):
        return np.array([(q[0] - x0) * k, (q[1] - y0) * k], np.float32)

    Q = {name: t(v) for name, v in P.items()}
    cont = np.array([t(q) for q in profile.contour], np.float32)
    lo, hi = Q["G"][1] - 0.6 * scale * k, Q["Pog"][1] + 0.45 * scale * k
    cont = cont[(cont[:, 1] > lo) & (cont[:, 1] < hi)]

    n = Neon(photo.size)
    n.line(cont, ORANGE, 2.6, 170)  # контур — фоновая линия, измерения ярче
    # E-линия Рикеттса
    e = Q["Pog"] - Q["Prn"]
    n.dashed(Q["Prn"] - 0.18 * e, Q["Pog"] + 0.25 * e, GOLD, 2.4, 14, 8)
    # выпуклость: G–Sn–Pog
    n.line([Q["G"], Q["Sn"], Q["Pog"]], FLAME, 2.4, 230)
    # лоб–нос
    n.line([Q["G"], Q["N"], Q["Prn"]], GOLD, 2.4, 235)
    # носогубный
    if "Cm" in Q:
        v = Q["Cm"] - Q["Sn"]
        n.line([Q["Sn"] + v * 1.6, Q["Sn"], Q["Ls"]], CREAM, 2.6, 245)
    r_arc = 0.11 * scale * k
    bis_nf = _arc_between(n, Q["N"], Q["G"], Q["Prn"], r_arc, GOLD)
    bis_cv = _arc_between(n, Q["Sn"], Q["G"], Q["Pog"], r_arc * 1.3, FLAME)
    bis_nl = _arc_between(n, Q["Sn"], Q["Cm"], Q["Ls"], r_arc * 0.8, CREAM) if "Cm" in Q else None
    for name in ("G", "N", "Prn", "Sn", "Ls", "Li", "Pog"):
        n.dot(Q[name], 6, CREAM)
        n.ring(Q[name], 10, ORANGE, 2)
    photo = n.render_onto(photo, glow=5)

    d = ImageDraw.Draw(photo, "RGBA")
    fs = font(21, "bold")
    m = profile.m

    def label_at(o, bis, dist, text, color):
        pos = (o[0] + math.cos(bis) * dist, o[1] + math.sin(bis) * dist)
        pill(d, pos, text, fs, fg=BG, bg=(*color, 235), pad=(9, 5), anchor="mm", clamp=photo.size)

    label_at(Q["N"], bis_nf + math.pi, 0.2 * scale * k, f"{m['nasofrontal']:.0f}°", GOLD)
    label_at(Q["Sn"], bis_cv + math.pi, 0.24 * scale * k, f"{m['convexity']:.0f}°", FLAME)
    if bis_nl is not None:
        label_at(Q["Sn"], bis_nl, 0.2 * scale * k, f"{m['nasolabial']:.0f}°", CREAM)
    ex = Q["Pog"] + 0.3 * e
    pill(d, (ex[0], ex[1]), f"E-линия {m['eline_li']:+.0f} мм", font(19, "bold"), fg=BG,
         bg=(*GOLD, 235), pad=(9, 5), anchor="mt", clamp=photo.size)

    part = next((pt for pt in report.parts if pt.key == "profile"), None)
    canvas, _ = _frame(
        photo, "ПРОФИЛЬ 90°", "углы профиля • E-линия Рикеттса", ("ПРОФИЛЬ", part.score) if part else None,
        [(GOLD, "лоб–нос"), (CREAM, "носогубный"), (FLAME, "выпуклость"), ((*GOLD, "dashed"), "E-линия")],
    )
    return to_jpeg(canvas)
