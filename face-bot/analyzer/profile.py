"""Анализ фото в профиль (90°): контур лица и углы профиля.

1. Детектор находит лицо, 3D-модель головы (3DDFA_V2) ставит 68 точек и оценивает поворот головы.
   По ней понятно, правда ли это профиль, в какую сторону смотрит лицо и где примерно нос, губы и подбородок.
2. Фон отделяется от головы (быстрая модель однотонного фона или GrabCut), по каждой строке берётся
   крайняя передняя точка силуэта — это линия профиля.
3. Точки профиля (глабелла, переносица, кончик носа, основание носа, губы, подбородок) ищутся на линии
   профиля только рядом с тем местом, где их видит 3D-модель. Поэтому подбородок не может «уехать»
   к уху или в волосы, а при сбое силуэта используются точки самой 3D-модели.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .common import AnalysisError, decode_image
from .models import get_models

POINT_NAMES = {
    "G": "Глабелла",
    "N": "Переносица",
    "Prn": "Кончик носа",
    "Sn": "Основание носа",
    "Ls": "Верхняя губа",
    "St": "Смыкание губ",
    "Li": "Нижняя губа",
    "B": "Подбородочная складка",
    "Pog": "Подбородок",
    "C": "Шея",
}

MIN_PROFILE_YAW = 50.0  # меньше — это ракурс 3/4, углы профиля по нему считать нельзя
FRONTAL_YAW = 25.0  # меньше — человек смотрит в камеру


@dataclass
class ProfileResult:
    image: np.ndarray  # исходное фото (BGR, в исходной ориентации)
    flipped: bool  # True, если лицо смотрит влево (для расчётов зеркалили)
    contour: np.ndarray  # Kx2 точки линии профиля (в координатах исходного фото)
    points: dict  # имя -> (x, y) в координатах исходного фото
    face_box: tuple[float, float, float, float]  # в координатах исходного фото
    m: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    head: np.ndarray | None = None  # 68x3 точек 3D-модели головы (координаты исходного фото)
    yaw: float = 0.0


def _angle_at(a: np.ndarray, o: np.ndarray, b: np.ndarray) -> float:
    v1, v2 = a - o, b - o
    c = float(v1 @ v2 / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _signed_dist_to_line(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Расстояние от p до прямой a-b; >0 — точка впереди линии (по направлению взгляда, +x)."""
    d = b - a
    n = np.array([d[1], -d[0]], np.float32)  # нормаль
    n /= np.linalg.norm(n) + 1e-9
    if n[0] < 0:
        n = -n
    return float((p - a) @ n)


def _scan_bg(small: np.ndarray, front: int, strip: int) -> np.ndarray | None:
    """Быстрый метод для однотонного фона: в каждой строке берём цвет фона прямо перед лицом
    и идём от него назад, пока цвет не начнёт заметно отличаться — это и есть край профиля."""
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    sh, sw = lab.shape[:2]
    x1 = min(sw, front + max(8, strip))
    if x1 - front < 4 or front < 10:
        return None
    zone = lab[:, front:x1].astype(np.float32)
    ref = np.median(zone, axis=1)  # цвет фона в каждой строке
    ref = cv2.medianBlur(np.clip(ref, 0, 255).astype(np.uint8).reshape(sh, 1, 3), 5).reshape(sh, 3).astype(np.float32)
    spread = np.median(np.linalg.norm(zone - ref[:, None, :], axis=2), axis=1)
    thr = np.maximum(14.0, 4.0 * spread)
    d = np.linalg.norm(lab[:, :front].astype(np.float32) - ref[:, None, :], axis=2)
    nonbg = (d > thr[:, None]).astype(np.uint8)
    nonbg = cv2.erode(nonbg, np.ones((1, 3), np.uint8))  # минимум 3 пикселя подряд
    has = nonbg.any(axis=1)
    if has.sum() < sh * 0.5:
        return None
    xs = np.where(has, front - 1 - np.argmax(nonbg[:, ::-1], axis=1), np.nan).astype(np.float32)
    return xs


