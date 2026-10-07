"""Telegram-бот оценки внешности: анфас + профиль, баланс оценок, оплата звёздами."""
from __future__ import annotations

import asyncio
import io
import logging
import math
import sys
import time
import unicodedata
from html import escape
from logging.handlers import RotatingFileHandler

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LabeledPrice,
    MessageOriginUser,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

import config
import texts
from analyzer import FEMALE, MALE, AnalysisError, analyze_front, analyze_profile, build_report, get_models
from database import Database, clan_key
from graphics.boards import HEADERS, render_header
from graphics.cards import render_balance, render_banner, render_result_card, render_step_front, render_step_profile
from graphics.overlays import render_front, render_profile

log = logging.getLogger("facebot")
HTML = ParseMode.HTML
MAX_FILE_SIZE = 20 * 1024 * 1024
SESSION_TTL = 30 * 60  # незавершённая оценка хранится в памяти 30 минут

db = Database(config.DB_PATH)
analysis_sem = asyncio.Semaphore(config.ANALYSIS_WORKERS)
file_ids: dict[str, str] = {}  # кэш file_id статичных картинок, чтобы не загружать их повторно


# ---------------------------------------------------------------- утилиты
def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def is_free(uid: int) -> bool:
    return config.ADMIN_FREE and is_admin(uid)


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    if config.MINI_APP_URL:
        mini = InlineKeyboardButton("📱 Мини-приложение", web_app=WebAppInfo(config.MINI_APP_URL))
    else:
        mini = InlineKeyboardButton("📱 Мини-приложение", callback_data="miniapp")
    bal = "∞" if is_free(uid) else str(db.get_balance(uid))
    rows = [
        [mini],
        [InlineKeyboardButton("📸 Оценить в чате", callback_data="rate")],
        [
            InlineKeyboardButton("🏆 Топы", callback_data="top:rating"),
            InlineKeyboardButton("🛡 Кланы", callback_data="clans"),
        ],
        [
            InlineKeyboardButton(f"💰 Баланс: {bal}", callback_data="balance"),
            InlineKeyboardButton("⭐ Пополнить", callback_data="shop"),
        ],
        [InlineKeyboardButton("ℹ️ Как это работает", callback_data="help")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def shop_kb() -> InlineKeyboardMarkup:
    rows = [
        [(f"{n} {texts.ratings_word(n)} — {n * config.PRICE_STARS} ⭐", f"buy:{n}")] for n in config.PACKS
    ]
    rows.append([("🏠 Меню", "menu")])
    return kb(rows)


CANCEL_KB = kb([[("❌ Отмена", "cancel")]])
AFTER_KB = kb([[("🔁 Новая оценка", "rate"), ("🏆 Топ", "top:rating")], [("🏠 Меню", "menu")]])


def remember_user(update: Update) -> None:
    u = update.effective_user
    if u:
        db.upsert_user(u.id, u.username, u.first_name, config.WELCOME_BONUS)


async def run_cpu(fn, *args):
    """Тяжёлые вычисления — в отдельном потоке и не больше ANALYSIS_WORKERS одновременно."""
    async with analysis_sem:
        return await asyncio.to_thread(fn, *args)


async def send_photo_cached(context: ContextTypes.DEFAULT_TYPE, chat_id: int, key: str, render, caption: str,
                            markup: InlineKeyboardMarkup | None):
    fid = file_ids.get(key)
    try:
        msg = await context.bot.send_photo(chat_id, photo=fid or render(), caption=caption, parse_mode=HTML,
                                           reply_markup=markup)
    except BadRequest:
        if not fid:
            raise
        file_ids.pop(key, None)
        msg = await context.bot.send_photo(chat_id, photo=render(), caption=caption, parse_mode=HTML,
                                           reply_markup=markup)
    if msg.photo:
        file_ids[key] = msg.photo[-1].file_id
    return msg


async def show_photo_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, render, caption: str,
                            markup: InlineKeyboardMarkup) -> None:
    """Если нажата кнопка под картинкой — меняем картинку на месте, иначе отправляем новое сообщение."""
    q = update.callback_query
    if q and q.message and getattr(q.message, "photo", None):
        fid = file_ids.get(key)
        try:
            msg = await q.edit_message_media(
                InputMediaPhoto(fid or render(), caption=caption, parse_mode=HTML), reply_markup=markup
            )
            if hasattr(msg, "photo") and msg.photo:
                file_ids[key] = msg.photo[-1].file_id
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            log.debug("edit_message_media failed: %s", e)
    await send_photo_cached(context, update.effective_chat.id, key, render, caption, markup)


async def safe_edit(msg, text: str, **kwargs) -> None:
    """Редактирует сообщение, не падая на «message is not modified» и сетевых мелочах."""
    try:
        await msg.edit_text(text, **kwargs)
    except TelegramError as e:
        log.debug("edit_text failed: %s", e)


FLOW_KEYS = ("state", "front", "front_ts", "front_jpg", "front_fid")


def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in FLOW_KEYS + ("busy",):
        context.user_data.pop(k, None)


async def cleanup_sessions(app: Application) -> None:
    """Удаляет из памяти фото брошенных на полпути оценок."""
    while True:
        await asyncio.sleep(600)
        now = time.time()
        for data in list(app.user_data.values()):
            if data.get("front") is not None and now - data.get("front_ts", now) > SESSION_TTL and not data.get("busy"):
                for k in FLOW_KEYS:
                    data.pop(k, None)


# ---------------------------------------------------------------- экраны
def banner_image() -> bytes:
    """Своя картинка приветствия из assets/banner.jpg, иначе — нарисованный баннер."""
    if config.BANNER_PATH.is_file():
        return config.BANNER_PATH.read_bytes()
    return render_banner(config.BRAND, config.PRICE_STARS)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    u = update.effective_user
    caption = texts.start_caption(u.first_name or "друг", db.get_balance(u.id), config.PRICE_STARS, is_free(u.id))
    try:
        await show_photo_screen(update, context, "banner", banner_image, caption, main_menu_kb(u.id))
    except BadRequest as e:  # Telegram не принял свою картинку (битый файл, > 10 МБ) — показываем нарисованную
        log.warning("Картинка приветствия отклонена Telegram (%s) — использую нарисованный баннер", e)
        await show_photo_screen(update, context, "banner_generated",
                                lambda: render_banner(config.BRAND, config.PRICE_STARS), caption, main_menu_kb(u.id))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_flow(context)
    await show_menu(update, context)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    uid = update.effective_user.id
    row = db.get_user(uid)
    bal, free = int(row["balance"]), is_free(uid)
    clan = db.get_clan(row["clan_id"]) if row["clan_id"] is not None else None
    hidden = bool(row["photo_hidden"])
    caption = texts.balance_caption(bal, free, int(row["ratings_count"]), row["best_score"], db.rating_place(uid),
                                    clan["name"] if clan else None, hidden)
    rows = [[("📸 Оценить", "rate"), ("⭐ Пополнить", "shop")]]
    if row["best_score"] is not None:
        rows.append([("🖼 Мой лучший результат", "me:best")])
        rows.append([("👁 Показывать фото в топе", "me:show") if hidden else ("🙈 Скрыть фото из топа", "me:hide")])
    rows.append([("🏆 Топы", "top:rating"), ("🏠 Меню", "menu")])
    await show_photo_screen(
        update, context, f"balance:{bal}:{free}", lambda: render_balance(bal, config.PRICE_STARS, free), caption, kb(rows),
    )


async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    uid = update.effective_user.id
    bal, free = db.get_balance(uid), is_free(uid)
    await show_photo_screen(update, context, f"balance:{bal}:{free}",
                            lambda: render_balance(bal, config.PRICE_STARS, free),
                            texts.shop_caption(bal, config.PRICE_STARS, free), shop_kb())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(update.effective_chat.id, texts.HELP, parse_mode=HTML,
                                   reply_markup=kb([[("📸 Оценить в чате", "rate")], [("🏠 Меню", "menu")]]))


# ---------------------------------------------------------------- сценарий оценки
async def start_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    if context.user_data.get("busy"):
        await context.bot.send_message(chat_id, "⏳ Подожди, я ещё анализирую предыдущее фото…")
        return
    if not is_free(uid) and db.get_balance(uid) < 1:
        await context.bot.send_message(chat_id, texts.not_enough(db.get_balance(uid), config.PRICE_STARS),
                                       parse_mode=HTML, reply_markup=shop_kb())
        return
    reset_flow(context)
    context.user_data["state"] = "gender"
    await context.bot.send_message(
        chat_id, texts.ASK_GENDER, parse_mode=HTML,
        reply_markup=kb([[("👨 Мужчина", f"gender:{MALE}"), ("👩 Женщина", f"gender:{FEMALE}")], [("❌ Отмена", "cancel")]]),
    )


async def choose_gender(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str) -> None:
    q = update.callback_query
    if context.user_data.get("state") != "gender":
        await context.bot.send_message(update.effective_chat.id, "Начни оценку заново 👇",
                                       reply_markup=kb([[("📸 Оценить", "rate")]]))
        return
    context.user_data["gender"] = gender
    context.user_data["state"] = "front"
    db.set_gender(update.effective_user.id, gender)
    try:
        await q.edit_message_text(f"👤 Пол: <b>{'мужской' if gender == MALE else 'женский'}</b> ✓", parse_mode=HTML)
    except TelegramError:
        pass
    await send_photo_cached(context, update.effective_chat.id, "step_front", render_step_front, texts.STEP_FRONT,
                            CANCEL_KB)


async def download_image(update: Update) -> bytes | None:
    msg = update.effective_message
    if msg.photo:
        tg_file = await msg.photo[-1].get_file()
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        if (msg.document.file_size or 0) > MAX_FILE_SIZE:
            return None
        tg_file = await msg.document.get_file()
    else:
        return None
    return bytes(await tg_file.download_as_bytearray())


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    msg = update.effective_message
    state = context.user_data.get("state")
    if state not in ("front", "side"):
        await msg.reply_text(texts.NO_STATE_PHOTO, reply_markup=kb([[("📸 Оценить в чате", "rate")], [("🏠 Меню", "menu")]]))
        return
    if context.user_data.get("busy"):
        await msg.reply_text("⏳ Подожди, я ещё анализирую предыдущее фото…")
        return
    context.user_data["busy"] = True
    try:
        data = await download_image(update)
        if not data:
            await msg.reply_text(texts.ERRORS["bad_image"], parse_mode=HTML)
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        if state == "front":
            await handle_front(update, context, data)
        else:
            await handle_side(update, context, data)
    finally:
        context.user_data["busy"] = False


async def handle_front(update: Update, context: ContextTypes.DEFAULT_TYPE, data: bytes) -> None:
    msg = update.effective_message
    status = await msg.reply_text(texts.PROGRESS[0])
    try:
        front = await run_cpu(analyze_front, data)
    except AnalysisError as e:
        await safe_edit(status, texts.ERRORS.get(e.code, texts.ERRORS["no_face"]), parse_mode=HTML, reply_markup=CANCEL_KB)
        return
    except Exception:
        log.exception("front analysis failed")
        await safe_edit(status, "😕 Ошибка при анализе фото. Попробуй другое фото.", reply_markup=CANCEL_KB)
        return
    context.user_data["front"] = front
    context.user_data["front_ts"] = time.time()
    context.user_data["front_jpg"] = await asyncio.to_thread(compress_photo, data)  # сохраним вместе с оценкой
    context.user_data["front_fid"] = msg.photo[-1].file_id if msg.photo else None
    context.user_data["state"] = "side"
    try:
        await status.delete()
    except TelegramError:
        pass
    await send_photo_cached(context, update.effective_chat.id, "step_side", render_step_profile, texts.STEP_SIDE,
                            CANCEL_KB)


async def handle_side(update: Update, context: ContextTypes.DEFAULT_TYPE, data: bytes) -> None:
    msg = update.effective_message
    status = await msg.reply_text("🔍 Ищу профиль на фото…")
    try:
        profile = await run_cpu(analyze_profile, data)
    except AnalysisError as e:
        await safe_edit(status, 
            texts.ERRORS.get(e.code, texts.ERRORS["profile_fail"]), parse_mode=HTML,
            reply_markup=kb([[("⏭ Продолжить без профиля", "skip_profile")], [("❌ Отмена", "cancel")]]),
        )
        return
    except Exception:
        log.exception("profile analysis failed")
        await safe_edit(status, 
            "😕 Ошибка при анализе профиля. Пришли другое фото или продолжи без профиля.",
            reply_markup=kb([[("⏭ Продолжить без профиля", "skip_profile")], [("❌ Отмена", "cancel")]]),
        )
        return
    side_fid = msg.photo[-1].file_id if msg.photo else None
    await finalize(update, context, profile, status, side_data=data, side_fid=side_fid)


def _build_all(front, profile, gender):
    report = build_report(front, profile, gender)
    card = render_result_card(report, front, config.BRAND)
    front_img = render_front(front, report)
    profile_img = render_profile(profile, report) if profile is not None else None
    return report, card, front_img, profile_img


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE, profile, status, side_data: bytes | None = None,
                   side_fid: str | None = None) -> None:
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    front = context.user_data.get("front")
    gender = context.user_data.get("gender", MALE)
    if front is None:
        reset_flow(context)
        await safe_edit(status, "Сессия устарела — начни оценку заново.", reply_markup=kb([[("📸 Оценить", "rate")]]))
        return
    free = is_free(uid)
    if not free and not db.try_spend(uid, 1):
        await safe_edit(status, 
            texts.not_enough(db.get_balance(uid), config.PRICE_STARS)
            + "\n\nПосле пополнения пришли фото профиля ещё раз — фото анфас я запомнил.",
            parse_mode=HTML, reply_markup=shop_kb(),
        )
        return
    try:
        await safe_edit(status, texts.PROGRESS[2])
        build = asyncio.ensure_future(run_cpu(_build_all, front, profile, gender))
        await asyncio.sleep(0.8)
        if not build.done():
            await safe_edit(status, texts.PROGRESS[3])
        report, card, front_img, profile_img = await build
        media = [InputMediaPhoto(card, caption=texts.result_caption(report), parse_mode=HTML),
                 InputMediaPhoto(front_img)]
        if profile_img:
            media.append(InputMediaPhoto(profile_img))
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
        sent = await context.bot.send_media_group(chat_id, media)
    except Exception:
        log.exception("result rendering/sending failed")
        if not free:
            db.add_balance(uid, 1)
        await safe_edit(status, "😕 Не удалось подготовить результат. Оценка не списана — попробуй ещё раз.",
                        reply_markup=kb([[("🔁 Попробовать снова", "rate")]]))
        return
    try:
        await status.delete()
    except TelegramError:
        pass

    rid, record = db.record_rating(uid, report.total, {p.key: round(p.score, 2) for p in report.parts}, paid=not free,
                                   gender=gender, details=report_details(report))
    card_fid = sent[0].photo[-1].file_id if sent and sent[0].photo else None
    await store_rating_files(uid, rid, context.user_data.get("front_jpg"),
                             side_data if profile is not None else None, card,
                             front_fid=context.user_data.get("front_fid"),
                             side_fid=side_fid if profile is not None else None, card_fid=card_fid)
    chunks = texts.format_report(report)
    bal_line = "\n\n💰 Осталось: ∞ (админ)" if free else (
        f"\n\n💰 Осталось: {db.get_balance(uid)} {texts.ratings_word(db.get_balance(uid))}")
    me = db.get_user(uid)
    my_clan = db.get_clan(me["clan_id"]) if me and me["clan_id"] is not None else None
    standing = texts.after_rating(record, me["best_score"] if me else None, db.rating_place(uid), my_clan)
    if standing:
        bal_line += "\n" + standing
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        await context.bot.send_message(chat_id, chunk + (bal_line if last else ""), parse_mode=HTML,
                                       reply_markup=AFTER_KB if last else None)
    reset_flow(context)


