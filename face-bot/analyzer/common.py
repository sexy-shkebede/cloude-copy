from __future__ import annotations

import cv2
import numpy as np

MAX_SIDE = 1600


class AnalysisError(Exception):
    """Ошибка анализа с кодом для понятного сообщения пользователю."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def decode_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise AnalysisError("bad_image")
    h, w = img.shape[:2]
    s = MAX_SIDE / max(h, w)
    if s < 1:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img
