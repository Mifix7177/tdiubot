import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Any, Optional, Set, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from backend.config import BOT_TOKEN, ADMIN_IDS
from backend.menu_engine import (
    process_user_action, 
    get_user_profile, 
    get_session, 
    update_user_language, 
    get_menu_buttons, 
    format_keyboard_grid
)
from backend.database import (
    get_db, 
    get_setting, 
    set_setting, 
    is_user_blocked, 
    block_user, 
    unblock_user, 
    get_blocked_users
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tdiu_bot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# In-memory maps & sessions
admin_notification_map: Dict[int, Dict[str, Any]] = {}
admin_msg_tracker: Dict[int, list] = {}
user_published_notif_map: Dict[int, tuple] = {}
admin_active_reply: Dict[int, Dict[str, Any]] = {}
admin_broadcast_sessions: Set[int] = set()
admin_channel_sessions: Set[int] = set()
admin_search_user_sessions: Set[int] = set()
admin_search_history_sessions: Set[int] = set()
admin_add_admin_sessions: Set[int] = set()
admin_remove_admin_sessions: Set[int] = set()

def build_reply_keyboard(keyboard_grid):
    if not keyboard_grid:
        return types.ReplyKeyboardRemove()
    
    rows = []
    for row in keyboard_grid:
        row_buttons = [KeyboardButton(text=b["display_text"]) for b in row]
        rows.append(row_buttons)
        
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)

def get_type_badge(msg_type: str) -> str:
    badges = {
        "confession": "💝 Признался в чувствах",
        "help": "🆘 Попросил о помощи",
        "normal": "✉️ Отправил сообщение",
        "photo": "📸 Отправил фото",
        "video": "🎬 Отправил видео",
        "voice": "🎤 Отправил голосовое",
        "video_note": "📹 Отправил видеосообщение"
    }
    return badges.get(msg_type, f"📝 {msg_type}")

def format_admin_notification(
    user_name: str, 
    username: str, 
    user_id: int, 
    msg_type: str, 
    content: str, 
    db_msg_id: Optional[int] = None, 
    is_auto_published: bool = False,
    channel_order: Optional[int] = None,
    next_channel_order: Optional[int] = None
) -> str:
    """Notification template for admins."""
    uname = f"@{username}" if username else "Нет"
    badge = get_type_badge(msg_type)
    
    display_id = None
    if channel_order:
        display_id = channel_order
    elif next_channel_order:
        display_id = next_channel_order
    elif db_msg_id:
        display_id = db_msg_id

    id_tag = f" <b>#{display_id}</b>" if display_id else ""
    if is_auto_published:
        status_line = f"📢 <b>Kanalga avtomatik joylandi (ID: #{display_id}) / Опубликовано в канал!</b>"
    elif next_channel_order:
        status_line = f"⏳ <b>Moderatsiya: Kanalga chiqarish uchun quyidagi «✅ Опубликовать (Approve)» tugmasini bosing (Kanal ID: #{display_id}).</b>"
    else:
        status_line = "⏳ <b>Moderatsiya: Kanalga chiqarish uchun quyidagi «✅ Опубликовать (Approve)» tugmasini bosing.</b>"

    return (
        f"📩 <b>Новое сообщение{id_tag}</b>\n\n"
        f"👤 <b>Имя:</b> {user_name}\n"
        f"🔗 <b>Username:</b> {uname}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
        f"📂 <b>Тип:</b> {badge}\n\n"
        f"📝 <b>Сообщение:</b>\n{content}\n\n"
        f"{status_line}"
    )

def get_message_action_markup(db_msg_id: int, user_id: int, is_auto_published: bool = False) -> InlineKeyboardMarkup:
    """Action buttons for incoming user submission."""
    if is_auto_published:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑️ Удалить из канала", callback_data=f"delete_channel_post:{db_msg_id}"),
                InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply:{user_id}:{db_msg_id}")
            ],
            [
                InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"block:{user_id}:{db_msg_id}"),
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete:{db_msg_id}")
            ]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать (Approve)", callback_data=f"publish:{db_msg_id}"),
            InlineKeyboardButton(text="❌ Отклонить (Reject)", callback_data=f"reject:{db_msg_id}")
        ],
        [
            InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"block:{user_id}:{db_msg_id}"),
            InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply:{user_id}:{db_msg_id}")
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete:{db_msg_id}")
        ]
    ])

async def get_next_channel_order() -> int:
    """Calculates next sequential channel post ID.
    Starts from 15 (or custom setting). Rejected/cancelled posts never consume an ID.
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT MAX(channel_order) FROM messages WHERE channel_order IS NOT NULL")
        row = await cur.fetchone()
        max_order = row[0] if row and row[0] is not None else None
        
        start_setting = await get_setting("channel_start_order", "15")
        try:
            start_num = int(start_setting)
        except (ValueError, TypeError):
            start_num = 15

        if max_order is None or max_order < (start_num - 1):
            return start_num
        return max_order + 1
    finally:
        await db.close()

async def publish_message_to_channel(db_msg_id: int) -> Tuple[Optional[str], int]:
    """Publishes a message from DB to the configured channel with #{channel_order} • {category}.
    channel_order is a sequential counter of published-only messages (starts at #15, reject/cancel gaps are skipped).
    Returns (channel_message_id, channel_order) on success, raises Exception on failure.
    """
    channel = await get_setting("channel_username") or "@TSUE_Anon"
    channel = channel.strip()
    if not channel.startswith("@") and not channel.startswith("-100"):
        channel = "@" + channel

    db = await get_db()
    row = None
    try:
        cur = await db.execute("""
        SELECT user_id, user_name, msg_type, content, media_url, media_type, status, created_at, channel_order, channel_message_id 
        FROM messages WHERE id = ?
        """, (db_msg_id,))
        row = await cur.fetchone()
    finally:
        await db.close()

    if not row:
        raise ValueError("Xabar topilmadi / Message not found")

    u_id, u_name, m_type, m_content, media_url, media_type, m_status, m_created_at, existing_order, existing_ch_id = row

    if m_status == "cancelled":
        raise ValueError("Xabar foydalanuvchi tomonidan bekor qilingan!")

    if m_status == "published" and existing_order:
        return str(existing_ch_id) if existing_ch_id else None, existing_order

    # Compute next sequential channel_order starting from 15 (+1 each)
    channel_order = existing_order or await get_next_channel_order()

    badge = get_type_badge(m_type)
    content_block = f"{m_content}\n\n" if m_content and m_content.strip() else ""
    channel_caption = (
        f"<b>#{channel_order}</b> • {badge}\n\n"
        f"{content_block}"
        f"<i>🤖 @TSUE_AnonBot</i>"
    )

    sent_msg = None
    sent_vn = None
    if media_type == "photo" and media_url:
        sent_msg = await bot.send_photo(channel, photo=media_url, caption=channel_caption)
    elif media_type == "video" and media_url:
        sent_msg = await bot.send_video(channel, video=media_url, caption=channel_caption)
    elif media_type == "voice" and media_url:
        sent_msg = await bot.send_voice(channel, voice=media_url, caption=channel_caption)
    elif media_type == "video_note" and media_url:
        sent_vn = await bot.send_video_note(channel, video_note=media_url)
        sent_msg = await bot.send_message(channel, channel_caption)
    else:
        sent_msg = await bot.send_message(channel, channel_caption)

    channel_msg_id = None
    if sent_vn and sent_msg:
        channel_msg_id = f"{sent_vn.message_id},{sent_msg.message_id}"
    elif sent_vn:
        channel_msg_id = str(sent_vn.message_id)
    elif sent_msg:
        channel_msg_id = str(sent_msg.message_id)

    user_notify_msg_id = None
    # Notify submitting user
    try:
        user_profile = await get_user_profile(u_id)
        u_lang = user_profile.get("language", "uz") if user_profile else "uz"
        notif = {
            "uz": f"🎉 <b>Xabaringiz (#{channel_order}) moderator tomonidan tasdiqlandi va kanalga joylandi!</b>\n\n📢 Kanal: <b>{channel}</b>",
            "ru": f"🎉 <b>Ваше сообщение (#{channel_order}) одобрено администратором и опубликовано в канале!</b>\n\n📢 Канал: <b>{channel}</b>",
            "en": f"🎉 <b>Your message (#{channel_order}) has been approved and published to the channel!</b>\n\n📢 Channel: <b>{channel}</b>"
        }
        sent_user_msg = await bot.send_message(u_id, notif.get(u_lang, notif["uz"]))
        if sent_user_msg:
            user_notify_msg_id = sent_user_msg.message_id
            user_published_notif_map[db_msg_id] = (u_id, user_notify_msg_id)
    except Exception as e:
        logger.debug(f"Could not notify user {u_id} of publication: {e}")

    # Mark as published in DB, save channel_order
    db = await get_db()
    try:
        await db.execute("""
        UPDATE messages 
        SET status = 'published', channel_message_id = ?, user_notify_message_id = ?, channel_order = ?
        WHERE id = ?
        """, (channel_msg_id, user_notify_msg_id, channel_order, db_msg_id))
        await db.commit()
    finally:
        await db.close()

    return channel_msg_id, channel_order

def get_user_cancel_markup(msg_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    """Inline button allowing user to cancel within 5 minutes."""
    labels = {
        "uz": "❌ Bekor qilish (5 daqiqa)",
        "ru": "❌ Отменить (5 минут)",
        "en": "❌ Cancel (5 minutes)"
    }
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels.get(lang, labels["uz"]), callback_data=f"user_cancel:{msg_id}")]
    ])


async def check_channel_subscription(user_id: int) -> bool:
    """Checks if user has joined the mandatory channel @TSUE_Anon. Admins are exempt."""
    if user_id in ADMIN_IDS:
        return True
    channel = await get_setting("channel_username") or "@TSUE_Anon"
    channel = channel.strip()
    if not channel.startswith("@") and not channel.startswith("-100"):
        channel = "@" + channel
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        if member.status in ["creator", "administrator", "member", "restricted"]:
            return True
        return False
    except Exception as e:
        logger.warning(f"Subscription check for user {user_id} in {channel}: {e}")
        return True

async def send_subscription_prompt(target, lang: str = "uz"):
    """Sends prompt requiring user to join the channel first."""
    channel = await get_setting("channel_username") or "@TSUE_Anon"
    ch_clean = channel.replace("@", "")
    ch_url = f"https://t.me/{ch_clean}"

    texts = {
        "uz": (
            f"⚠️ <b>Botdan to'liq foydalanish uchun rasmiy kanalimizga a'zo bo'ling!</b>\n\n"
            f"📢 Rasmiy kanal: <b>{channel}</b>\n\n"
            f"Kanalga a'zo bo'lgach, <b>«✅ A'zo bo'ldim»</b> tugmasini bosing:"
        ),
        "ru": (
            f"⚠️ <b>Для использования бота подпишитесь на наш официальный канал!</b>\n\n"
            f"📢 Официальный канал: <b>{channel}</b>\n\n"
            f"После подписки нажмите кнопку <b>«✅ Я подписался»</b>:"
        ),
        "en": (
            f"⚠️ <b>Please join our official channel to use the bot!</b>\n\n"
            f"📢 Official channel: <b>{channel}</b>\n\n"
            f"After joining, click <b>«✅ Check Subscription»</b>:"
        )
    }
    btn_join = {"uz": "📢 Kanalga a'zo bo'lish", "ru": "📢 Подписаться на канал", "en": "📢 Join Channel"}
    btn_check = {"uz": "✅ A'zo bo'ldim", "ru": "✅ Я подписался", "en": "✅ Check Subscription"}

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_join.get(lang, btn_join["uz"]), url=ch_url)],
        [InlineKeyboardButton(text=btn_check.get(lang, btn_check["uz"]), callback_data="check_sub")]
    ])
    if isinstance(target, types.Message):
        await target.answer(texts.get(lang, texts["uz"]), reply_markup=kb)
    else:
        await target.message.answer(texts.get(lang, texts["uz"]), reply_markup=kb)

def get_admin_panel_markup() -> InlineKeyboardMarkup:
    """👨‍💻 Admin panel Menu matching client specification."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Новые сообщения", callback_data="admin_inbox"),
            InlineKeyboardButton(text="📜 История", callback_data="admin_history")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton(text="🚫 Заблокированные", callback_data="admin_blocked"),
            InlineKeyboardButton(text="👨‍💼 Администраторы", callback_data="admin_admins")
        ],
        [
            InlineKeyboardButton(text="📢 Канал", callback_data="admin_channel"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")
        ]
    ])

