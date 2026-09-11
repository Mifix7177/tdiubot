import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "8755619754:AAGMukhaxvStDkSedAUOBEUv7T4dOMMiyhk")
ADMIN_IDS = [7058416364, 6459260657]

DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "tdiu_bot.db"))

# Server Host & Port (Render.com provides PORT dynamically)
SERVER_HOST = os.getenv("HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PORT", "8000"))

# Webhook Settings for Render.com
WEBHOOK_URL = os.getenv("WEBHOOK_URL", os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "tdiu_render_secret")

# Default Daily Limits
DEFAULT_GUEST_AI_LIMIT = 5
DEFAULT_STUDENT_AI_LIMIT = 20

SUPPORTED_LANGUAGES = ["uz", "ru", "en"]
DEFAULT_LANGUAGE = "uz"
