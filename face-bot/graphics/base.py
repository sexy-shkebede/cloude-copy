"""Общие инструменты рисования: шрифты, палитра, неоновые линии, градиенты."""
from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# «Огненная» палитра в стиле приветственного баннера
BG = (13, 6, 3)  # почти чёрный с коричневым
EMBER = (46, 16, 5)  # тёмный жар для градиентов
PANEL = (22, 9, 4)  # шапки и подвалы
TEXT = (255, 246, 236)
MUTED = (196, 152, 120)
ORANGE = (255, 122, 26)  # основной неон: сетка, контуры
AMBER = (255, 168, 38)
GOLD = (255, 205, 64)  # плашки, трети, E-линия
FLAME = (255, 74, 36)  # красно-оранжевый акцент
CREAM = (255, 230, 200)  # светлые вспомогательные линии
RED = (255, 64, 64)


# Статические начертания Montserrat (не зависят от поддержки вариативных шрифтов в FreeType)
FONT_FILES = {
    "regular": "Montserrat-Regular.ttf",
    "semibold": "Montserrat-SemiBold.ttf",
    "bold": "Montserrat-Bold.ttf",
    "display": "Montserrat-Display.ttf",  # жирный, как заголовок баннера
}


@lru_cache(maxsize=64)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """weight: regular | semibold | bold | display."""
    try:
        return ImageFont.truetype(str(FONTS_DIR / FONT_FILES.get(weight, FONT_FILES["regular"])), size)
    except OSError:
        return ImageFont.load_default()


def score_color(score: float) -> tuple[int, int, int]:
    """Красный (низко) → оранжевый (типично, 5) → золотой (высоко) по шкале 0..10."""
    stops = [(0.0, RED), (3.0, RED), (5.0, ORANGE), (7.0, GOLD), (10.0, (255, 240, 150))]
    s = max(0.0, min(10.0, score))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if s <= b:
            t = 0 if b == a else (s - a) / (b - a)
            return tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return stops[-1][1]


def gradient(size: tuple[int, int], top: tuple, bottom: tuple, angle: float = 90.0) -> Image.Image:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = math.radians(angle)
    t = (xx * math.cos(a) + yy * math.sin(a)) / (abs(w * math.cos(a)) + abs(h * math.sin(a)))
    t = np.clip(t, 0, 1)[..., None]
    arr = np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def radial_glow(img: Image.Image, center: tuple[float, float], radius: float, color: tuple, strength: float = 0.5) -> Image.Image:
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / radius
    a = np.clip(1 - d, 0, 1) ** 2 * strength
    base = np.asarray(img.convert("RGB"), np.float32)
    out = base * (1 - a[..., None]) + np.array(color, np.float32) * a[..., None]
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")


def vignette(img: Image.Image, strength: float = 0.55) -> Image.Image:
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    k = 1 - strength * np.clip(d, 0, 1) ** 1.6
    arr = np.asarray(img.convert("RGB"), np.float32) * k[..., None]
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


def darken(img: Image.Image, amount: float = 0.35, tint: tuple = BG) -> Image.Image:
    overlay = Image.new("RGB", img.size, tint)
    return Image.blend(img.convert("RGB"), overlay, amount)


def warm_grade(img: Image.Image, amount: float = 0.3) -> Image.Image:
    """Тёплая «огненная» цветокоррекция фото, как на баннере."""
    from PIL import ImageOps

    toned = ImageOps.colorize(img.convert("L"), black=(12, 4, 2), mid=(150, 70, 30), white=(255, 214, 170))
    return Image.blend(img.convert("RGB"), toned, amount)


def light_streaks(img: Image.Image, seed: int = 0, strength: float = 1.0) -> Image.Image:
    """Размытые огненные шлейфы света на фоне."""
    w, h = img.size
    rng = np.random.RandomState(seed)
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(7):
        y0 = rng.uniform(-0.2, 1.1) * h
        amp = rng.uniform(0.1, 0.35) * h
        tilt = rng.uniform(-0.5, 0.5) * h
        phase = rng.uniform(0, math.pi)
        xs = np.linspace(-0.1 * w, 1.1 * w, 60)
        ys = y0 + tilt * (xs / w) + amp * np.sin(xs / w * math.pi * rng.uniform(0.6, 1.4) + phase)
        col = ORANGE if i % 3 else AMBER
        k = rng.uniform(0.35, 0.8)
        d.line(list(zip(xs.tolist(), ys.tolist())), fill=tuple(int(c * k) for c in col), width=int(rng.uniform(2, 7)))
    glow = layer.filter(ImageFilter.GaussianBlur(14))
    core = layer.filter(ImageFilter.GaussianBlur(2))
    arr = np.asarray(img.convert("RGB"), np.float32)
    add = (np.asarray(glow, np.float32) * 0.9 + np.asarray(core, np.float32) * 0.5) * strength
    out = 255 - (255 - arr) * (255 - add.clip(0, 255)) / 255  # режим «экран»
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")