def get_back_to_admin_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
    ])

async def notify_admins(
    db_msg_id: int, 
    user_id: int, 
    user_name: str, 
    username: str, 
    msg_type: str, 
    content: str, 
    media_file_id: Optional[str] = None, 
    media_type: str = "text",
    is_auto_published: bool = False,
    channel_order: Optional[int] = None
):
    """Sends immediate Telegram notifications to all ADMIN_IDS following the requested template."""
    next_order = None
    if not is_auto_published:
        next_order = await get_next_channel_order()

    admin_text = format_admin_notification(
        user_name=user_name,
        username=username,
        user_id=user_id,
        msg_type=msg_type,
        content=content,
        db_msg_id=db_msg_id,
        is_auto_published=is_auto_published,
        channel_order=channel_order,
        next_channel_order=next_order
    )
    markup = get_message_action_markup(db_msg_id, user_id, is_auto_published=is_auto_published)


    for admin_id in ADMIN_IDS:
        try:
            sent_msg = None
            if media_type == "photo" and media_file_id:
                sent_msg = await bot.send_photo(admin_id, photo=media_file_id, caption=admin_text, reply_markup=markup)
            elif media_type == "video" and media_file_id:
                sent_msg = await bot.send_video(admin_id, video=media_file_id, caption=admin_text, reply_markup=markup)
            elif media_type == "voice" and media_file_id:
                sent_msg = await bot.send_voice(admin_id, voice=media_file_id, caption=admin_text, reply_markup=markup)
            elif media_type == "video_note" and media_file_id:
                await bot.send_video_note(admin_id, video_note=media_file_id)
                sent_msg = await bot.send_message(admin_id, admin_text, reply_markup=markup)
            else:
                sent_msg = await bot.send_message(admin_id, admin_text, reply_markup=markup)

            if sent_msg:
                admin_notification_map[sent_msg.message_id] = {
                    "user_id": user_id,
                    "db_msg_id": db_msg_id,
                    "user_name": user_name
                }
                if db_msg_id not in admin_msg_tracker:
                    admin_msg_tracker[db_msg_id] = []
                admin_msg_tracker[db_msg_id].append((admin_id, sent_msg.message_id))
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# ====================================================
# 👨‍💻 ADMIN PANEL HANDLERS (ALL 8 SECTIONS IN TELEGRAM)
# ====================================================

@dp.message(F.text.in_(["👨‍💻 Admin panel", "👨‍💻 Админ панель", "⚙️ Админ панель", "/admin"]))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ Sizda administrator huquqlari yo'q.")
        return

    admin_text = (
        "👨‍💻 <b>Admin panel</b>\n\n"
        "<i>Kerakli bo'limni tanlang / Выберите нужный раздел:</i>"
    )
    await message.answer(admin_text, reply_markup=get_admin_panel_markup())

