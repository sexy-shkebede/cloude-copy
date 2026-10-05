"""Telegram-бот оценки внешности: анфас + профиль, баланс оценок, оплата звёздами."""
from __future__ import annotations

import asyncio
import logging
import sys
import time
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
from database import Database
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
AFTER_KB = kb([[("🔁 Новая оценка", "rate"), ("🏠 Меню", "menu")]])


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


def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("state", "front", "front_ts", "busy"):
        context.user_data.pop(k, None)


async def cleanup_sessions(app: Application) -> None:
    """Удаляет из памяти фото брошенных на полпути оценок."""
    while True:
        await asyncio.sleep(600)
        now = time.time()
        for data in list(app.user_data.values()):
            if data.get("front") is not None and now - data.get("front_ts", now) > SESSION_TTL and not data.get("busy"):
                for k in ("state", "front", "front_ts"):
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
    await show_photo_screen(update, context, "banner", banner_image, caption, main_menu_kb(u.id))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_flow(context)
    await show_menu(update, context)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_user(update)
    uid = update.effective_user.id
    row = db.get_user(uid)
    bal, free = int(row["balance"]), is_free(uid)
    caption = texts.balance_caption(bal, free, int(row["ratings_count"]), row["best_score"])
    await show_photo_screen(
        update, context, f"balance:{bal}:{free}", lambda: render_balance(bal, config.PRICE_STARS, free), caption,
        kb([[("📸 Оценить", "rate"), ("⭐ Пополнить", "shop")], [("🏠 Меню", "menu")]]),
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
    await finalize(update, context, profile, status)


def _build_all(front, profile, gender):
    report = build_report(front, profile, gender)
    card = render_result_card(report, front, config.BRAND)
    front_img = render_front(front, report)
    profile_img = render_profile(profile, report) if profile is not None else None
    return report, card, front_img, profile_img


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE, profile, status) -> None:
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
        await context.bot.send_media_group(chat_id, media)
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

    db.record_rating(uid, report.total, {p.key: round(p.score, 2) for p in report.parts}, paid=not free)
    chunks = texts.format_report(report)
    bal_line = "\n\n💰 Осталось: ∞ (админ)" if free else (
        f"\n\n💰 Осталось: {db.get_balance(uid)} {texts.ratings_word(db.get_balance(uid))}")
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        await context.bot.send_message(chat_id, chunk + (bal_line if last else ""), parse_mode=HTML,
                                       reply_markup=AFTER_KB if last else None)
    reset_flow(context)


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
        f"🗓 С нами с {row['created_at'][:10]}",
    ]
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


# ---------------------------------------------------------------- кнопки и прочее
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    if data == "miniapp":
        await q.answer(texts.MINIAPP_SOON, show_alert=True)
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
    if state in ("front", "side"):
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


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("rate", "Оценить внешность"),
        BotCommand("balance", "Мой баланс"),
        BotCommand("buy", "Купить оценки"),
        BotCommand("help", "Как это работает"),
        BotCommand("paysupport", "Поддержка по оплате"),
        BotCommand("terms", "Условия"),
    ])
    app.create_task(cleanup_sessions(app))
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
    app = build_app(config.BOT_TOKEN)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