# ---------------------------------------------------------------- хранение фото и результатов
def compress_photo(data: bytes | None, max_side: int = 1280) -> bytes | None:
    """Фото для архива на телефоне: JPEG до 1280 px по большей стороне (~100–200 КБ)."""
    if not data:
        return None
    try:
        from PIL import Image, ImageOps

        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=87, optimize=True)
        return buf.getvalue()
    except Exception:
        log.exception("Не удалось сжать фото для архива")
        return None


def report_details(report) -> dict:
    """Полный результат для базы: итог, части лица и все замеры."""
    return {
        "total": report.total,
        "tier": report.tier,
        "face_shape": report.face_shape,
        "has_profile": report.has_profile,
        "parts": [
            {"key": p.key, "title": p.title, "score": round(p.score, 2),
             "metrics": [{"key": m.key, "label": m.label, "value": m.value_text, "ideal": m.ideal_text,
                          "score": round(m.score, 2)} for m in p.metrics]}
            for p in report.parts
        ],
        "warnings": list(report.warnings),
    }


async def store_rating_files(uid: int, rid: int, front_jpg: bytes | None, side_data: bytes | None, card: bytes | None,
                             front_fid: str | None, side_fid: str | None, card_fid: str | None) -> None:
    """Кладёт фото анфас, профиль и карточку результата в data/photos/<uid>/ и запоминает пути в базе."""
    def work() -> dict:
        folder = config.PHOTOS_DIR / str(uid)
        folder.mkdir(parents=True, exist_ok=True)
        out = {}
        for kind, data in (("front", front_jpg), ("side", compress_photo(side_data)), ("card", card)):
            if data:
                path = folder / f"{rid}_{kind}.jpg"
                path.write_bytes(data)
                out[f"{kind}_path"] = path.relative_to(config.DATA_DIR).as_posix()
        return out

    try:
        paths = await asyncio.to_thread(work)
    except Exception:  # нет места на телефоне и т. п. — оценка всё равно засчитана
        log.exception("Не удалось сохранить фото оценки %s", rid)
        paths = {}
    db.set_rating_files(rid, front_file_id=front_fid, side_file_id=side_fid, card_file_id=card_fid, **paths)


