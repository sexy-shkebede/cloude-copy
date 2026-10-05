#!/usr/bin/env bash
# Запуск бота с автоперезапуском. Остановить: Ctrl+C
cd "$(dirname "$0")"

# не даём Android усыпить Termux, пока бот работает
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
fi

trap 'echo; echo "Остановлено"; command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock; exit 0' INT TERM

while true; do
    python bot.py
    code=$?
    if [ "$code" -eq 2 ]; then
        echo "Бот не запущен из-за ошибки настройки (см. сообщение выше)."
        break
    fi
    echo "Бот остановился (код $code). Перезапуск через 5 секунд… (Ctrl+C — выход)"
    sleep 5
done
