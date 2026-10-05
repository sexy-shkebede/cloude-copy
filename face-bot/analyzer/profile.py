"""Анализ фото в профиль (90°): контур лица и углы профиля.

Лицо находится детектором, фон отделяется GrabCut, затем по каждой строке
берётся крайняя передняя точка силуэта — это линия профиля. На ней ищутся
глабелла, переносица, кончик носа, подносовая точка, губы и подбородок.
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


@dataclass
class ProfileResult:
    image: np.ndarray  # исходное фото (BGR, в исходной ориентации)
    flipped: bool  # True, если лицо смотрит влево (для расчётов зеркалили)
    contour: np.ndarray  # Kx2 точки линии профиля (в координатах исходного фото)
    points: dict  # имя -> (x, y) в координатах исходного фото
    face_box: tuple[float, float, float, float]  # в координатах исходного фото
    m: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


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


def _find_face(img: np.ndarray):
    """Возвращает (бокс, глаз_y, рот_y, смотрит_вправо) или None."""
    models = get_models()
    faces = models.detect_faces(img, 0.5)
    if len(faces):
        f = faces[0]
        x, y, w, h = map(float, f[:4])
        pts = f[4:14].reshape(5, 2)
        eyes = pts[:2]
        nose = pts[2]
        mouth = pts[3:5]
        # насколько нос вынесен вперёд относительно глаз — признак профиля
        offset = float(nose[0] - eyes[:, 0].mean()) / w
        eye_gap = abs(float(eyes[0, 0] - eyes[1, 0])) / w
        return {
            "box": (x, y, w, h),
            "eye_y": float(eyes[:, 1].mean()),
            "mouth_y": float(mouth[:, 1].mean()),
            "right": offset > 0,
            "offset": abs(offset),
            "eye_gap": eye_gap,
            "source": "yunet",
        }
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    W = gray.shape[1]
    cands = []
    for flip in (False, True):
        g = cv2.flip(gray, 1) if flip else gray
        for x, y, w, h in models.detect_profile_haar(g):
            if flip:
                x = W - x - w
            # каскад обучен на лицах, смотрящих влево
            cands.append(((x, y, w, h), flip))
    if not cands:
        return None
    (x, y, w, h), flip = max(cands, key=lambda c: c[0][2] * c[0][3])
    return {
        "box": (float(x), float(y), float(w), float(h)),
        "eye_y": y + 0.42 * h,
        "mouth_y": y + 0.80 * h,
        "right": flip,
        "offset": 0.3,
        "eye_gap": 0.0,
        "source": "haar",
    }


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


def _contour_points(contour: np.ndarray, eye_y: float, mouth_y: float, face_h: float) -> dict | None:
    """Ищет антропометрические точки на линии профиля. None — если профиль неправдоподобный."""
    P: dict = {}

    def find(name, y0, y1, mode):
        P[name] = _argext(contour, y0, y1, mode)
        return P[name] is not None

    if not find("Prn", eye_y + 0.12 * face_h, eye_y + 0.75 * max(mouth_y - eye_y, 0.3 * face_h), "max"):
        return None
    prn_y = P["Prn"][1]
    if not (
        find("Sn", prn_y + 0.02 * face_h, prn_y + 0.6 * max(mouth_y - prn_y, 0.1 * face_h), "min")
        and find("N", eye_y - 0.15 * face_h, eye_y + 0.08 * face_h, "min")
        and find("G", P["N"][1] - 0.3 * face_h, P["N"][1] - 0.04 * face_h, "max")
        and find("Ls", P["Sn"][1] + 0.01 * face_h, mouth_y + 0.04 * face_h, "max")
        and find("St", P["Ls"][1] + 0.01 * face_h, P["Ls"][1] + 0.14 * face_h, "min")
        and find("Li", P["St"][1] + 0.01 * face_h, P["St"][1] + 0.16 * face_h, "max")
        and find("B", P["Li"][1] + 0.02 * face_h, P["Li"][1] + 0.22 * face_h, "min")
        and find("Pog", P["B"][1] + 0.02 * face_h, P["B"][1] + 0.28 * face_h, "max")
    ):
        return None
    find("C", P["Pog"][1] + 0.12 * face_h, P["Pog"][1] + 0.45 * face_h, "min")

    # проверки правдоподобия
    scale = float(np.linalg.norm(P["N"] - P["Pog"]))
    if scale < 30:
        return None
    order = ["G", "N", "Prn", "Sn", "Ls", "St", "Li", "B", "Pog"]
    if any(P[a][1] >= P[b][1] for a, b in zip(order, order[1:])):
        return None
    if (P["Prn"][0] - P["Sn"][0]) < 0.05 * scale or (P["Prn"][0] - P["N"][0]) < 0.07 * scale:
        return None
    if (P["Ls"][0] - P["Sn"][0]) < 0.0 or (P["Pog"][0] - P["B"][0]) < 0.0:
        return None
    # анатомически невозможные углы — признак того, что это не профиль (например, ракурс 3/4)
    convexity = _angle_at(P["G"], P["Sn"], P["Pog"])
    nasofrontal = _angle_at(P["G"], P["N"], P["Prn"])
    projection = _signed_dist_to_line(P["Prn"], P["N"], P["Sn"]) / scale
    if not (145 <= convexity and 95 <= nasofrontal <= 165 and 0.05 <= projection <= 0.35):
        return None
    return P


def analyze_profile(data: bytes) -> ProfileResult:
    img = decode_image(data)
    found = _find_face(img)
    if found is None:
        raise AnalysisError("no_profile")
    if found["source"] == "yunet" and found["offset"] < 0.04 and found["eye_gap"] > 0.38:
        # нос ровно посередине между глазами — это анфас, а не профиль
        raise AnalysisError("not_profile")

    W = img.shape[1]
    eye_y, mouth_y = found["eye_y"], found["mouth_y"]
    # направление взгляда по 5 точкам на профиле ненадёжно — пробуем обе стороны
    P = None
    for flipped in (not found["right"], found["right"]):
        work = cv2.flip(img, 1) if flipped else img
        x, y, w, h = found["box"]
        if flipped:
            x = W - x - w
        # сначала быстрая модель фона, затем GrabCut с разной инициализацией
        for method, seed in (("bg", 0), ("grabcut", 0), ("grabcut", 1)):
            cv2.setRNGSeed(seed)
            try:
                contour, face_h = _profile_line(work, (x, y, w, h), eye_y, mouth_y, method)
            except AnalysisError:
                continue
            P = _contour_points(contour, eye_y, mouth_y, face_h)
            if P is not None:
                break
        if P is not None:
            break
    if P is None:
        raise AnalysisError("profile_fail")
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

    warnings: list[str] = []
    if found["source"] == "haar":
        warnings.append("Профиль найден с трудом — точность ниже обычной.")

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
        face_box=(bx, y, w, h),
        m=m,
        warnings=warnings,
    )