def _rating_media(rating, prefer_ids: bool) -> list[tuple[str, object]]:
    """Что отправить: фото анфас, профиль и карточку — по file_id из Telegram или из файлов на телефоне."""
    out = []
    for kind in ("front", "side", "card"):
        fid = rating[f"{kind}_file_id"]
        rel = rating[f"{kind}_path"]
        path = config.DATA_DIR / rel if rel else None
        if prefer_ids and fid:
            out.append((kind, fid))
        elif path is not None and path.is_file():
            out.append((kind, path.read_bytes()))
    return out


def has_rating_photos(rating) -> bool:
    return rating is not None and bool(_rating_media(rating, prefer_ids=True))


async def send_rating_photos(context: ContextTypes.DEFAULT_TYPE, chat_id: int, rating, caption: str) -> bool:
    for prefer_ids in (True, False):
        items = await asyncio.to_thread(_rating_media, rating, prefer_ids)
        if not items:
            return False
        try:
            if len(items) == 1:
                msgs = [await context.bot.send_photo(chat_id, items[0][1], caption=caption, parse_mode=HTML)]
            else:
                msgs = await context.bot.send_media_group(chat_id, [
                    InputMediaPhoto(src, caption=caption if i == 0 else None, parse_mode=HTML if i == 0 else None)
                    for i, (_, src) in enumerate(items)
                ])
        except BadRequest as e:  # file_id устарел (например, сменили токен) — шлём файлы с телефона
            if prefer_ids and any(isinstance(src, str) for _, src in items):
                log.info("file_id не подошёл (%s), отправляю фото из файлов", e)
                continue
            raise
        ids = {f"{kind}_file_id": m.photo[-1].file_id for (kind, _), m in zip(items, msgs) if m.photo}
        db.set_rating_files(rating["id"], **ids)
        return True
    return False