@dp.callback_query(F.data == "admin_menu")
async def cb_admin_menu(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_text = (
        "👨‍💻 <b>Admin panel</b>\n\n"
        "<i>Kerakli bo'limni tanlang / Выберите нужный раздел:</i>"
    )
    try:
        await query.message.edit_text(admin_text, reply_markup=get_admin_panel_markup())
    except Exception:
        await query.message.answer(admin_text, reply_markup=get_admin_panel_markup())
    await query.answer()

# ====================================================
# 🌐 LANGUAGE SELECTION & USER CANCEL CALLBACKS
# ====================================================

@dp.callback_query(F.data.startswith("set_lang:"))
async def cb_set_language(query: CallbackQuery):
    new_lang = query.data.split(":")[1]
    user_id = query.from_user.id
    await update_user_language(user_id, new_lang)
    
    texts = {
        "uz": "✅ Bot tili muvaffaqiyatli tanlandi: 🇺🇿 O'zbekcha",
        "ru": "✅ Язык бота успешно выбран: 🇷🇺 Русский",
        "en": "✅ Bot language successfully selected: 🇬🇧 English"
    }
    ans_text = texts.get(new_lang, texts["uz"])
    await query.answer(ans_text)
    
    try:
        await query.message.delete()
    except Exception:
        pass

    # TIL TANLANGANDAN KEYIN kanalga a'zo bo'lishni TANLANGAN YANGI TILDA talab qilamiz!
    if not await check_channel_subscription(user_id):
        await send_subscription_prompt(query.message, new_lang)
        return

    buttons = await get_menu_buttons("main_menu", lang=new_lang, user_id=user_id)
    markup = build_reply_keyboard(format_keyboard_grid(buttons))
    welcome_text = MESSAGES["welcome"].get(new_lang, MESSAGES["welcome"]["uz"])
    await query.message.answer(f"{ans_text}\n\n{welcome_text}", reply_markup=markup)

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(query: CallbackQuery):
    user_id = query.from_user.id
    user = await get_user_profile(user_id)
    lang = user.get("language", "uz")
    is_sub = await check_channel_subscription(user_id)
    if is_sub:
        ack_msgs = {
            "uz": "✅ Rahmat! Obuna tasdiqlandi.",
            "ru": "✅ Спасибо! Подписка подтверждена.",
            "en": "✅ Thank you! Subscription confirmed."
        }
        await query.answer(ack_msgs.get(lang, ack_msgs["uz"]))
        try:
            await query.message.delete()
        except Exception:
            pass
        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        markup = build_reply_keyboard(format_keyboard_grid(buttons))
        welcome_text = MESSAGES["welcome"].get(lang, MESSAGES["welcome"]["uz"])
        await query.message.answer(f"{ack_msgs.get(lang, ack_msgs['uz'])}\n\n{welcome_text}", reply_markup=markup)
    else:
        err_msgs = {
            "uz": "❌ Siz hali kanalga a'zo bo'lmadingiz! Iltimos, avval kanalga a'zo bo'ling.",
            "ru": "❌ Вы еще не подписались на канал! Пожалуйста, сначала подпишитесь на канал.",
            "en": "❌ You have not joined the channel yet! Please subscribe to the channel first."
        }
        await query.answer(err_msgs.get(lang, err_msgs["uz"]), show_alert=True)

@dp.callback_query(F.data.startswith("user_cancel:"))
async def cb_user_cancel(query: CallbackQuery):
    msg_id = int(query.data.split(":")[1])
    user_id = query.from_user.id
    user = await get_user_profile(user_id)
    lang = user.get("language", "uz")

    db = await get_db()
    row = None
    try:
        cur = await db.execute("""
        SELECT user_id, status, created_at, channel_message_id, user_notify_message_id 
        FROM messages WHERE id = ?
        """, (msg_id,))
        row = await cur.fetchone()
    finally:
        await db.close()

    if not row:
        not_found = {
            "uz": "Xabar topilmadi!",
            "ru": "Сообщение не найдено!",
            "en": "Message not found!"
        }
        await query.answer(not_found.get(lang, not_found["uz"]), show_alert=True)
        return

    m_uid, m_status, m_created_at, m_ch_id = row[0], row[1], row[2], row[3]
    u_notif_id = row[4] if len(row) > 4 else None

    if m_uid != user_id:
        not_yours = {
            "uz": "Bu sizning xabaringiz emas!",
            "ru": "Это не ваше сообщение!",
            "en": "This is not your message!"
        }
        await query.answer(not_yours.get(lang, not_yours["uz"]), show_alert=True)
        return

    if m_status == "cancelled":
        already_canc = {
            "uz": "Xabaringiz allaqachon bekor qilingan.",
            "ru": "Ваше сообщение уже отменено.",
            "en": "Your message is already cancelled."
        }
        await query.answer(already_canc.get(lang, already_canc["uz"]), show_alert=True)
        return

    try:
        msg_dt = datetime.strptime(m_created_at, "%Y-%m-%d %H:%M:%S")
        elapsed = (datetime.now() - msg_dt).total_seconds()
    except Exception:
        elapsed = 0

    if elapsed > 300:
        expired = {
            "uz": "⚠️ 5 daqiqalik bekor qilish muddati tugagan!",
            "ru": "⚠️ 5 минут истекли! Сообщение нельзя отменить.",
            "en": "⚠️ 5-minute cancellation window has expired! The message cannot be cancelled."
        }
        await query.answer(expired.get(lang, expired["uz"]), show_alert=True)
        return

    # If it was published to channel, delete it from channel
    if m_ch_id:
        try:
            channel = await get_setting("channel_username") or "@TSUE_Anon"
            ch_clean = channel.strip()
            if not ch_clean.startswith("@") and not ch_clean.startswith("-100"):
                ch_clean = "@" + ch_clean
            msg_ids = str(m_ch_id).split(",")
            for m_id in msg_ids:
                if m_id.strip():
                    try:
                        await bot.delete_message(chat_id=ch_clean, message_id=int(m_id.strip()))
                    except Exception as e:
                        logger.warning(f"Error deleting cancelled message {m_id} from channel: {e}")
        except Exception as e:
            logger.warning(f"Error deleting cancelled message {m_ch_id} from channel: {e}")

    # Also delete the user publish notification (🎉 Your message has been approved...) from user's chat!
    if not u_notif_id and msg_id in user_published_notif_map:
        _, u_notif_id = user_published_notif_map[msg_id]

    if u_notif_id:
        try:
            await bot.delete_message(chat_id=user_id, message_id=u_notif_id)
        except Exception as e:
            logger.debug(f"Error deleting user publish notification {u_notif_id}: {e}")

    # Mark as cancelled
    db = await get_db()
    try:
        await db.execute("UPDATE messages SET status = 'cancelled' WHERE id = ?", (msg_id,))
        await db.commit()
    finally:
        await db.close()

    # Disable admin buttons and notify admins that user cancelled
    if msg_id in admin_msg_tracker:
        for a_id, a_msg_id in admin_msg_tracker[msg_id]:
            try:
                await bot.edit_message_reply_markup(chat_id=a_id, message_id=a_msg_id, reply_markup=None)
            except Exception:
                pass
            try:
                await bot.send_message(
                    a_id, 
                    f"⚠️ <b>#{msg_id}-sonli xabar foydalanuvchi tomonidan bekor qilindi (Kanalga chiqarilmaydi / Kanaldan o'chirildi).</b>"
                )
            except Exception:
                pass

    txt = {
        "uz": "❌ <b>Xabaringiz bekor qilindi!</b>\n\nU kanaldan o'chirildi (yoki chiqarilmaydi).",
        "ru": "❌ <b>Ваше сообщение отменено!</b>\n\nОно удалено из канала (или не будет опубликовано).",
        "en": "❌ <b>Your message has been cancelled!</b>\n\nIt has been removed from the channel (or will not be published)."
    }
    cancel_ack = {
        "uz": "❌ Bekor qilindi!",
        "ru": "❌ Отменено!",
        "en": "❌ Cancelled!"
    }
    user_cancel_text = txt.get(lang, txt["uz"])
    try:
        await query.message.edit_text(user_cancel_text)
    except Exception:
        await query.message.answer(user_cancel_text)
    await query.answer(cancel_ack.get(lang, cancel_ack["uz"]))

@dp.callback_query(F.data == "admin_menu")
async def cb_admin_menu(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_text = (
        "👨‍💻 <b>Admin panel</b>\n\n"
        "<i>Kerakli bo'limni tanlang / Выберите нужный раздел:</i>"
    )
    try:
        await query.message.edit_text(admin_text, reply_markup=get_admin_panel_markup())
    except Exception:
        await query.message.answer(admin_text, reply_markup=get_admin_panel_markup())
    await query.answer()

# 1. 📥 Новые сообщения
@dp.callback_query(F.data == "admin_inbox")
async def cb_admin_inbox(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    db = await get_db()
    try:
        cur = await db.execute("""
        SELECT id, user_id, user_name, msg_type, content, media_url, media_type, created_at 
        FROM messages 
        WHERE status = 'new' 
        ORDER BY id ASC LIMIT 5
        """)
        rows = await cur.fetchall()
    finally:
        await db.close()

    if not rows:
        await query.message.edit_text("🎉 <b>Новых сообщений нет!</b>", reply_markup=get_back_to_admin_markup())
        await query.answer()
        return

    await query.answer()
    await query.message.answer(f"📥 <b>Новые сообщения (показано {len(rows)} шт.):</b>")

    for r in rows:
        m_id, u_id, u_name, m_type, m_content, m_url, m_media_type, m_time = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
        card_text = format_admin_notification(
            user_name=u_name,
            username="",
            user_id=u_id,
            msg_type=m_type,
            content=m_content,
            db_msg_id=m_id,
            is_auto_published=False
        )
        markup = get_message_action_markup(m_id, u_id, is_auto_published=False)
        
        if m_media_type == "photo" and m_url:
            await query.message.answer_photo(photo=m_url, caption=card_text, reply_markup=markup)
        elif m_media_type == "video" and m_url:
            await query.message.answer_video(video=m_url, caption=card_text, reply_markup=markup)
        elif m_media_type == "voice" and m_url:
            await query.message.answer_voice(voice=m_url, caption=card_text, reply_markup=markup)
        elif m_media_type == "video_note" and m_url:
            await query.message.answer_video_note(video_note=m_url)
            await query.message.answer(card_text, reply_markup=markup)
        else:
            await query.message.answer(card_text, reply_markup=markup)

    await query.message.answer("<i>Действия с сообщениями выше:</i>", reply_markup=get_back_to_admin_markup())

# 2. 📜 История
@dp.callback_query(F.data == "admin_history")
async def cb_admin_history(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    db = await get_db()
    try:
        cur = await db.execute("""
        SELECT id, user_id, user_name, msg_type, content, status, created_at 
        FROM messages 
        WHERE status IN ('published', 'rejected', 'replied') 
        ORDER BY id DESC LIMIT 5
        """)
        rows = await cur.fetchall()
    finally:
        await db.close()

    if not rows:
        await query.message.edit_text("📜 <b>История обработанных сообщений пуста.</b>", reply_markup=get_back_to_admin_markup())
        await query.answer()
        return

    status_labels = {
        "published": "🟢 Опубликовано",
        "rejected": "🔴 Отклонено",
        "replied": "💬 Отвечено"
    }

    lines = ["📜 <b>История обработанных сообщений:</b>\n"]
    buttons = []
    for idx, r in enumerate(rows, 1):
        m_id, u_id, u_name, m_type, m_content, m_st, m_time = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        st_text = status_labels.get(m_st, m_st)
        lines.append(
            f"<b>#{m_id}</b> | <b>{u_name}</b> (ID: <code>{u_id}</code>)\n"
            f"📂 {get_type_badge(m_type)} | {st_text} | 🕒 {m_time}\n"
            f"💬 <i>\"{m_content[:80]}\"</i>\n"
        )
        buttons.append([
            InlineKeyboardButton(text=f"🔄 Повторить #{m_id}", callback_data=f"retry:{m_id}"),
            InlineKeyboardButton(text=f"🗑️ Удалить #{m_id}", callback_data=f"delete:{m_id}")
        ])

    buttons.append([InlineKeyboardButton(text="🔎 Поиск в истории", callback_data="admin_search_history_prompt")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await query.message.edit_text("\n".join(lines), reply_markup=markup)
    except Exception:
        await query.message.answer("\n".join(lines), reply_markup=markup)
    await query.answer()

# 3. 📊 Статистика
@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM messages WHERE status = 'new'")
        new_messages = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM messages WHERE status = 'published'")
        published_messages = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM messages WHERE status = 'rejected'")
        rejected_messages = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM messages WHERE status = 'replied'")
        replied_messages = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM users WHERE account_status = 'Blocked'")
        blocked_users = (await cur.fetchone())[0]
    finally:
        await db.close()

    stats_text = (
        "📊 <b>Статистика бота:</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"📥 Новых сообщений: <b>{new_messages}</b>\n"
        f"✅ Опубликовано: <b>{published_messages}</b>\n"
        f"❌ Отклонено: <b>{rejected_messages}</b>\n"
        f"💬 Отвечено: <b>{replied_messages}</b>\n"
        f"🚫 Заблокировано: <b>{blocked_users}</b>\n"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
    ])

    try:
        await query.message.edit_text(stats_text, reply_markup=markup)
    except Exception:
        await query.message.answer(stats_text, reply_markup=markup)
    await query.answer()

# 4. 👥 Пользователи
@dp.callback_query(F.data == "admin_users")
async def cb_admin_users(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    db = await get_db()
    try:
        cur = await db.execute("""
        SELECT telegram_id, full_name, username, role, account_status, created_at 
        FROM users 
        ORDER BY created_at DESC LIMIT 8
        """)
        users = await cur.fetchall()
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_count = (await cur.fetchone())[0]
    finally:
        await db.close()

    lines = [f"👥 <b>Пользователи бота (Всего: {total_count} чел.):</b>\n"]
    for idx, u in enumerate(users, 1):
        uname = f"@{u['username']}" if u['username'] else "нет username"
        status_icon = "🟢" if u['account_status'] != 'Blocked' else "🔴 [Блок]"
        lines.append(f"{idx}. {status_icon} <b>{u['full_name']}</b> ({uname})\n   🆔 <code>{u['telegram_id']}</code> | {u['role']}")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔎 Поиск пользователя", callback_data="admin_search_users_prompt"),
            InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast_prompt")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
    ])

    try:
        await query.message.edit_text("\n".join(lines), reply_markup=markup)
    except Exception:
        await query.message.answer("\n".join(lines), reply_markup=markup)
    await query.answer()

# 5. 🚫 Заблокированные
@dp.callback_query(F.data == "admin_blocked")
async def cb_admin_blocked(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    blocked = await get_blocked_users()
    if not blocked:
        try:
            await query.message.edit_text("🎉 <b>Заблокированных пользователей нет.</b>", reply_markup=get_back_to_admin_markup())
        except Exception:
            await query.message.answer("🎉 <b>Заблокированных пользователей нет.</b>", reply_markup=get_back_to_admin_markup())
        await query.answer()
        return

    buttons = []
    lines = ["🚫 <b>Список заблокированных пользователей:</b>\n"]
    for u in blocked:
        uname = f"@{u['username']}" if u['username'] else "без username"
        lines.append(f"• <b>{u['full_name']}</b> ({uname}) — ID: <code>{u['telegram_id']}</code>")
        buttons.append([InlineKeyboardButton(text=f"🔓 Разблокировать {u['full_name'][:15]}", callback_data=f"unblock:{u['telegram_id']}")])

    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await query.message.edit_text("\n".join(lines), reply_markup=markup)
    except Exception:
        await query.message.answer("\n".join(lines), reply_markup=markup)
    await query.answer()

# 6. 👨‍💼 Администраторы
@dp.callback_query(F.data == "admin_admins")
async def cb_admin_admins(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    db = await get_db()
    try:
        cur = await db.execute("SELECT telegram_id, full_name, username FROM users WHERE role = 'admin'")
        admins = await cur.fetchall()
        existing_ids = {a['telegram_id'] for a in admins}
        for aid in ADMIN_IDS:
            if aid not in existing_ids:
                admins.append({"telegram_id": aid, "full_name": f"Admin {aid}", "username": ""})
    finally:
        await db.close()

    lines = ["👨‍💼 <b>Список администраторов бота / Bot administratorlari:</b>\n"]
    admin_buttons = []
    
    for idx, a in enumerate(admins, 1):
        uname = f"@{a['username']}" if a['username'] else "yo'q"
        name = a['full_name'] or f"Admin {a['telegram_id']}"
        lines.append(f"{idx}. 👑 <b>{name}</b> ({uname}) — ID: <code>{a['telegram_id']}</code>")
        if a['telegram_id'] != admin_id:
            admin_buttons.append([
                InlineKeyboardButton(
                    text=f"🗑️ O'chirish (ID: {a['telegram_id']})", 
                    callback_data=f"admin_remove_direct:{a['telegram_id']}"
                )
            ])

    action_buttons = [
        [
            InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="admin_add_admin_prompt"),
            InlineKeyboardButton(text="➖ ID bo'yicha o'chirish", callback_data="admin_remove_admin_prompt")
        ]
    ]
    action_buttons.extend(admin_buttons)
    action_buttons.append([InlineKeyboardButton(text="🔙 Orqaga / Назад", callback_data="admin_menu")])

    markup = InlineKeyboardMarkup(inline_keyboard=action_buttons)

    try:
        await query.message.edit_text("\n".join(lines), reply_markup=markup)
    except Exception:
        await query.message.answer("\n".join(lines), reply_markup=markup)
    await query.answer()

# 7. 📢 Канал
@dp.callback_query(F.data == "admin_channel")
async def cb_admin_channel(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    channel = await get_setting("channel_username") or "@TSUE_Anon"
    channel_str = f"<code>{channel}</code>" if channel else "<i>Не настроен</i>"
    is_auto = (await get_setting("auto_post_channel", "0")) == "1"
    auto_status_str = "ВКЛ ✅ (Darhol kanalga tashlanadi)" if is_auto else "ВЫКЛ ❌ (Moderatsiya - Admin tasdig'i bilan)"
    auto_btn_text = "🔄 Авто-постинг: ВКЛ ✅" if is_auto else "🔄 Авто-постинг: ВЫКЛ ❌ (Moderatsiya)"

    ch_text = (
        "📢 <b>Настройка канала для публикаций</b>\n\n"
        f"Текущий канал: <b>{channel_str}</b>\n"
        f"Авто-постинг: <b>{auto_status_str}</b>\n\n"
        "<i>Чтобы бот публиковал сообщения в канал:</i>\n"
        "1. Добавьте бота @TSUE_AnonBot в администраторы канала с правом публикации сообщений.\n"
        "2. Нажмите кнопку ниже и отправьте юзернейм канала (например: <code>@my_channel</code>).\n"
        "3. Переключайте режим: <b>ВКЛ</b> — посты сразу публикуются с #ID, <b>ВЫКЛ</b> — только после одобрения админом (Approve)."
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=auto_btn_text, callback_data="admin_toggle_autopost")],
        [InlineKeyboardButton(text="✏️ Указать / Изменить канал", callback_data="admin_set_channel_prompt")],
        [InlineKeyboardButton(text="📢 Отправить тестовый пост", callback_data="admin_test_channel")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
    ])

    try:
        await query.message.edit_text(ch_text, reply_markup=markup)
    except Exception:
        await query.message.answer(ch_text, reply_markup=markup)
    await query.answer()

@dp.callback_query(F.data == "admin_toggle_autopost")
async def cb_toggle_autopost(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    current = await get_setting("auto_post_channel", "0")
    new_val = "0" if current == "1" else "1"
    await set_setting("auto_post_channel", new_val)
    
    alert_text = (
        "✅ Авто-постинг ВКЛЮЧЕН!\nТеперь все сообщения сразу публикуются в канал с #ID."
        if new_val == "1" else
        "❌ Авто-постинг ВЫКЛЮЧЕН (Moderatsiya rejimi)!\nСообщения будут отправляться админам на модерацию (кнопка «Опубликовать (Approve)»)."
    )
    await query.answer(alert_text, show_alert=True)
    await cb_admin_channel(query)

# 8. ⚙️ Настройки
@dp.callback_query(F.data == "admin_settings")
async def cb_admin_settings(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    channel = await get_setting("channel_username")
    channel_str = f"<code>{channel}</code>" if channel else "<i>Не настроен</i>"
    is_auto = (await get_setting("auto_post_channel", "0")) == "1"
    auto_status = "ВКЛ ✅" if is_auto else "ВЫКЛ ❌ (Moderatsiya)"

    settings_text = (
        "⚙️ <b>Настройки бота:</b>\n\n"
        f"📢 Канал публикаций: {channel_str}\n"
        f"🔄 Авто-постинг в канал: <b>{auto_status}</b>\n"
        f"👑 Администраторов: <b>{len(ADMIN_IDS)} чел.</b>\n"
        f"🤖 Имя бота: <b>@TSUE_AnonBot</b>\n"
        f"🌐 Режим: <b>Web Service (Render.com + Polling)</b>\n"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Настроить канал", callback_data="admin_channel")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton(text="🌐 Сменить язык / Tilni tanlash", callback_data="admin_change_lang")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
    ])

    try:
        await query.message.edit_text(settings_text, reply_markup=markup)
    except Exception:
        await query.message.answer(settings_text, reply_markup=markup)
    await query.answer()

@dp.callback_query(F.data == "admin_change_lang")
async def cb_admin_change_lang(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="set_lang:uz")],
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_settings")]
    ])
    try:
        await query.message.edit_text("🌐 <b>Выберите язык / Tilni tanlang:</b>", reply_markup=kb)
    except Exception:
        await query.message.answer("🌐 <b>Выберите язык / Tilni tanlang:</b>", reply_markup=kb)
    await query.answer()

# ====================================================
# ⚡ ADMIN ACTIONS: PUBLISH, REJECT, BLOCK, UNBLOCK, DELETE, RETRY
# ====================================================

@dp.callback_query(F.data.startswith("publish:"))
async def cb_publish(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    db_msg_id = int(query.data.split(":")[1])

    # 1. Check if message was cancelled by user
    db = await get_db()
    row = None
    try:
        cur = await db.execute("SELECT status FROM messages WHERE id = ?", (db_msg_id,))
        row = await cur.fetchone()
    finally:
        await db.close()

    if row and row[0] == "cancelled":
        cancel_tag = "\n\n❌ <b>Bu xabar foydalanuvchi tomonidan bekor qilingan! Kanalga chiqarilmadi.</b>"
        try:
            if query.message.text:
                await query.message.edit_text(query.message.text + cancel_tag, reply_markup=None)
            elif query.message.caption:
                await query.message.edit_caption(caption=query.message.caption + cancel_tag, reply_markup=None)
        except Exception:
            pass
        await query.answer("❌ Bu xabar foydalanuvchi tomonidan bekor qilingan!", show_alert=True)
        return

    try:
        channel_msg_id, channel_order = await publish_message_to_channel(db_msg_id)
        channel = await get_setting("channel_username") or "@TSUE_Anon"
        success_tag = f"\n\n✅ <b>Опубликовано в канале {channel} (ID: #{channel_order})!</b>"
        post_markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑️ Удалить из канала", callback_data=f"delete_channel_post:{db_msg_id}"),
            ]
        ])
        try:
            if query.message.text:
                await query.message.edit_text(query.message.text + success_tag, reply_markup=post_markup)
            elif query.message.caption:
                await query.message.edit_caption(caption=query.message.caption + success_tag, reply_markup=post_markup)
        except Exception:
            pass
        await query.answer(f"✅ Успешно опубликовано в канал (#{channel_order})!")
    except Exception as e:
        logger.error(f"Failed to publish #{db_msg_id}: {e}")
        await query.answer(f"⚠️ Ошибка публикации: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("delete_channel_post:"))
async def cb_delete_channel_post(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    db_msg_id = int(query.data.split(":")[1])
    db = await get_db()
    row = None
    try:
        cur = await db.execute("SELECT channel_message_id, user_id, user_notify_message_id FROM messages WHERE id = ?", (db_msg_id,))
        row = await cur.fetchone()
    finally:
        await db.close()

    if row:
        ch_msg_id, u_id, u_notif_id = row[0], row[1], row[2]
        if ch_msg_id:
            channel = await get_setting("channel_username") or "@TSUE_Anon"
            ch_clean = channel.strip()
            if not ch_clean.startswith("@") and not ch_clean.startswith("-100"):
                ch_clean = "@" + ch_clean
            msg_ids = str(ch_msg_id).split(",")
            for m_id in msg_ids:
                if m_id.strip():
                    try:
                        await bot.delete_message(chat_id=ch_clean, message_id=int(m_id.strip()))
                    except Exception as e:
                        logger.warning(f"Error deleting post #{db_msg_id} (msg {m_id}) from channel: {e}")

        # Also delete the user publish notification if present
        if not u_notif_id and db_msg_id in user_published_notif_map:
            _, u_notif_id = user_published_notif_map[db_msg_id]
        if u_notif_id and u_id:
            try:
                await bot.delete_message(chat_id=u_id, message_id=u_notif_id)
            except Exception as e:
                logger.debug(f"Error deleting user notification {u_notif_id}: {e}")

    db = await get_db()
    try:
        await db.execute("UPDATE messages SET status = 'deleted' WHERE id = ?", (db_msg_id,))
        await db.commit()
    finally:
        await db.close()

    try:
        if query.message.text:
            await query.message.edit_text(query.message.text + "\n\n🗑️ <b>Пост удален из канала и базы.</b>")
        elif query.message.caption:
            await query.message.edit_caption(caption=query.message.caption + "\n\n🗑️ <b>Пост удален из канала и базы.</b>")
    except Exception:
        pass
    await query.answer("🗑️ Пост удален из канала!")


@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    db_msg_id = int(query.data.split(":")[1])
    db = await get_db()
    u_id = None
    try:
        cur = await db.execute("SELECT user_id FROM messages WHERE id = ?", (db_msg_id,))
        row = await cur.fetchone()
        if row:
            u_id = row[0]
        await db.execute("UPDATE messages SET status = 'rejected' WHERE id = ?", (db_msg_id,))
        await db.commit()
    finally:
        await db.close()

    reject_tag = "\n\n❌ <b>Отклонено администратором (Kanalga chiqarilmadi).</b>"
    try:
        if query.message.text:
            await query.message.edit_text(query.message.text + reject_tag)
        elif query.message.caption:
            await query.message.edit_caption(caption=query.message.caption + reject_tag)
    except Exception:
        pass

    if u_id:
        try:
            user_profile = await get_user_profile(u_id)
            u_lang = user_profile.get("language", "uz") if user_profile else "uz"
            notif = {
                "uz": "ℹ️ <b>Xabaringiz moderator tomonidan rad etildi (kanalga chiqarilmadi).</b>",
                "ru": "ℹ️ <b>Ваше сообщение было отклонено модератором (не опубликовано в канале).</b>",
                "en": "ℹ️ <b>Your message was rejected by the moderator (not published).</b>"
            }
            await bot.send_message(u_id, notif.get(u_lang, notif["uz"]))
        except Exception as e:
            logger.debug(f"Could not notify user {u_id} of rejection: {e}")

    await query.answer("❌ Сообщение отклонено.")

@dp.callback_query(F.data.startswith("block:"))
async def cb_block(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = query.data.split(":")
    target_user_id = int(parts[1])
    db_msg_id = int(parts[2])

    await block_user(target_user_id)
    db = await get_db()
    try:
        await db.execute("UPDATE messages SET status = 'rejected' WHERE id = ?", (db_msg_id,))
        await db.commit()
    finally:
        await db.close()

    block_tag = "\n\n🚫 <b>Пользователь заблокирован!</b>"
    try:
        if query.message.text:
            await query.message.edit_text(query.message.text + block_tag)
        elif query.message.caption:
            await query.message.edit_caption(caption=query.message.caption + block_tag)
    except Exception:
        pass

    await query.answer("🚫 Пользователь заблокирован.", show_alert=True)

@dp.callback_query(F.data.startswith("unblock:"))
async def cb_unblock(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    target_id = int(query.data.split(":")[1])
    await unblock_user(target_id)
    await query.answer("🔓 Пользователь разблокирован.", show_alert=True)
    await cb_admin_blocked(query)

@dp.callback_query(F.data.startswith("delete:"))
async def cb_delete(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    db_msg_id = int(query.data.split(":")[1])
    db = await get_db()
    try:
        await db.execute("UPDATE messages SET status = 'deleted' WHERE id = ?", (db_msg_id,))
        await db.commit()
    finally:
        await db.close()

    del_tag = "\n\n🗑️ <b>Удалено.</b>"
    try:
        if query.message.text:
            await query.message.edit_text(query.message.text + del_tag)
        elif query.message.caption:
            await query.message.edit_caption(caption=query.message.caption + del_tag)
    except Exception:
        pass

    await query.answer("🗑️ Сообщение удалено.")

@dp.callback_query(F.data.startswith("retry:"))
async def cb_retry(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    db_msg_id = int(query.data.split(":")[1])
    db = await get_db()
    row = None
    try:
        await db.execute("UPDATE messages SET status = 'new' WHERE id = ?", (db_msg_id,))
        await db.commit()
        cur = await db.execute("SELECT user_id, user_name, msg_type, content, media_url, media_type FROM messages WHERE id = ?", (db_msg_id,))
        row = await cur.fetchone()
    finally:
        await db.close()

    if not row:
        await query.answer("Сообщение не найдено!", show_alert=True)
        return

    u_id, u_name, m_type, m_content, media_url, media_type = row
    card_text = format_admin_notification(
        user_name=u_name,
        username="",
        user_id=u_id,
        msg_type=m_type,
        content=m_content
    )
    markup = get_message_action_markup(db_msg_id, u_id)
    await query.message.answer(card_text, reply_markup=markup)
    await query.answer("🔄 Отправлено на повторную обработку!")

@dp.callback_query(F.data.startswith("reply:"))
async def cb_admin_reply(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    parts = query.data.split(":")
    target_user_id = int(parts[1])
    db_msg_id = int(parts[2])

    admin_active_reply[admin_id] = {
        "target_user_id": target_user_id,
        "db_msg_id": db_msg_id,
        "user_name": f"ID {target_user_id}"
    }

    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin_action")]
    ])

    await query.message.answer(
        f"✍️ <b>Отправьте ответ для пользователя (ID: <code>{target_user_id}</code>):</b>\n\n"
        f"<i>(Вы можете отправить текст, фото или голосовое сообщение)</i>",
        reply_markup=cancel_markup
    )
    await query.answer()

@dp.callback_query(F.data == "cancel_admin_action")
async def cb_cancel_admin_action(query: CallbackQuery):
    admin_id = query.from_user.id
    admin_active_reply.pop(admin_id, None)
    admin_broadcast_sessions.discard(admin_id)
    admin_channel_sessions.discard(admin_id)
    admin_search_user_sessions.discard(admin_id)
    admin_search_history_sessions.discard(admin_id)
    admin_add_admin_sessions.discard(admin_id)
    admin_remove_admin_sessions.discard(admin_id)
    try:
        await query.message.edit_text("❌ Действие отменено.", reply_markup=get_back_to_admin_markup())
    except Exception:
        await query.message.answer("❌ Действие отменено.", reply_markup=get_back_to_admin_markup())
    await query.answer()

@dp.callback_query(F.data == "admin_search_users_prompt")
async def cb_search_users_prompt(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_search_user_sessions.add(admin_id)
    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin_action")]
    ])
    await query.message.answer(
        "🔎 <b>Введите ID или Username пользователя для поиска:</b>",
        reply_markup=cancel_markup
    )
    await query.answer()

@dp.callback_query(F.data == "admin_search_history_prompt")
async def cb_search_history_prompt(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_search_history_sessions.add(admin_id)
    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin_action")]
    ])
    await query.message.answer(
        "🔎 <b>Введите ключевое слово или ID пользователя для поиска в истории:</b>",
        reply_markup=cancel_markup
    )
    await query.answer()

@dp.callback_query(F.data == "admin_add_admin_prompt")
async def cb_add_admin_prompt(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_add_admin_sessions.add(admin_id)
    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin_action")]
    ])
    await query.message.answer(
        "➕ <b>Введите Telegram ID нового администратора:</b>",
        reply_markup=cancel_markup
    )
    await query.answer()

@dp.callback_query(F.data == "admin_remove_admin_prompt")
async def cb_remove_admin_prompt(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_remove_admin_sessions.add(admin_id)
    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена / Bekor qilish", callback_data="cancel_admin_action")]
    ])
    await query.message.answer(
        "➖ <b>O'chirmoqchi bo'lgan administratorning Telegram ID raqamini kiriting:</b>\n\n"
        "<i>(Masalan: <code>123456789</code>)</i>",
        reply_markup=cancel_markup
    )
    await query.answer()

@dp.callback_query(F.data.startswith("admin_remove_direct:"))
async def cb_remove_admin_direct(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    try:
        target_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("Xatolik!", show_alert=True)
        return

    if target_id == admin_id:
        await query.answer("⚠️ O'zingizni administratorlikdan o'chira olmaysiz!", show_alert=True)
        return

    if target_id in ADMIN_IDS:
        ADMIN_IDS.remove(target_id)

    db = await get_db()
    try:
        await db.execute("UPDATE users SET role = 'student' WHERE telegram_id = ?", (target_id,))
        await db.commit()
    finally:
        await db.close()

    await query.answer(f"✅ Administrator (ID: {target_id}) muvaffaqiyatli o'chirildi!", show_alert=True)
    await cb_admin_admins(query)

@dp.callback_query(F.data == "admin_set_channel_prompt")
async def cb_set_channel_prompt(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    admin_channel_sessions.add(admin_id)
    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin_action")]
    ])
    await query.message.answer(
        "✍️ <b>Отправьте юзернейм канала (например: <code>@my_channel</code>):</b>\n\n"
        "<i>(Убедитесь, что бот назначен администратором в этом канале)</i>",
        reply_markup=cancel_markup
    )
    await query.answer()

@dp.callback_query(F.data == "admin_test_channel")
async def cb_test_channel(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    channel = await get_setting("channel_username")
    if not channel:
        await query.answer("⚠️ Канал еще не настроен!", show_alert=True)
        return

    try:
        await bot.send_message(channel, "🎉 <b>Тестовое сообщение</b>\n\nКанал успешно подключен к боту @TSUE_AnonBot!")
        await query.answer(f"✅ Тестовый пост отправлен в {channel}!", show_alert=True)
    except Exception as e:
        await query.answer(f"⚠️ Ошибка отправки в {channel}: {e}", show_alert=True)

@dp.callback_query(F.data == "admin_broadcast_prompt")
async def cb_broadcast_prompt(query: CallbackQuery):
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    admin_broadcast_sessions.add(admin_id)
    cancel_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin_action")]
    ])
    await query.message.answer(
        "📢 <b>Отправьте текст объявления для всех пользователей:</b>",
        reply_markup=cancel_markup
    )
    await query.answer()

