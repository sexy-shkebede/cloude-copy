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
        "Нажми «📸 Оценить в чате» и пришли два фото — анфас и профиль 90°. "
        "Я сделаю разлиновку лица, оценю каждую его часть и поставлю итоговый балл.\n\n"
        "🏆 Соревнуйся в топах и собирай 🛡 клан с друзьями.\n\n"
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
    "🏆 <b>Топы</b> (по 10 мест): по рейтингу, по оценкам на балансе и топ кланов. "
    "Твой рейтинг — твоя лучшая оценка. В топе по рейтингу можно посмотреть фото, за которое участник получил место.\n\n"
    "🛡 <b>Кланы</b>: создай свой (один на человека) или вступи в любой. "
    "Рейтинг клана — сумма рейтингов всех участников.\n\n"
    "⚠️ Это геометрический анализ в развлекательном формате: красота не сводится к цифрам 🙂"
)

ASK_GENDER = "👤 <b>Укажи пол</b>\n\nИдеальные пропорции для мужчин и женщин немного отличаются — так оценка будет точнее."

STEP_FRONT = (
    "📸 <b>Шаг 1 из 2 — фото анфас</b>\n\n"
    "• смотри прямо в камеру, голову не наклоняй\n"
    "• камера на уровне глаз, примерно на расстоянии вытянутой руки\n"
    "• ровный свет, без фильтров и сильных теней\n"
    "• открой лоб, сними очки, нейтральное выражение лица\n\n"
    "🏆 Лучшая оценка попадает в топ вместе с фото (скрыть фото можно в «💰 Баланс»).\n\n"
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
    "• Фото и результаты хранятся у владельца бота. Фото с твоей лучшей оценкой могут посмотреть другие "
    "участники, если ты в топ-10 по рейтингу. Скрыть его можно в «💰 Баланс».\n"
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


def balance_caption(balance: int, unlimited: bool, ratings: int, best: float | None, place: int | None = None,
                    clan_name: str | None = None, photo_hidden: bool = False) -> str:
    bal = "∞ (админ — бесплатно)" if unlimited else f"{balance} {ratings_word(balance)}"
    lines = [f"💰 <b>Баланс:</b> {bal}", f"📊 Сделано оценок: {ratings}"]
    if best is not None:
        lines.append(f"🏆 Рейтинг (лучшая оценка): <b>{best:.1f}</b>/10" + (f" • {place} место в топе" if place else ""))
    lines.append(f"🛡 Клан: «{escape(clan_name)}»" if clan_name else "🛡 Клан: нет")
    if best is not None:
        lines.append("🙈 Фото в топе: скрыто" if photo_hidden else "👁 Фото в топе: видно другим")
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
        f"📈 Средний балл: <b>{avg}</b>\n"
        f"🛡 Кланов: <b>{stats['clans']}</b> (в кланах {stats['in_clans']} чел.)\n\n"
        "<b>Команды:</b>\n"
        "<code>/give ID 5</code> — выдать оценки (ID или @username)\n"
        "<code>/take ID 2</code> — забрать оценки\n"
        "<code>/user ID</code> — информация о пользователе\n"
        "<code>/refund CHARGE_ID</code> — вернуть звёзды за платёж\n"
        "<code>/unrate ID</code> — убрать лучшую оценку человека из топа (например, чужое фото)\n"
        "<code>/delclan ID</code> — удалить клан (номер клана или точное название)\n"
        "<code>/stats</code> — статистика\n\n"
        "Подсказка: ответь командой <code>/give 5</code> на пересланное сообщение пользователя — ID подставится сам."
    )


# ---------------------------------------------------------------- топы и кланы
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def place_mark(place: int) -> str:
    return MEDALS.get(place, f"{place}.")


def short(text: str | None, limit: int = 18) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def user_name(row) -> str:
    """Имя для топов и списков (уже экранировано). ID и @username чужим людям не показываем."""
    name = short(row["first_name"]) if row is not None else ""
    return escape(name or "Без имени")


def clan_title(name: str, limit: int = 24) -> str:
    return f"«{escape(short(name, limit))}»"


def score(x: float | None) -> str:
    return f"{x:.1f}" if x is not None else "—"


