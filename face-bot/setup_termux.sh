#!/usr/bin/env bash
# Установка бота в Termux (Android). Запуск: bash setup_termux.sh
set -e
cd "$(dirname "$0")"

echo "==> Обновляю пакеты Termux"
pkg update -y
pkg upgrade -y -o Dpkg::Options::="--force-confnew"

echo "==> Ставлю Python и репозиторий x11 (в нём лежит OpenCV)"
pkg install -y python python-pip x11-repo
pkg update -y

echo "==> Ставлю NumPy, Pillow и OpenCV (это самый долгий шаг)"
pkg install -y python-numpy python-pillow opencv-python

echo "==> Ставлю python-telegram-bot"
pip install -r requirements.txt

echo "==> Скачиваю модели (~55 МБ)"
python download_models.py

python - <<'EOF'
import cv2, numpy, PIL
assert hasattr(cv2, "face"), "в OpenCV нет модуля face (contrib)"
assert hasattr(cv2, "FaceDetectorYN"), "OpenCV слишком старый, нужен 4.6+"
print("OpenCV", cv2.__version__, "| NumPy", numpy.__version__, "| Pillow", PIL.__version__, "— OK")
EOF

if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> Создан файл .env"
fi

echo
echo "Готово! Осталось:"
echo "  1) вписать токен бота:   nano .env"
echo "  2) запустить бота:       bash run.sh"