def photo_cooldown(context: ContextTypes.DEFAULT_TYPE, seconds: float = 3.0) -> bool:
    """Не даём заспамить чат альбомами: одно фото раз в несколько секунд."""
    now = time.monotonic()
    if now - context.user_data.get("photo_ts", 0.0) < seconds:
        return True
    context.user_data["photo_ts"] = now
    return False


# ---------------------------------------------------------------- топы
TOP_TABS = (("rating", "⭐ Рейтинг"), ("balance", "💰 Баланс"), ("clans", "🛡 Кланы"))
ANSWERED = object()  # обработчик уже ответил на нажатие кнопки сам


def header_image(kind: str):
    return lambda: render_header(kind)


def place_buttons(ids: list[int], prefix: str) -> list[list[InlineKeyboardButton]]:
    btns = [InlineKeyboardButton(texts.MEDALS.get(i, str(i)), callback_data=f"{prefix}:{x}") for i, x in enumerate(ids, 1)]
    return [row for row in (btns[:5], btns[5:10]) if row]


async def show_top(update: Update, context: ContextTypes.DEFAULT_TYPE, tab: str = "rating") -> None:
    remember_user(update)
    uid = update.effective_user.id
    me = db.get_user(uid)
    extra: list[list[InlineKeyboardButton]] = []
    if tab == "balance":
        rows = db.top_balance(config.TOP_SIZE)
        caption = texts.top_balance_caption(rows, db.balance_place(uid), int(me["balance"]), is_free(uid))
        kind = "top_balance"
    elif tab == "clans":
        rows = db.top_clans(config.TOP_SIZE)
        my_clan = db.get_clan(me["clan_id"]) if me["clan_id"] is not None else None
        caption = texts.top_clans_caption(rows, my_clan)
        extra = place_buttons([r["id"] for r in rows], "clan:view")
        extra.append([InlineKeyboardButton("🛡 Мой клан" if my_clan else "➕ Вступить или создать клан",
                                           callback_data="clans")])
        kind = "top_clans"
    else:
        tab = "rating"
        rows = db.top_rating(config.TOP_SIZE)
        caption = texts.top_rating_caption(rows, db.rating_place(uid), me["best_score"], db.rated_count())
        extra = place_buttons([r["id"] for r in rows], "top:ph")
        kind = "top_rating"
    tabs = [InlineKeyboardButton(f"• {label} •" if key == tab else label, callback_data=f"top:{key}")
            for key, label in TOP_TABS]
    markup = InlineKeyboardMarkup([tabs, *extra, [InlineKeyboardButton("🏠 Меню", callback_data="menu")]])
    await show_photo_screen(update, context, f"hdr:{kind}", header_image(kind), caption, markup)


async def show_top_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, target: int):
    """Фото, за которое участник занял место в топе по рейтингу."""
    uid = update.effective_user.id
    rows = db.top_rating(config.TOP_SIZE)
    ids = [int(r["id"]) for r in rows]
    if target not in ids:
        return "Этот участник уже не в топ-10. Открой топ заново 🔄"
    place = ids.index(target) + 1
    row = rows[place - 1]
    if row["photo_hidden"] and target != uid:
        return "🙈 Участник скрыл своё фото из топа."
    rating = db.best_rating(target)
    if not has_rating_photos(rating):
        return texts.NO_PHOTO
    if photo_cooldown(context):
        return "⏳ Подожди пару секунд…"
    await update.callback_query.answer()
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
    if not await send_rating_photos(context, update.effective_chat.id, rating,
                                    texts.photo_caption(place, row, rating, row["clan_name"])):
        await context.bot.send_message(update.effective_chat.id, texts.NO_PHOTO)
    return ANSWERED


async def show_my_best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rating = db.best_rating(uid)
    if rating is None:
        return "У тебя пока нет оценок 📸"
    if not has_rating_photos(rating):
        return texts.NO_PHOTO
    if photo_cooldown(context):
        return "⏳ Подожди пару секунд…"
    await update.callback_query.answer()
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
    if not await send_rating_photos(context, update.effective_chat.id, rating,
                                    texts.my_best_caption(rating, db.rating_place(uid))):
        await context.bot.send_message(update.effective_chat.id, texts.NO_PHOTO)
    return ANSWERED


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_top(update, context, "rating")


# ---------------------------------------------------------------- кланы
CLANS_PER_PAGE = 8
NAME_PUNCT = set(" -_.,!?'\"()#&+*~|:")


def clean_clan_name(raw: str | None) -> tuple[str | None, str | None]:
    """Возвращает (название, None) или (None, код ошибки): length | chars | letters."""
    name = " ".join((raw or "").split())
    if not 2 <= len(name) <= 24:
        return None, "length"
    for ch in name:
        cat = unicodedata.category(ch)
        if ch in "<>" or not (ch in NAME_PUNCT or cat[0] in "LNMS" or ch == "‍"):
            return None, "chars"
    if not any(unicodedata.category(ch)[0] in "LN" for ch in name):
        return None, "letters"
    return name, None