# ----------------------------------------------------
# NATIVE TELEGRAM QUOTE / SWIPE REPLY HANDLER
# ----------------------------------------------------
@dp.message(F.reply_to_message)
async def handle_admin_quote_reply(message: types.Message):
    admin_id = message.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    replied = message.reply_to_message
    target_user_id = None
    db_msg_id = None
    user_name = "Foydalanuvchi"

    if replied.message_id in admin_notification_map:
        info = admin_notification_map[replied.message_id]
        target_user_id = info.get("user_id")
        db_msg_id = info.get("db_msg_id")
        user_name = info.get("user_name", "Foydalanuvchi")
    else:
        full_text = (replied.text or "") + " " + (replied.caption or "")
        id_match = re.search(r"(?:ID|Telegram ID):\s*<code>?(\d+)</code>?", full_text)
        if id_match:
            target_user_id = int(id_match.group(1))
        name_match = re.search(r"Имя:\s*([^\n]+)", full_text)
        if name_match:
            user_name = name_match.group(1).strip()

    if not target_user_id:
        return

    admin_answer = message.text or (message.caption if (message.photo or message.video) else "")
    if not admin_answer and not message.photo and not message.voice and not message.video:
        return

    try:
        user_profile = await get_user_profile(target_user_id)
        target_lang = user_profile.get("language", "uz") if user_profile else "uz"
        reply_headers = {
            "uz": "💬 <b>Admindan javob:</b>",
            "ru": "💬 <b>Ответ от администратора:</b>",
            "en": "💬 <b>Reply from admin:</b>"
        }
        voice_headers = {
            "uz": "💬 <b>Admindan ovozli javob</b>",
            "ru": "💬 <b>Голосовой ответ от администратора</b>",
            "en": "💬 <b>Voice reply from admin</b>"
        }
        header = reply_headers.get(target_lang, reply_headers["uz"])
        voice_header = voice_headers.get(target_lang, voice_headers["uz"])

        if message.text:
            await bot.send_message(
                target_user_id, 
                f"{header}\n\n{message.text}"
            )
        elif message.photo:
            caption_text = f"\n\n{message.caption}" if message.caption else ""
            await bot.send_photo(
                target_user_id, 
                photo=message.photo[-1].file_id, 
                caption=f"{header}{caption_text}"
            )
        elif message.voice:
            await bot.send_voice(
                target_user_id, 
                voice=message.voice.file_id, 
                caption=voice_header
            )
        elif message.video:
            caption_text = f"\n\n{message.caption}" if message.caption else ""
            await bot.send_video(
                target_user_id, 
                video=message.video.file_id, 
                caption=f"{header}{caption_text}"
            )

        await message.reply(f"✅ Javobingiz <b>{user_name}</b> ga yetkazildi!")

        if db_msg_id:
            db = await get_db()
            try:
                await db.execute("""
                UPDATE messages 
                SET status = 'replied', admin_reply = ?, replied_at = datetime('now')
                WHERE id = ?
                """, (admin_answer, db_msg_id))
                await db.commit()
            finally:
                await db.close()

    except Exception as e:
        await message.reply(f"⚠️ Foydalanuvchiga yuborishda xatolik: {e}")

