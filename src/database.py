import sqlite3
from datetime import datetime
from src.config import Config

def get_db_connection():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            # Create tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
            """)
            # Create notes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    tags TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            # Create chat history table for conversational memory
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            # Create user credentials table for Google Workspace OAuth
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_credentials (
                    chat_id INTEGER PRIMARY KEY,
                    credentials_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Create sent reminders table to prevent duplicate notifications
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_reminders (
                    chat_id INTEGER,
                    event_id TEXT,
                    start_time TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (chat_id, event_id)
                )
            """)
            # Create gmail filters table for alert configuration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gmail_filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    filter_type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # Create sent email alerts table to prevent duplicate alerts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_email_alerts (
                    chat_id INTEGER,
                    message_id TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (chat_id, message_id)
                )
            """)
            # Create user settings table for daily briefing configuration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    chat_id INTEGER PRIMARY KEY,
                    briefing_time TEXT NOT NULL DEFAULT '08:00',
                    location TEXT NOT NULL DEFAULT 'Seoul',
                    google_auth_expiry_notified INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            # Schema migration: Add google_auth_expiry_notified column if it doesn't exist
            try:
                cursor.execute("ALTER TABLE user_settings ADD COLUMN google_auth_expiry_notified INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass # Already exists
            # Create sent briefings table to prevent duplicate daily briefings
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_briefings (
                    chat_id INTEGER,
                    briefing_date TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (chat_id, briefing_date)
                )
            """)
            # Create user_news_keywords table for personalized news feeds
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_news_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(chat_id, keyword)
                )
            """)
            # Create news_articles_cache table to bypass Telegram 64-byte callback limit and save API costs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news_articles_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    title TEXT,
                    summary TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            # Create user_expenses table for ledger feature
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT,
                    spent_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # Create user_ddays table for D-Day and Anniversary Management
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_ddays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # Create sent_dday_alerts table to prevent duplicate notifications
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_dday_alerts (
                    chat_id INTEGER,
                    dday_id INTEGER,
                    alert_type TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (chat_id, dday_id, alert_type),
                    FOREIGN KEY(dday_id) REFERENCES user_ddays(id) ON DELETE CASCADE
                )
            """)
            # Create sent weekly reports table to prevent duplicate weekly reports
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_weekly_reports (
                    chat_id INTEGER,
                    report_week TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (chat_id, report_week)
                )
            """)
            # Create pending travel plans table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_travel_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    destination TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    events_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # 유튜브/아티클 요약본 및 복습 퀴즈 세션을 관리하는 quiz_sessions 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quiz_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    questions_json TEXT NOT NULL, -- 퀴즈 질문 목록 데이터 (JSON 포맷)
                    source_content TEXT,          -- 추가 퀴즈 생성을 위한 원본 본문 텍스트 보관
                    current_index INTEGER NOT NULL DEFAULT 0, -- 사용자가 현재 풀어야 할 퀴즈의 인덱스 번호
                    score INTEGER NOT NULL DEFAULT 0,          -- 첫 시도 정답 누적 점수
                    is_current_failed INTEGER NOT NULL DEFAULT 0, -- 현재 풀고 있는 문제의 첫 오답 여부 (0: 안틀림, 1: 틀림)
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
            """)
            # [스키마 마이그레이션] 기존 DB 사용자 호환성 유지: source_content 컬럼이 없는 경우 동적 추가
            try:
                cursor.execute("ALTER TABLE quiz_sessions ADD COLUMN source_content TEXT")
            except sqlite3.OperationalError:
                pass # 이미 존재함
            # [스키마 마이그레이션] 기존 DB 사용자 호환성 유지: is_current_failed 컬럼이 없는 경우 동적 추가
            try:
                cursor.execute("ALTER TABLE quiz_sessions ADD COLUMN is_current_failed INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass # 이미 존재함
            # 사용자가 틀린 문제를 보관 및 복습할 수 있게 지원하는 quiz_incorrect_notes 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quiz_incorrect_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,          -- 출처가 된 유튜브/아티클의 제목
                    question_text TEXT NOT NULL,  -- 질문 내용
                    options_json TEXT NOT NULL,   -- 객관식 보기 리스트 (JSON 배열 형태)
                    correct_option INTEGER NOT NULL, -- 정답 인덱스 (0, 1, 2, 3)
                    explanation TEXT NOT NULL,    -- 정답 해설
                    wrong_count INTEGER NOT NULL DEFAULT 1, -- 사용자가 이 문제를 틀린 횟수 (누적 가중치)
                    created_at TEXT NOT NULL,
                    UNIQUE(chat_id, question_text) -- 한 사용자가 동일한 질문에 대해 여러 번 틀릴 경우 wrong_count만 증가하도록 제한
                )
            """)
    finally:
        conn.close()

# Task Operations
def add_task(title: str, description: str = None, due_date: str = None) -> int:
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (title, description, due_date, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (title, description, due_date, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def list_tasks(status: str = None) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT * FROM tasks WHERE status = ? ORDER BY id DESC", (status,))
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def complete_task(task_id: int) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

# Note Operations
def add_note(content: str, tags: str = None) -> int:
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO notes (content, tags, created_at) VALUES (?, ?, ?)",
                (content, tags, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def search_notes(query: str) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Search content or tags
        cursor.execute(
            "SELECT * FROM notes WHERE content LIKE ? OR tags LIKE ? ORDER BY id DESC",
            (f"%{query}%", f"%{query}%")
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def delete_note(note_id: int) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def clear_notes() -> int:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notes")
            deleted_count = cursor.rowcount
            # Reset sqlite sequence for notes
            try:
                cursor.execute("UPDATE sqlite_sequence SET seq = 0 WHERE name = 'notes'")
            except sqlite3.OperationalError:
                pass # sqlite_sequence might not have notes yet
            return deleted_count
    finally:
        conn.close()


# Chat Memory Operations
def save_chat_message(chat_id: int, role: str, content: str):
    timestamp = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO chat_history (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, timestamp)
            )
    finally:
        conn.close()

def get_chat_history(chat_id: int, limit: int = 15) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Fetch the most recent chat history for the user, in chronological order
        cursor.execute(
            "SELECT role, content FROM (SELECT * FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (chat_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def save_user_credentials(chat_id: int, creds_json: str):
    updated_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_credentials (chat_id, credentials_json, updated_at) VALUES (?, ?, ?)",
                (chat_id, creds_json, updated_at)
            )
            # Also reset google_auth_expiry_notified flag to 0 in user_settings
            cursor.execute("SELECT 1 FROM user_settings WHERE chat_id = ?", (chat_id,))
            if cursor.fetchone():
                cursor.execute(
                    "UPDATE user_settings SET google_auth_expiry_notified = 0, updated_at = ? WHERE chat_id = ?",
                    (updated_at, chat_id)
                )
    finally:
        conn.close()

def get_user_credentials(chat_id: int) -> str:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT credentials_json FROM user_credentials WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def delete_user_credentials(chat_id: int) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_credentials WHERE chat_id = ?", (chat_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_all_authenticated_users() -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM user_credentials")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

def is_reminder_sent(chat_id: int, event_id: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sent_reminders WHERE chat_id = ? AND event_id = ?",
            (chat_id, event_id)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()

def save_sent_reminder(chat_id: int, event_id: str, start_time: str):
    sent_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sent_reminders (chat_id, event_id, start_time, sent_at) VALUES (?, ?, ?, ?)",
                (chat_id, event_id, start_time, sent_at)
            )
    finally:
        conn.close()

def cleanup_old_reminders(days: int = 1):
    from datetime import timedelta
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sent_reminders WHERE sent_at < ?",
                (threshold,)
            )
    finally:
        conn.close()

def add_gmail_filter(chat_id: int, filter_type: str, value: str):
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM gmail_filters WHERE chat_id = ? AND filter_type = ? AND value = ?",
                (chat_id, filter_type, value)
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO gmail_filters (chat_id, filter_type, value, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, filter_type, value, created_at)
                )
    finally:
        conn.close()

def delete_gmail_filter(chat_id: int, filter_type: str, value: str) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM gmail_filters WHERE chat_id = ? AND filter_type = ? AND value = ?",
                (chat_id, filter_type, value)
            )
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_gmail_filters(chat_id: int) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT filter_type, value FROM gmail_filters WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def is_email_alert_sent(chat_id: int, message_id: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sent_email_alerts WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()

def save_sent_email_alert(chat_id: int, message_id: str):
    sent_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sent_email_alerts (chat_id, message_id, sent_at) VALUES (?, ?, ?)",
                (chat_id, message_id, sent_at)
            )
    finally:
        conn.close()

def cleanup_old_email_alerts(days: int = 7):
    from datetime import timedelta
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sent_email_alerts WHERE sent_at < ?",
                (threshold,)
            )
    finally:
        conn.close()

def save_user_setting(chat_id: int, key: str, value: str):
    updated_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM user_settings WHERE chat_id = ?", (chat_id,))
            exists = cursor.fetchone() is not None
            
            if exists:
                if key in ['briefing_time', 'location', 'google_auth_expiry_notified']:
                    cursor.execute(
                        f"UPDATE user_settings SET {key} = ?, updated_at = ? WHERE chat_id = ?",
                        (value, updated_at, chat_id)
                    )
            else:
                briefing_time = '08:00'
                location = 'Seoul'
                google_auth_expiry_notified = 0
                if key == 'briefing_time':
                    briefing_time = value
                elif key == 'location':
                    location = value
                elif key == 'google_auth_expiry_notified':
                    google_auth_expiry_notified = int(value)
                    
                cursor.execute(
                    "INSERT INTO user_settings (chat_id, briefing_time, location, google_auth_expiry_notified, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (chat_id, briefing_time, location, google_auth_expiry_notified, updated_at)
                )
    finally:
        conn.close()

def get_user_settings(chat_id: int) -> dict:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT briefing_time, location, google_auth_expiry_notified FROM user_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            return {"briefing_time": "08:00", "location": "Seoul", "google_auth_expiry_notified": 0}
    finally:
        conn.close()

def is_briefing_sent(chat_id: int, date_str: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sent_briefings WHERE chat_id = ? AND briefing_date = ?",
            (chat_id, date_str)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()

def save_sent_briefing(chat_id: int, date_str: str):
    sent_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sent_briefings (chat_id, briefing_date, sent_at) VALUES (?, ?, ?)",
                (chat_id, date_str, sent_at)
            )
    finally:
        conn.close()

def cleanup_old_briefings(days: int = 30):
    from datetime import timedelta
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sent_briefings WHERE sent_at < ?",
                (threshold,)
            )
    finally:
        conn.close()

# News Operations
def add_news_keyword(chat_id: int, keyword: str) -> bool:
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM user_news_keywords WHERE chat_id = ? AND keyword = ?",
                (chat_id, keyword)
            )
            if cursor.fetchone():
                return False  # Already exists
            cursor.execute(
                "INSERT INTO user_news_keywords (chat_id, keyword, created_at) VALUES (?, ?, ?)",
                (chat_id, keyword, created_at)
            )
            return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()

def delete_news_keyword(chat_id: int, keyword: str) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM user_news_keywords WHERE chat_id = ? AND keyword = ?",
                (chat_id, keyword)
            )
            return cursor.rowcount > 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()

def get_news_keywords(chat_id: int) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT keyword FROM user_news_keywords WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,)
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

def save_news_article_cache(url: str, title: str, summary: str = None) -> int:
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM news_articles_cache WHERE url = ?", (url,))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            cursor.execute(
                "INSERT INTO news_articles_cache (url, title, summary, created_at) VALUES (?, ?, ?, ?)",
                (url, title, summary, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def get_news_article_by_id(article_id: int) -> dict:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM news_articles_cache WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_news_article_summary(article_id: int, summary: str):
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE news_articles_cache SET summary = ? WHERE id = ?",
                (summary, article_id)
            )
    finally:
        conn.close()

# Expense (Household Ledger) Operations
def add_expense(chat_id: int, amount: int, category: str, description: str = None, spent_at: str = None) -> int:
    created_at = datetime.now().isoformat()
    if not spent_at:
        # Default to today in YYYY-MM-DD
        spent_at = datetime.now().strftime("%Y-%m-%d")
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_expenses (chat_id, amount, category, description, spent_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, amount, category, description, spent_at, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def delete_expense(expense_id: int) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_expenses WHERE id = ?", (expense_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_expenses(chat_id: int, start_date: str = None, end_date: str = None) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if start_date and end_date:
            cursor.execute(
                "SELECT * FROM user_expenses WHERE chat_id = ? AND spent_at BETWEEN ? AND ? ORDER BY spent_at DESC, id DESC",
                (chat_id, start_date, end_date)
            )
        else:
            cursor.execute(
                "SELECT * FROM user_expenses WHERE chat_id = ? ORDER BY spent_at DESC, id DESC",
                (chat_id,)
            )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_expenses_summary(chat_id: int, start_date: str, end_date: str) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category, SUM(amount) as total_amount FROM user_expenses "
            "WHERE chat_id = ? AND spent_at BETWEEN ? AND ? "
            "GROUP BY category ORDER BY total_amount DESC",
            (chat_id, start_date, end_date)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

# D-Day and Anniversary Operations
def add_dday(chat_id: int, title: str, target_date: str) -> int:
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_ddays (chat_id, title, target_date, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, title, target_date, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def get_ddays(chat_id: int) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM user_ddays WHERE chat_id = ? ORDER BY target_date ASC",
            (chat_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def delete_dday(dday_id: int) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sent_dday_alerts WHERE dday_id = ?", (dday_id,))
            cursor.execute("DELETE FROM user_ddays WHERE id = ?", (dday_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def clear_ddays(chat_id: int) -> int:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sent_dday_alerts WHERE chat_id = ?", (chat_id,))
            cursor.execute("DELETE FROM user_ddays WHERE chat_id = ?", (chat_id,))
            deleted_count = cursor.rowcount
            # Reset sequence if empty
            try:
                cursor.execute("UPDATE sqlite_sequence SET seq = 0 WHERE name = 'user_ddays'")
            except sqlite3.OperationalError:
                pass
            return deleted_count
    finally:
        conn.close()

def is_dday_alert_sent(chat_id: int, dday_id: int, alert_type: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sent_dday_alerts WHERE chat_id = ? AND dday_id = ? AND alert_type = ?",
            (chat_id, dday_id, alert_type)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()

def save_sent_dday_alert(chat_id: int, dday_id: int, alert_type: str):
    sent_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sent_dday_alerts (chat_id, dday_id, alert_type, sent_at) VALUES (?, ?, ?, ?)",
                (chat_id, dday_id, alert_type, sent_at)
            )
    finally:
        conn.close()

def cleanup_old_dday_alerts(days: int = 30):
    from datetime import timedelta
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sent_dday_alerts WHERE sent_at < ?", (threshold,))
    finally:
        conn.close()

def is_weekly_report_sent(chat_id: int, report_week: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sent_weekly_reports WHERE chat_id = ? AND report_week = ?",
            (chat_id, report_week)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()

def save_sent_weekly_report(chat_id: int, report_week: str):
    sent_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sent_weekly_reports (chat_id, report_week, sent_at) VALUES (?, ?, ?)",
                (chat_id, report_week, sent_at)
            )
    finally:
        conn.close()

def cleanup_old_weekly_reports(days: int = 365):
    from datetime import timedelta
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sent_weekly_reports WHERE sent_at < ?",
                (threshold,)
            )
    finally:
        conn.close()

# Travel Planner Operations
def save_pending_travel_plan(chat_id: int, destination: str, start_date: str, end_date: str, events_json: str) -> int:
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO pending_travel_plans (chat_id, destination, start_date, end_date, events_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, destination, start_date, end_date, events_json, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def get_pending_travel_plan(plan_id: int) -> dict:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_travel_plans WHERE id = ?", (plan_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# 퀴즈 세션 관련 데이터베이스 처리 함수 목록
def create_quiz_session(chat_id: int, title: str, questions_json: str, source_content: str = None) -> int:
    """
    새로운 퀴즈 세션을 데이터베이스에 생성합니다.
    기존에 존재하던 해당 채팅방의 활성화('active')된 다른 세션들은 중복 방지를 위해 모두 'completed'로 처리합니다.
    """
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            # 1. 기존의 활성화된 다른 퀴즈 세션 강제 완료 처리 (용량 절약을 위해 완료되는 세션의 원본 본문 텍스트는 지웁니다)
            cursor.execute("UPDATE quiz_sessions SET status = 'completed', source_content = NULL WHERE chat_id = ? AND status = 'active'", (chat_id,))
            # 2. 새로운 세션 정보 INSERT (최초 점수: 0, 최초 인덱스: 0, 오답 상태: 0)
            cursor.execute(
                "INSERT INTO quiz_sessions (chat_id, title, questions_json, source_content, current_index, score, is_current_failed, status, created_at) "
                "VALUES (?, ?, ?, ?, 0, 0, 0, 'active', ?)",
                (chat_id, title, questions_json, source_content, created_at)
            )
            return cursor.lastrowid
    finally:
        conn.close()

def get_active_quiz_session(chat_id: int) -> dict:
    """채팅방 내에서 현재 활성화 상태('active')인 최신 퀴즈 세션 데이터를 가져옵니다."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quiz_sessions WHERE chat_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (chat_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_quiz_session(session_id: int) -> dict:
    """세션 ID에 해당하는 특정 퀴즈 세션의 전체 데이터를 가져옵니다."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quiz_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_quiz_session(session_id: int, current_index: int, score: int, status: str, questions_json: str = None, is_current_failed: int = None) -> bool:
    """
    진행 중인 퀴즈 세션 정보를 갱신합니다.
    - index, score, status 필드는 고정 업데이트
    - questions_json (문제 추가 시), is_current_failed (오답 감지 시) 필드는 제공되었을 때만 동적으로 업데이트 쿼리에 빌드
    """
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            query = "UPDATE quiz_sessions SET current_index = ?, score = ?, status = ?"
            params = [current_index, score, status]
            
            # 동적으로 문제 목록(JSON) 업데이트 적용
            if questions_json is not None:
                query += ", questions_json = ?"
                params.append(questions_json)
                
            # 동적으로 오답 시도 이력 필드 업데이트 적용
            if is_current_failed is not None:
                query += ", is_current_failed = ?"
                params.append(is_current_failed)
                
            query += " WHERE id = ?"
            params.append(session_id)
            
            cursor.execute(query, tuple(params))
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_completed_quiz_stats(chat_id: int) -> dict:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT questions_json, score FROM quiz_sessions WHERE chat_id = ? AND status = 'completed'", (chat_id,))
        rows = cursor.fetchall()
        total_quizzes = len(rows)
        total_score = sum(r['score'] for r in rows)
        
        import json
        total_questions = 0
        for r in rows:
            try:
                q_list = json.loads(r['questions_json'])
                total_questions += len(q_list)
            except Exception:
                total_questions += 3
                
        avg_rate = (total_score / total_questions * 100) if total_questions > 0 else 0.0
        return {
            "total_quizzes": total_quizzes,
            "total_score": total_score,
            "total_questions": total_questions,
            "avg_rate": avg_rate
        }
    finally:
        conn.close()

def get_completed_quizzes(chat_id: int, limit: int = 5) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM quiz_sessions WHERE chat_id = ? AND status = 'completed' ORDER BY id DESC LIMIT ?",
            (chat_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def delete_quiz_session(session_id: int) -> bool:
    """테스트 혹은 특정 사유로 생성된 퀴즈 세션을 삭제합니다."""
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM quiz_sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

# 오답 노트(quiz_incorrect_notes) 테이블 조작 헬퍼 함수 목록
def add_or_increment_incorrect_note(chat_id: int, title: str, question_text: str, options_json: str, correct_option: int, explanation: str) -> bool:
    """
    오답 노트를 생성하거나 틀린 횟수(wrong_count)를 1 증가시킵니다.
    UNIQUE(chat_id, question_text) 제약조건을 이용해 이미 존재할 경우 wrong_count만 증가하도록 처리합니다.
    """
    created_at = datetime.now().isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO quiz_incorrect_notes (chat_id, title, question_text, options_json, correct_option, explanation, wrong_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(chat_id, question_text) DO UPDATE SET
                    wrong_count = wrong_count + 1,
                    created_at = excluded.created_at
            """, (chat_id, title, question_text, options_json, correct_option, explanation, created_at))
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_incorrect_notes_for_review(chat_id: int, limit: int = 5) -> list:
    """
    사용자가 틀린 문제들 중 복습할 5(limit)개의 문항을 추출합니다.
    틀린 횟수(wrong_count)가 많은 순으로 가중치를 두고, 그 중 무작위 요소로 추출하도록 쿼리를 설계했습니다.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # wrong_count가 높은 상위 15개 중 임의로 limit(5)개를 가져옴으로써 가중치와 무작위성 조율
        cursor.execute("""
            SELECT * FROM (
                SELECT * FROM quiz_incorrect_notes 
                WHERE chat_id = ? 
                ORDER BY wrong_count DESC 
                LIMIT 15
            ) ORDER BY RANDOM() LIMIT ?
        """, (chat_id, limit))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_incorrect_notes_count(chat_id: int) -> int:
    """사용자가 현재 오답 노트에 보관 중인 문제들의 총 개수를 조회합니다."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM quiz_incorrect_notes WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()

def remove_incorrect_note_by_text(chat_id: int, question_text: str) -> bool:
    """복습 과정에서 사용자가 정답을 맞춘 문제를 오답 노트 테이블에서 영구 삭제합니다."""
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM quiz_incorrect_notes WHERE chat_id = ? AND question_text = ?", (chat_id, question_text))
            return cursor.rowcount > 0
    finally:
        conn.close()

def cleanup_old_quiz_sessions(days: int = 30):
    """
    지정된 일수(days)보다 오래된 퀴즈 세션의 원본 본문 텍스트(source_content)를 NULL 처리하여
    데이터베이스 파일 용량을 최적화합니다.
    """
    from datetime import datetime, timedelta
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE quiz_sessions SET source_content = NULL WHERE created_at < ? AND source_content IS NOT NULL",
                (threshold,)
            )
    finally:
        conn.close()