async def show_clans(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str | None = None) -> None:
    """«🛡 Кланы»: свой клан, если он есть, иначе — что такое кланы и как вступить."""
    remember_user(update)
    cid = db.user_clan_id(update.effective_user.id)
    if cid is not None and db.get_clan(cid) is not None:
        await show_clan(update, context, cid, note=note)
        return
    markup = kb([[("➕ Создать клан", "clan:create"), ("📋 Все кланы", "clan:list:0")],
                 [("🏆 Топ кланов", "top:clans"), ("🏠 Меню", "menu")]])
    await show_photo_screen(update, context, "hdr:clans", header_image("clans"),
                            texts.clans_intro(db.clans_count(), note), markup)


async def show_clan(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, note: str | None = None) -> None:
    uid = update.effective_user.id
    clan = db.get_clan(cid)
    if clan is None:
        await show_clan_list(update, context, 0, note="😕 Клан не найден — возможно, его распустили.")
        return
    is_member = db.user_clan_id(uid) == cid
    rows = []
    if clan["owner_id"] == uid:
        rows.append([("🗑 Распустить клан", "clan:disband")])
    elif is_member:
        rows.append([("🚪 Выйти из клана", "clan:leave")])
    else:
        rows.append([("✅ Вступить в клан", f"clan:join:{cid}")])
    rows.append([("📋 Все кланы", "clan:list:0"), ("🏆 Топ кланов", "top:clans")])
    rows.append([("🏠 Меню", "menu")])
    caption = texts.clan_caption(clan, db.clan_members(cid, config.TOP_SIZE), db.get_user(clan["owner_id"]),
                                 db.clans_count(), is_member, note, admin=is_admin(uid))
    await show_photo_screen(update, context, "hdr:clans", header_image("clans"), caption, kb(rows))


async def show_clan_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, note: str | None = None) -> None:
    remember_user(update)
    uid = update.effective_user.id
    total = db.clans_count()
    pages = max(1, math.ceil(total / CLANS_PER_PAGE))
    page = min(max(0, page), pages - 1)
    clans = db.clans_page(page * CLANS_PER_PAGE, CLANS_PER_PAGE)
    rows = [[InlineKeyboardButton(texts.clan_button(page * CLANS_PER_PAGE + i + 1, c), callback_data=f"clan:view:{c['id']}")]
            for i, c in enumerate(clans)]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"clan:list:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"clan:list:{page + 1}"))
        rows.append(nav)
    bottom = []
    if db.owned_clan(uid) is None:
        bottom.append(InlineKeyboardButton("➕ Создать клан", callback_data="clan:create"))
    if db.user_clan_id(uid) is not None:
        bottom.append(InlineKeyboardButton("🛡 Мой клан", callback_data="clans"))
    if bottom:
        rows.append(bottom)
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu")])
    caption = texts.clan_list_caption(total, page, pages)
    if note:
        caption = f"{note}\n\n{caption}"
    await show_photo_screen(update, context, "hdr:clans", header_image("clans"), caption, InlineKeyboardMarkup(rows))


async def clan_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    own = db.owned_clan(uid)
    if own is not None:
        return texts.has_clan(own["name"])
    if context.user_data.get("busy"):
        return "⏳ Подожди, я ещё анализирую фото…"
    await update.callback_query.answer()
    reset_flow(context)
    context.user_data["state"] = "clan_name"
    cid = db.user_clan_id(uid)
    current = db.get_clan(cid) if cid is not None else None
    await context.bot.send_message(update.effective_chat.id, texts.clan_name_prompt(current["name"] if current else None),
                                   parse_mode=HTML, reply_markup=kb([[("❌ Отмена", "clan:cancel")]]))
    return ANSWERED


async def on_clan_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    msg = update.effective_message
    uid = update.effective_user.id
    name, err = clean_clan_name(msg.text)
    cancel = kb([[("❌ Отмена", "clan:cancel")]])
    if err:
        await msg.reply_text(texts.CLAN_NAME_ERRORS[err], reply_markup=cancel)
        return
    clan, err = db.create_clan(uid, name, clan_key(name))
    if err == "name_taken":
        await msg.reply_text(texts.CLAN_NAME_ERRORS["name_taken"], reply_markup=cancel)
        return
    context.user_data.pop("state", None)
    if err == "has_clan":
        own = db.owned_clan(uid)
        await msg.reply_text(texts.has_clan(own["name"] if own else ""), reply_markup=kb([[("🛡 Мой клан", "clans")]]))
        return
    log.info("Клан #%s «%s» создан пользователем %s", clan["id"], name, uid)
    await show_clan(update, context, int(clan["id"]), note=texts.clan_created(name))


async def clan_join(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, confirmed: bool):
    uid = update.effective_user.id
    clan = db.get_clan(cid)
    if clan is None:
        return "😕 Клан не найден — возможно, его распустили."
    current = db.user_clan_id(uid)
    if current == cid:
        return "Ты уже в этом клане 🙂"
    own = db.owned_clan(uid)
    if own is not None:
        return texts.owner_cant_join(own["name"])
    old = db.get_clan(current) if current is not None else None
    if old is not None and not confirmed:
        await show_photo_screen(update, context, "hdr:clans", header_image("clans"),
                                texts.switch_confirm(old["name"], clan["name"]),
                                kb([[("✅ Перейти", f"clan:joinc:{cid}"), ("◀️ Назад", f"clan:view:{cid}")]]))
        return None
    res = db.join_clan(uid, cid)
    if res == "no_clan":
        return "😕 Клан не найден — возможно, его распустили."
    if res == "owner":
        own = db.owned_clan(uid)
        return texts.owner_cant_join(own["name"] if own else "")
    if res == "ok" and clan["owner_id"] != uid:
        try:
            await context.bot.send_message(clan["owner_id"], texts.new_member(texts.user_name(db.get_user(uid)), clan["name"]),
                                           parse_mode=HTML)
        except TelegramError:
            pass
    await show_clan(update, context, cid, note=texts.joined(clan["name"]))
    return None


