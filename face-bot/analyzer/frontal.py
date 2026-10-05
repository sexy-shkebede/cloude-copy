"""Анализ фото анфас: 68 точек, линия роста волос и замеры пропорций."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .common import AnalysisError, decode_image
from .models import get_models

# Эталонные 5 точек (глаза, кончик носа, уголки рта) в кадре 112x112
_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    np.float32,
)
# Нормализованный кадр: лицо выровнено, межзрачковое расстояние ~106 px
CANON_SCALE = 3.0
CANON_OFFSET = np.array([72.0, 150.0], np.float32)
CANON_SIZE = (480, 600)  # (w, h)
CANON_TEMPLATE = _TEMPLATE * CANON_SCALE + CANON_OFFSET
# Несколько стартовых рамок для LBF (в единицах шаблона) — медиана сглаживает ошибки
_FIT_BOXES = [(11, 25, 101, 115), (13, 23, 99, 117), (9, 27, 103, 113), (12, 21, 100, 113), (10, 28, 102, 118)]

# Пары симметричных точек (левая/правая сторона)
SYMMETRY_PAIRS = [
    (0, 16), (1, 15), (2, 14), (3, 13), (4, 12), (5, 11), (6, 10), (7, 9),
    (17, 26), (18, 25), (19, 24), (20, 23), (21, 22),
    (36, 45), (37, 44), (38, 43), (39, 42), (40, 47), (41, 46),
    (31, 35), (32, 34), (48, 54), (49, 53), (50, 52), (59, 55), (58, 56),
]
MIDLINE_POINTS = [27, 28, 29, 30, 33, 51, 62, 66, 57, 8]


@dataclass
class FrontalResult:
    image: np.ndarray  # исходное фото (BGR)
    to_canon: np.ndarray  # аффинное преобразование фото -> нормализованный кадр
    canon: np.ndarray  # нормализованный кадр (BGR)
    points: np.ndarray  # 68x2 в нормализованном кадре
    hairline_y: float | None
    midline: tuple[np.ndarray, np.ndarray]  # (точка, единичное направление)
    src_ipd: float  # межзрачковое расстояние на исходном фото, px
    m: dict = field(default_factory=dict)  # замеры
    warnings: list[str] = field(default_factory=list)


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _fit_midline(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pts = p[MIDLINE_POINTS]
    c = pts.mean(axis=0)
    # x = a*y + b (линия почти вертикальная)
    a, _ = np.polyfit(pts[:, 1], pts[:, 0], 1)
    d = np.array([a, 1.0], np.float32)
    d /= np.linalg.norm(d)
    return c.astype(np.float32), d


def _midline_x(midline: tuple[np.ndarray, np.ndarray], y: float) -> float:
    c, d = midline
    return float(c[0] + (y - c[1]) * d[0] / d[1])


def _symmetry_error(p: np.ndarray, midline, ipd: float) -> tuple[float, float]:
    """Асимметрия, устойчивая к небольшому повороту головы.

    Вертикальные расхождения пар учитываются полностью, а горизонтальные —
    после компенсации общего «сжатия» одной половины (эффект поворота).
    Возвращает (ошибка в долях IPD, оценка поворота).
    """
    c, d = midline
    n = np.array([d[1], -d[0]], np.float32)  # нормаль к средней линии
    dl, dr, vy = [], [], []
    for i, j in SYMMETRY_PAIRS:
        a, b = p[i] - c, p[j] - c
        dl.append(abs(float(a @ n)))
        dr.append(abs(float(b @ n)))
        vy.append(float(a @ d) - float(b @ d))
    dl, dr, vy = np.array(dl), np.array(dr), np.array(vy)
    k = float(dl.sum() / max(dr.sum(), 1e-6))  # отношение ширин половин
    k_s = math.sqrt(k)
    horiz = dl / k_s - dr * k_s
    err = float(np.mean(np.sqrt(horiz**2 + vy**2)) / ipd)
    yaw = float(np.clip((k - 1.0) / (k + 1.0), -1, 1))
    return err, yaw


def _detect_hairline(canon: np.ndarray, p: np.ndarray, midline, ipd: float) -> float | None:
    """Ищет линию роста волос: идём вверх от бровей, пока полоса похожа на кожу."""
    lab = cv2.cvtColor(canon, cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    # образцы кожи — щёки под глазами
    samples = []
    for ex, nx in ((p[36:42].mean(0), p[31]), (p[42:48].mean(0), p[35])):
        cx = int((ex[0] * 0.6 + nx[0] * 0.4))
        y0, y1 = int(ex[1] + 0.25 * ipd), int(ex[1] + 0.55 * ipd)
        x0, x1 = int(cx - 0.15 * ipd), int(cx + 0.15 * ipd)
        if 0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w:
            samples.append(lab[y0:y1, x0:x1].reshape(-1, 3))
    if not samples:
        return None
    s = np.concatenate(samples)
    med = np.median(s, axis=0)
    mad = np.median(np.abs(s - med), axis=0) * 1.4826 + np.array([6.0, 2.0, 2.0])
    brow_y = float(p[17:27, 1].mean())
    brow_top = float(p[17:27, 1].min())
    cx = _midline_x(midline, brow_y)
    half = 0.35 * ipd
    x0, x1 = int(max(0, cx - half)), int(min(w, cx + half))
    y_start = int(brow_top - 0.05 * ipd)
    y_end = int(max(0, brow_y - 2.3 * ipd))
    if x1 - x0 < 5 or y_start <= y_end:
        return None
    frac = []
    ys = list(range(y_start, y_end, -1))
    for y in ys:
        row = lab[y, x0:x1]
        dist = (row - med) / (mad * np.array([2.5, 1.0, 1.0]))
        frac.append(float(np.mean(np.sqrt((dist**2).sum(axis=1)) < 3.2)))
    frac = np.convolve(np.array(frac), np.ones(5) / 5, mode="same")
    # лоб прямо над бровями должен быть похож на кожу (иначе чёлка)
    if frac[: max(3, int(0.15 * ipd))].mean() < 0.55:
        return None
    run = max(4, int(0.08 * ipd))
    for i in range(len(frac) - run):
        if frac[i] < 0.45 and np.all(frac[i : i + run] < 0.5):
            return float(ys[i])
    return None


def analyze_front(data: bytes) -> FrontalResult:
    models = get_models()
    img = decode_image(data)
    faces = models.detect_faces(img, 0.6)
    if len(faces) == 0:
        raise AnalysisError("no_face")
    face = faces[0]
    warnings: list[str] = []
    if len(faces) > 1 and faces[1][2] * faces[1][3] > 0.5 * face[2] * face[3]:
        warnings.append("На фото несколько лиц — анализирую самое крупное.")

    src5 = face[4:14].reshape(5, 2).astype(np.float32)
    src_ipd = _dist(src5[0], src5[1])
    if src_ipd < 28:
        raise AnalysisError("too_small")
    # проверка, что лицо анфас: кончик носа близко к середине между глазами
    eyes_mid = (src5[0] + src5[1]) / 2
    eye_vec = (src5[1] - src5[0]) / max(src_ipd, 1e-6)
    yaw_5pt = float((src5[2] - eyes_mid) @ eye_vec) / src_ipd
    if abs(yaw_5pt) > 0.33:
        raise AnalysisError("not_frontal")

    M, _ = cv2.estimateAffinePartial2D(src5, CANON_TEMPLATE, method=cv2.LMEDS)
    if M is None:
        raise AnalysisError("no_face")
    canon = cv2.warpAffine(img, M, CANON_SIZE, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    boxes = []
    for x0, y0, x1, y1 in _FIT_BOXES:
        boxes.append(
            (
                x0 * CANON_SCALE + CANON_OFFSET[0],
                y0 * CANON_SCALE + CANON_OFFSET[1],
                (x1 - x0) * CANON_SCALE,
                (y1 - y0) * CANON_SCALE,
            )
        )
    fits = models.fit_landmarks(canon, boxes)
    if len(fits) == 0:
        raise AnalysisError("no_landmarks")
    p = np.median(fits, axis=0)

    midline = _fit_midline(p)
    m = measure_front(p, midline)
    hairline = _detect_hairline(canon, p, midline, m["ipd"])
    if hairline is not None:
        up = m["brow_y"] - hairline
        if not (0.6 * m["third_mid"] <= up <= 1.5 * m["third_mid"]):
            hairline = None
    if hairline is not None:
        m["third_up"] = m["brow_y"] - hairline
        m["face_h_full"] = m["menton_y"] - hairline
    else:
        warnings.append("Линию роста волос не видно (чёлка/головной убор) — верхнюю треть не оцениваю.")
    if abs(m["yaw"]) > 0.4:
        raise AnalysisError("not_frontal")
    if abs(m["yaw"]) > 0.15:
        warnings.append("Голова немного повёрнута — для точности держи телефон ровно напротив лица.")

    return FrontalResult(
        image=img,
        to_canon=M,
        canon=canon,
        points=p,
        hairline_y=hairline,
        midline=midline,
        src_ipd=src_ipd,
        m=m,
        warnings=warnings,
    )


def measure_front(p: np.ndarray, midline) -> dict:
    eye_r, eye_l = p[36:42].mean(0), p[42:48].mean(0)
    ipd = _dist(eye_r, eye_l)
    ew_r, ew_l = _dist(p[36], p[39]), _dist(p[42], p[45])
    ew = (ew_r + ew_l) / 2
    icd = _dist(p[39], p[42])
    temple_w = _dist(p[0], p[16])
    cheek_w = _dist(p[1], p[15])
    jaw_w = _dist(p[4], p[12])
    chin_w = _dist(p[6], p[10])
    nose_w = _dist(p[31], p[35])
    mouth_w = _dist(p[48], p[54])

    brow_y = float(p[17:27, 1].mean())
    nasion_y = float(p[27, 1])
    sn_y = float(p[33, 1])
    stom_y = float((p[62, 1] + p[66, 1]) / 2)
    menton_y = float(p[8, 1])

    third_mid = sn_y - brow_y
    third_low = menton_y - sn_y

    # наклон глаз (кантальный тилт): +, если внешний угол выше внутреннего
    tilt_r = math.degrees(math.atan2(p[39][1] - p[36][1], p[39][0] - p[36][0]))
    tilt_l = math.degrees(math.atan2(p[42][1] - p[45][1], p[45][0] - p[42][0]))
    canthal = (tilt_r + tilt_l) / 2

    ear_r = (_dist(p[37], p[41]) + _dist(p[38], p[40])) / (2 * ew_r)
    ear_l = (_dist(p[43], p[47]) + _dist(p[44], p[46])) / (2 * ew_l)
    eye_top_y = float(np.mean([p[37, 1], p[38, 1], p[43, 1], p[44, 1]]))
    brow_eye = (eye_top_y - brow_y) / ew
    brow_tilt_r = math.degrees(math.atan2(p[21][1] - p[17][1], p[21][0] - p[17][0]))
    brow_tilt_l = math.degrees(math.atan2(p[22][1] - p[26][1], p[26][0] - p[22][0]))

    upper_lip = _dist(p[51], p[62])
    lower_lip = _dist(p[66], p[57])
    mouth_c_y = (p[62, 1] + p[66, 1]) / 2
    mouth_tilt = math.degrees(math.atan2(mouth_c_y - (p[48, 1] + p[54, 1]) / 2, mouth_w / 2))

    sym_err, yaw = _symmetry_error(p, midline, ipd)
    nose_dev = abs(float(p[30, 0]) - _midline_x(midline, float(p[30, 1]))) / ipd

    chin_angle = math.degrees(
        math.acos(
            np.clip(
                np.dot(p[5] - p[8], p[11] - p[8]) / (np.linalg.norm(p[5] - p[8]) * np.linalg.norm(p[11] - p[8])),
                -1,
                1,
            )
        )
    )

    return {
        "ipd": ipd,
        "eye_w": ew,
        "icd": icd,
        "temple_w": temple_w,
        "cheek_w": cheek_w,
        "jaw_w": jaw_w,
        "chin_w": chin_w,
        "nose_w": nose_w,
        "mouth_w": mouth_w,
        "brow_y": brow_y,
        "nasion_y": nasion_y,
        "sn_y": sn_y,
        "stom_y": stom_y,
        "menton_y": menton_y,
        "third_mid": third_mid,
        "third_low": third_low,
        "third_up": None,
        "face_h_full": None,
        # отношения
        "mid_low": third_mid / third_low,
        "lower_split": (menton_y - stom_y) / max(stom_y - sn_y, 1e-6),
        "fifths": temple_w / ew,
        "icd_ew": icd / ew,
        "esr": ipd / temple_w,
        "canthal": canthal,
        "ear": (ear_r + ear_l) / 2,
        "brow_eye": brow_eye,
        "brow_tilt": (brow_tilt_r + brow_tilt_l) / 2,
        "nose_icd": nose_w / icd,
        "nose_len": (sn_y - nasion_y) / (menton_y - nasion_y),
        "nose_dev": nose_dev,
        "mouth_nose": mouth_w / nose_w,
        "lip_ratio": lower_lip / max(upper_lip, 1e-6),
        "lip_full": (upper_lip + lower_lip) / mouth_w,
        "mouth_tilt": mouth_tilt,
        "fwhr": cheek_w / max(float(p[51, 1]) - brow_y, 1e-6),
        "jaw_cheek": jaw_w / cheek_w,
        "chin_mouth": chin_w / mouth_w,
        "chin_angle": chin_angle,
        "face_wh": (menton_y - brow_y) / cheek_w,
        "sym_err": sym_err,
        "yaw": yaw,
    }
