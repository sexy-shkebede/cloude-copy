"""Загрузка нейросетевых моделей OpenCV (один раз на весь процесс).

- YuNet — детектор лица и 5 опорных точек;
- MediaPipe Face Mesh v2 (ONNX) — 478 точек лица для фото анфас;
- 3DDFA_V2 (MobileNet, ONNX) — 3D-модель головы: 68 точек и поворот головы при любом ракурсе, в том числе
  в профиль 90°;
- каскад Хаара для профиля — запасной детектор, если YuNet не нашёл лицо сбоку.

Все сети запускаются через cv2.dnn, отдельных библиотек (onnxruntime, mediapipe) не нужно.
"""
from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
YUNET_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
MESH_PATH = MODELS_DIR / "face_landmarks.onnx"
TDDFA_PATH = MODELS_DIR / "tddfa_mb1_120x120.onnx"
TDDFA_BASES = {
    "mean": ("tddfa_param_mean.bin", (62,)),
    "std": ("tddfa_param_std.bin", (62,)),
    "u": ("tddfa_u_base.bin", (204, 1)),
    "w_shp": ("tddfa_w_shp_base.bin", (204, 40)),
    "w_exp": ("tddfa_w_exp_base.bin", (204, 10)),
}
PROFILE_CASCADE_PATH = MODELS_DIR / "haarcascade_profileface.xml"
REQUIRED = [YUNET_PATH, MESH_PATH, TDDFA_PATH] + [MODELS_DIR / f for f, _ in TDDFA_BASES.values()]

MESH_SIZE = 256  # вход Face Mesh: квадрат 256x256, RGB 0..1
TDDFA_SIZE = 120  # вход 3DDFA: квадрат 120x120, BGR, (x - 127.5) / 128


