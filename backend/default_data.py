import aiosqlite
from datetime import datetime
from backend.config import DATABASE_PATH, ADMIN_IDS

DEFAULT_BUTTONS = [
    # --- MAIN MESSAGE MENU ---
    {
        "menu_key": "main_menu",
        "parent_menu": "root",
        "label_uz": "Xabar yuborish",
        "label_ru": "Отправить сообщение",
        "label_en": "Send message",
        "emoji": "💬",
        "position": 1,
        "row_index": 1,
        "button_type": "open_submenu",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "msg_type_menu"
    },
    {
        "menu_key": "main_menu",
        "parent_menu": "root",
        "label_uz": "Rasm yuborish",
        "label_ru": "Отправить фотографию",
        "label_en": "Send photo",
        "emoji": "📸",
        "position": 2,
        "row_index": 2,
        "button_type": "request_photo",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "upload_photo"
    },
    {
        "menu_key": "main_menu",
        "parent_menu": "root",
        "label_uz": "Video yuborish",
        "label_ru": "Отправить видео",
        "label_en": "Send video",
        "emoji": "🎬",
        "position": 3,
        "row_index": 2,
        "button_type": "request_video",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "upload_video"
    },
    {
        "menu_key": "main_menu",
        "parent_menu": "root",
        "label_uz": "Ovozli xabar",
        "label_ru": "Отправить голосовое",
        "label_en": "Send voice",
        "emoji": "🎤",
        "position": 4,
        "row_index": 3,
        "button_type": "request_voice",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "upload_voice"
    },
    {
        "menu_key": "main_menu",
        "parent_menu": "root",
        "label_uz": "Dumaloq video",
        "label_ru": "Отправить видеосообщение",
        "label_en": "Send video note",
        "emoji": "📹",
        "position": 5,
        "row_index": 3,
        "button_type": "request_video_note",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "upload_video_note"
    },

    # --- SUBMENU: MESSAGE CATEGORIES ---
    {
        "menu_key": "msg_type_menu",
        "parent_menu": "main_menu",
        "label_uz": "Yordam so'rash",
        "label_ru": "Попросить о помощи",
        "label_en": "Ask for help",
        "emoji": "🆘",
        "position": 1,
        "row_index": 1,
        "button_type": "request_text",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "msg_help"
    },
    {
        "menu_key": "msg_type_menu",
        "parent_menu": "main_menu",
        "label_uz": "Tuyg'ularni izhor qilish",
        "label_ru": "Признаться в чувствах",
        "label_en": "Confess feelings",
        "emoji": "💝",
        "position": 2,
        "row_index": 2,
        "button_type": "request_text",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "msg_confession"
    },
    {
        "menu_key": "msg_type_menu",
        "parent_menu": "main_menu",
        "label_uz": "Oddiy xabar",
        "label_ru": "Обычное сообщение",
        "label_en": "Normal message",
        "emoji": "✉️",
        "position": 3,
        "row_index": 3,
        "button_type": "request_text",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "msg_normal"
    },
    {
        "menu_key": "msg_type_menu",
        "parent_menu": "main_menu",
        "label_uz": "Orqaga",
        "label_ru": "Назад",
        "label_en": "Back",
        "emoji": "🔙",
        "position": 4,
        "row_index": 4,
        "button_type": "back",
        "required_role": "all",
        "is_enabled": 1,
        "action_payload": "main_menu"
    }
]

DEFAULT_SETTINGS = {
    "welcome_uz": "👋 Salom! Kerakli amalni tanlang:",
    "welcome_ru": "👋 Привет! Выберите действие:",
    "welcome_en": "👋 Hello! Choose an action:",
    "received_uz": "✅ Xabaringiz qabul qilindi. Tez orada ko'rib chiqiladi.",
    "received_ru": "✅ Ваше сообщение принято. Скоро админ рассмотрит его.",
    "received_en": "✅ Your message has been received.",
    "prompt_text_uz": "📝 Iltimos, xabaringizni yozing:",
    "prompt_text_ru": "📝 Пожалуйста, отправьте ваше сообщение:",
    "prompt_text_en": "📝 Please send your message:",
    "prompt_photo_uz": "📷 Iltimos, rasmni yuboring:",
    "prompt_photo_ru": "📷 Пожалуйста, отправьте фото:",
    "prompt_photo_en": "📷 Please send your photo:",
    "prompt_video_uz": "🎥 Iltimos, videoni yuboring:",
    "prompt_video_ru": "🎥 Пожалуйста, отправьте видео:",
    "prompt_video_en": "🎥 Please send your video:",
    "prompt_voice_uz": "🎙 Iltimos, ovozli xabarni yuboring:",
    "prompt_voice_ru": "🎙 Пожалуйста, отправьте голосовое сообщение:",
    "prompt_voice_en": "🎙 Please send your voice message:",
    "prompt_video_note_uz": "🎞 Iltimos, dumaloq video-xabarni yuboring:",
    "prompt_video_note_ru": "🎞 Пожалуйста, отправьте видео-сообщение:",
    "prompt_video_note_en": "🎞 Please send your video note:",
    "channel_username": "@TSUE_Anon"
}

async def seed_database():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Clear existing buttons to match exact message focus
        await db.execute("DELETE FROM buttons")
        for b in DEFAULT_BUTTONS:
            await db.execute("""
            INSERT INTO buttons (
                menu_key, parent_menu, label_uz, label_ru, label_en, emoji,
                position, row_index, button_type, required_role, is_enabled,
                guest_access, student_access, guest_limit, student_limit, action_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, 0, ?)
            """, (
                b["menu_key"], b["parent_menu"], b["label_uz"], b["label_ru"], b["label_en"], b["emoji"],
                b["position"], b["row_index"], b["button_type"], b["required_role"], b["is_enabled"],
                b["action_payload"]
            ))

        # Check settings
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # Ensure admin user
        for admin_id in ADMIN_IDS:
            await db.execute("""
            INSERT OR IGNORE INTO users (
                telegram_id, username, full_name, role, language, created_at
            ) VALUES (?, ?, 'Admin', 'admin', 'ru', datetime('now'))
            """, (admin_id, f"admin_{admin_id}"))

        await db.commit()
        print("Database seeded with message-only buttons.")
