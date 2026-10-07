"""Перевод замеров в баллы и понятные выводы по частям лица."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .frontal import FrontalResult
from .profile import ProfileResult

MALE, FEMALE = "m", "f"


@dataclass
class MetricResult:
    key: str
    label: str
    value_text: str
    ideal_text: str
    score: float
    verdict: str  # "low" | "ok" | "high"
    comment: str
    tip: str | None = None


@dataclass
class PartResult:
    key: str
    title: str
    emoji: str
    score: float  # итоговый балл части по откалиброванной шкале (5 — как у типичного лица)
    metrics: list[MetricResult]
    raw: float = 0.0  # близость к идеалу 0–10 до калибровки


@dataclass
class Report:
    parts: list[PartResult]
    total: float
    tier: str
    tier_emoji: str
    face_shape: str
    strengths: list[MetricResult]
    weaknesses: list[MetricResult]
    tips: list[str]
    warnings: list[str] = field(default_factory=list)
    has_profile: bool = False


def range_score(v: float, lo: float, hi: float, tol: float, floor: float = 3.0) -> tuple[float, str]:
    """10 в центре идеала, 9 на его границах, дальше плавное снижение до floor."""
    if lo <= v <= hi:
        half = (hi - lo) / 2 or 1e-9
        return 10.0 - abs(v - (lo + hi) / 2) / half, "ok"
    d = lo - v if v < lo else v - hi
    s = floor + (9.0 - floor) * math.exp(-0.5 * (d / tol) ** 2)
    return s, ("low" if v < lo else "high")


@dataclass
class Spec:
    key: str
    label: str
    ideal: dict  # пол -> (lo, hi)
    tol: float
    fmt: Callable[[float], str]
    comments: dict  # "low"/"ok"/"high" -> текст
    tips: dict = field(default_factory=dict)  # "low"/"high" -> совет
    weight: float = 1.0
    ideal_text: str | None = None  # своя подпись идеала вместо «lo … hi»


def _num(d: int = 2, suffix: str = "") -> Callable[[float], str]:
    return lambda v: f"{v:.{d}f}{suffix}"


def _deg(v: float) -> str:
    return f"{v:+.1f}°" if abs(v) < 30 else f"{v:.0f}°"


def _both(lo, hi):
    return {MALE: (lo, hi), FEMALE: (lo, hi)}


FRONT_SPECS: dict[str, list[Spec]] = {
    "sym": [
        Spec(
            "sym_err", "Зеркальность черт", _both(0.0, 0.025), 0.035,
            lambda v: f"{100 * math.exp(-4 * v):.0f}%",
            {
                "ok": "Лицо очень симметричное — один из главных маркеров привлекательности.",
                "high": "Есть асимметрия: частично её даёт ракурс и мимика, лёгкая асимметрия есть у всех.",
            },
            {"high": "Снимайся при ровном свете, держа камеру строго на уровне глаз — часть асимметрии уйдёт."},
            weight=1.0,
            ideal_text="идеал ≥ 90%",
        ),
        Spec(
            "nose_dev", "Нос по центру лица", _both(0.0, 0.03), 0.05,
            lambda v: f"смещение {v * 100:.0f}% IPD",
            {"ok": "Нос стоит ровно по средней линии.", "high": "Нос немного смещён от средней линии."},
            ideal_text="идеал ≤ 3%",
        ),
    ],
    "prop": [
        Spec(
            "thirds_dev", "Трети лица", _both(0.0, 0.06), 0.1,
            lambda v: f"откл. {v * 100:.0f}%",
            {
                "ok": "Лоб, середина и низ лица почти равны — классическая гармония.",
                "high": "Трети лица заметно отличаются по высоте.",
            },
            weight=1.5,
            ideal_text="идеал — трети равны, откл. ≤ 6%",
        ),
        Spec(
            "mid_low", "Средняя / нижняя треть", {MALE: (0.92, 1.1), FEMALE: (0.95, 1.15)}, 0.11, _num(2),
            {
                "low": "Нижняя треть длиннее средней — выразительная, «волевая» нижняя часть лица.",
                "ok": "Середина и низ лица в балансе 1:1.",
                "high": "Средняя треть длиннее нижней — нижняя часть лица компактная.",
            },
        ),
        Spec(
            "lower_split", "Губы ↔ подбородок (1:2)", _both(1.75, 2.15), 0.34, lambda v: f"1:{v:.2f}",
            {
                "low": "Подбородок коротковат относительно верхней губы.",
                "ok": "Пропорция верхней губы и подбородка близка к идеальной 1:2.",
                "high": "Подбородок вытянут относительно верхней губы.",
            },
        ),
        Spec(
            "face_wh", "Длина / ширина лица", {MALE: (0.88, 0.97), FEMALE: (0.89, 0.99)}, 0.07, _num(2),
            {
                "low": "Лицо скорее широкое и короткое.",
                "ok": "Соотношение длины и ширины лица гармоничное.",
                "high": "Лицо вытянутое.",
            },
            {
                "low": "Объём на макушке и открытый лоб визуально вытянут лицо.",
                "high": "Объём по бокам в причёске или чёлка визуально укоротят лицо.",
            },
        ),
        Spec(
            "fifths", "Размер глаз (правило пятых)", {MALE: (4.0, 5.1), FEMALE: (4.0, 5.0)}, 0.5,
            lambda v: f"{v:.1f} глаза",
            {
                "low": "Очень крупные глаза относительно ширины лица.",
                "ok": "Глаза не меньше «пятой» ширины лица — крупные глаза считаются одним из самых сильных "
                      "маркеров привлекательности.",
                "high": "Глаза небольшие относительно ширины лица.",
            },
            ideal_text="идеал: ширина лица ≤ 5 глаз",
        ),
    ],
    "eyes": [
        Spec(
            "canthal", "Наклон глаз (кантальный тилт)", {MALE: (3.0, 12.0), FEMALE: (5.0, 14.0)}, 4.0, _deg,
            {
                "low": "Нейтральный/отрицательный наклон — мягкий, немного «грустный» взгляд.",
                "ok": "Позитивный наклон — внешние уголки приподняты, взгляд выразительный.",
                "high": "Очень сильный позитивный наклон — «лисий» разрез глаз.",
            },
            {"low": "Стрелки и подкручивание ресниц с акцентом к вискам визуально приподнимут уголки глаз."},
            weight=1.5,
        ),
        Spec(
            "icd_ew", "Расстояние между глазами", {MALE: (0.9, 1.22), FEMALE: (0.9, 1.2)}, 0.2, _num(2),
            {
                "low": "Глаза посажены близко друг к другу.",
                "ok": "Глаза посажены гармонично — примерно на ширину глаза.",
                "high": "Глаза посажены широко.",
            },
        ),
        Spec(
            "esr", "Межзрачковое / ширина лица", _both(0.43, 0.47), 0.025, _num(3),
            {
                "low": "Зрачки близко относительно ширины лица.",
                "ok": "Положение зрачков на лице близко к идеалу (≈0.46).",
                "high": "Зрачки широко относительно ширины лица.",
            },
        ),
        Spec(
            "ear", "Открытость глаз", {MALE: (0.25, 0.35), FEMALE: (0.28, 0.4)}, 0.075, _num(2),
            {
                "low": "Узкий разрез глаз или прищур.",
                "ok": "Миндалевидная форма глаз.",
                "high": "Большие, округлые глаза.",
            },
            weight=0.6,
        ),
    ],
    "brows": [
        Spec(
            "brow_eye", "Высота бровей над глазами", {MALE: (0.3, 0.52), FEMALE: (0.42, 0.66)}, 0.18, _num(2),
            {
                "low": "Брови посажены низко — глубокий, «хищный» взгляд.",
                "ok": "Брови на гармоничной высоте.",
                "high": "Брови высоко — открытый, слегка удивлённый взгляд.",
            },
            {
                "high": "Чуть более плотная форма бровей с прямым изгибом визуально «опустит» их.",
                "low": "Аккуратная коррекция нижнего края бровей откроет взгляд.",
            },
        ),
    ],
    "nose": [
        Spec(
            "nose_icd", "Ширина носа", _both(0.85, 1.07), 0.13, _num(2),
            {
                "low": "Узкий, аккуратный нос.",
                "ok": "Ширина носа ≈ расстоянию между глазами — идеальная пропорция.",
                "high": "Крылья носа широковаты относительно глаз.",
            },
            {"high": "Контуринг по бокам спинки носа визуально сделает его уже."},
        ),
        Spec(
            "nose_len", "Длина носа", {MALE: (0.41, 0.46), FEMALE: (0.42, 0.47)}, 0.036, lambda v: f"{v * 100:.0f}% лица",
            {
                "low": "Короткий нос.",
                "ok": "Длина носа гармонична высоте лица.",
                "high": "Нос длинноват относительно лица.",
            },
            ideal_text=None,
        ),
    ],
    "lips": [
        Spec(
            "mouth_nose", "Ширина рта / носа", _both(1.5, 1.76), 0.2, _num(2),
            {
                "low": "Рот небольшой относительно носа.",
                "ok": "Ширина рта гармонирует с носом.",
                "high": "Широкий рот, широкая улыбка.",
            },
        ),
        Spec(
            "lip_ratio", "Верхняя : нижняя губа", _both(1.3, 1.9), 0.36, lambda v: f"1:{v:.2f}",
            {
                "low": "Верхняя губа почти равна нижней или полнее.",
                "ok": "Нижняя губа полнее верхней примерно в 1.6 раза — эталон.",
                "high": "Нижняя губа значительно полнее верхней.",
            },
        ),
        Spec(
            "lip_full", "Полнота губ", {MALE: (0.22, 0.36), FEMALE: (0.29, 0.41)}, 0.08, _num(2),
            {"low": "Губы тонкие.", "ok": "Полнота губ гармоничная.", "high": "Губы пухлые."},
            {"low": "Увлажнение и бальзам с лёгким оттенком визуально добавят губам объёма."},
        ),
    ],
    "cheeks": [
        Spec(
            "fwhr", "Ширина скул (индекс FWHR)", {MALE: (1.72, 2.05), FEMALE: (1.66, 1.88)}, 0.17, _num(2),
            {
                "low": "Скулы не доминируют, лицо узкое в верхней части.",
                "ok": "Скулы выраженные и пропорциональные.",
                "high": "Очень широкие, мощные скулы.",
            },
            {"low": "Причёска с объёмом по бокам и снижение отёчности подчеркнут скулы."},
        ),
    ],
    "jaw": [
        Spec(
            "jaw_cheek", "Ширина челюсти / скул", {MALE: (0.77, 0.87), FEMALE: (0.72, 0.79)}, 0.041, _num(2),
            {
                "low": "Челюсть заметно уже скул — мягкий V-образный овал.",
                "ok": "Челюсть в гармонии со скулами.",
                "high": "Широкая, массивная челюсть.",
            },
            {
                "low": "Лёгкая щетина и снижение процента жира визуально усилят линию челюсти.",
                "high": "Мягкие пряди у лица и чуть больше объёма сверху сбалансируют широкую челюсть.",
            },
            weight=1.5,
        ),
        Spec(
            "chin_angle", "Форма подбородка", {MALE: (114.0, 136.0), FEMALE: (104.0, 122.0)}, 11.0,
            lambda v: f"{v:.0f}°",
            {
                "low": "Узкий, заострённый подбородок.",
                "ok": "Подбородок гармоничной ширины.",
                "high": "Широкий, «квадратный» подбородок.",
            },
        ),
    ],
}

PROFILE_SPECS: list[Spec] = [
    Spec(
        "convexity", "Выпуклость профиля", _both(163.0, 175.0), 6.0, lambda v: f"{v:.0f}°",
        {
            "low": "Выпуклый профиль — подбородок отступает назад.",
            "ok": "Прямой гармоничный профиль.",
            "high": "Вогнутый профиль — подбородок выдаётся вперёд.",
        },
        {"low": "Правильная осанка головы, положение языка у нёба и борода/щетина визуально выдвигают подбородок."},
        weight=1.5,
    ),
    Spec(
        "nasofrontal", "Угол лоб–нос", _both(115.0, 135.0), 10.0, lambda v: f"{v:.0f}°",
        {
            "low": "Глубокая переносица, резкий переход лба в нос.",
            "ok": "Переход лба в нос гармоничный.",
            "high": "Плавный, почти прямой переход лба в нос («греческий» профиль).",
        },
    ),
    Spec(
        "nasolabial", "Носогубный угол", {MALE: (90.0, 105.0), FEMALE: (95.0, 115.0)}, 10.0,
        lambda v: f"{v:.0f}°",
        {
            "low": "Кончик носа опущен вниз.",
            "ok": "Кончик носа расположен идеально.",
            "high": "Кончик носа вздёрнут.",
        },
    ),
    Spec(
        "eline_li", "Губы и E-линия", _both(-4.0, 1.0), 2.5, lambda v: f"{v:+.1f} мм",
        {
            "low": "Губы далеко позади линии нос–подбородок — профиль плоский в зоне губ.",
            "ok": "Губы лежат у линии нос–подбородок — эталон Рикеттса.",
            "high": "Губы выступают за линию нос–подбородок.",
        },
    ),
    Spec(
        "dorsum", "Спинка носа", _both(-0.03, 0.03), 0.035, lambda v: f"{v * 100:+.0f}%",
        {
            "low": "Спинка носа вогнутая (курносый нос).",
            "ok": "Ровная спинка носа.",
            "high": "Есть горбинка на спинке носа.",
        },
    ),
]

PART_INFO = {
    "sym": ("Симметрия", "🪞", 1.3),
    "prop": ("Пропорции лица", "📐", 1.3),
    "eyes": ("Глаза", "👁", 1.3),
    "brows": ("Брови", "〰️", 0.6),
    "nose": ("Нос", "👃", 1.0),
    "lips": ("Губы", "👄", 0.9),
    "cheeks": ("Скулы", "💎", 0.8),
    "jaw": ("Челюсть и подбородок", "🗿", 1.1),
    "profile": ("Профиль", "👤", 1.3),
}

# Шкала откалибрована: 5 — типичное лицо, 6.5 — лучше 90% людей, 7.4 — лучше 98%, 8+ — единицы из тысячи
TIERS = [
    (8.5, "Эталонные пропорции", "💎"),
    (7.5, "Очень гармоничное лицо", "🔥"),
    (6.5, "Выше среднего", "✨"),
    (5.5, "Хорошая гармония черт", "👍"),
    (4.5, "Средние пропорции", "🙂"),
    (0.0, "Неклассические пропорции", "🌱"),
]

# ---------------------------------------------------------------- калибровка
# Сырые баллы — это близость замеров к идеальным диапазонам; у большинства людей они 8–9 из 10,
# поэтому без калибровки почти все получали 7–8 («оверрейт»). Сырой балл переводится в шкалу,
# где 5 — типичное лицо. Опорные точки — перцентили 2/10/30/50/70/90/98 сырых баллов на ~390 фото
# обычных людей (набор LFW, разметка Face Mesh); им соответствуют значения CAL_TARGETS.
CAL_TARGETS = (2.8, 3.6, 4.4, 5.0, 5.6, 6.5, 7.4)
CAL_RAW = {
    "total": (7.51, 8.02, 8.35, 8.59, 8.81, 9.07, 9.29),
    "sym": (4.66, 5.44, 7.07, 8.50, 9.11, 9.41, 9.51),
    "prop": (5.61, 6.41, 7.26, 7.87, 8.43, 8.94, 9.31),
    "eyes": (6.66, 7.61, 8.40, 8.80, 9.11, 9.41, 9.57),
    "brows": (5.84, 7.81, 8.88, 9.19, 9.53, 9.86, 9.99),
    "nose": (6.25, 7.05, 8.33, 8.91, 9.30, 9.62, 9.85),
    "lips": (6.85, 7.29, 8.36, 8.92, 9.27, 9.53, 9.71),
    "cheeks": (4.89, 8.06, 9.00, 9.33, 9.63, 9.87, 9.97),
    "jaw": (7.86, 8.99, 9.36, 9.52, 9.67, 9.81, 9.92),
    "profile": (5.00, 6.20, 7.30, 7.90, 8.40, 9.00, 9.40),  # уточняется по набору фото в профиль
}


def _interp(x: float, xs: tuple, ys: tuple) -> float:
    if x <= xs[0]:
        return ys[0]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(x1 - x0, 1e-9)
    return ys[-1]


def calibrate(raw: float, key: str = "total") -> float:
    """Сырой балл (0–10) части лица или итога -> балл по шкале, где 5 — типичное лицо."""
    q = CAL_RAW.get(key, CAL_RAW["total"])
    if key == "total":
        xs = (3.0, min(6.0, q[0] - 0.5)) + q + (q[-1] + 0.15, 9.6, 10.0)
        ys = (1.0, 2.0) + CAL_TARGETS + (8.0, 9.0, 10.0)
    else:
        xs = (0.0,) + q + (10.0,)
        ys = (1.0,) + CAL_TARGETS + (9.5,)
    return round(max(1.0, min(10.0, _interp(raw, xs, ys))), 1)


def _legacy_raw(display: float) -> float:
    """Обратная функция старой шкалы (до калибровки): итог 1–10 -> сырой балл."""
    x = max(0.0, min(1.0, (display - 1.0) / 9.0)) ** (1 / 1.6)
    return 3.0 + 7.0 * x


def legacy_total_to_current(score: float) -> float:
    """Переводит итоговую оценку, сделанную до калибровки шкалы, в текущую шкалу (для старых оценок в базе)."""
    return calibrate(_legacy_raw(score), "total")


def _eval(spec: Spec, value: float | None, gender: str) -> MetricResult | None:
    if value is None or not math.isfinite(value):
        return None
    lo, hi = spec.ideal[gender]
    score, verdict = range_score(value, lo, hi, spec.tol)
    ideal_text = spec.ideal_text or f"идеал {spec.fmt(lo)} … {spec.fmt(hi)}"
    comment = spec.comments.get(verdict) or spec.comments.get("ok", "")
    if verdict != "ok" and score >= 8.5:
        comment = "Почти идеал. " + comment
    return MetricResult(
        key=spec.key,
        label=spec.label,
        value_text=spec.fmt(value),
        ideal_text=ideal_text,
        score=score,
        verdict=verdict,
        comment=comment,
        tip=spec.tips.get(verdict),
    )


def _part(key: str, specs: list[Spec], values: dict, gender: str) -> PartResult | None:
    metrics, weights = [], []
    for spec in specs:
        r = _eval(spec, values.get(spec.key), gender)
        if r:
            metrics.append(r)
            weights.append(spec.weight)
    if not metrics:
        return None
    score = sum(m.score * w for m, w in zip(metrics, weights)) / sum(weights)
    title, emoji, _ = PART_INFO[key]
    return PartResult(key=key, title=title, emoji=emoji, score=calibrate(score, key), metrics=metrics, raw=score)


def _face_shape(m: dict) -> str:
    wh, jc, ca = m["face_wh"], m["jaw_cheek"], m["chin_angle"]
    if wh > 1.01:
        return "вытянутая"
    if wh < 0.89 and jc > 0.827:
        return "квадратная"
    if wh < 0.89 and ca > 130:
        return "круглая"
    if jc < 0.768 and ca < 120:
        return "сердечком"
    if jc > 0.849:
        return "прямоугольная"
    return "овальная"


def build_report(front: FrontalResult, profile: ProfileResult | None, gender: str) -> Report:
    m = dict(front.m)
    if m.get("third_up"):
        thirds = [m["third_up"], m["third_mid"], m["third_low"]]
        mean = sum(thirds) / 3
        m["thirds_dev"] = max(abs(t / mean - 1) for t in thirds)
    parts: list[PartResult] = []
    for key, specs in FRONT_SPECS.items():
        p = _part(key, specs, m, gender)
        if p:
            parts.append(p)
    if profile is not None:
        p = _part("profile", PROFILE_SPECS, profile.m, gender)
        if p:
            parts.append(p)

    total_w = sum(PART_INFO[p.key][2] for p in parts)
    raw = sum(p.raw * PART_INFO[p.key][2] for p in parts) / total_w
    total = calibrate(raw, "total")
    tier, tier_emoji = next((t, e) for th, t, e in TIERS if total >= th)

    all_metrics = [mm for p in parts for mm in p.metrics]
    strengths = sorted([mm for mm in all_metrics if mm.score >= 9.0], key=lambda r: -r.score)[:3]
    weaknesses = sorted([mm for mm in all_metrics if mm.score < 7.0], key=lambda r: r.score)[:3]
    tips: list[str] = []
    for mm in sorted(all_metrics, key=lambda r: r.score):
        if mm.tip and mm.score < 8.0 and mm.tip not in tips:
            tips.append(mm.tip)
        if len(tips) >= 3:
            break

    warnings = list(front.warnings)
    if profile is not None:
        warnings += profile.warnings
    return Report(
        parts=parts,
        total=total,
        tier=tier,
        tier_emoji=tier_emoji,
        face_shape=_face_shape(m),
        strengths=strengths,
        weaknesses=weaknesses,
        tips=tips,
        warnings=warnings,
        has_profile=profile is not None,
    )