async def clan_leave(update: Update, context: ContextTypes.DEFAULT_TYPE, confirmed: bool):
    uid = update.effective_user.id
    cid = db.user_clan_id(uid)
    clan = db.get_clan(cid) if cid is not None else None
    if clan is None:
        await show_clans(update, context)
        return "Ты не в клане."
    if clan["owner_id"] == uid:
        return "Глава не может выйти из своего клана — его можно только распустить."
    if not confirmed:
        await show_photo_screen(update, context, "hdr:clans", header_image("clans"), texts.leave_confirm(clan["name"]),
                                kb([[("🚪 Да, выйти", "clan:leavec"), ("◀️ Нет", "clans")]]))
        return None
    res = db.leave_clan(uid)
    if res == "owner":
        return "Глава не может выйти из своего клана — его можно только распустить."
    await show_clans(update, context, note=texts.left(clan["name"]))
    return None


async def clan_disband(update: Update, context: ContextTypes.DEFAULT_TYPE, confirmed: bool):
    uid = update.effective_user.id
    own = db.owned_clan(uid)
    if own is None:
        await show_clans(update, context)
        return "У тебя нет своего клана."
    clan = db.get_clan(own["id"])
    if not confirmed:
        await show_photo_screen(update, context, "hdr:clans", header_image("clans"),
                                texts.disband_confirm(own["name"], int(clan["members"]) if clan else 1),
                                kb([[("🗑 Да, распустить", "clan:disbandc"), ("◀️ Нет", "clans")]]))
        return None
    deleted, _ = db.delete_clan(int(own["id"]))
    if deleted is not None:
        log.info("Клан #%s «%s» распущен главой %s", own["id"], own["name"], uid)
    await show_clans(update, context, note=texts.disbanded(own["name"]))
    return None


async def cmd_clan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_clans(update, context)


def _int_arg(parts: list[str], i: int) -> int | None:
    try:
        return int(parts[i])
    except (IndexError, ValueError):
        return None


