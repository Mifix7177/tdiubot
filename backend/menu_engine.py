import aiosqlite
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from backend.config import DATABASE_PATH, ADMIN_IDS

user_sessions: Dict[int, Dict[str, Any]] = {}

def get_session(user_id: int) -> Dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "state": "IDLE",
            "current_menu": "main_menu",
            "menu_history": ["main_menu"],
            "temp_msg_type": "normal"
        }
    return user_sessions[user_id]

async def get_user_profile(user_id: int) -> Dict[str, Any]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        row = await cur.fetchone()
        if row:
            return dict(row)
        
        role = "admin" if user_id in ADMIN_IDS else "student"
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await db.execute("""
        INSERT INTO users (telegram_id, username, full_name, role, language, language_selected, created_at)
        VALUES (?, ?, ?, ?, 'uz', 0, ?)
        """, (user_id, f"user_{user_id}", f"Foydalanuvchi {user_id}", role, created_at))
        await db.commit()
        
        cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row)

async def update_user_language(user_id: int, lang: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET language = ?, language_selected = 1 WHERE telegram_id = ?", (lang, user_id))
        await db.commit()

async def update_user_role(user_id: int, role: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET role = ? WHERE telegram_id = ?", (role, user_id))
        await db.commit()

async def get_menu_buttons(menu_key: str = "main_menu", role: str = "all", lang: str = "uz", user_id: Optional[int] = None, *args, **kwargs) -> List[Dict[str, Any]]:
    if role in ["ru", "uz", "en"] and lang == "uz":
        lang = role
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
        SELECT * FROM buttons WHERE menu_key = ? AND is_enabled = 1 ORDER BY position ASC
        """, (menu_key,))
        rows = await cur.fetchall()
        
        buttons = []
        for r in rows:
            b = dict(r)
            if lang == "uz":
                label = b["label_uz"]
            elif lang == "en":
                label = b["label_en"]
            else:
                label = b["label_ru"]
                
            emoji = b.get("emoji", "")
            b["display_text"] = f"{emoji} {label}".strip()
            buttons.append(b)

        # For main_menu, append Admin Panel (if admin) and Language button
        if menu_key == "main_menu":
            is_admin = False
            if user_id and user_id in ADMIN_IDS:
                is_admin = True
            elif role == "admin":
                is_admin = True
            
            if is_admin:
                admin_label = "👨‍💻 Admin panel" if lang == "uz" else ("👨‍💻 Админ панель" if lang == "ru" else "👨‍💻 Admin panel")
                buttons.append({
                    "menu_key": "main_menu",
                    "display_text": admin_label,
                    "action_payload": "admin_panel"
                })

            lang_label = "🌐 Tilni o'zgartirish" if lang == "uz" else ("🌐 Сменить язык" if lang == "ru" else "🌐 Change language")
            buttons.append({
                "menu_key": "main_menu",
                "display_text": lang_label,
                "action_payload": "change_lang"
            })

        return buttons

def format_keyboard_grid(buttons: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Layout matching user specification:
    Main Menu:
    Row 1: [ 💬 Отправить сообщение ] (Full width)
    Row 2: [ 📸 Отправить фотографию ] [ 🎬 Отправить видео ]
    Row 3: [ 🎤 Отправить голосовое ] [ 📹 Отправить видеосообщение ]
    Row 4: [ 👨‍💻 Admin panel ] [ 🌐 Tilni o'zgartirish ] (Admins)
       or: [ 🌐 Tilni o'zgartirish ] (Students)

    Categories Menu:
    Row 1: [ 🆘 Попросить о помощи ]
    Row 2: [ 💝 Признаться в чувствах ]
    Row 3: [ ✉️ Обычное сообщение ]
    Row 4: [ 🔙 Назад ]
    """
    grid = []
    
    # Check if this is main menu with admin/lang buttons
    admin_btn = None
    lang_btn = None
    content_buttons = []

    for b in buttons:
        dt = b["display_text"]
        if any(x in dt for x in ["Admin panel", "Админ панель"]):
            admin_btn = b
        elif any(x in dt for x in ["Tilni o'zgartirish", "Сменить язык", "Change language"]):
            lang_btn = b
        else:
            content_buttons.append(b)

    i = 0
    while i < len(content_buttons):
        b1 = content_buttons[i]
        text1 = b1["display_text"]
        
        # If full-width
        if any(x in text1 for x in [
            "Отправить сообщение", "Xabar yuborish", "Write a message", "Send message",
            "Назад", "Orqaga", "Back", "Отмена",
            "Попросить о помощи", "Yordam so'rash", "Ask for help",
            "Признаться в чувствах", "Tuyg'ularni izhor qilish", "Confess feelings",
            "Обычное сообщение", "Oddiy xabar", "Normal message"
        ]):
            grid.append([b1])
            i += 1
            continue
            
        if i + 1 < len(content_buttons):
            b2 = content_buttons[i + 1]
            text2 = b2["display_text"]
            if any(x in text2 for x in ["Назад", "Orqaga", "Back", "Отмена"]):
                grid.append([b1])
                i += 1
            else:
                grid.append([b1, b2])
                i += 2
        else:
            grid.append([b1])
            i += 1

    # Append bottom row for Admin / Language
    if admin_btn and lang_btn:
        grid.append([admin_btn, lang_btn])
    elif admin_btn:
        grid.append([admin_btn])
    elif lang_btn:
        grid.append([lang_btn])

    return grid

MESSAGES = {
    "welcome": {
        "ru": "👋 Привет! Выберите действие:",
        "uz": "👋 Salom! Kerakli amalni tanlang:",
        "en": "👋 Hello! Choose an action:"
    },
    "choose_type": {
        "ru": "📝 <b>Категории сообщений</b>\n\nВыберите нужную категорию:",
        "uz": "📝 <b>Xabar kategoriyalari</b>\n\nKerakli kategoriyani tanlang:",
        "en": "📝 <b>Message Categories</b>\n\nChoose category:"
    },
    "prompt_help": {
        "ru": "🆘 Пожалуйста, опишите вашу проблему или вопрос:\n\n<i>(Админ ответит вам в ближайшее время)</i>",
        "uz": "🆘 Iltimos, muammoingiz yoki savolingizni yozing:\n\n<i>(Admin tez orada sizga javob beradi)</i>",
        "en": "🆘 Please describe your problem or question:\n\n<i>(Admin will reply soon)</i>"
    },
    "prompt_confession": {
        "ru": "💝 Пожалуйста, напишите ваше признание в чувствах:\n\n<i>(Сообщение будет передано админу)</i>",
        "uz": "💝 Iltimos, tuyg'ularingizni izhor qilib xabar yozing:\n\n<i>(Xabaringiz adminga yetkaziladi)</i>",
        "en": "💝 Please write your confession:\n\n<i>(Your message will be sent to admin)</i>"
    },
    "prompt_normal": {
        "ru": "✉️ Пожалуйста, отправьте ваше обычное сообщение:\n\n<i>(Текст, вопрос или предложение)</i>",
        "uz": "✉️ Iltimos, xabaringizni yuboring:\n\n<i>(Matn, savol yoki taklif)</i>",
        "en": "✉️ Please send your message:\n\n<i>(Text, question, or suggestion)</i>"
    },
    "prompt_photo": {
        "ru": "📸 Пожалуйста, прикрепите и отправьте фотографию:",
        "uz": "📸 Iltimos, rasmni biriktirib yuboring:",
        "en": "📸 Please attach and send a photo:"
    },
    "prompt_video": {
        "ru": "🎬 Пожалуйста, прикрепите и отправьте видео:",
        "uz": "🎬 Iltimos, videoni biriktirib yuboring:",
        "en": "🎬 Please attach and send a video:"
    },
    "prompt_voice": {
        "ru": "🎤 Пожалуйста, запишите и отправьте голосовое сообщение:",
        "uz": "🎤 Iltimos, ovozli xabarni yozib yuboring:",
        "en": "🎤 Please record and send a voice message:"
    },
    "prompt_video_note": {
        "ru": "📹 Пожалуйста, запишите и отправьте круглое видеосообщение:",
        "uz": "📹 Iltimos, dumaloq video-xabarni yozib yuboring:",
        "en": "📹 Please record and send a video note:"
    },
    "received": {
        "ru": "✅ <b>Ваше сообщение принято и передано админу.</b>\n\n⏳ <i>У вас есть 5 минут: если вы передумаете, можете отменить его кнопкой ниже. Отмененное сообщение не будет опубликовано.</i>",
        "uz": "✅ <b>Xabaringiz qabul qilindi va adminga yetkazildi.</b>\n\n⏳ <i>Sizda 5 daqiqa vaqt bor: agar fikringiz o'zgarsa, quyidagi tugma orqali bekor qilishingiz mumkin. Bekor qilingan xabar kanalga chiqarilmaydi.</i>",
        "en": "✅ <b>Your message has been received and sent to admin.</b>\n\n⏳ <i>You have 5 minutes: if you change your mind, you can cancel it using the button below. Cancelled messages will not be published.</i>"
    },
    "awaiting_media_reminder": {
        "ru": "⚠️ Пожалуйста, отправьте файл выбранного типа или нажмите '🔙 Назад'.",
        "uz": "⚠️ Iltimos, belgilangan faylni yuboring yoki '🔙 Orqaga' tugmasini bosing.",
        "en": "⚠️ Please send the requested media file or press '🔙 Back'."
    }
}

def is_back_or_cancel(text: str) -> bool:
    """Strictly checks if the input is a back/cancel command or button."""
    t = text.strip().lower()
    keywords = [
        "orqaga", "назад", "back", "bekor", "cancel", "отмена",
        "bosh menyu", "главное меню", "main menu"
    ]
    if any(k == t or t.endswith(k) or t.startswith(k) for k in keywords):
        return True
    if t in ["/cancel", "/back", "↩️", "🔙", "⬅️", "◀️", "❌"]:
        return True
    return False

def is_submenu_button(text: str) -> Optional[str]:
    """Matches exact button clicks for message subtypes."""
    t = text.strip()
    t_clean = re.sub(r'[^\w\s]', '', t).strip().lower()

    help_labels = [
        "🆘 Попросить о помощи", "Попросить о помощи",
        "🆘 Yordam so'rash", "Yordam so'rash",
        "🆘 Need help", "Need help",
        "🆘 Ask for help", "Ask for help",
        "Yordam kerak", "🆘 Yordam kerak"
    ]
    if any(t == b or t.endswith(b) for b in help_labels) or t_clean in [
        "попросить о помощи", "yordam sorash", "need help", "ask for help", "yordam kerak"
    ]:
        return "help"
        
    confession_labels = [
        "💝 Признаться в чувствах", "Признаться в чувствах",
        "💝 Tuyg'ularni izhor qilish", "Tuyg'ularni izhor qilish",
        "💌 Признание", "Признание", "Iqrornoma", "Confession",
        "💝 Confess feelings", "Confess feelings"
    ]
    if any(t == b or t.endswith(b) for b in confession_labels) or t_clean in [
        "признаться в чувствах", "tuygularni izhor qilish", "признание", "iqrornoma", "confession", "confess feelings"
    ]:
        return "confession"
        
    normal_labels = [
        "✉️ Обычное сообщение", "Обычное сообщение",
        "✉️ Oddiy xabar", "Oddiy xabar",
        "✉️ Normal message", "Normal message"
    ]
    if any(t == b or t.endswith(b) for b in normal_labels) or t_clean in [
        "обычное сообщение", "oddiy xabar", "normal message"
    ]:
        return "normal"
        
    return None

def is_main_menu_button(text: str) -> Optional[str]:
    """Matches exact button clicks for main menu actions."""
    t = text.strip()
    t_clean = re.sub(r'[^\w\s]', '', t).strip().lower()

    write_labels = [
        "💬 Отправить сообщение", "Отправить сообщение",
        "💬 Xabar yuborish", "Xabar yuborish",
        "💬 Write a message", "Write a message",
        "💬 Send message", "Send message",
        "✍️ Xabar yozish", "Xabar yozish"
    ]
    if any(t == b or t.endswith(b) for b in write_labels) or t_clean in [
        "отправить сообщение", "xabar yuborish", "send message", "write a message", "xabar yozish"
    ]:
        return "write_message"
        
    photo_labels = [
        "📸 Отправить фотографию", "Отправить фотографию",
        "📸 Rasm yuborish", "Rasm yuborish",
        "📸 Send photo", "Send photo",
        "📷 Отправить фото", "Отправить фото", "📷 Send photo"
    ]
    if any(t == b or t.endswith(b) for b in photo_labels) or t_clean in [
        "отправить фотографию", "rasm yuborish", "send photo", "отправить фото", "фотография", "rasm", "photo"
    ]:
        return "photo"
        
    video_labels = [
        "🎬 Отправить видео", "Отправить видео",
        "🎬 Video yuborish", "Video yuborish",
        "🎬 Send video", "Send video",
        "🎥 Отправить видео", "🎥 Send video"
    ]
    if any(t == b or t.endswith(b) for b in video_labels) or t_clean in [
        "отправить видео", "video yuborish", "send video", "видео", "video"
    ]:
        return "video"
        
    voice_labels = [
        "🎤 Отправить голосовое", "Отправить голосовое",
        "🎤 Ovozli xabar", "Ovozli xabar",
        "🎤 Send voice", "Send voice",
        "🎤 Send voice message", "Send voice message",
        "🎙 Ovozli xabar", "🎙 Send voice"
    ]
    if any(t == b or t.endswith(b) for b in voice_labels) or t_clean in [
        "отправить голосовое", "ovozli xabar", "send voice", "send voice message", "голосовое", "ovozli", "voice"
    ]:
        return "voice"
        
    vnote_labels = [
        "📹 Отправить видеосообщение", "Отправить видеосообщение",
        "📹 Dumaloq video", "Dumaloq video",
        "📹 Send video note", "Send video note",
        "🎞 Dumaloq video", "🎞 Send video note"
    ]
    if any(t == b or t.endswith(b) for b in vnote_labels) or t_clean in [
        "отправить видеосообщение", "dumaloq video", "send video note", "видеосообщение", "dumaloq", "video note"
    ]:
        return "video_note"
        
    return None


async def process_user_action(user_id: int, action_text: str, custom_payload: Optional[str] = None) -> Dict[str, Any]:
    user = await get_user_profile(user_id)
    if user.get("account_status") == "Blocked":
        return {
            "text": "🚫 Siz administrator tomonidan bloklangansiz.\n🚫 Вы заблокированы администратором.",
            "keyboard": [],
            "current_menu": "blocked"
        }

    lang = user.get("language", "uz")
    session = get_session(user_id)
    text = action_text.strip()
    
    # 0. Language change prompt
    if any(x in text for x in [
        "Tilni o'zgartirish", "Сменить язык", "Change language",
        "/language", "/til", "/lang"
    ]):
        keyboard = [
            [{"display_text": "🇺🇿 O'zbekcha", "payload": "set_lang_uz"}],
            [{"display_text": "🇷🇺 Русский", "payload": "set_lang_ru"}],
            [{"display_text": "🇬🇧 English", "payload": "set_lang_en"}],
            [{"display_text": "🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back"), "payload": "back"}]
        ]
        prompt_txt = {
            "uz": "🌐 <b>Iltimos, kerakli tilni tanlang:</b>",
            "ru": "🌐 <b>Пожалуйста, выберите язык:</b>",
            "en": "🌐 <b>Please choose your language:</b>"
        }
        return {
            "text": prompt_txt.get(lang, prompt_txt["uz"]),
            "keyboard": keyboard,
            "current_menu": "lang_select"
        }

    # 0.1 Language selection actions
    if any(x in text for x in ["O'zbekcha", "set_lang_uz"]):
        await update_user_language(user_id, "uz")
        lang = "uz"
        session["state"] = "IDLE"
        session["current_menu"] = "main_menu"
        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        return {
            "text": "✅ Til muvaffaqiyatli tanlandi!\n\n" + MESSAGES["welcome"][lang],
            "keyboard": format_keyboard_grid(buttons),
            "current_menu": "main_menu",
            "language_just_selected": True,
            "new_lang": "uz"
        }
    elif any(x in text for x in ["Русский", "set_lang_ru"]):
        await update_user_language(user_id, "ru")
        lang = "ru"
        session["state"] = "IDLE"
        session["current_menu"] = "main_menu"
        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        return {
            "text": "✅ Язык успешно выбран!\n\n" + MESSAGES["welcome"][lang],
            "keyboard": format_keyboard_grid(buttons),
            "current_menu": "main_menu",
            "language_just_selected": True,
            "new_lang": "ru"
        }
    elif any(x in text for x in ["English", "set_lang_en"]):
        await update_user_language(user_id, "en")
        lang = "en"
        session["state"] = "IDLE"
        session["current_menu"] = "main_menu"
        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        return {
            "text": "✅ Language successfully set!\n\n" + MESSAGES["welcome"][lang],
            "keyboard": format_keyboard_grid(buttons),
            "current_menu": "main_menu",
            "language_just_selected": True,
            "new_lang": "en"
        }

    # 1. Admin panel request
    if any(x in text for x in ["Admin panel", "Админ панель", "/admin"]) and user_id in ADMIN_IDS:
        return {
            "text": "👨‍💻 <b>Admin panel</b>\n\n<i>Kerakli bo'limni tanlang / Выберите нужный раздел:</i>",
            "keyboard": [],
            "current_menu": "admin_panel",
            "open_admin_panel": True
        }

    # 2. /start command
    if text == "/start":
        session["state"] = "IDLE"
        session["current_menu"] = "main_menu"
        session["menu_history"] = ["main_menu"]

        # First interaction: ask for language if not selected
        if not user.get("language_selected"):
            keyboard = [
                [{"display_text": "🇺🇿 O'zbekcha", "payload": "set_lang_uz"}],
                [{"display_text": "🇷🇺 Русский", "payload": "set_lang_ru"}],
                [{"display_text": "🇬🇧 English", "payload": "set_lang_en"}]
            ]
            return {
                "text": "🌐 Iltimos, tilni tanlang:\nПожалуйста, выберите язык:\nPlease choose your language:",
                "keyboard": keyboard,
                "current_menu": "lang_select"
            }

        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        return {
            "text": MESSAGES["welcome"][lang],
            "keyboard": format_keyboard_grid(buttons),
            "current_menu": "main_menu"
        }

    # 3. Back / Cancel ALWAYS takes highest precedence
    if is_back_or_cancel(text):
        session["state"] = "IDLE"
        session["current_menu"] = "main_menu"
        session["menu_history"] = ["main_menu"]
        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        return {
            "text": MESSAGES["welcome"][lang],
            "keyboard": format_keyboard_grid(buttons),
            "current_menu": "main_menu"
        }

    # 4. User is actively submitting a text message
    if session["state"] == "AWAITING_TEXT":
        # Ignore commands
        if text.startswith("/"):
            buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
            return {
                "text": MESSAGES["welcome"][lang],
                "keyboard": format_keyboard_grid(buttons),
                "current_menu": "main_menu"
            }

        msg_type = session.get("temp_msg_type", "normal")
        user_name = user.get("full_name") or f"User {user_id}"
        username = user.get("username") or ""
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("""
            INSERT INTO messages (user_id, user_name, msg_type, content, media_type, status, created_at)
            VALUES (?, ?, ?, ?, 'text', 'new', ?)
            """, (user_id, user_name, msg_type, text, created_at))
            inserted_id = cur.lastrowid
            await db.commit()

        session["state"] = "IDLE"
        session["current_menu"] = "main_menu"
        buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
        return {
            "text": MESSAGES["received"][lang],
            "keyboard": format_keyboard_grid(buttons),
            "current_menu": "main_menu",
            "saved_message_id": inserted_id,
            "user_id": user_id,
            "user_name": user_name,
            "username": username,
            "msg_type": msg_type,
            "content": text
        }

    # 5. Check Submenu button clicks (Help, Confession, Normal message)
    submenu_type = is_submenu_button(text)
    if submenu_type:
        session["state"] = "AWAITING_TEXT"
        session["temp_msg_type"] = submenu_type
        back_label = "🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back")
        prompt_text = MESSAGES[f"prompt_{submenu_type}"][lang]
        return {
            "text": prompt_text,
            "keyboard": [[{"display_text": back_label, "payload": "back"}]],
            "current_menu": "awaiting_text"
        }

    # 6. Check Main Menu button clicks
    main_action = is_main_menu_button(text)
    if main_action:
        back_label = "🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back")
        
        if main_action == "write_message":
            session["state"] = "IDLE"
            session["current_menu"] = "msg_type_menu"
            buttons = await get_menu_buttons("msg_type_menu", lang=lang, user_id=user_id)
            return {
                "text": MESSAGES["choose_type"][lang],
                "keyboard": format_keyboard_grid(buttons),
                "current_menu": "msg_type_menu"
            }
        elif main_action == "photo":
            session["state"] = "AWAITING_PHOTO"
            session["temp_msg_type"] = "photo"
            return {
                "text": MESSAGES["prompt_photo"][lang],
                "keyboard": [[{"display_text": back_label, "payload": "back"}]],
                "current_menu": "awaiting_photo"
            }
        elif main_action == "video":
            session["state"] = "AWAITING_VIDEO"
            session["temp_msg_type"] = "video"
            return {
                "text": MESSAGES["prompt_video"][lang],
                "keyboard": [[{"display_text": back_label, "payload": "back"}]],
                "current_menu": "awaiting_video"
            }
        elif main_action == "voice":
            session["state"] = "AWAITING_VOICE"
            session["temp_msg_type"] = "voice"
            return {
                "text": MESSAGES["prompt_voice"][lang],
                "keyboard": [[{"display_text": back_label, "payload": "back"}]],
                "current_menu": "awaiting_voice"
            }
        elif main_action == "video_note":
            session["state"] = "AWAITING_VIDEO_NOTE"
            session["temp_msg_type"] = "video_note"
            return {
                "text": MESSAGES["prompt_video_note"][lang],
                "keyboard": [[{"display_text": back_label, "payload": "back"}]],
                "current_menu": "awaiting_video_note"
            }


    # 7. User is awaiting media but sent text
    if session["state"].startswith("AWAITING_"):
        back_label = "🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back")
        return {
            "text": MESSAGES["awaiting_media_reminder"][lang],
            "keyboard": [[{"display_text": back_label, "payload": "back"}]],
            "current_menu": session.get("current_menu", "awaiting_media")
        }

    # 8. Fallback to main menu
    buttons = await get_menu_buttons("main_menu", lang=lang, user_id=user_id)
    return {
        "text": MESSAGES["welcome"][lang],
        "keyboard": format_keyboard_grid(buttons),
        "current_menu": "main_menu"
    }
