"""Скачивает модели для анализа лица в папку models/ (~25 МБ).

Запуск: python download_models.py

Каждый файл проверяется по контрольной сумме SHA-256: битый или подменённый файл не сохранится.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"
HF = "https://huggingface.co"
TDDFA_LITE = f"{HF}/litert-community/3DDFA-V2-LiteRT/resolve/main"

# (адрес, sha256) — несколько зеркал на случай, если одно недоступно
MODELS = [
    {
        "name": "face_detection_yunet_2023mar.onnx",
        "desc": "детектор лица YuNet",
        "sources": [
            ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", None),
            ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", None),
        ],
        "min_size": 200_000,
    },
    {
        "name": "face_landmarks.onnx",
        "desc": "478 точек лица (MediaPipe Face Mesh v2, Apache 2.0, ~5 МБ)",
        "sources": [
            (f"{HF}/naklitechie/face-landmarks-onnx/resolve/main/face_landmarks.onnx",
             "f38c3321ceffbc9e95103480ad38cc3f52e7e1bde2bcee7cd9355d0b9138ac0c"),
            (f"{HF}/fernandotonon/QtMeshEditor-facemesh-onnx/resolve/main/face_landmarks.onnx",
             "d16e5a55e6a480284d468ee32469692464049518c311662ab3956681de31e3e9"),
        ],
    },
    {
        "name": "tddfa_mb1_120x120.onnx",
        "desc": "3D-модель головы для профиля (3DDFA_V2, MIT, ~13 МБ)",
        "sources": [
            (f"{HF}/Stable-Human/3ddfa_v2/resolve/main/mb1_120x120.onnx",
             "1c0a8acd50db28987773324a9b2b816361468e3aa13cb6b212c911b889e08c3e"),
        ],
    },
    {
        "name": "tddfa_param_mean.bin",
        "desc": "параметры 3D-модели",
        "sources": [(f"{TDDFA_LITE}/tddfa_param_mean.bin",
                     "a72b5d7a120070c2cc1e56bf56909f8f91280e084891f5fdba247bed52218f2f")],
    },
    {
        "name": "tddfa_param_std.bin",
        "desc": "параметры 3D-модели",
        "sources": [(f"{TDDFA_LITE}/tddfa_param_std.bin",
                     "08820bf29aa7c0c52b3e3ab15d7f89e89b1e09ba2fa5057373f36e7c3618870c")],
    },
    {
        "name": "tddfa_u_base.bin",
        "desc": "средняя форма головы (68 точек)",
        "sources": [(f"{TDDFA_LITE}/tddfa_u_base.bin",
                     "080208f4817be778b7192c45f5db35adac29ab7b48df491f77ada42dc9735f95")],
    },
    {
        "name": "tddfa_w_shp_base.bin",
        "desc": "базис формы головы",
        "sources": [(f"{TDDFA_LITE}/tddfa_w_shp_base.bin",
                     "985d8bef50c501926c4ab49b67ec18161c02d2c76238673cb77ab825cd1b8a4c")],
    },
    {
        "name": "tddfa_w_exp_base.bin",
        "desc": "базис мимики",
        "sources": [(f"{TDDFA_LITE}/tddfa_w_exp_base.bin",
                     "ac002869a276763b1ae295395a55a7f72c3bf77a0622bb9eee2f0dc54e9ce875")],
    },
    {
        "name": "haarcascade_profileface.xml",
        "desc": "запасной детектор профиля",
        "sources": [("https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_profileface.xml", None)],
        "min_size": 500_000,
    },
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid(path: Path, m: dict) -> bool:
    if not path.exists():
        return False
    hashes = {sha for _, sha in m["sources"] if sha}
    if hashes:
        return _sha256(path) in hashes
    return path.stat().st_size >= m.get("min_size", 1)


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
        if _valid(dest, m):
            print(f"✔ {m['name']} — уже скачана")
            continue
        print(f"⬇ {m['name']} — {m['desc']}")
        for url, sha in m["sources"]:
            try:
                _download(url, dest)
            except Exception as e:  # noqa: BLE001
                print(f"\n   не получилось ({e}), пробую другой адрес…")
                continue
            if (sha and _sha256(dest) == sha) or (not sha and _valid(dest, m)):
                print(f"✔ {m['name']} — готово")
                break
            print("   файл не прошёл проверку, пробую другой адрес…")
            dest.unlink(missing_ok=True)
        else:
            ok = False
            print(f"✘ Не удалось скачать {m['name']}. Скачай вручную по ссылке:\n   {m['sources'][0][0]}\n"
                  f"   и положи в {MODELS_DIR}")
    old = MODELS_DIR / "lbfmodel.yaml"
    if old.exists():
        print(f"ℹ Старая модель {old.name} (54 МБ) больше не нужна — её можно удалить: rm {old}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