async def route_social(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Кнопки топов, кланов и «моего» профиля. Возвращает текст всплывающего ответа, None или ANSWERED."""
    parts = data.split(":")
    head = parts[0]
    if data == "clans":
        await show_clans(update, context)
    elif head == "top":
        if len(parts) == 3 and parts[1] == "ph":
            target = _int_arg(parts, 2)
            return await show_top_photo(update, context, target) if target is not None else None
        await show_top(update, context, parts[1] if len(parts) > 1 else "rating")
    elif head == "me":
        uid = update.effective_user.id
        action = parts[1] if len(parts) > 1 else ""
        if action == "best":
            return await show_my_best(update, context)
        if action in ("hide", "show"):
            remember_user(update)
            db.set_photo_hidden(uid, action == "hide")
            await show_balance(update, context)
            return "🙈 Фото больше не видно в топе" if action == "hide" else "👁 Фото снова видно в топе"
    elif head == "clan":
        action = parts[1] if len(parts) > 1 else ""
        remember_user(update)
        if action == "create":
            return await clan_create_start(update, context)
        if action == "cancel":
            context.user_data.pop("state", None)
            await show_clans(update, context)
        elif action == "list":
            await show_clan_list(update, context, _int_arg(parts, 2) or 0)
        elif action == "view":
            cid = _int_arg(parts, 2)
            if cid is not None:
                await show_clan(update, context, cid)
        elif action in ("join", "joinc"):
            cid = _int_arg(parts, 2)
            if cid is not None:
                return await clan_join(update, context, cid, confirmed=action == "joinc")
        elif action in ("leave", "leavec"):
            return await clan_leave(update, context, confirmed=action == "leavec")
        elif action in ("disband", "disbandc"):
            return await clan_disband(update, context, confirmed=action == "disbandc")
    return None


async def on_social_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    q = update.callback_query
    result = None
    try:
        result = await route_social(update, context, data)
    finally:
        if result is not ANSWERED:
            try:
                if result:
                    await q.answer(result, show_alert=True)
                else:
                    await q.answer()
            except TelegramError:
                pass


# ---------------------------------------------------------------- оплата звёздами
async def send_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, n: int) -> None:
    if n not in config.PACKS:
        return
    word = texts.ratings_word(n)
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"{n} {word} внешности",
        description=f"Пополнение баланса на {n} {word}. Одна оценка — разбор фото анфас и профиля.",
        payload=f"credits:{n}",
        currency="XTR",
        prices=[LabeledPrice(f"{n} {word}", n * config.PRICE_STARS)],
    )


def _parse_payload(payload: str) -> int | None:
    try:
        kind, n = payload.split(":")
        return int(n) if kind == "credits" and int(n) > 0 else None
    except ValueError:
        return None


async def on_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.pre_checkout_query
    n = _parse_payload(q.invoice_payload)
    if n is None or q.currency != "XTR" or q.total_amount != n * config.PRICE_STARS:
        await q.answer(ok=False, error_message="Цена изменилась — открой магазин заново: /buy")
        return
    await q.answer(ok=True)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    sp = update.effective_message.successful_payment
    uid = update.effective_user.id
    n = _parse_payload(sp.invoice_payload) or max(1, sp.total_amount // max(1, config.PRICE_STARS))
    is_new = db.record_payment(sp.telegram_payment_charge_id, uid, sp.total_amount, n, sp.invoice_payload)
    bal = db.get_balance(uid)
    text = texts.payment_ok(n, bal) if is_new else f"Этот платёж уже учтён. Баланс: <b>{bal}</b>"
    await update.effective_message.reply_text(
        text, parse_mode=HTML, reply_markup=kb([[("📸 Оценить сейчас", "rate")], [("🏠 Меню", "menu")]]),
    )
    if is_new:
        u = update.effective_user
        who = f"@{u.username}" if u.username else (u.first_name or "")
        try:
            await context.bot.send_message(
                config.OWNER_ID,
                f"💰 Оплата: {who} (<code>{uid}</code>) +{n} {texts.ratings_word(n)} за {sp.total_amount} ⭐\n"
                f"charge_id: <code>{sp.telegram_payment_charge_id}</code>",
                parse_mode=HTML,
            )
        except TelegramError:
            pass


# ---------------------------------------------------------------- админ-команды
async def _resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, need_amount: bool):
    """Цель — из аргумента (ID/@username) или из пересланного сообщения, на которое ответил админ."""
    msg = update.effective_message
    args = list(context.args or [])
    target = None
    reply = msg.reply_to_message
    if reply is not None:
        origin = getattr(reply, "forward_origin", None)
        if isinstance(origin, MessageOriginUser):
            target = origin.sender_user.id
            db.upsert_user(target, origin.sender_user.username, origin.sender_user.first_name)
    if target is None:
        if not args:
            return None, None, "Укажи пользователя: ID или @username."
        ref = args.pop(0)
        row = db.find_user(ref)
        if row is None:
            if ref.lstrip("-").isdigit():
                target = int(ref)
                db.ensure_user(target)
            else:
                return None, None, "Пользователь с таким @username не найден — пусть сначала запустит бота, либо используй ID."
        else:
            target = int(row["id"])
    amount = None
    if need_amount:
        if not args or not args[0].lstrip("-").isdigit() or not 0 < int(args[0]) <= 100000:
            return None, None, "Укажи количество оценок (целое число от 1 до 100000)."
        amount = int(args[0])
    return target, amount, None


def _user_label(uid: int) -> str:
    row = db.get_user(uid)
    if row and row["username"]:
        return f"@{row['username']} (<code>{uid}</code>)"
    if row and row["first_name"]:
        return f"{escape(row['first_name'])} (<code>{uid}</code>)"
    return f"<code>{uid}</code>"


async def cmd_give(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_balance(update, context, +1)


async def cmd_take(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_balance(update, context, -1)


async def _change_balance(update: Update, context: ContextTypes.DEFAULT_TYPE, sign: int) -> None:
    admin = update.effective_user.id
    if not is_admin(admin):
        return
    target, amount, err = await _resolve_target(update, context, need_amount=True)
    if err:
        usage = "/give ID 5" if sign > 0 else "/take ID 2"
        await update.effective_message.reply_text(f"⚠️ {err}\nПример: <code>{usage}</code>", parse_mode=HTML)
        return
    new_bal = db.add_balance(target, sign * amount)
    db.record_grant(admin, target, sign * amount)
    word = texts.ratings_word(amount)
    await update.effective_message.reply_text(
        f"✅ {_user_label(target)}: <b>{'+' if sign > 0 else '−'}{amount} {word}</b>\n"
        f"Баланс: <b>{new_bal} {texts.ratings_word(new_bal)}</b>", parse_mode=HTML)
    if sign > 0 and target != admin:
        try:
            await context.bot.send_message(
                target, f"🎁 Тебе начислено <b>{amount} {word}</b>!\nБаланс: <b>{new_bal} {texts.ratings_word(new_bal)}</b>",
                parse_mode=HTML, reply_markup=kb([[("📸 Оценить сейчас", "rate")]]),
            )
        except (Forbidden, BadRequest):
            await update.effective_message.reply_text("ℹ️ Пользователь ещё не запускал бота — уведомление не доставлено.")


async def cmd_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    target, _, err = await _resolve_target(update, context, need_amount=False)
    if err:
        await update.effective_message.reply_text(f"⚠️ {err}\nПример: <code>/user 123456</code>", parse_mode=HTML)
        return
    row = db.get_user(target)
    pays = db.last_payments(target)
    lines = [
        f"👤 {_user_label(target)}",
        f"💰 Баланс: <b>{row['balance']}</b>",
        f"📊 Оценок: {row['ratings_count']} • последняя: {row['last_score'] or '—'} • лучшая: {row['best_score'] or '—'}",
        f"🏆 Место в топе по рейтингу: {db.rating_place(target) or '—'}"
        + (" • фото скрыто" if row["photo_hidden"] else ""),
        f"🗓 С нами с {row['created_at'][:10]}",
    ]
    clan = db.get_clan(row["clan_id"]) if row["clan_id"] is not None else None
    if clan is not None:
        role = "глава" if clan["owner_id"] == target else "участник"
        lines.insert(4, f"🛡 Клан: {texts.clan_title(clan['name'])} (№{clan['id']}, {role})")
    if pays:
        lines.append("\n⭐ Последние оплаты:")
        lines += [f"• {p['created_at'][:16]} — {p['stars']}⭐ (+{p['credits']}){' ↩️' if p['refunded'] else ''}\n"
                  f"  <code>{p['charge_id']}</code>" for p in pays]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    await update.effective_message.reply_text(texts.admin_help(db.stats()), parse_mode=HTML)


async def cmd_refund(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.effective_message.reply_text("Пример: <code>/refund CHARGE_ID</code>", parse_mode=HTML)
        return
    pay = db.get_payment(context.args[0])
    if pay is None:
        await update.effective_message.reply_text("Платёж не найден.")
        return
    if pay["refunded"]:
        await update.effective_message.reply_text("Этот платёж уже возвращён.")
        return
    try:
        await context.bot.refund_star_payment(pay["user_id"], pay["charge_id"])
    except TelegramError as e:
        await update.effective_message.reply_text(f"Не удалось вернуть: {escape(str(e))}")
        return
    db.mark_refunded(pay["charge_id"])
    new_bal = db.add_balance(pay["user_id"], -int(pay["credits"]))
    await update.effective_message.reply_text(
        f"↩️ Возвращено {pay['stars']}⭐ пользователю {_user_label(pay['user_id'])}. Баланс: {new_bal}", parse_mode=HTML)


async def cmd_delclan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    ref = " ".join(context.args or [])
    clan = db.find_clan(ref) if ref else None
    if clan is None:
        await update.effective_message.reply_text(
            "⚠️ Клан не найден. Пример: <code>/delclan 12</code> или <code>/delclan Название</code>\n"
            "Номер клана админ видит в карточке клана.", parse_mode=HTML)
        return
    deleted, members = db.delete_clan(int(clan["id"]))
    if deleted is None:
        await update.effective_message.reply_text("Клан уже удалён.")
        return
    log.info("Админ %s удалил клан #%s «%s»", update.effective_user.id, clan["id"], clan["name"])
    await update.effective_message.reply_text(
        f"🗑 Клан {texts.clan_title(clan['name'])} (№{clan['id']}) удалён, участников было: {len(members)}.",
        parse_mode=HTML)
    if clan["owner_id"] != update.effective_user.id:
        try:
            await context.bot.send_message(clan["owner_id"],
                                           f"🗑 Твой клан {texts.clan_title(clan['name'])} удалён администратором.",
                                           parse_mode=HTML)
        except TelegramError:
            pass


async def cmd_unrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Убрать лучшую оценку пользователя из топа (например, если прислал чужое фото)."""
    if not is_admin(update.effective_user.id):
        return
    target, _, err = await _resolve_target(update, context, need_amount=False)
    if err:
        await update.effective_message.reply_text(f"⚠️ {err}\nПример: <code>/unrate 123456</code>", parse_mode=HTML)
        return
    removed = db.exclude_best_rating(target)
    if removed is None:
        await update.effective_message.reply_text("У пользователя нет оценок в топе.")
        return
    row = db.get_user(target)
    now_best = f"{row['best_score']:.1f}" if row["best_score"] is not None else "нет (выбыл из топа)"
    await update.effective_message.reply_text(
        f"✅ Оценка {removed['score']:.1f} пользователя {_user_label(target)} убрана из топа.\n"
        f"Теперь его рейтинг: <b>{now_best}</b>", parse_mode=HTML)


# ---------------------------------------------------------------- кнопки и прочее
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    if context.user_data.get("state") == "clan_name" and data != "clan:create":
        context.user_data.pop("state", None)  # ушёл с экрана ввода названия — не ловим следующий текст
    if data == "miniapp":
        await q.answer(texts.MINIAPP_SOON, show_alert=True)
        return
    if data == "noop":
        await q.answer()
        return
    if data == "clans" or data.startswith(("clan:", "top:", "me:")):
        await on_social_callback(update, context, data)
        return
    await q.answer()
    if data == "menu":
        await show_menu(update, context)
    elif data == "rate":
        await start_rating(update, context)
    elif data.startswith("gender:"):
        await choose_gender(update, context, FEMALE if data.endswith(FEMALE) else MALE)
    elif data == "cancel":
        reset_flow(context)
        await context.bot.send_message(update.effective_chat.id, "❌ Оценка отменена. Оценки с баланса не списаны.",
                                       reply_markup=kb([[("🏠 Меню", "menu")]]))
    elif data == "skip_profile":
        if context.user_data.get("state") == "side" and context.user_data.get("front") is not None:
            if context.user_data.get("busy"):
                return
            context.user_data["busy"] = True
            try:
                status = await context.bot.send_message(update.effective_chat.id, "⏭ Продолжаю без профиля…")
                await finalize(update, context, None, status)
            finally:
                context.user_data["busy"] = False
        else:
            await context.bot.send_message(update.effective_chat.id, "Начни оценку заново 👇",
                                           reply_markup=kb([[("📸 Оценить", "rate")]]))
    elif data == "balance":
        await show_balance(update, context)
    elif data == "shop":
        await show_shop(update, context)
    elif data.startswith("buy:"):
        try:
            await send_invoice(update, context, int(data.split(":")[1]))
        except ValueError:
            pass
    elif data == "help":
        await cmd_help(update, context)
    elif data == "admin":
        if is_admin(update.effective_user.id):
            await context.bot.send_message(update.effective_chat.id, texts.admin_help(db.stats()), parse_mode=HTML)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")
    if state == "clan_name":
        await on_clan_name(update, context)
    elif state in ("front", "side"):
        what = "анфас" if state == "front" else "в профиль (90°)"
        await update.effective_message.reply_text(f"📸 Жду фото {what}. Отправь его как фото или файл.",
                                                  reply_markup=CANCEL_KB)
    elif state == "gender":
        await update.effective_message.reply_text("👆 Выбери пол кнопкой выше.")
    else:
        await update.effective_message.reply_text("Нажми /start, чтобы открыть меню 🙂")


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(texts.PAYSUPPORT, parse_mode=HTML)


async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(texts.TERMS, parse_mode=HTML)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Ошибка при обработке апдейта", exc_info=context.error)


async def warm_up_images() -> None:
    """Рисуем шапки топов и кланов заранее в фоне, чтобы первое открытие не ждало."""
    for kind in HEADERS:
        try:
            await asyncio.to_thread(render_header, kind)
        except Exception:
            log.exception("Не удалось нарисовать картинку %s", kind)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("rate", "Оценить внешность"),
        BotCommand("top", "Топы"),
        BotCommand("clan", "Кланы"),
        BotCommand("balance", "Мой баланс"),
        BotCommand("buy", "Купить оценки"),
        BotCommand("help", "Как это работает"),
        BotCommand("paysupport", "Поддержка по оплате"),
        BotCommand("terms", "Условия"),
    ])
    app.create_task(cleanup_sessions(app))
    app.create_task(warm_up_images())
    me = await app.bot.get_me()
    log.info("Бот @%s запущен. Админы: %s", me.username, ", ".join(map(str, sorted(config.ADMIN_IDS))))