def top_rating_caption(rows, my_place: int | None, my_best: float | None, total: int) -> str:
    lines = ["⭐ <b>Топ-10 по рейтингу</b>", "<i>Рейтинг — лучшая оценка человека.</i>", ""]
    if not rows:
        lines.append("Пока никто не оценивался — стань первым 📸")
    for i, r in enumerate(rows, 1):
        clan = f" • 🛡 {escape(short(r['clan_name'], 14))}" if r["clan_name"] else ""
        lines.append(f"{place_mark(i)} <b>{user_name(r)}</b> — {score(r['best_score'])}{clan}")
    lines.append("")
    if my_place:
        lines.append(f"📍 Ты: <b>{my_place} место</b> из {total} • {score(my_best)}")
    else:
        lines.append("📍 Тебя пока нет в рейтинге — сделай оценку 📸")
    if rows:
        lines.append("📷 Нажми на место ниже, чтобы посмотреть фото")
    return "\n".join(lines)


def top_balance_caption(rows, my_place: int | None, my_balance: int, unlimited: bool) -> str:
    lines = ["💰 <b>Топ-10 по балансу</b>", "<i>Сколько оценок лежит на балансе.</i>", ""]
    if not rows:
        lines.append("Пока ни у кого нет оценок на балансе.")
    for i, r in enumerate(rows, 1):
        n = int(r["balance"])
        lines.append(f"{place_mark(i)} <b>{user_name(r)}</b> — {n} {ratings_word(n)}")
    lines.append("")
    if my_place:
        lines.append(f"📍 Ты: <b>{my_place} место</b> • {my_balance} {ratings_word(my_balance)}")
    elif unlimited:
        lines.append("📍 У тебя безлимит (админ) — в топ не попадаешь")
    else:
        lines.append("📍 На твоём балансе пусто — пополни ⭐")
    return "\n".join(lines)


def top_clans_caption(rows, my_clan) -> str:
    lines = ["🛡 <b>Топ-10 кланов</b>", "<i>Рейтинг клана — сумма рейтингов всех участников.</i>", ""]
    if not rows:
        lines.append("Кланов пока нет — создай первый!")
    for i, r in enumerate(rows, 1):
        lines.append(f"{place_mark(i)} <b>{escape(short(r['name'], 24))}</b> — {score(r['rating'])} • 👥 {r['members']}")
    lines.append("")
    if my_clan is not None:
        lines.append(f"📍 Твой клан {clan_title(my_clan['name'])}: <b>{my_clan['place']} место</b> • {score(my_clan['rating'])}")
    else:
        lines.append("📍 Ты не в клане — вступи в любой или создай свой 🛡")
    if rows:
        lines.append("🛡 Нажми на место ниже, чтобы открыть клан")
    return "\n".join(lines)


def clans_intro(total: int, note: str | None = None) -> str:
    lines = [note, ""] if note else []
    lines += [
        "🛡 <b>Кланы</b>",
        "",
        "Объединяйтесь и поднимайтесь в топе кланов:",
        "• рейтинг участника — его лучшая оценка",
        "• рейтинг клана — сумма рейтингов всех участников",
        "• создать можно один клан, вступить — в любой",
        "",
        f"Кланов сейчас: <b>{total}</b>. Ты пока не в клане 👇",
    ]
    return "\n".join(lines)


def clan_caption(clan, members, owner, total_clans: int, is_member: bool, note: str | None = None,
                 admin: bool = False) -> str:
    lines = [note, ""] if note else []
    lines += [
        f"🛡 <b>Клан {clan_title(clan['name'])}</b>",
        f"👑 Глава: {user_name(owner)}",
        f"⭐ Рейтинг клана: <b>{score(clan['rating'])}</b>",
        f"🏆 Место в топе: <b>{clan['place']}</b> из {total_clans}",
        f"👥 Участников: <b>{clan['members']}</b>",
        "",
        "<b>Состав</b> (рейтинг — лучшая оценка):",
    ]
    for i, m in enumerate(members, 1):
        crown = "👑 " if m["id"] == clan["owner_id"] else ""
        sc = score(m["best_score"]) if m["best_score"] is not None else "нет оценки"
        lines.append(f"{i}. {crown}{user_name(m)} — {sc}")
    if clan["members"] > len(members):
        lines.append(f"…и ещё {clan['members'] - len(members)}")
    if is_member:
        lines += ["", "✅ Ты в этом клане"]
    if admin:
        lines.append(f"🔧 Номер клана: <code>{clan['id']}</code>")
    return "\n".join(lines)


def clan_list_caption(total: int, page: int, pages: int) -> str:
    if not total:
        return "📋 <b>Все кланы</b>\n\nКланов пока нет — создай первый!"
    pg = f" • стр. {page + 1}/{pages}" if pages > 1 else ""
    return f"📋 <b>Все кланы</b>: {total}{pg}\n\nКланы отсортированы по рейтингу. Нажми на клан, чтобы посмотреть состав и вступить 👇"


