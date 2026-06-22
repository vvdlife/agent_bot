import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    DATABASE_PATH = os.getenv("DATABASE_PATH", "agent.db")
    GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
    ALLOWED_USERS = [x.strip().lower() for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
    
    # Parse port safely
    try:
        GOOGLE_REDIRECT_PORT = int(os.getenv("GOOGLE_REDIRECT_PORT", "8080"))
    except ValueError:
        GOOGLE_REDIRECT_PORT = 8080

    # Polling & Reminder Configuration
    try:
        CALENDAR_POLL_INTERVAL_SECONDS = int(os.getenv("CALENDAR_POLL_INTERVAL_SECONDS", "600"))
    except ValueError:
        CALENDAR_POLL_INTERVAL_SECONDS = 600

    try:
        CALENDAR_REMINDER_LEAD_MINUTES = int(os.getenv("CALENDAR_REMINDER_LEAD_MINUTES", "15"))
    except ValueError:
        CALENDAR_REMINDER_LEAD_MINUTES = 15

    WEEKLY_REPORT_TIME = os.getenv("WEEKLY_REPORT_TIME", "20:00")


    @classmethod
    def validate(cls):
        missing = []
        if not cls.TELEGRAM_BOT_TOKEN or cls.TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.GEMINI_API_KEY or cls.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
            missing.append("GEMINI_API_KEY")
        
        if missing:
            raise ValueError(f"Missing required environment variables in .env: {', '.join(missing)}")

