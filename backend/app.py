import os
import asyncio
import aiosqlite
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Body, Query, BackgroundTasks, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram.types import Update

from backend.config import (
    BASE_DIR, 
    ADMIN_IDS, 
    DATABASE_PATH,
    WEBHOOK_URL, 
    WEBHOOK_PATH, 
    WEBHOOK_SECRET
)
from backend.database import get_db, init_db
from backend.default_data import seed_database
from backend.menu_engine import (
    process_user_action, 
    get_user_profile, 
    update_user_language, 
    update_user_role, 
    get_menu_buttons,
    format_keyboard_grid,
    get_session
)
from backend.bot import bot, dp, notify_admins, publish_message_to_channel, start_bot_polling

app = FastAPI(title="TDIU University Telegram Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BASE_DIR / "frontend"

# Mount static files
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")

# Serve main pages & health checks (UptimeRobot ping endpoint)
@app.get("/")
async def serve_root(request: Request):
    user_agent = request.headers.get("user-agent", "").lower()
    accept = request.headers.get("accept", "")
    if "uptimerobot" in user_agent or "curl" in user_agent or "text/plain" in accept:
        return PlainTextResponse("200 OK", status_code=200)
    return FileResponse(FRONTEND_DIR / "index.html", status_code=200)

@app.get("/health")
async def health_check():
    return PlainTextResponse("200 OK", status_code=200)

@app.get("/admin")
async def serve_admin():
    return FileResponse(FRONTEND_DIR / "admin.html")

# -----------------
# TELEGRAM WEBHOOK ENDPOINT
# -----------------
@app.post(WEBHOOK_PATH)
@app.post("/webhook")
async def handle_telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if WEBHOOK_SECRET and secret and secret != WEBHOOK_SECRET:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot=bot, update=update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        print(f"Webhook processing error: {e}")
        return Response(status_code=status.HTTP_200_OK)

# -----------------
# BOT SIMULATOR API
# -----------------

class BotActionRequest(BaseModel):
    user_id: int = 7058416364
    action_text: str
    custom_payload: Optional[str] = None
    role: Optional[str] = None
    language: Optional[str] = None

@app.post("/api/bot/action")
async def handle_bot_action(req: BotActionRequest):
    if req.role:
        await update_user_role(req.user_id, req.role)
    if req.language:
        await update_user_language(req.user_id, req.language)
        
    result = await process_user_action(req.user_id, req.action_text, req.custom_payload)
    profile = await get_user_profile(req.user_id)

    if result.get("saved_message_id"):
        asyncio.create_task(notify_admins(
            db_msg_id=result["saved_message_id"],
            user_id=req.user_id,
            user_name=result.get("user_name", profile.get("full_name", f"User {req.user_id}")),
            username=result.get("username", profile.get("username", "")),
            msg_type=result.get("msg_type", "normal"),
            content=result.get("content", req.action_text),
            media_type="text"
        ))

    return {
        "response": result,
        "profile": profile,
        "session": get_session(req.user_id)
    }

@app.get("/api/bot/state/{user_id}")
async def get_bot_state(user_id: int):
    profile = await get_user_profile(user_id)
    session = get_session(user_id)
    curr_menu = session.get("current_menu", "guest_menu" if profile.get("role") == "guest" else "main_menu")
    buttons = await get_menu_buttons(curr_menu, profile.get("role", "student"), profile.get("language", "uz"))
    return {
        "profile": profile,
        "session": session,
        "current_menu": curr_menu,
        "keyboard": format_keyboard_grid(buttons)
    }

@app.post("/api/bot/switch-role")
async def switch_role(data: Dict[str, Any] = Body(...)):
    user_id = data.get("user_id", 7058416364)
    new_role = data.get("role", "student")
    await update_user_role(user_id, new_role)
    session = get_session(user_id)
    session["state"] = "IDLE"
    session["current_menu"] = "guest_menu" if new_role == "guest" else "main_menu"
    session["menu_history"] = [session["current_menu"]]
    
    result = await process_user_action(user_id, "/start")
    profile = await get_user_profile(user_id)
    return {"profile": profile, "response": result}

@app.post("/api/bot/switch-lang")
async def switch_lang(data: Dict[str, Any] = Body(...)):
    user_id = data.get("user_id", 7058416364)
    lang = data.get("language", "uz")
    await update_user_language(user_id, lang)
    result = await process_user_action(user_id, f"set_lang_{lang}")
    profile = await get_user_profile(user_id)
    return {"profile": profile, "response": result}

@app.post("/api/bot/reset")
async def reset_bot(data: Dict[str, Any] = Body(...)):
    user_id = data.get("user_id", 7058416364)
    session = get_session(user_id)
    session["state"] = "IDLE"
    session["current_menu"] = "main_menu"
    session["menu_history"] = ["main_menu"]
    # Reset language_selected to 0 so it asks for language first
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET language_selected = 0 WHERE telegram_id = ?", (user_id,))
        await db.commit()
    result = await process_user_action(user_id, "/start")
    profile = await get_user_profile(user_id)
    return {"profile": profile, "response": result}

# -----------------
# ADMIN PORTAL API
# -----------------

@app.get("/api/admin/stats")
async def get_admin_stats():
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM messages")
        total_messages = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM messages WHERE status = 'new'")
        new_messages = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM buttons WHERE is_enabled = 1")
        total_buttons = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM test_attempts")
        total_test_attempts = (await cur.fetchone())[0]

        cur = await db.execute("SELECT SUM(ai_queries_today) FROM users")
        row = await cur.fetchone()
        ai_queries_today = row[0] if row and row[0] else 0

        return {
            "total_users": total_users,
            "total_messages": total_messages,
            "new_messages": new_messages,
            "total_buttons": total_buttons,
            "total_test_attempts": total_test_attempts,
            "ai_queries_today": ai_queries_today
        }
    finally:
        await db.close()

# MENU BUILDER ENDPOINTS
@app.get("/api/admin/menus")
async def list_menus(menu_key: Optional[str] = None):
    db = await get_db()
    try:
        if menu_key:
            cur = await db.execute("SELECT * FROM buttons WHERE menu_key = ? ORDER BY position ASC", (menu_key,))
        else:
            cur = await db.execute("SELECT * FROM buttons ORDER BY menu_key, position ASC")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

@app.get("/api/admin/menu-keys")
async def list_unique_menu_keys():
    db = await get_db()
    try:
        cur = await db.execute("SELECT DISTINCT menu_key FROM buttons ORDER BY menu_key ASC")
        rows = await cur.fetchall()
        return [r[0] for r in rows]
    finally:
        await db.close()

@app.post("/api/admin/buttons")
async def create_button(button: Dict[str, Any] = Body(...)):
    db = await get_db()
    try:
        # Determine position
        cur = await db.execute("SELECT MAX(position) FROM buttons WHERE menu_key = ?", (button["menu_key"],))
        max_pos = (await cur.fetchone())[0] or 0
        new_pos = button.get("position", max_pos + 1)
        
        cur = await db.execute("""
        INSERT INTO buttons (
            menu_key, parent_menu, label_uz, label_ru, label_en, emoji,
            position, row_index, button_type, required_role, is_enabled,
            guest_access, student_access, guest_limit, student_limit, action_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            button["menu_key"],
            button.get("parent_menu", "main_menu"),
            button.get("label_uz", "Yangi Tugma"),
            button.get("label_ru", "Новая Кнопка"),
            button.get("label_en", "New Button"),
            button.get("emoji", "🔘"),
            new_pos,
            button.get("row_index", 1),
            button.get("button_type", "open_submenu"),
            button.get("required_role", "all"),
            button.get("is_enabled", 1),
            button.get("guest_access", 1),
            button.get("student_access", 1),
            button.get("guest_limit", 0),
            button.get("student_limit", 0),
            button.get("action_payload", "")
        ))
        await db.commit()
        btn_id = cur.lastrowid
        return {"status": "ok", "id": btn_id}
    finally:
        await db.close()

@app.put("/api/admin/buttons/{btn_id}")
async def update_button(btn_id: int, button: Dict[str, Any] = Body(...)):
    db = await get_db()
    try:
        await db.execute("""
        UPDATE buttons SET
            menu_key = ?, parent_menu = ?, label_uz = ?, label_ru = ?, label_en = ?,
            emoji = ?, position = ?, row_index = ?, button_type = ?, required_role = ?,
            is_enabled = ?, guest_access = ?, student_access = ?, guest_limit = ?,
            student_limit = ?, action_payload = ?
        WHERE id = ?
        """, (
            button["menu_key"], button["parent_menu"], button["label_uz"], button["label_ru"], button["label_en"],
            button.get("emoji", ""), button.get("position", 0), button.get("row_index", 0),
            button["button_type"], button.get("required_role", "all"), button.get("is_enabled", 1),
            button.get("guest_access", 1), button.get("student_access", 1),
            button.get("guest_limit", 0), button.get("student_limit", 0), button.get("action_payload", ""),
            btn_id
        ))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()

@app.delete("/api/admin/buttons/{btn_id}")
async def delete_button(btn_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM buttons WHERE id = ?", (btn_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()

@app.post("/api/admin/buttons/reorder")
async def reorder_buttons(order_data: List[Dict[str, Any]] = Body(...)):
    """order_data = [{'id': 1, 'position': 1}, {'id': 2, 'position': 2}]"""
    db = await get_db()
    try:
        for item in order_data:
            await db.execute("UPDATE buttons SET position = ? WHERE id = ?", (item["position"], item["id"]))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()

# MESSAGES & CONFESSIONS ENDPOINTS
@app.get("/api/admin/messages")
async def get_admin_messages(msg_type: Optional[str] = None):
    db = await get_db()
    try:
        if msg_type and msg_type != "all":
            cur = await db.execute("SELECT * FROM messages WHERE msg_type = ? ORDER BY id DESC", (msg_type,))
        else:
            cur = await db.execute("SELECT * FROM messages ORDER BY id DESC")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

@app.post("/api/admin/messages/{msg_id}/reply")
async def reply_to_message(msg_id: int, data: Dict[str, Any] = Body(...), background_tasks: BackgroundTasks = None):
    reply_text = data.get("reply_text", "")
    if not reply_text:
        raise HTTPException(status_code=400, detail="Reply text is required")
        
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        msg = dict(row)
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await db.execute("""
        UPDATE messages SET status = 'replied', admin_reply = ?, replied_at = ? WHERE id = ?
        """, (reply_text, now, msg_id))
        await db.commit()

        # If user has a valid Telegram ID, dispatch message directly to Telegram
        telegram_id = msg.get("user_id")
        if telegram_id:
            try:
                await bot.send_message(
                    telegram_id, 
                    f"💬 <b>Admindan javob:</b>\n\nSizning murojaatingizga rasmiy javob:\n\n<i>{reply_text}</i>"
                )
            except Exception as e:
                print(f"Could not forward message to Telegram user {telegram_id}: {e}")

        return {"status": "ok", "replied_at": now}
    finally:
        await db.close()

@app.post("/api/admin/messages/{msg_id}/publish")
async def admin_publish_message(msg_id: int):
    """Admin endpoint to approve and publish a message to the channel."""
    try:
        channel_msg_id = await publish_message_to_channel(msg_id)
        return {"status": "ok", "channel_message_id": channel_msg_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/messages/{msg_id}/reject")
async def admin_reject_message(msg_id: int):
    """Admin endpoint to reject a message."""
    db = await get_db()
    u_id = None
    try:
        cur = await db.execute("SELECT user_id FROM messages WHERE id = ?", (msg_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        u_id = row[0]
        await db.execute("UPDATE messages SET status = 'rejected' WHERE id = ?", (msg_id,))
        await db.commit()
    finally:
        await db.close()

    if u_id:
        try:
            user_profile = await get_user_profile(u_id)
            u_lang = user_profile.get("language", "uz") if user_profile else "uz"
            notif = {
                "uz": f"ℹ️ <b>Xabaringiz (#{msg_id}) moderator tomonidan rad etildi.</b>",
                "ru": f"ℹ️ <b>Ваше сообщение (#{msg_id}) было отклонено модератором.</b>",
                "en": f"ℹ️ <b>Your message (#{msg_id}) was rejected by the moderator.</b>"
            }
            await bot.send_message(u_id, notif.get(u_lang, notif["uz"]))
        except Exception:
            pass

    return {"status": "ok"}

@app.delete("/api/admin/messages/{msg_id}")
async def delete_message(msg_id: int):
    db = await get_db()
    try:
        cur = await db.execute("SELECT channel_message_id, user_id, user_notify_message_id FROM messages WHERE id = ?", (msg_id,))
        row = await cur.fetchone()
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
                            print(f"Error deleting channel message {m_id}: {e}")
            if u_notif_id and u_id:
                try:
                    await bot.delete_message(chat_id=u_id, message_id=u_notif_id)
                except Exception as e:
                    print(f"Error deleting user notification message {u_notif_id}: {e}")
        await db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()

# SCHEDULE ENDPOINTS
@app.get("/api/admin/schedules")
async def get_all_schedules(group_name: Optional[str] = None):
    db = await get_db()
    try:
        if group_name:
            cur = await db.execute("SELECT * FROM schedules WHERE group_name = ? ORDER BY day_of_week, start_time", (group_name,))
        else:
            cur = await db.execute("SELECT * FROM schedules ORDER BY group_name, day_of_week, start_time")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

@app.post("/api/admin/schedules")
async def create_schedule_item(item: Dict[str, Any] = Body(...)):
    db = await get_db()
    try:
        cur = await db.execute("""
        INSERT INTO schedules (faculty, group_name, day_of_week, start_time, end_time, subject, room, teacher)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.get("faculty", "Raqamli Iqtisodiyot"),
            item.get("group_name", "DI-21"),
            item.get("day_of_week", "monday"),
            item.get("start_time", "08:30"),
            item.get("end_time", "09:50"),
            item.get("subject", "Fan nomi"),
            item.get("room", "101-xona"),
            item.get("teacher", "O'qituvchi")
        ))
        await db.commit()
        return {"status": "ok", "id": cur.lastrowid}
    finally:
        await db.close()