def clan_button(place: int, clan) -> str:
    return f"{place}. {short(clan['name'], 20)} — {score(clan['rating'])} 👥{clan['members']}"


def clan_name_prompt(current_clan: str | None) -> str:
    lines = [
        "✍️ <b>Название клана</b>",
        "",
        "Пришли название одним сообщением: от 2 до 24 символов — буквы, цифры, пробелы, эмодзи.",
    ]
    if current_clan:
        lines += ["", f"⚠️ Сейчас ты в клане {clan_title(current_clan)} — после создания перейдёшь в новый."]
    return "\n".join(lines)


CLAN_NAME_ERRORS = {
    "length": "✍️ Название должно быть от 2 до 24 символов. Пришли другое:",
    "chars": "✍️ В названии можно использовать буквы, цифры, пробелы, эмодзи и знаки - _ . , ! ? ' \" ( ) # & + * ~ | :\nПришли другое:",
    "letters": "✍️ В названии должна быть хотя бы одна буква или цифра. Пришли другое:",
    "name_taken": "😕 Клан с таким названием уже есть. Придумай другое:",
}


def clan_created(name: str) -> str:
    return f"🎉 Клан {clan_title(name)} создан! Зови друзей: пусть откроют «🛡 Кланы» → «📋 Все кланы» и вступят."


def has_clan(name: str) -> str:
    return f"У тебя уже есть клан «{short(name, 24)}». Один человек может создать только один клан."


def owner_cant_join(name: str) -> str:
    return f"Ты глава клана «{short(name, 24)}». Чтобы вступить в другой, сначала распусти свой."


def switch_confirm(old: str, new: str) -> str:
    return f"🔄 Сейчас ты в клане {clan_title(old)}.\n\nПерейти в клан {clan_title(new)}?"


def leave_confirm(name: str) -> str:
    return f"🚪 Выйти из клана {clan_title(name)}?\n\nТвой рейтинг перестанет приносить клану очки."


def disband_confirm(name: str, members: int) -> str:
    others = max(0, members - 1)
    who = f"Остальные участники ({others}) останутся без клана. " if others else ""
    return f"🗑 Распустить клан {clan_title(name)}?\n\n{who}Это нельзя отменить."


def joined(name: str) -> str:
    return f"✅ Добро пожаловать в клан {clan_title(name)}!"


def left(name: str) -> str:
    return f"🚪 Ты больше не в клане {clan_title(name)}."


def disbanded(name: str) -> str:
    return f"🗑 Клан {clan_title(name)} распущен."


def new_member(user: str, clan: str) -> str:
    return f"👋 В клане {clan_title(clan)} новый участник: <b>{user}</b>"


def photo_caption(place: int, row, rating, clan_name: str | None) -> str:
    date = (rating["created_at"] or "")[:10]
    if len(date) == 10:
        date = f"{date[8:10]}.{date[5:7]}.{date[:4]}"
    clan = f" • 🛡 {escape(short(clan_name, 20))}" if clan_name else ""
    return (f"{place_mark(place)} <b>{place} место</b> в топе по рейтингу\n"
            f"👤 <b>{user_name(row)}</b> • ⭐ <b>{score(rating['score'])}</b>/10{clan}\n"
            f"📅 Оценка от {date}")


def my_best_caption(rating, place: int | None) -> str:
    date = (rating["created_at"] or "")[:10]
    if len(date) == 10:
        date = f"{date[8:10]}.{date[5:7]}.{date[:4]}"
    where = f" • {place} место в топе" if place else ""
    return f"🖼 <b>Твой лучший результат</b>: {score(rating['score'])}/10{where}\n📅 Оценка от {date}"


NO_PHOTO = "📷 Фото к этой оценке не сохранилось: она сделана до обновления бота."


def after_rating(record: bool, best: float | None, place: int | None, clan) -> str:
    lines = []
    if record:
        lines.append(f"🔥 <b>Новый личный рекорд!</b> Место в топе по рейтингу: <b>{place}</b>" if place
                     else "🔥 <b>Новый личный рекорд!</b>")
    elif best is not None:
        lines.append(f"🏆 Твой рейтинг (лучшая оценка): <b>{score(best)}</b>" + (f" • {place} место в топе" if place else ""))
    if clan is not None:
        lines.append(f"🛡 Рейтинг клана {clan_title(clan['name'])}: <b>{score(clan['rating'])}</b> • {clan['place']} место")
    return "\n".join(lines)
