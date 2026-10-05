"""Скачивает модели для анализа лица в папку models/.

Запуск: python download_models.py
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"

MODELS = [
    {
        "name": "lbfmodel.yaml",
        "desc": "68 точек лица (LBF, ~54 МБ)",
        "min_size": 50_000_000,
        "urls": [
            "https://raw.githubusercontent.com/kurnianggoro/GSOC2017/master/data/lbfmodel.yaml",
            "https://github.com/kurnianggoro/GSOC2017/raw/master/data/lbfmodel.yaml",
        ],
    },
    {
        "name": "face_detection_yunet_2023mar.onnx",
        "desc": "детектор лица YuNet",
        "min_size": 200_000,
        "urls": [
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        ],
    },
    {
        "name": "haarcascade_profileface.xml",
        "desc": "запасной детектор профиля",
        "min_size": 500_000,
        "urls": [
            "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_profileface.xml",
        ],
    },
]


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "face-bot/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r   {done / 1e6:6.1f} / {total / 1e6:.1f} МБ ({done * 100 // total}%)", end="", flush=True)
            else:
                print(f"\r   {done / 1e6:6.1f} МБ", end="", flush=True)
    print()
    tmp.replace(dest)


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for m in MODELS:
        dest = MODELS_DIR / m["name"]
        if dest.exists() and dest.stat().st_size >= m["min_size"]:
            print(f"✔ {m['name']} — уже скачана")
            continue
        print(f"⬇ {m['name']} — {m['desc']}")
        for url in m["urls"]:
            try:
                _download(url, dest)
                if dest.stat().st_size >= m["min_size"]:
                    print(f"✔ {m['name']} — готово")
                    break
                print("   файл неполный, пробую другой адрес…")
            except Exception as e:  # noqa: BLE001
                print(f"\n   не получилось ({e}), пробую другой адрес…")
        else:
            ok = False
            print(f"✘ Не удалось скачать {m['name']}. Скачай вручную по ссылке:\n   {m['urls'][0]}\n   и положи в {MODELS_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