def _segment_grabcut(small: np.ndarray, bx0: float, by0: float, bw: float, bh: float, front: int) -> np.ndarray | None:
    sh, sw = small.shape[:2]
    mask = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    mask[int(max(0, by0)) : int(min(sh, by0 + bh)), int(max(0, bx0)) : int(min(sw, bx0 + bw))] = cv2.GC_PR_FGD
    # уверенное «лицо»: центральная часть бокса, отодвинутая от линии профиля
    mask[
        int(max(0, by0 + 0.2 * bh)) : int(min(sh, by0 + 0.85 * bh)),
        int(max(0, bx0 + 0.15 * bw)) : int(min(sw, bx0 + 0.55 * bw)),
    ] = cv2.GC_FGD
    # уверенный фон: полоса перед лицом и правый край
    mask[:, front:] = cv2.GC_BGD
    mask[:, sw - max(3, sw // 30) :] = cv2.GC_BGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    return ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8)


def _profile_line(img: np.ndarray, box, eye_y: float, mouth_y: float, method: str):
    """Сегментация силуэта и извлечение передней линии профиля (лицо смотрит вправо).

    method: "bg" — быстрая модель фона, "grabcut" — медленнее, но для сложного фона.
    """
    H, W = img.shape[:2]
    x, y, w, h = box
    face_h = max(mouth_y - eye_y, 0.15 * h) / 0.54  # примерная высота лица: брови..подбородок
    rx0 = int(max(0, x - 0.3 * w))
    rx1 = int(min(W, x + w + 0.6 * w))
    ry0 = int(max(0, eye_y - 1.0 * face_h))
    ry1 = int(min(H, mouth_y + 0.75 * face_h))
    roi = img[ry0:ry1, rx0:rx1]
    if roi.size == 0 or roi.shape[0] < 40 or roi.shape[1] < 40:
        raise AnalysisError("profile_fail")
    target = 480.0 if method == "bg" else 240.0
    s = min(1.0, target / max(roi.shape[:2]))
    small = cv2.resize(roi, (int(roi.shape[1] * s), int(roi.shape[0] * s)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (3, 3), 0)
    sh, sw = small.shape[:2]
    bx0, by0 = (x - rx0) * s, (y - ry0) * s
    bw, bh = w * s, h * s
    front = int(min(sw - 2, bx0 + bw + 0.3 * bw))
    if method == "bg":
        xs = _scan_bg(small, int(min(sw - 2, bx0 + 1.05 * bw)), int(0.2 * bw))
        if xs is None:
            raise AnalysisError("profile_fail")
    else:
        fg = _segment_grabcut(small, bx0, by0, bw, bh, front)
        if fg is None:
            raise AnalysisError("profile_fail")
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(fg)
        if n <= 1:
            raise AnalysisError("profile_fail")
        cy, cx = int(min(sh - 1, by0 + 0.5 * bh)), int(min(sw - 1, bx0 + 0.35 * bw))
        lab = labels[cy, cx] if labels[cy, cx] > 0 else 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        fg = labels == lab
        # самая передняя (правая) точка силуэта в каждой строке
        has = fg.any(axis=1)
        if has.sum() < sh * 0.5:
            raise AnalysisError("profile_fail")
        xs = np.where(has, sw - 1 - np.argmax(fg[:, ::-1], axis=1), np.nan).astype(np.float32)
    has = ~np.isnan(xs)
    idx = np.arange(sh)
    xs = np.interp(idx, idx[has], xs[has]).astype(np.float32)
    xs = cv2.medianBlur(xs.reshape(-1, 1), 5).ravel()
    sigma = max(1.0, sh * 0.006)
    xs = cv2.GaussianBlur(xs.reshape(-1, 1), (1, 0), sigmaX=0, sigmaY=sigma).ravel()
    # обратно в координаты исходного фото
    ys_full = ry0 + idx / s
    xs_full = rx0 + xs / s
    return np.stack([xs_full, ys_full], axis=1).astype(np.float32), face_h


def _argext(contour: np.ndarray, y0: float, y1: float, mode: str):
    ys = contour[:, 1]
    sel = np.flatnonzero((ys >= min(y0, y1)) & (ys <= max(y0, y1)))
    if len(sel) == 0:
        return None
    seg = contour[sel, 0]
    i = sel[int(np.argmax(seg) if mode == "max" else np.argmin(seg))]
    return contour[i].copy()


def _detect_box(img: np.ndarray) -> tuple[tuple[float, float, float, float], str] | None:
    """Бокс лица: YuNet, а если он не видит лицо сбоку — каскад Хаара для профиля (в обе стороны)."""
    models = get_models()
    faces = models.detect_faces(img, 0.5)
    if len(faces):
        x, y, w, h = map(float, faces[0][:4])
        return (x, y, w, h), "yunet"
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    W = gray.shape[1]
    cands = []
    for flip in (False, True):
        g = cv2.flip(gray, 1) if flip else gray
        for x, y, w, h in models.detect_profile_haar(g):
            if flip:
                x = W - x - w
            cands.append((x, y, w, h))
    if not cands:
        return None
    x, y, w, h = max(cands, key=lambda c: c[2] * c[3])
    return (float(x), float(y), float(w), float(h)), "haar"


def _fit_head(img: np.ndarray, box) -> tuple[np.ndarray, dict]:
    """Два прохода 3DDFA: по боксу детектора, затем по собственным точкам (кадр точнее)."""
    models = get_models()
    pts, pose = models.head3d(img, models.roi_from_box(box))
    pts, pose = models.head3d(img, models.roi_from_points(pts))
    return pts, pose


# Точки средней линии лица в схеме 68 точек: в профиль они лежат прямо на силуэте
def _priors(h: np.ndarray) -> dict:
    """Примерные положения точек профиля по 3D-модели (лицо смотрит вправо)."""
    pr = {
        "G": (h[21, :2] + h[22, :2]) / 2,
        "N": h[27, :2],
        "Prn": h[30, :2],
        "Sn": h[33, :2],
        "Ls": h[51, :2],
        "St": (h[62, :2] + h[66, :2]) / 2,
        "Li": h[57, :2],
        "Pog": h[8, :2] + 0.25 * (h[57, :2] - h[8, :2]),  # подбородок чуть выше нижней точки (ментона)
    }
    pr["B"] = (pr["Li"] + pr["Pog"]) / 2
    return {k: np.asarray(v, np.float32) for k, v in pr.items()}


def _contour_points(contour: np.ndarray, pr: dict, scale: float) -> dict | None:
    """Ищет точки на линии профиля в окрестности примерных положений от 3D-модели.
    None — если силуэт не совпадает с 3D-моделью (поймали волосы, ухо, фон) или профиль неправдоподобный."""
    P: dict = {}
    tol = 0.07 * scale  # окно поиска по высоте вокруг точки 3D-модели

    def find(name, lo, hi, mode):
        P[name] = _argext(contour, lo, hi, mode)
        return P[name] is not None

    def around(name, lo_bound=None, hi_bound=None, k=1.0):
        y = float(pr[name][1])
        lo, hi = y - tol * k, y + tol * k
        if lo_bound is not None:
            lo = max(lo, lo_bound)
        if hi_bound is not None:
            hi = min(hi, hi_bound)
        return lo, hi

    ok = (
        find("Prn", *around("Prn", k=1.3), "max")
        and find("N", *around("N", hi_bound=P["Prn"][1] - 0.05 * scale, k=1.3), "min")
        and find("G", *around("G", hi_bound=P["N"][1] - 0.02 * scale, k=1.5), "max")
        and find("Sn", *around("Sn", lo_bound=P["Prn"][1] + 0.01 * scale), "min")
        and find("Ls", *around("Ls", lo_bound=P["Sn"][1] + 0.01 * scale), "max")
        and find("St", *around("St", lo_bound=P["Ls"][1] + 0.005 * scale), "min")
        and find("Li", *around("Li", lo_bound=P["St"][1] + 0.005 * scale), "max")
        and find("B", *around("B", lo_bound=P["Li"][1] + 0.01 * scale), "min")
        and find("Pog", *around("Pog", lo_bound=P["B"][1] + 0.01 * scale, k=1.5), "max")
    )
    if not ok:
        return None
    find("C", P["Pog"][1] + 0.12 * scale, P["Pog"][1] + 0.45 * scale, "min")

    # силуэт должен проходить рядом с 3D-моделью: иначе это волосы, ухо или фон
    for name, limit in (("Prn", 0.12), ("N", 0.15), ("Ls", 0.15), ("Pog", 0.2)):
        if abs(float(P[name][0] - pr[name][0])) > limit * scale:
            return None
    return P if _plausible(P) else None


def _plausible(P: dict) -> bool:
    scale = float(np.linalg.norm(P["N"] - P["Pog"]))
    if scale < 30:
        return False
    order = ["G", "N", "Prn", "Sn", "Ls", "St", "Li", "B", "Pog"]
    if any(P[a][1] >= P[b][1] for a, b in zip(order, order[1:])):
        return False
    if (P["Prn"][0] - P["Sn"][0]) < 0.05 * scale or (P["Prn"][0] - P["N"][0]) < 0.07 * scale:
        return False
    if (P["Ls"][0] - P["Sn"][0]) < -0.02 * scale or (P["Pog"][0] - P["B"][0]) < -0.02 * scale:
        return False
    convexity = _angle_at(P["G"], P["Sn"], P["Pog"])
    nasofrontal = _angle_at(P["G"], P["N"], P["Prn"])
    projection = _signed_dist_to_line(P["Prn"], P["N"], P["Sn"]) / scale
    return 140 <= convexity and 95 <= nasofrontal <= 170 and 0.04 <= projection <= 0.35


def _model_contour(h: np.ndarray) -> np.ndarray:
    """Запасная линия профиля прямо по средней линии 3D-модели (если силуэт выделить не удалось)."""
    pr = _priors(h)
    chain = [pr["G"], pr["N"], h[28, :2], h[29, :2], pr["Prn"], pr["Sn"], pr["Ls"], pr["St"], pr["Li"], pr["B"],
             pr["Pog"], h[8, :2]]
    pts = np.array(chain, np.float32)
    ys = np.arange(float(pts[0, 1]), float(pts[-1, 1]), 1.0)
    order = np.argsort(pts[:, 1], kind="stable")
    xs = np.interp(ys, pts[order, 1], pts[order, 0])
    return np.stack([xs, ys], axis=1).astype(np.float32)


def analyze_profile(data: bytes) -> ProfileResult:
    img = decode_image(data)
    found = _detect_box(img)
    if found is None:
        raise AnalysisError("no_profile")
    box, source = found
    try:
        head, pose = _fit_head(img, box)
    except cv2.error:
        raise AnalysisError("profile_fail") from None
    yaw = abs(pose["yaw"])
    if yaw < FRONTAL_YAW:
        raise AnalysisError("not_profile")
    if yaw < MIN_PROFILE_YAW:
        raise AnalysisError("half_profile")

    W = img.shape[1]
    # куда смотрит лицо: кончик носа впереди точек овала лица (они в профиль сходятся к уху)
    flipped = bool(head[30, 0] < head[:17, 0].mean())
    work = cv2.flip(img, 1) if flipped else img
    h = head.copy()
    if flipped:
        h[:, 0] = W - 1 - h[:, 0]
    pr = _priors(h)
    scale = float(np.linalg.norm(pr["N"] - h[8, :2]))
    if scale < 30:
        raise AnalysisError("too_small")

    lo, hi = h[:, :2].min(0), h[:, :2].max(0)
    x, y, w, hh = float(lo[0]), float(lo[1]), float(hi[0] - lo[0]), float(hi[1] - lo[1])
    eye_y = float(h[36:48, 1].mean())
    mouth_y = float(h[48:68, 1].mean())

    P, contour = None, None
    for method, seed in (("bg", 0), ("grabcut", 0), ("grabcut", 1)):
        cv2.setRNGSeed(seed)
        try:
            cont, _ = _profile_line(work, (x, y, w, hh), eye_y, mouth_y, method)
        except AnalysisError:
            continue
        P = _contour_points(cont, pr, scale)
        if P is not None:
            contour = cont
            break

    warnings: list[str] = []
    if P is None:
        # силуэт не выделился (сложный фон, волосы) — берём точки самой 3D-модели
        contour = _model_contour(h)
        P = {k: v.copy() for k, v in pr.items()}
        if not _plausible(P):
            raise AnalysisError("profile_fail")
        warnings.append("Контур профиля размечен по 3D-модели головы — углы приблизительные. "
                        "Для точности сфотографируйся на однотонном фоне.")
    if source == "haar":
        warnings.append("Лицо в профиль найдено с трудом — точность ниже обычной.")
    if yaw < 65:
        warnings.append("Голова повёрнута не совсем боком — углы профиля могут быть неточными.")

    scale = float(np.linalg.norm(P["N"] - P["Pog"]))
    m: dict = {}
    m["nasofrontal"] = _angle_at(P["G"], P["N"], P["Prn"])
    # колумелла: точка контура между кончиком носа и основанием
    cm_y = P["Sn"][1] - 0.45 * (P["Sn"][1] - P["Prn"][1])
    cm = contour[int(np.argmin(np.abs(contour[:, 1] - cm_y)))].copy()
    P["Cm"] = cm
    m["nasolabial"] = _angle_at(cm, P["Sn"], P["Ls"])
    m["convexity"] = _angle_at(P["G"], P["Sn"], P["Pog"])
    # E-линия Рикеттса (кончик носа — подбородок); переводим в «мм» (N–Pog ≈ 110 мм)
    mm = 110.0 / scale
    m["eline_ls"] = _signed_dist_to_line(P["Ls"], P["Prn"], P["Pog"]) * mm
    m["eline_li"] = _signed_dist_to_line(P["Li"], P["Prn"], P["Pog"]) * mm
    # спинка носа: максимальное отклонение контура от прямой N–Prn (горбинка > 0)
    sel = (contour[:, 1] > P["N"][1]) & (contour[:, 1] < P["Prn"][1])
    if sel.any():
        devs = [_signed_dist_to_line(q, P["N"], P["Prn"]) for q in contour[sel]]
        i = int(np.argmax(np.abs(devs)))
        m["dorsum"] = devs[i] / float(np.linalg.norm(P["Prn"] - P["N"]))
    else:
        m["dorsum"] = 0.0
    m["nose_proj"] = _signed_dist_to_line(P["Prn"], P["N"], P["Sn"]) / scale
    m["chin_proj"] = _signed_dist_to_line(P["Pog"], P["N"], P["Sn"]) * mm
    if P.get("C") is not None:
        m["neck_depth"] = float(P["Pog"][0] - P["C"][0]) / scale
    else:
        m["neck_depth"] = None

    # обратно в исходную ориентацию
    def unflip(q):
        q = np.asarray(q, np.float32).copy()
        if flipped:
            q[..., 0] = W - 1 - q[..., 0]
        return q

    points = {k: tuple(map(float, unflip(v))) for k, v in P.items() if v is not None}
    bx = W - x - w if flipped else x
    return ProfileResult(
        image=img,
        flipped=flipped,
        contour=unflip(contour),
        points=points,
        face_box=(bx, y, w, hh),
        m=m,
        warnings=warnings,
        head=head,
        yaw=yaw,
    )