# ==========================================
# STANDARD STUDENT / USER BOT HANDLERS
# ==========================================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "Talaba"
    role = "admin" if user_id in ADMIN_IDS else "student"

    if await is_user_blocked(user_id):
        user = await get_user_profile(user_id)
        lang = user.get("language", "uz")
        blocked_msg = {
            "uz": "🚫 Siz administrator tomonidan bloklangansiz.",
            "ru": "🚫 Вы заблокированы администратором.",
            "en": "🚫 You have been blocked by the administrator."
        }
        try:
            await message.answer(blocked_msg.get(lang, blocked_msg["uz"]))
        except Exception:
            pass
        return

    db = await get_db()
    try:
        await db.execute("""
        INSERT INTO users (telegram_id, username, full_name, role, language, student_id, faculty, group_name, course, account_status)
        VALUES (?, ?, ?, ?, 'uz', ?, 'Raqamli Iqtisodiyot', 'DI-21', 3, 'Active')
        ON CONFLICT(telegram_id) DO UPDATE SET 
            username = excluded.username,
            full_name = excluded.full_name,
            role = excluded.role
        """, (user_id, username, first_name, role, f"TSUE-{user_id % 10000}"))
        await db.commit()
    finally:
        await db.close()

    user = await get_user_profile(user_id)

    # 1. Agar foydalanuvchi hali tilni tanlamagan bo'lsa:
    # Faqat bitta toza xabar inline tugmalar bilan yuboriladi!
    if not user.get("language_selected"):
        inline_lang_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="set_lang:uz")],
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en")]
        ])
        await message.answer("🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык / Please choose language:</b>", reply_markup=inline_lang_kb)
        return


    # 2. Til tanlangan bo'lsa, kanalga a'zolikni foydalanuvchi tanlagan tilda tekshiramiz:
    lang = user.get("language", "uz")
    if not await check_channel_subscription(user_id):
        await send_subscription_prompt(message, lang)
        return

    result = await process_user_action(user_id, "/start")
    markup = build_reply_keyboard(result.get("keyboard"))
    await message.answer(result["text"], reply_markup=markup)

