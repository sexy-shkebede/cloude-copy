"""Тексты сообщений (HTML) и форматирование отчёта."""
from __future__ import annotations

from html import escape

from analyzer.scoring import Report
from graphics.cards import plural


def ratings_word(n: int) -> str:
    return plural(n, ("оценка", "оценки", "оценок"))


def bar(score: float, length: int = 10) -> str:
    filled = max(0, min(length, round(score / 10 * length)))
    return "▰" * filled + "▱" * (length - filled)


def start_caption(name: str, balance: int, price: int, unlimited: bool) -> str:
    bal = "∞ (админ)" if unlimited else f"{balance} {ratings_word(balance)}"
    return (
        f"👋 Привет, <b>{escape(name)}</b>!\n\n"
        "Пришли два фото — анфас и профиль 90°, а я сделаю разлиновку лица, "
        "оценю каждую его часть и поставлю итоговый балл.\n\n"
        f"💰 Баланс: <b>{bal}</b>\n"
        f"⭐ 1 оценка = <b>{price} звёзд</b>\n\n"
        "Выбери, как продолжить 👇"
    )


HELP = (
    "ℹ️ <b>Как это работает</b>\n\n"
    "1️⃣ Нажми «📸 Оценить в чате» и выбери пол (идеалы пропорций у мужчин и женщин разные).\n"
    "2️⃣ Отправь фото <b>анфас</b> — лицо прямо, камера на уровне глаз.\n"
    "3️⃣ Отправь фото <b>в профиль (90°)</b> — строго боком, на однотонном фоне.\n"
    "4️⃣ Получи разлиновку лица, разбор каждой части и итоговый балл.\n\n"
    "📐 <b>Что оцениваю</b>: симметрию, трети и пятые лица, глаза (наклон, посадка), брови, нос, губы, "
    "скулы (FWHR), челюсть и подбородок, а по профилю — углы лоб–нос, носогубный, выпуклость и E-линию.\n\n"
    "💳 Оценка списывается только после успешного анализа. Если фото не подошло — я попрошу другое бесплатно.\n\n"
    "⚠️ Это геометрический анализ в развлекательном формате: красота не сводится к цифрам 🙂"
)

ASK_GENDER = "👤 <b>Укажи пол</b>\n\nИдеальные пропорции для мужчин и женщин немного отличаются — так оценка будет точнее."

STEP_FRONT = (
    "📸 <b>Шаг 1 из 2 — фото анфас</b>\n\n"
    "• смотри прямо в камеру, голову не наклоняй\n"
    "• камера на уровне глаз, примерно на расстоянии вытянутой руки\n"
    "• ровный свет, без фильтров и сильных теней\n"
    "• открой лоб, сними очки, нейтральное выражение лица\n\n"
    "Отправь фото сюда 👇"
)

STEP_SIDE = (
    "✅ Фото анфас принято!\n\n"
    "👤 <b>Шаг 2 из 2 — фото в профиль (90°)</b>\n\n"
    "• повернись строго боком — видно один глаз\n"
    "• камера на уровне носа, голову не наклоняй\n"
    "• за лицом однотонный фон (стена)\n"
    "• убери волосы за ухо, чтобы был виден лоб\n\n"
    "Отправь фото сюда 👇"
)

ERRORS = {
    "bad_image": "😕 Не получилось открыть картинку. Отправь фото в формате JPG или PNG.",
    "no_face": "😕 Не нашёл лицо на фото. Сфотографируйся анфас при хорошем свете — лицо должно быть хорошо видно.",
    "too_small": "🔎 Лицо слишком маленькое. Подойди ближе — лицо должно занимать около половины кадра.",
    "not_frontal": "↔️ Голова повёрнута. Нужно фото <b>строго анфас</b>: смотри прямо в камеру и пришли фото ещё раз.",
    "no_landmarks": "😕 Не получилось разметить лицо. Попробуй другое фото при ровном свете.",
    "no_profile": "😕 Не нашёл лицо в профиль. Повернись боком (90°) и сфотографируйся на однотонном фоне.",
    "not_profile": "↔️ Это фото анфас, а сейчас нужен <b>профиль</b> — повернись боком на 90°.",
    "profile_fail": (
        "😕 Не получилось чётко выделить линию профиля.\n"
        "Нужно: лицо строго боком, однотонный фон, видны лоб, нос, губы и подбородок.\n\n"
        "Пришли другое фото или продолжи без профиля."
    ),
}

NO_STATE_PHOTO = "📸 Чтобы оценить внешность, нажми «Оценить в чате» — я подскажу, какие фото нужны."

PROGRESS = [
    "🔍 Ищу лицо на фото…",
    "📐 Размечаю 68 точек…",
    "🧮 Считаю пропорции…",
    "🎨 Рисую разлиновку…",
]

PAYSUPPORT = (
    "💬 <b>Поддержка по оплате</b>\n\n"
    "Оценки покупаются за звёзды Telegram и начисляются на баланс сразу после оплаты. "
    "Если оценка не была выполнена или возникла проблема — напиши администратору, "
    "приложив дату платежа. Возврат звёзд возможен за неиспользованные оценки."
)

TERMS = (
    "📄 <b>Условия</b>\n\n"
    "• Оценка — результат автоматического геометрического анализа фото и носит развлекательный характер.\n"
    "• Фото обрабатываются только для анализа и не публикуются.\n"
    "• 1 оценка списывается после успешного анализа пары фото.\n"
    "• Покупая оценки, ты соглашаешься с этими условиями."
)

