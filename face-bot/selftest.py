"""Проверка анализа без Telegram: python selftest.py анфас.jpg [профиль.jpg] [m|f]

Результаты (карточка, разлиновка, профиль) сохраняются в папку selftest_out/.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import config
from analyzer import AnalysisError, analyze_front, analyze_profile, build_report, get_models
from graphics.cards import render_result_card
from graphics.overlays import render_front, render_profile
from texts import format_report


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    gender = "m"
    if args[-1] in ("m", "f"):
        gender = args.pop()
    front_path = Path(args[0])
    side_path = Path(args[1]) if len(args) > 1 else None

    t = time.time()
    get_models()
    print(f"Модели загружены за {time.time() - t:.1f} с")

    t = time.time()
    try:
        front = analyze_front(front_path.read_bytes())
    except AnalysisError as e:
        print(f"Фото анфас не подошло: {e.code}")
        return 1
    print(f"Анфас: {time.time() - t:.1f} с")

    profile = None
    if side_path:
        t = time.time()
        try:
            profile = analyze_profile(side_path.read_bytes())
            print(f"Профиль: {time.time() - t:.1f} с")
        except AnalysisError as e:
            print(f"Фото профиля не подошло: {e.code} — продолжаю без профиля")

    report = build_report(front, profile, gender)
    out = Path(__file__).resolve().parent / "selftest_out"
    out.mkdir(exist_ok=True)
    (out / "1_card.jpg").write_bytes(render_result_card(report, front, config.BRAND))
    (out / "2_front.jpg").write_bytes(render_front(front, report))
    if profile:
        (out / "3_profile.jpg").write_bytes(render_profile(profile, report))
    for chunk in format_report(report):
        print(re.sub(r"<[^>]+>", "", chunk))
    print(f"\nКартинки сохранены в {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