@dp.message(Command("language"))
async def cmd_language(message: types.Message):
    user_id = message.from_user.id
    if await is_user_blocked(user_id):
        return

    result = await process_user_action(user_id, "/language")
    markup = build_reply_keyboard(result.get("keyboard"))
    await message.answer(result["text"], reply_markup=markup)

@dp.message(Command("til"))
async def cmd_til(message: types.Message):
    user_id = message.from_user.id
    if await is_user_blocked(user_id):
        return

    result = await process_user_action(user_id, "/til")
    markup = build_reply_keyboard(result.get("keyboard"))
    await message.answer(result["text"], reply_markup=markup)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.full_name or "Talaba"
    username = message.from_user.username or ""
    photo = message.photo[-1]
    caption = message.caption or ""

    if await is_user_blocked(user_id):
        return

    # Check channel subscription
    if not await check_channel_subscription(user_id):
        user = await get_user_profile(user_id)
        await send_subscription_prompt(message, user.get("language", "uz"))
        return

    if user_id in admin_active_reply:
        reply_info = admin_active_reply.pop(user_id)
        target_id = reply_info["target_user_id"]
        db_msg_id = reply_info.get("db_msg_id")
        try:
            user_profile = await get_user_profile(target_id)
            target_lang = user_profile.get("language", "uz") if user_profile else "uz"
            reply_headers = {
                "uz": "💬 <b>Admindan javob:</b>",
                "ru": "💬 <b>Ответ от администратора:</b>",
                "en": "💬 <b>Reply from admin:</b>"
            }
            header = reply_headers.get(target_lang, reply_headers["uz"])
            caption_text = f"\n\n{caption}" if caption else ""
            await bot.send_photo(target_id, photo=photo.file_id, caption=f"{header}{caption_text}")
            await message.answer("✅ Rasm muvaffaqiyatli yuborildi!")
            if db_msg_id:
                db = await get_db()
                try:
                    await db.execute("UPDATE messages SET status = 'replied', admin_reply = ?, replied_at = datetime('now') WHERE id = ?", (f"[Rasm]: {caption}", db_msg_id))
                    await db.commit()
                finally:
                    await db.close()
        except Exception as e:
            await message.answer(f"⚠️ Yuborishda xatolik: {e}")
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = await get_db()
    inserted_id = None
    try:
        cur = await db.execute("""
        INSERT INTO messages (user_id, user_name, msg_type, content, media_url, media_type, status, created_at)
        VALUES (?, ?, 'photo', ?, ?, 'photo', 'new', ?)
        """, (user_id, first_name, caption, photo.file_id, created_at))
        inserted_id = cur.lastrowid
        await db.commit()
    finally:
        await db.close()

    user = await get_user_profile(user_id)
    lang = user.get("language", "uz")
    result = await process_user_action(user_id, "/start")
    markup = build_reply_keyboard(result.get("keyboard"))
    cancel_markup = get_user_cancel_markup(inserted_id, lang)

    is_auto = (await get_setting("auto_post_channel", "0")) == "1"
    published_to_ch = False
    pub_channel_order = None
    if is_auto and inserted_id:
        try:
            _, pub_channel_order = await publish_message_to_channel(inserted_id)
            published_to_ch = True
        except Exception as e:
            logger.error(f"Auto-post failed for photo #{inserted_id}: {e}")

    if published_to_ch:
        conf_text = {
            "uz": f"✅ <b>Rasmingiz qabul qilindi va kanalga joylandi! (#{pub_channel_order})</b>\n\n⏳ <i>Sizda 5 daqiqa vaqt bor: agar fikringiz o'zgarsa, quyidagi tugma orqali bekor qilishingiz mumkin. Bekor qilingan rasm kanaldan o'chiriladi.</i>",
            "ru": f"✅ <b>Ваша фотография принята и опубликована в канале! (#{pub_channel_order})</b>\n\n⏳ <i>У вас есть 5 минут: если вы передумаете, можете отменить ее кнопкой ниже (она будет удалена из канала).</i>",
            "en": f"✅ <b>Your photo has been received and published to the channel! (#{pub_channel_order})</b>\n\n⏳ <i>You have 5 minutes: if you change your mind, you can cancel it using the button below (it will be removed from the channel).</i>"
        }
    else:
        conf_text = {
            "uz": "✅ <b>Rasmingiz qabul qilindi va moderatorga yetkazildi.</b>\n\n⏳ <i>Admin tasdiqlaganidan so'ng kanalga chiqariladi. Agar fikringiz o'zgarsa, 5 daqiqa ichida quyidagi tugma orqali bekor qilishingiz mumkin.</i>",
            "ru": "✅ <b>Ваша фотография принята и отправлена на модерацию админу.</b>\n\n⏳ <i>Она будет опубликована в канале после одобрения. У вас есть 5 минут, чтобы отменить ее.</i>",
            "en": "✅ <b>Your photo has been received and sent for admin moderation.</b>\n\n⏳ <i>It will be published to the channel after approval. You have 5 minutes to cancel it.</i>"
        }
    await message.answer(conf_text.get(lang, conf_text["uz"]), reply_markup=cancel_markup)
    await message.answer("Asosiy menyu:" if lang == "uz" else ("Главное меню:" if lang == "ru" else "Main menu:"), reply_markup=markup)

    if inserted_id:
        await notify_admins(
            db_msg_id=inserted_id,
            user_id=user_id,
            user_name=first_name,
            username=username,
            msg_type="photo",
            content=caption,
            media_file_id=photo.file_id,
            media_type="photo",
            is_auto_published=published_to_ch,
            channel_order=pub_channel_order
        )

