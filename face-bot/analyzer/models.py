"""Загрузка нейросетевых моделей OpenCV (один раз на весь процесс)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
YUNET_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
LBF_PATH = MODELS_DIR / "lbfmodel.yaml"
PROFILE_CASCADE_PATH = MODELS_DIR / "haarcascade_profileface.xml"


class Models:
    """YuNet (детектор лица + 5 точек), LBF (68 точек) и каскад для профиля."""

    def __init__(self) -> None:
        missing = [p.name for p in (YUNET_PATH, LBF_PATH) if not p.exists()]
        if missing:
            raise RuntimeError(
                f"Не найдены модели: {', '.join(missing)}. "
                "Запусти: python download_models.py"
            )
        if not hasattr(cv2, "face"):
            raise RuntimeError(
                "В установленном OpenCV нет модуля cv2.face (opencv-contrib). "
                "Termux: pkg install opencv-python; ПК: pip install opencv-contrib-python-headless"
            )
        log.info("Загрузка LBF-модели (68 точек)... это может занять несколько секунд")
        self.facemark = cv2.face.createFacemarkLBF()
        self.facemark.loadModel(str(LBF_PATH))
        self.cascade = None
        if PROFILE_CASCADE_PATH.exists():
            cascade = cv2.CascadeClassifier(str(PROFILE_CASCADE_PATH))
            if not cascade.empty():
                self.cascade = cascade
        self._lock = threading.Lock()
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

    def fit_landmarks(self, img: np.ndarray, boxes: list[tuple[float, float, float, float]]) -> np.ndarray:
        """68 точек LBF для каждого бокса (x, y, w, h) -> массив Kx68x2."""
        out = []
        with self._lock:
            for box in boxes:
                ok, lms = self.facemark.fit(img, np.array([box], dtype=np.float32))
                if ok and len(lms):
                    out.append(np.asarray(lms[0], dtype=np.float32).reshape(-1, 2))
        return np.array(out, dtype=np.float32)

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
