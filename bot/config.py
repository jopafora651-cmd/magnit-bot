import os
from dotenv import load_dotenv

load_dotenv()

# Bot token from environment variables - NEVER commit actual tokens
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set. Please create a .env file with your bot token.")

# API settings
MAGNIT_API_BASE = os.getenv("MAGNIT_API_BASE", "https://magnit.ru")

# Polling interval in seconds
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))

# Default minimum discount threshold percentage
DEFAULT_MIN_DISCOUNT = int(os.getenv("DEFAULT_MIN_DISCOUNT", "10"))

# Max favourite stores per user
MAX_STORES = int(os.getenv("MAX_STORES", "10"))

# Database path
DATABASE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "magnit_bot.db")