@dp.message(F.video)
async def handle_video(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.full_name or "Talaba"
    username = message.from_user.username or ""
    video = message.video
    caption = message.caption or ""

    if await is_user_blocked(user_id):
        return

    # Check channel subscription
    if not await check_channel_subscription(user_id):
        user = await get_user_profile(user_id)
        await send_subscription_prompt(message, user.get("language", "uz"))
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = await get_db()
    inserted_id = None
    try:
        cur = await db.execute("""
        INSERT INTO messages (user_id, user_name, msg_type, content, media_url, media_type, status, created_at)
        VALUES (?, ?, 'video', ?, ?, 'video', 'new', ?)
        """, (user_id, first_name, caption, video.file_id, created_at))
        inserted_id = cur.lastrowid
        await db.commit()
    finally:
        await db.close()

    user = await get_user_profile(user_id)
    lang = user.get("language", "uz")
    result = await process_user_action(user_id, "/start")
    markup = build_reply_keyboard(result.get("keyboard"))
    cancel_markup = get_user_cancel_markup(inserted_id, lang)

    is_auto = (await get_setting("auto_post_channel", "0")) == "1"
    published_to_ch = False
    pub_channel_order = None
    if is_auto and inserted_id:
        try:
            _, pub_channel_order = await publish_message_to_channel(inserted_id)
            published_to_ch = True
        except Exception as e:
            logger.error(f"Auto-post failed for video #{inserted_id}: {e}")

    if published_to_ch:
        conf_text = {
            "uz": f"🎥 <b>Videongiz qabul qilindi va kanalga joylandi! (#{pub_channel_order})</b>\n\n⏳ <i>Sizda 5 daqiqa vaqt bor: agar fikringiz o'zgarsa, quyidagi tugma orqali bekor qilishingiz mumkin. Bekor qilingan video kanaldan o'chiriladi.</i>",
            "ru": f"🎥 <b>Ваше видео принято и опубликовано в канале! (#{pub_channel_order})</b>\n\n⏳ <i>У вас есть 5 минут: если вы передумаете, можете отменить его кнопкой ниже (оно будет удалено из канала).</i>",
            "en": f"🎥 <b>Your video has been received and published to the channel! (#{pub_channel_order})</b>\n\n⏳ <i>You have 5 minutes: if you change your mind, you can cancel it using the button below (it will be removed from the channel).</i>"
        }
    else:
        conf_text = {
            "uz": "🎥 <b>Videongiz qabul qilindi va moderatorga yetkazildi.</b>\n\n⏳ <i>Admin tasdiqlaganidan so'ng kanalga chiqariladi. Agar fikringiz o'zgarsa, 5 daqiqa ichida quyidagi tugma orqali bekor qilishingiz mumkin.</i>",
            "ru": "🎥 <b>Ваше видео принято и отправлено на модерацию админу.</b>\n\n⏳ <i>Оно будет опубликовано в канале после одобрения. У вас есть 5 минут, чтобы отменить его.</i>",
            "en": "🎥 <b>Your video has been received and sent for admin moderation.</b>\n\n⏳ <i>It will be published to the channel after approval. You have 5 minutes to cancel it.</i>"
        }
    await message.answer(conf_text.get(lang, conf_text["uz"]), reply_markup=cancel_markup)
    await message.answer("Asosiy menyu:" if lang == "uz" else ("Главное меню:" if lang == "ru" else "Main menu:"), reply_markup=markup)

    if inserted_id:
        await notify_admins(
            db_msg_id=inserted_id,
            user_id=user_id,
            user_name=first_name,
            username=username,
            msg_type="video",
            content=caption,
            media_file_id=video.file_id,
            media_type="video",
            is_auto_published=published_to_ch,
            channel_order=pub_channel_order
        )

@dp.message(F.video_note)
async def handle_video_note(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.full_name or "Talaba"
    username = message.from_user.username or ""
    vn = message.video_note

    if await is_user_blocked(user_id):
        return

    # Check channel subscription
    if not await check_channel_subscription(user_id):
        user = await get_user_profile(user_id)
        await send_subscription_prompt(message, user.get("language", "uz"))
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = await get_db()
    inserted_id = None
    try:
        cur = await db.execute("""
        INSERT INTO messages (user_id, user_name, msg_type, content, media_url, media_type, status, created_at)
        VALUES (?, ?, 'video_note', '', ?, 'video_note', 'new', ?)
        """, (user_id, first_name, vn.file_id, created_at))
        inserted_id = cur.lastrowid
        await db.commit()
    finally:
        await db.close()

    user = await get_user_profile(user_id)
    lang = user.get("language", "uz")
    result = await process_user_action(user_id, "/start")
    markup = build_reply_keyboard(result.get("keyboard"))
    cancel_markup = get_user_cancel_markup(inserted_id, lang)

    is_auto = (await get_setting("auto_post_channel", "0")) == "1"
    published_to_ch = False
    pub_channel_order = None
    if is_auto and inserted_id:
        try:
            _, pub_channel_order = await publish_message_to_channel(inserted_id)
            published_to_ch = True
        except Exception as e:
            logger.error(f"Auto-post failed for video_note #{inserted_id}: {e}")

    if published_to_ch:
        conf_text = {
            "uz": f"🎞 <b>Dumaloq video-xabaringiz qabul qilindi va kanalga joylandi! (#{pub_channel_order})</b>\n\n⏳ <i>Sizda 5 daqiqa vaqt bor: agar fikringiz o'zgarsa, quyidagi tugma orqali bekor qilishingiz mumkin. Bekor qilingan video kanaldan o'chiriladi.</i>",
            "ru": f"🎞 <b>Круглое видеосообщение принято и опубликовано в канале! (#{pub_channel_order})</b>\n\n⏳ <i>У вас есть 5 минут: если вы передумаете, можете отменить его кнопкой ниже (оно будет удалено из канала).</i>",
            "en": f"🎞 <b>Your video note has been received and published to the channel! (#{pub_channel_order})</b>\n\n⏳ <i>You have 5 minutes: if you change your mind, you can cancel it using the button below (it will be removed from the channel).</i>"
        }
    else:
        conf_text = {
            "uz": "🎞 <b>Dumaloq video-xabaringiz qabul qilindi va moderatorga yetkazildi.</b>\n\n⏳ <i>Admin tasdiqlaganidan so'ng kanalga chiqariladi. Agar fikringiz o'zgarsa, 5 daqiqa ichida quyidagi tugma orqali bekor qilishingiz mumkin.</i>",
            "ru": "🎞 <b>Круглое видеосообщение принято и отправлено на модерацию.</b>\n\n⏳ <i>Оно будет опубликовано в канале после одобрения. У вас есть 5 минут, чтобы отменить его.</i>",
            "en": "🎞 <b>Your video note has been received and sent for moderation.</b>\n\n⏳ <i>It will be published to the channel after approval. You have 5 minutes to cancel it.</i>"
        }
    await message.answer(conf_text.get(lang, conf_text["uz"]), reply_markup=cancel_markup)
    await message.answer("Asosiy menyu:" if lang == "uz" else ("Главное меню:" if lang == "ru" else "Main menu:"), reply_markup=markup)

    if inserted_id:
        await notify_admins(
            db_msg_id=inserted_id,
            user_id=user_id,
            user_name=first_name,
            username=username,
            msg_type="video_note",
            content="",
            media_file_id=vn.file_id,
            media_type="video_note",
            is_auto_published=published_to_ch,
            channel_order=pub_channel_order
        )

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.full_name or "Talaba"
    username = message.from_user.username or ""
    voice = message.voice

    if await is_user_blocked(user_id):
        return

    # Check channel subscription
    if not await check_channel_subscription(user_id):
        user = await get_user_profile(user_id)
        await send_subscription_prompt(message, user.get("language", "uz"))
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = await get_db()
    inserted_id = None
    try:
        cur = await db.execute("""
        INSERT INTO messages (user_id, user_name, msg_type, content, media_url, media_type, status, created_at)
        VALUES (?, ?, 'voice', '', ?, 'voice', 'new', ?)
        """, (user_id, first_name, voice.file_id, created_at))
        inserted_id = cur.lastrowid
        await db.commit()
    finally:
        await db.close()

    user = await get_user_profile(user_id)
    lang = user.get("language", "uz")
    result = await process_user_action(user_id, "/start")
    markup = build_reply_keyboard(result.get("keyboard"))
    cancel_markup = get_user_cancel_markup(inserted_id, lang)

    is_auto = (await get_setting("auto_post_channel", "0")) == "1"
    published_to_ch = False
    pub_channel_order = None
    if is_auto and inserted_id:
        try:
            _, pub_channel_order = await publish_message_to_channel(inserted_id)
            published_to_ch = True
        except Exception as e:
            logger.error(f"Auto-post failed for voice #{inserted_id}: {e}")

    if published_to_ch:
        conf_text = {
            "uz": f"🎙 <b>Ovozli xabaringiz qabul qilindi va kanalga joylandi! (#{pub_channel_order})</b>\n\n⏳ <i>Sizda 5 daqiqa vaqt bor: agar fikringiz o'zgarsa, quyidagi tugma orqali bekor qilishingiz mumkin. Bekor qilingan ovozli xabar kanaldan o'chiriladi.</i>",
            "ru": f"🎙 <b>Голосовое сообщение принято и опубликовано в канале! (#{pub_channel_order})</b>\n\n⏳ <i>У вас есть 5 минут: если вы передумаете, можете отменить его кнопкой ниже (оно будет удалено из канала).</i>",
            "en": f"🎙 <b>Your voice message has been received and published to the channel! (#{pub_channel_order})</b>\n\n⏳ <i>You have 5 minutes: if you change your mind, you can cancel it using the button below (it will be removed from the channel).</i>"
        }
    else:
        conf_text = {
            "uz": "🎙 <b>Ovozli xabaringiz qabul qilindi va moderatorga yetkazildi.</b>\n\n⏳ <i>Admin tasdiqlaganidan so'ng kanalga chiqariladi. Agar fikringiz o'zgarsa, 5 daqiqa ichida quyidagi tugma orqali bekor qilishingiz mumkin.</i>",
            "ru": "🎙 <b>Голосовое сообщение принято и отправлено на модерацию.</b>\n\n⏳ <i>Оно будет опубликовано в канале после одобрения. У вас есть 5 минут, чтобы отменить его.</i>",
            "en": "🎙 <b>Your voice message has been received and sent for moderation.</b>\n\n⏳ <i>It will be published to the channel after approval. You have 5 minutes to cancel it.</i>"
        }
    await message.answer(conf_text.get(lang, conf_text["uz"]), reply_markup=cancel_markup)
    await message.answer("Asosiy menyu:" if lang == "uz" else ("Главное меню:" if lang == "ru" else "Main menu:"), reply_markup=markup)

    if inserted_id:
        await notify_admins(
            db_msg_id=inserted_id,
            user_id=user_id,
            user_name=first_name,
            username=username,
            msg_type="voice",
            content="",
            media_file_id=voice.file_id,
            media_type="voice",
            is_auto_published=published_to_ch,
            channel_order=pub_channel_order
        )


@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    if await is_user_blocked(user_id):
        user = await get_user_profile(user_id)
        lang = user.get("language", "uz")
        blocked_msg = {
            "uz": "🚫 Siz administrator tomonidan bloklangansiz.",
            "ru": "🚫 Вы заблокированы администратором.",
            "en": "🚫 You have been blocked by the administrator."
        }
        try:
            await message.answer(blocked_msg.get(lang, blocked_msg["uz"]))
        except Exception:
            pass
        return

    # 1. Check if Admin is in active reply mode
    if user_id in admin_active_reply:
        reply_info = admin_active_reply.pop(user_id)
        target_id = reply_info["target_user_id"]
        db_msg_id = reply_info.get("db_msg_id")
        user_name = reply_info.get("user_name", "Foydalanuvchi")

        if user_text.strip().lower() in ["/cancel", "bekor qilish", "отмена", "cancel"]:
            await message.answer("❌ Ответ отменен.")
            return

        try:
            user_profile = await get_user_profile(target_id)
            target_lang = user_profile.get("language", "uz") if user_profile else "uz"
            reply_headers = {
                "uz": "💬 <b>Admindan javob:</b>",
                "ru": "💬 <b>Ответ от администратора:</b>",
                "en": "💬 <b>Reply from admin:</b>"
            }
            header = reply_headers.get(target_lang, reply_headers["uz"])
            await bot.send_message(target_id, f"{header}\n\n{user_text}")
            await message.answer(f"✅ Ответ успешно отправлен для <b>{user_name}</b>!")
            if db_msg_id:
                db = await get_db()
                try:
                    await db.execute("""
                    UPDATE messages SET status = 'replied', admin_reply = ?, replied_at = datetime('now')
                    WHERE id = ?
                    """, (user_text, db_msg_id))
                    await db.commit()
                finally:
                    await db.close()
        except Exception as e:
            await message.answer(f"⚠️ Ошибка отправки пользователю: {e}")
        return

    # 2. Check if Admin is setting channel username
    if user_id in admin_channel_sessions:
        admin_channel_sessions.discard(user_id)
        if user_text.strip().lower() in ["/cancel", "отмена", "bekor qilish"]:
            await message.answer("❌ Настройка канала отменена.")
            return

        channel_val = user_text.strip()
        if not channel_val.startswith("@") and not channel_val.startswith("-100"):
            channel_val = "@" + channel_val

        await set_setting("channel_username", channel_val)
        await message.answer(
            f"✅ <b>Канал успешно привязан:</b> <code>{channel_val}</code>\n\n"
            f"Теперь при нажатии <b>'✅ Опубликовать'</b> сообщения будут публиковаться в этот канал.",
            reply_markup=get_admin_panel_markup()
        )
        return

    # 3. Check if Admin is typing a broadcast announcement
    if user_id in admin_broadcast_sessions:
        admin_broadcast_sessions.discard(user_id)
        if user_text.strip().lower() in ["/cancel", "bekor qilish", "отмена"]:
            await message.answer("❌ Рассылка отменена.")
            return

        db = await get_db()
        try:
            cur = await db.execute("SELECT telegram_id FROM users WHERE account_status != 'Blocked'")
            all_users = await cur.fetchall()
        finally:
            await db.close()

        sent_count = 0
        for u in all_users:
            try:
                await bot.send_message(u[0], f"📢 <b>Объявление:</b>\n\n{user_text}")
                sent_count += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass

        await message.answer(f"✅ Объявление отправлено {sent_count} пользователям.")
        return

    # 4. Check if Admin is searching for a user
    if user_id in admin_search_user_sessions:
        admin_search_user_sessions.discard(user_id)
        query_val = user_text.strip().replace("@", "")
        db = await get_db()
        try:
            cur = await db.execute("""
            SELECT telegram_id, full_name, username, role, account_status, created_at 
            FROM users 
            WHERE telegram_id = ? OR username LIKE ? OR full_name LIKE ?
            LIMIT 5
            """, (query_val, f"%{query_val}%", f"%{query_val}%"))
            found = await cur.fetchall()
        finally:
            await db.close()

        if not found:
            await message.answer("❌ Пользователь не найден.", reply_markup=get_back_to_admin_markup())
            return

        lines = [f"🔎 <b>Результаты поиска ({len(found)} чел.):</b>\n"]
        buttons = []
        for u in found:
            uname = f"@{u['username']}" if u['username'] else "нет username"
            status_tag = "🟢 [Активен]" if u['account_status'] != 'Blocked' else "🔴 [Заблокирован]"
            lines.append(f"• <b>{u['full_name']}</b> ({uname})\n  🆔 <code>{u['telegram_id']}</code> | {status_tag} | {u['role']}")
            if u['account_status'] == 'Blocked':
                buttons.append([InlineKeyboardButton(text=f"🔓 Разблокировать {u['full_name'][:15]}", callback_data=f"unblock:{u['telegram_id']}")])
            else:
                buttons.append([InlineKeyboardButton(text=f"🚫 Заблокировать {u['full_name'][:15]}", callback_data=f"block:{u['telegram_id']}:0")])

        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    # 5. Check if Admin is searching history
    if user_id in admin_search_history_sessions:
        admin_search_history_sessions.discard(user_id)
        query_val = user_text.strip()
        db = await get_db()
        try:
            cur = await db.execute("""
            SELECT id, user_id, user_name, msg_type, content, status, created_at 
            FROM messages 
            WHERE content LIKE ? OR user_name LIKE ? OR user_id = ?
            ORDER BY id DESC LIMIT 5
            """, (f"%{query_val}%", f"%{query_val}%", query_val))
            found = await cur.fetchall()
        finally:
            await db.close()

        if not found:
            await message.answer("❌ В истории ничего не найдено.", reply_markup=get_back_to_admin_markup())
            return

        lines = [f"🔎 <b>Найдено в истории ({len(found)} сообщений):</b>\n"]
        buttons = []
        for r in found:
            m_id, u_id, u_name, m_type, m_content, m_st, m_time = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            lines.append(f"<b>#{m_id}</b> | <b>{u_name}</b> (ID: <code>{u_id}</code>)\n📂 {get_type_badge(m_type)} | 🕒 {m_time}\n💬 <i>\"{m_content[:80]}\"</i>\n")
            buttons.append([
                InlineKeyboardButton(text=f"🔄 Повторить #{m_id}", callback_data=f"retry:{m_id}"),
                InlineKeyboardButton(text=f"🗑️ Удалить #{m_id}", callback_data=f"delete:{m_id}")
            ])
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    # 6. Check if Admin is adding another admin
    if user_id in admin_add_admin_sessions:
        admin_add_admin_sessions.discard(user_id)
        new_id_str = user_text.strip()
        if not new_id_str.isdigit():
            await message.answer("⚠️ ID faqat raqamlardan iborat bo'lishi kerak.", reply_markup=get_back_to_admin_markup())
            return

        new_admin_id = int(new_id_str)
        if new_admin_id not in ADMIN_IDS:
            ADMIN_IDS.append(new_admin_id)

        db = await get_db()
        try:
            await db.execute("UPDATE users SET role = 'admin' WHERE telegram_id = ?", (new_admin_id,))
            await db.commit()
        finally:
            await db.close()

        await message.answer(f"✅ Administrator muvaffaqiyatli qo'shildi: <code>{new_admin_id}</code>", reply_markup=get_back_to_admin_markup())
        return

    # 6.1 Check if Admin is removing an admin by ID
    if user_id in admin_remove_admin_sessions:
        admin_remove_admin_sessions.discard(user_id)
        rem_id_str = user_text.strip()
        if not rem_id_str.isdigit():
            await message.answer("⚠️ ID faqat raqamlardan iborat bo'lishi kerak.", reply_markup=get_back_to_admin_markup())
            return

        rem_admin_id = int(rem_id_str)
        if rem_admin_id == user_id:
            await message.answer("⚠️ O'zingizni administratorlikdan o'chira olmaysiz!", reply_markup=get_back_to_admin_markup())
            return

        if rem_admin_id in ADMIN_IDS:
            ADMIN_IDS.remove(rem_admin_id)

        db = await get_db()
        try:
            await db.execute("UPDATE users SET role = 'student' WHERE telegram_id = ?", (rem_admin_id,))
            await db.commit()
        finally:
            await db.close()

        await message.answer(f"✅ Administrator (ID: <code>{rem_admin_id}</code>) muvaffaqiyatli o'chirildi va huquqlari bekor qilindi.", reply_markup=get_back_to_admin_markup())
        return

    # 7. Check if user is requesting Admin Panel via text
    if any(x in user_text for x in ["Admin panel", "Админ панель", "/admin"]) and user_id in ADMIN_IDS:
        await cmd_admin(message)
        return

    # 8. Check if user is selecting language via text keyboard ("🇺🇿 O'zbekcha", "🇷🇺 Русский", "🇬🇧 English")
    if any(x in user_text for x in ["O'zbekcha", "Русский", "English", "set_lang_"]):
        result = await process_user_action(user_id, user_text)
        new_lang = result.get("new_lang", "uz")
        # Til tanlangach, kanalga a'zo bo'lishni TANLANGAN YANGI TILDA talab qilamiz!
        if not await check_channel_subscription(user_id):
            await send_subscription_prompt(message, new_lang)
            return
        markup = build_reply_keyboard(result.get("keyboard"))
        await message.answer(result["text"], reply_markup=markup)
        return

    user = await get_user_profile(user_id)
    # Agar foydalanuvchi hali umuman til tanlamagan bo'lsa, birinchi bo'lib til tanlashni talab qilamiz (bitta toza xabar):
    if not user.get("language_selected"):
        inline_lang_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="set_lang:uz")],
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en")]
        ])
        await message.answer("🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык / Please choose language:</b>", reply_markup=inline_lang_kb)
        return

    # Til tanlangan, lekin kanalga a'zo bo'lmagan bo'lsa (foydalanuvchi tanlagan tilda talab qilamiz!):
    lang = user.get("language", "uz")
    if not await check_channel_subscription(user_id):
        await send_subscription_prompt(message, lang)
        return

    # 9. Standard user action through menu engine
    result = await process_user_action(user_id, user_text)

    # If action requested to open admin panel
    if result.get("open_admin_panel") and user_id in ADMIN_IDS:
        await cmd_admin(message)
        return

    # If message was saved to DB: send confirmation with 5-minute cancellation button!
    if result.get("saved_message_id"):
        inserted_id = result["saved_message_id"]
        user = await get_user_profile(user_id)
        lang = user.get("language", "uz")

        is_auto = (await get_setting("auto_post_channel", "0")) == "1"
        published_to_ch = False
        pub_channel_order = None
        if is_auto:
            try:
                _, pub_channel_order = await publish_message_to_channel(inserted_id)
                published_to_ch = True
            except Exception as e:
                logger.error(f"Auto-post failed for #{inserted_id}: {e}")

        cancel_markup = get_user_cancel_markup(inserted_id, lang)
        
        if published_to_ch:
            conf_texts = {
                "uz": f"✅ <b>Xabaringiz qabul qilindi va kanalga joylandi! (#{pub_channel_order})</b>\n\n⏳ <i>Sizda 5 daqiqa vaqt bor: agar fikringiz o'zgarsa, quyidagi tugma orqali bekor qilishingiz mumkin. Bekor qilingan xabar kanaldan o'chiriladi.</i>",
                "ru": f"✅ <b>Ваше сообщение принято и опубликовано в канале! (#{pub_channel_order})</b>\n\n⏳ <i>У вас есть 5 минут: если вы передумаете, можете отменить его кнопкой ниже (оно будет удалено из канала).</i>",
                "en": f"✅ <b>Your message has been received and published to the channel! (#{pub_channel_order})</b>\n\n⏳ <i>You have 5 minutes: if you change your mind, you can cancel it using the button below (it will be removed from the channel).</i>"
            }
        else:
            conf_texts = {
                "uz": "✅ <b>Xabaringiz qabul qilindi va moderatorga yetkazildi.</b>\n\n⏳ <i>Admin tasdiqlaganidan so'ng kanalga chiqariladi. Agar fikringiz o'zgarsa, 5 daqiqa ichida quyidagi tugma orqali bekor qilishingiz mumkin.</i>",
                "ru": "✅ <b>Ваше сообщение принято и отправлено на модерацию админу.</b>\n\n⏳ <i>Оно будет опубликовано в канале после одобрения. У вас есть 5 минут, чтобы отменить его.</i>",
                "en": "✅ <b>Your message has been received and sent for admin moderation.</b>\n\n⏳ <i>It will be published to the channel after approval. You have 5 minutes to cancel it.</i>"
            }
        
        await message.answer(conf_texts.get(lang, conf_texts["uz"]), reply_markup=cancel_markup)
        
        main_buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        kb_markup = build_reply_keyboard(format_keyboard_grid(main_buttons))
        await message.answer("Asosiy menyu:" if lang == "uz" else ("Главное меню:" if lang == "ru" else "Main menu:"), reply_markup=kb_markup)

        # Notify admins in Telegram
        await notify_admins(
            db_msg_id=inserted_id,
            user_id=user_id,
            user_name=result.get("user_name", message.from_user.full_name or "Talaba"),
            username=message.from_user.username or "",
            msg_type=result.get("msg_type", "normal"),
            content=result.get("content", user_text),
            media_type="text",
            is_auto_published=published_to_ch,
            channel_order=pub_channel_order
        )
        return

    # Normal menu response
    markup = build_reply_keyboard(result.get("keyboard"))
    await message.answer(result["text"], reply_markup=markup)

async def start_bot_polling():
    """Starts the Telegram bot polling worker."""
    logger.info("Starting Telegram bot polling for @TSUE_AnonBot...")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.error(f"Telegram polling exception: {e}")