class Neon:
    """Слой для неоновых линий/точек с суперсэмплингом (сглаживание) и свечением."""

    def __init__(self, size: tuple[int, int], ss: int = 2) -> None:
        self.size = size
        self.ss = ss
        self.layer = Image.new("RGBA", (size[0] * ss, size[1] * ss), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.layer)

    def _p(self, pts):
        return [(float(x) * self.ss, float(y) * self.ss) for x, y in pts]

    def line(self, pts, color, width: float = 2.0, alpha: int = 255, closed: bool = False) -> None:
        pts = list(pts)
        if closed:
            pts = pts + [pts[0]]
        self.draw.line(self._p(pts), fill=(*color, alpha), width=max(1, int(width * self.ss)), joint="curve")

    def dashed(self, a, b, color, width: float = 2.0, dash: float = 10, gap: float = 7, alpha: int = 255) -> None:
        a, b = np.array(a, float), np.array(b, float)
        length = float(np.linalg.norm(b - a))
        if length < 1:
            return
        d = (b - a) / length
        pos = 0.0
        while pos < length:
            e = min(pos + dash, length)
            self.line([a + d * pos, a + d * e], color, width, alpha)
            pos = e + gap

    def dot(self, p, r: float, color, alpha: int = 255) -> None:
        x, y = p[0] * self.ss, p[1] * self.ss
        rr = r * self.ss
        self.draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*color, alpha))

    def ring(self, p, r: float, color, width: float = 2.0, alpha: int = 255) -> None:
        x, y = p[0] * self.ss, p[1] * self.ss
        rr = r * self.ss
        self.draw.ellipse([x - rr, y - rr, x + rr, y + rr], outline=(*color, alpha), width=max(1, int(width * self.ss)))

    def arc(self, center, r: float, a0: float, a1: float, color, width: float = 2.0, alpha: int = 255) -> None:
        """Дуга от a0 до a1 по часовой стрелке (градусы от оси x, ось y вниз)."""
        x, y = center[0] * self.ss, center[1] * self.ss
        rr = r * self.ss
        self.draw.arc([x - rr, y - rr, x + rr, y + rr], a0, a1, fill=(*color, alpha), width=max(1, int(width * self.ss)))

    def render_onto(self, base: Image.Image, glow: float = 6.0) -> Image.Image:
        layer = self.layer.resize(self.size, Image.LANCZOS)
        out = base.convert("RGBA")
        if glow > 0:
            g = layer.filter(ImageFilter.GaussianBlur(glow))
            out = Image.alpha_composite(out, g)
            out = Image.alpha_composite(out, g)
        out = Image.alpha_composite(out, layer)
        return out.convert("RGB")


def text_size(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont) -> tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=f)
    return r - l, b - t


def pill(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, f, fg=TEXT, bg=(0, 0, 0, 170), border=None,
         pad=(12, 6), anchor="lt", clamp: tuple[int, int] | None = None) -> tuple:
    """Плашка с текстом. anchor: lt | mt | rt | lm | rm | mm (позиция xy относительно плашки).
    clamp=(W, H) — не дать плашке выйти за края картинки."""
    tw, th = text_size(draw, text, f)
    w, h = tw + 2 * pad[0], th + 2 * pad[1]
    x, y = xy
    if anchor[0] == "m":
        x -= w / 2
    elif anchor[0] == "r":
        x -= w
    if anchor[1] == "m":
        y -= h / 2
    elif anchor[1] == "b":
        y -= h
    if clamp:
        x = min(max(6, x), clamp[0] - w - 6)
        y = min(max(6, y), clamp[1] - h - 6)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=bg, outline=border, width=2 if border else 0)
    l, t, _, _ = draw.textbbox((0, 0), text, font=f)
    draw.text((x + pad[0] - l, y + pad[1] - t), text, font=f, fill=fg)
    return (x, y, x + w, y + h)


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if text_size(draw, t, f)[0] <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def star(draw: ImageDraw.ImageDraw, c: tuple[float, float], r: float, fill) -> None:
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((c[0] + rr * math.cos(a), c[1] + rr * math.sin(a)))
    draw.polygon(pts, fill=fill)


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def bgr_to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr[..., ::-1].copy(), "RGB")