def setup_logging() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(config.LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_app(token: str, request=None) -> Application:
    builder = ApplicationBuilder().token(token).concurrent_updates(True).post_init(post_init)
    if request is not None:  # для тестов: подменённый сетевой слой
        builder = builder.request(request).get_updates_request(request)
    else:
        builder = builder.connect_timeout(20).read_timeout(30).write_timeout(60).media_write_timeout(120)
    app = builder.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("rate", start_rating))
    app.add_handler(CommandHandler("balance", show_balance))
    app.add_handler(CommandHandler("buy", show_shop))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("paysupport", cmd_paysupport))
    app.add_handler(CommandHandler("terms", cmd_terms))
    app.add_handler(CommandHandler("give", cmd_give))
    app.add_handler(CommandHandler("take", cmd_take))
    app.add_handler(CommandHandler("user", cmd_user))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("admin", cmd_stats))
    app.add_handler(CommandHandler("refund", cmd_refund))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler(["clan", "clans"], cmd_clan))
    app.add_handler(CommandHandler("delclan", cmd_delclan))
    app.add_handler(CommandHandler("unrate", cmd_unrate))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(on_precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    setup_logging()
    if not config.BOT_TOKEN:
        log.error("Не задан BOT_TOKEN. Создай файл .env (см. .env.example) и впиши токен от @BotFather.")
        sys.exit(2)
    try:
        get_models()  # загружаем модели заранее, чтобы первый анализ не ждал
    except RuntimeError as e:
        log.error("%s", e)
        sys.exit(2)
    if not config.BANNER_PATH.is_file():
        log.warning("Картинка приветствия %s не найдена — использую нарисованный баннер", config.BANNER_PATH)
    app = build_app(config.BOT_TOKEN)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