class Models:
    def __init__(self) -> None:
        missing = [p.name for p in REQUIRED if not p.exists()]
        if missing:
            raise RuntimeError(
                f"Не найдены модели: {', '.join(missing)}. "
                "Запусти: python download_models.py"
            )
        if not hasattr(cv2, "FaceDetectorYN"):
            raise RuntimeError("OpenCV слишком старый: нужен 4.8 или новее (cv2.FaceDetectorYN).")
        self.mesh_net = cv2.dnn.readNetFromONNX(str(MESH_PATH))
        self.tddfa_net = cv2.dnn.readNetFromONNX(str(TDDFA_PATH))
        self._mesh_outs = list(self.mesh_net.getUnconnectedOutLayersNames())
        self.bases = {k: np.fromfile(str(MODELS_DIR / f), np.float32).reshape(shape)
                      for k, (f, shape) in TDDFA_BASES.items()}
        self.cascade = None
        if PROFILE_CASCADE_PATH.exists():
            cascade = cv2.CascadeClassifier(str(PROFILE_CASCADE_PATH))
            if not cascade.empty():
                self.cascade = cascade
        # сети cv2.dnn не потокобезопасны: каждая под своим замком
        self._mesh_lock = threading.Lock()
        self._tddfa_lock = threading.Lock()
        log.info("Модели загружены")

    def detect_faces(self, img: np.ndarray, score_threshold: float = 0.6) -> np.ndarray:
        """Возвращает массив Nx15 (x, y, w, h, 5 точек, score), отсортированный по площади."""
        h, w = img.shape[:2]
        detector = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (w, h), score_threshold, 0.3, 5000)
        _, faces = detector.detect(img)
        if faces is None or len(faces) == 0:
            return np.zeros((0, 15), np.float32)
        order = np.argsort(-(faces[:, 2] * faces[:, 3]))
        return faces[order]

    # ---------- Face Mesh ----------
    def mesh(self, img: np.ndarray, center, size: float, angle: float = 0.0) -> tuple[np.ndarray, float]:
        """478 точек (x, y, z) в координатах img для квадрата size x size вокруг center.
        Возвращает (точки, уверенность 0..1, что в кадре лицо)."""
        s = MESH_SIZE / float(size)
        M = cv2.getRotationMatrix2D((float(center[0]), float(center[1])), angle, s)
        M[0, 2] += MESH_SIZE / 2 - center[0]
        M[1, 2] += MESH_SIZE / 2 - center[1]
        crop = cv2.warpAffine(img, M, (MESH_SIZE, MESH_SIZE), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        x = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        with self._mesh_lock:
            self.mesh_net.setInput(x)
            # OpenCV 5 требует запросить все выходы сети сразу
            outs = dict(zip(self._mesh_outs, self.mesh_net.forward(self._mesh_outs)))
        lm, presence = outs["Identity"], outs["Identity_1"]
        pts = lm.reshape(-1, 3).astype(np.float32)
        Mi = cv2.invertAffineTransform(M)
        xy = pts[:, :2] @ Mi[:, :2].T + Mi[:, 2]
        conf = 1.0 / (1.0 + math.exp(-float(presence.ravel()[0])))
        return np.concatenate([xy, pts[:, 2:3] / s], axis=1).astype(np.float32), conf

    def mesh_refined(self, img: np.ndarray, center, size: float, passes: int = 3) -> tuple[np.ndarray, float]:
        """Face Mesh с уточнением кадра: после каждого прохода кадр строится заново по найденным точкам
        (квадрат 1.5 размера лица, как в MediaPipe), так сеть видит лицо в привычном масштабе."""
        pts, conf = self.mesh(img, center, size)
        for _ in range(passes - 1):
            lo, hi = pts[:, :2].min(0), pts[:, :2].max(0)
            pts, conf = self.mesh(img, (lo + hi) / 2, float((hi - lo).max() * 1.5))
        return pts, conf

    # ---------- 3DDFA_V2 ----------
    @staticmethod
    def roi_from_box(box: tuple[float, float, float, float]) -> tuple[float, float, float]:
        """Квадратный кадр для 3DDFA по боксу детектора (x, y, w, h), как parse_roi_box_from_bbox в 3DDFA_V2."""
        x, y, w, h = map(float, box)
        old = (w + h) / 2
        size = old * 1.58
        cx, cy = x + w / 2, y + h / 2 + old * 0.14
        return cx - size / 2, cy - size / 2, size

    @staticmethod
    def roi_from_points(pts: np.ndarray) -> tuple[float, float, float]:
        """Кадр по уже найденным 68 точкам (второй, более точный проход, как parse_roi_box_from_landmark)."""
        lo, hi = pts[:, :2].min(0), pts[:, :2].max(0)
        c = (lo + hi) / 2
        radius = float((hi - lo).max()) / 2
        size = 2 * radius * math.sqrt(2)
        return float(c[0]) - size / 2, float(c[1]) - size / 2, size

    def head3d(self, img: np.ndarray, roi: tuple[float, float, float]) -> tuple[np.ndarray, dict]:
        """68 точек 3D-модели головы (x, y, z в координатах img) и поворот головы в градусах.
        roi — квадрат (x, y, сторона): roi_from_box() или roi_from_points().
        Работает при любом повороте головы, в том числе в профиль."""
        sx, sy, size = map(float, roi)
        k = TDDFA_SIZE / size
        M = np.array([[k, 0, -sx * k], [0, k, -sy * k]], np.float32)
        crop = cv2.warpAffine(img, M, (TDDFA_SIZE, TDDFA_SIZE), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT)
        inp = ((crop.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        with self._tddfa_lock:
            self.tddfa_net.setInput(inp)
            out = self.tddfa_net.forward().ravel()
        b = self.bases
        p = out * b["std"] + b["mean"]
        P = p[:12].reshape(3, 4)
        R, offset = P[:, :3], P[:, 3:]
        verts = (b["u"] + b["w_shp"] @ p[12:52, None] + b["w_exp"] @ p[52:, None]).reshape(-1, 3).T
        pts = R @ verts + offset  # 3x68 в кадре 120x120 (ось y снизу вверх)
        pts[0] -= 1
        pts[2] -= 1
        pts[1] = TDDFA_SIZE - pts[1]
        scale = size / TDDFA_SIZE
        out_pts = np.stack([pts[0] * scale + sx, pts[1] * scale + sy, pts[2] * scale], axis=1).astype(np.float32)
        out_pts[:, 2] -= out_pts[:, 2].min()
        # поворот головы из матрицы (как matrix2angle в 3DDFA_V2)
        r1, r2 = P[0, :3], P[1, :3]
        r1 = r1 / (np.linalg.norm(r1) + 1e-9)
        r2 = r2 / (np.linalg.norm(r2) + 1e-9)
        Rn = np.stack([r1, r2, np.cross(r1, r2)])
        yaw = math.degrees(math.asin(float(np.clip(Rn[2, 0], -1.0, 1.0))))
        c = math.cos(math.radians(yaw)) or 1e-9
        pitch = math.degrees(math.atan2(Rn[2, 1] / c, Rn[2, 2] / c))
        roll = math.degrees(math.atan2(Rn[1, 0] / c, Rn[0, 0] / c))
        return out_pts, {"yaw": yaw, "pitch": pitch, "roll": roll}

    def detect_profile_haar(self, gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        if self.cascade is None:
            return []
        min_side = max(40, min(gray.shape[:2]) // 6)
        faces = self.cascade.detectMultiScale(gray, 1.1, 4, minSize=(min_side, min_side))
        return [tuple(map(int, f)) for f in faces] if len(faces) else []


_models: Models | None = None
_init_lock = threading.Lock()


def get_models() -> Models:
    global _models
    with _init_lock:
        if _models is None:
            _models = Models()
        return _models