@app.delete("/api/admin/schedules/{item_id}")
async def delete_schedule_item(item_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM schedules WHERE id = ?", (item_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()

# TESTS ENDPOINTS
@app.get("/api/admin/tests")
async def get_tests_list():
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM tests ORDER BY id DESC")
        tests = [dict(r) for r in await cur.fetchall()]
        for t in tests:
            cur_q = await db.execute("SELECT * FROM test_questions WHERE test_id = ?", (t["id"],))
            t["questions"] = [dict(q) for q in await cur_q.fetchall()]
        return tests
    finally:
        await db.close()

# USERS ENDPOINT
@app.get("/api/admin/users")
async def get_users_list():
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM users ORDER BY telegram_id DESC")
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

@app.put("/api/admin/users/{uid}/role")
async def update_user_role_admin(uid: int, data: Dict[str, Any] = Body(...)):
    role = data.get("role", "student")
    await update_user_role(uid, role)
    return {"status": "ok", "role": role}

# SETTINGS ENDPOINT
@app.get("/api/admin/settings")
async def get_settings():
    db = await get_db()
    try:
        cur = await db.execute("SELECT key, value FROM settings")
        rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        await db.close()

@app.post("/api/admin/settings")
async def save_settings(settings: Dict[str, Any] = Body(...)):
    db = await get_db()
    try:
        for k, v in settings.items():
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()

# BROADCAST PUSH ENDPOINT
@app.post("/api/admin/broadcast")
async def admin_broadcast(data: Dict[str, Any] = Body(...)):
    message_text = data.get("message", "")
    if not message_text:
        raise HTTPException(status_code=400, detail="Broadcast message cannot be empty")
        
    db = await get_db()
    try:
        cur = await db.execute("SELECT telegram_id FROM users")
        users = await cur.fetchall()
        
        # Save as announcement too
        await db.execute("""
        INSERT INTO announcements (title, content, type, event_date, created_at)
        VALUES ('Tezkor Xabar', ?, 'announcement', date('now'), datetime('now'))
        """, (message_text,))
        await db.commit()
        
        sent_count = 0
        for u in users:
            try:
                await bot.send_message(u[0], f"📢 <b>Rasmiy E'lon:</b>\n\n{message_text}")
                sent_count += 1
            except Exception:
                pass
                
        return {"status": "ok", "sent_count": sent_count}
    finally:
        await db.close()

bot_polling_task = None

@app.on_event("startup")
async def startup_event():
    global bot_polling_task
    await init_db()
    await seed_database()
    if WEBHOOK_URL:
        webhook_full = f"{WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"
        print(f"🚀 Registering Telegram Webhook: {webhook_full}")
        try:
            await bot.set_webhook(
                url=webhook_full,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"]
            )
            print("✅ Telegram Webhook registered successfully!")
        except Exception as e:
            print(f"⚠️ Failed to set webhook on startup: {e}")
    else:
        print("🚀 WEBHOOK_URL not set: starting Bot Polling in background...")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        bot_polling_task = asyncio.create_task(start_bot_polling())

@app.on_event("shutdown")
async def shutdown_event():
    global bot_polling_task
    if bot_polling_task and not bot_polling_task.done():
        bot_polling_task.cancel()
    if WEBHOOK_URL:
        print("🛑 Removing Telegram Webhook on shutdown...")
        try:
            await bot.delete_webhook()
        except Exception:
            pass
    await bot.session.close()