MINIAPP_SOON = "📱 Мини-приложение скоро появится! А пока можно оценить лицо прямо в чате 😉"


def not_enough(balance: int, price: int) -> str:
    return (
        f"💸 <b>Недостаточно оценок</b>\n\nНа балансе: {balance} {ratings_word(balance)}.\n"
        f"Одна оценка стоит <b>{price} ⭐</b>. Выбери пакет 👇"
    )


def shop_caption(balance: int, price: int, unlimited: bool) -> str:
    bal = "∞ (админ)" if unlimited else f"{balance} {ratings_word(balance)}"
    return (
        f"⭐ <b>Пополнение баланса</b>\n\nСейчас на балансе: <b>{bal}</b>\n"
        f"Цена: <b>{price} звёзд</b> за 1 оценку.\n\nВыбери пакет 👇"
    )


def balance_caption(balance: int, unlimited: bool, ratings: int, best: float | None) -> str:
    bal = "∞ (админ — бесплатно)" if unlimited else f"{balance} {ratings_word(balance)}"
    lines = [f"💰 <b>Баланс:</b> {bal}", f"📊 Сделано оценок: {ratings}"]
    if best:
        lines.append(f"🏆 Лучший результат: {best:.1f}/10")
    return "\n".join(lines)


def payment_ok(credits: int, balance: int) -> str:
    return (
        f"🎉 <b>Оплата прошла!</b>\n\nНачислено: +{credits} {ratings_word(credits)}\n"
        f"Баланс: <b>{balance} {ratings_word(balance)}</b>"
    )


def result_caption(report: Report) -> str:
    return f"{report.tier_emoji} <b>Итог: {report.total:.1f}/10</b> — {escape(report.tier)}"


def format_report(report: Report) -> list[str]:
    """Подробный разбор. Возвращает части, каждая < 4096 символов."""
    head = [
        f"{report.tier_emoji} <b>ИТОГОВАЯ ОЦЕНКА: {report.total:.1f} / 10</b>",
        f"{bar(report.total, 12)}",
        f"<i>{escape(report.tier)}</i> • форма лица: <b>{escape(report.face_shape)}</b>",
        "",
        "<b>Баллы по частям лица:</b>",
    ]
    for p in report.parts:
        head.append(f"{p.emoji} {escape(p.title)} — <b>{p.score:.1f}</b>  {bar(p.score)}")
    if not report.has_profile:
        head.append("👤 Профиль — <i>не оценивался</i>")

    blocks: list[str] = []
    for p in report.parts:
        lines = [f"{p.emoji} <b>{escape(p.title.upper())}: {p.score:.1f}/10</b>"]
        details = []
        for m in p.metrics:
            mark = "✅" if m.score >= 8.5 else ("🟡" if m.score >= 6.5 else "🔻")
            details.append(
                f"{mark} <b>{escape(m.label)}</b>: {escape(m.value_text)} <i>({escape(m.ideal_text)})</i>\n"
                f"{escape(m.comment)}"
            )
        lines.append("<blockquote expandable>" + "\n\n".join(details) + "</blockquote>")
        blocks.append("\n".join(lines))

    tail: list[str] = []
    if report.strengths:
        tail.append("💪 <b>Сильные стороны</b>")
        tail += [f"• <b>{escape(m.label)}</b>: {escape(m.comment)}" for m in report.strengths]
        tail.append("")
    if report.weaknesses:
        tail.append("🎯 <b>Что выделяется</b>")
        tail += [f"• <b>{escape(m.label)}</b>: {escape(m.comment)}" for m in report.weaknesses]
        tail.append("")
    if report.tips:
        tail.append("💡 <b>Советы</b>")
        tail += [f"• {escape(t)}" for t in report.tips]
        tail.append("")
    if report.warnings:
        tail.append("⚠️ <b>Примечания</b>")
        tail += [f"• {escape(w)}" for w in report.warnings]
        tail.append("")
    tail.append("<i>Анализ геометрический и развлекательный — красота не сводится к цифрам.</i>")

    chunks: list[str] = []
    cur = "\n".join(head)
    for piece in ["<b>📋 ПОДРОБНЫЙ РАЗБОР</b>"] + blocks + ["\n".join(tail)]:
        if len(cur) + len(piece) + 2 > 3900:
            chunks.append(cur)
            cur = piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def admin_help(stats: dict) -> str:
    avg = f"{stats['avg_score']:.2f}" if stats["avg_score"] else "—"
    return (
        "👑 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{stats['users']}</b>\n"
        f"📊 Оценок всего: <b>{stats['ratings']}</b> (сегодня {stats['ratings_today']})\n"
        f"⭐ Оплат: <b>{stats['payments']}</b> на <b>{stats['stars']}</b> звёзд\n"
        f"🎁 Выдано админом: <b>{stats['granted']}</b> оценок\n"
        f"📈 Средний балл: <b>{avg}</b>\n\n"
        "<b>Команды:</b>\n"
        "<code>/give ID 5</code> — выдать оценки (ID или @username)\n"
        "<code>/take ID 2</code> — забрать оценки\n"
        "<code>/user ID</code> — информация о пользователе\n"
        "<code>/refund CHARGE_ID</code> — вернуть звёзды за платёж\n"
        "<code>/stats</code> — статистика\n\n"
        "Подсказка: ответь командой <code>/give 5</code> на пересланное сообщение пользователя — ID подставится сам."
    )
