from src import database, google_auth
from duckduckgo_search import DDGS
import httpx
import unicodedata
import contextvars
import datetime
import base64
import html
import re
import asyncio
from urllib.parse import urlparse
from email.mime.text import MIMEText
from googleapiclient.discovery import build

current_chat_id = contextvars.ContextVar("current_chat_id")


def create_local_task(title: str, description: str = None, due_date: str = None) -> str:
    """
    Creates a new local task (to-do item) in the database.

    Args:
        title: The title of the task (e.g., 'Buy groceries'). This is required.
        description: Detailed information about the task (optional).
        due_date: The due date/time of the task, preferably in YYYY-MM-DD or standard format (optional).

    Returns:
        A text confirmation message indicating the task was created with its ID.
    """
    if not title:
        return "Error: Task title is required."
    
    task_id = database.add_task(title, description, due_date)
    return f"Success: Created task '{title}' with ID {task_id}."

def list_local_tasks(status: str = None) -> str:
    """
    Retrieves and lists local tasks from the database.

    Args:
        status: Filter tasks by status. Value can be 'pending' or 'completed'.
                If not provided, lists all tasks.

    Returns:
        A formatted text string listing the tasks with their IDs, titles, descriptions, due dates, and status.
    """
    tasks = database.list_tasks(status)
    if not tasks:
        filter_str = f" with status '{status}'" if status else ""
        return f"No tasks found{filter_str}."
    
    lines = []
    for t in tasks:
        desc_str = f" - {t['description']}" if t['description'] else ""
        due_str = f" (Due: {t['due_date']})" if t['due_date'] else ""
        lines.append(f"[{t['id']}] {t['title']}{desc_str}{due_str} [{t['status']}]")
    
    return "\n".join(lines)

def complete_local_task(task_id: int) -> str:
    """
    Marks a specific task as completed in the database.

    Args:
        task_id: The unique integer ID of the task to complete. This is required.

    Returns:
        A text confirmation message indicating if the task was completed successfully.
    """
    success = database.complete_task(task_id)
    if success:
        return f"Success: Task with ID {task_id} marked as completed."
    else:
        return f"Error: Task with ID {task_id} not found."

def save_note(content: str, tags: str = None) -> str:
    """
    Saves a text note or memo into the database.

    Args:
        content: The text content of the note. This is required.
        tags: Optional tags or labels to categorize the note (e.g. 'work, ideas', 'personal').

    Returns:
        A text confirmation message indicating the note was saved with its ID.
    """
    if not content:
        return "Error: Note content cannot be empty."
    
    note_id = database.add_note(content, tags)
    return f"Success: Saved note with ID {note_id}."

def search_notes(query: str) -> str:
    """
    Searches the saved notes using a text query. Matches against note content and tags.

    Args:
        query: The search term or keyword to look for. This is required.

    Returns:
        A formatted list of notes matching the search query, or a message indicating no matches.
    """
    if not query:
        return "Error: Search query is required."
    
    notes = database.search_notes(query)
    if not notes:
        return f"No notes found matching query: '{query}'."
    
    lines = []
    for n in notes:
        tag_str = f" [Tags: {n['tags']}]" if n['tags'] else ""
        lines.append(f"[{n['id']}] {n['content']}{tag_str} (Created: {n['created_at'][:10]})")
    
    return "\n".join(lines)

def delete_local_note(note_id: int) -> str:
    """
    Deletes a specific local note (memo) from the database by its ID.

    Args:
        note_id: The unique integer ID of the note to delete. This is required.

    Returns:
        A text confirmation message indicating if the note was successfully deleted.
    """
    if not note_id:
        return "Error: Note ID is required."
        
    success = database.delete_note(note_id)
    if success:
        return f"Success: Note with ID {note_id} has been deleted."
    else:
        return f"Error: Note with ID {note_id} not found."

def clear_all_local_notes() -> str:
    """
    Deletes all saved local notes (memos) from the database.

    Returns:
        A text confirmation message indicating how many notes were deleted.
    """
    count = database.clear_notes()
    return f"Success: Deleted all saved notes (Total {count} notes cleared)."


def add_expense_tool(amount: int, category: str, description: str = None, spent_at: str = None) -> str:
    """
    Saves a daily expense details (ledger entry) into the database.

    Args:
        amount: The expense amount in KRW (e.g., 12000). This is required and must be greater than 0.
        category: The category of the expense (e.g., '식비', '교통비', '쇼핑', '문화생활' 등). This is required.
        description: A short description of what was purchased (optional, e.g., '스타벅스 아메리카노').
        spent_at: The date the expense occurred, format: YYYY-MM-DD (optional, defaults to today).

    Returns:
        A text confirmation message indicating the expense was saved with its ID.
    """
    if not amount or amount <= 0:
        return "Error: Expense amount must be greater than 0."
    if not category:
        return "Error: Expense category is required."
        
    chat_id = current_chat_id.get()
    expense_id = database.add_expense(
        chat_id=chat_id,
        amount=amount,
        category=category,
        description=description,
        spent_at=spent_at
    )
    return f"Success: Saved expense '{category}: {amount:,}원' with ID {expense_id}."

def delete_expense_tool(expense_id: int) -> str:
    """
    Deletes a specific expense entry from the database.

    Args:
        expense_id: The unique integer ID of the expense entry to delete. This is required.

    Returns:
        A text confirmation message indicating if the expense was deleted successfully.
    """
    if not expense_id:
        return "Error: Expense ID is required."
        
    success = database.delete_expense(expense_id)
    if success:
        return f"Success: Expense with ID {expense_id} deleted."
    else:
        return f"Error: Expense with ID {expense_id} not found."

def get_expense_summary_tool(start_date: str = None, end_date: str = None) -> str:
    """
    Retrieves and summarizes user expenses grouped by category over a specified date range.
    If no dates are provided, defaults to the current month's start up to today.

    Args:
        start_date: The start date in YYYY-MM-DD format (optional).
        end_date: The end date in YYYY-MM-DD format (optional).

    Returns:
        A formatted summary report listing category-wise expense breakdown, total expenditure, and percentages.
    """
    import datetime
    today = datetime.date.today()
    if not start_date:
        start_date = today.replace(day=1).strftime("%Y-%m-%d")
    if not end_date:
        end_date = today.strftime("%Y-%m-%d")
        
    chat_id = current_chat_id.get()
    
    summary = database.get_expenses_summary(chat_id, start_date, end_date)
    expenses = database.get_expenses(chat_id, start_date, end_date)
    
    if not expenses:
        return f"지정된 기간({start_date} ~ {end_date}) 동안 등록된 지출 내역이 없습니다."
        
    total_spent = sum(item['amount'] for item in expenses)
    
    lines = [
        f"📊 <b>지출 통계 요약 ({start_date} ~ {end_date})</b>\n",
        f"• 총 지출액: <b>{total_spent:,}원</b>\n",
        "<b>[카테고리별 지출 현황]</b>"
    ]
    
    category_emojis = {
        "식비": "🍔", "카페": "☕", "교통": "🚗", "쇼핑": "🛍️", "문화": "🎬", 
        "식비/카페": "🍔", "교통비": "🚗", "문화생활": "🎬", "의료": "💊", "주거": "🏠", 
        "교육": "📚", "통신": "📱", "기타": "💡"
    }
    
    for item in summary:
        cat = item['category']
        amt = item['total_amount']
        pct = (amt / total_spent) * 100 if total_spent > 0 else 0
        
        # Get matching emoji or default to generic ledger emoji
        emoji = "💸"
        for key, value in category_emojis.items():
            if key in cat:
                emoji = value
                break
                
        # Draw a simple text-based progress bar (scale of 5 steps)
        bar_length = int(round(pct / 20)) # 20% per block
        bar_str = "■" * bar_length + "░" * (5 - bar_length)
        
        lines.append(f"{emoji} <b>{cat}</b>: {amt:,}원 ({pct:.1f}%) {bar_str}")
        
    return "\n".join(lines)

def web_search(query: str, timelimit: str = None, max_results: int = 10) -> str:
    """
    Searches the web for current, real-time information, news, or general knowledge.

    Args:
        query: The search term or question to query (e.g., 'weather in Seoul today', 'latest tech news'). This is required.
        timelimit: Optional time limit for search ('d' for day, 'w' for week, 'm' for month, 'y' for year).
        max_results: Optional maximum number of results to return. Default is 10.

    Returns:
        A formatted string of search results including titles, links, and snippets.
    """
    if not query:
        return "Error: Search query is required."
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, timelimit=timelimit))
            if not results:
                return f"No web search results found for: '{query}'"
            
            lines = []
            for r in results:
                lines.append(f"Title: {r['title']}\nLink: {r['href']}\nSnippet: {r['body']}\n")
            return "\n".join(lines)
    except Exception as e:
        return f"Error during web search: {str(e)}"

def get_current_weather(location: str) -> str:
    """
    Retrieves the real-time weather information (temperature, humidity, apparent temperature, wind speed, precipitation, weather description) for a given location.

    Args:
        location: The name of the city/region in English (e.g. 'Seoul', 'Busan', 'Tokyo', 'New York', 'London'). Translate Korean names to English before calling this. This is required.

    Returns:
        A formatted string describing the current weather conditions, or an error message.
    """
    if not location:
        return "Error: Location is required."
    
    # Fallback mapping for common Korean city names to ensure geocoding matches
    korean_mapping = {
        "서울": "Seoul", "서울특별시": "Seoul", "서울시": "Seoul",
        "도쿄": "Tokyo", "동경": "Tokyo",
        "부산": "Busan", "부산광역시": "Busan",
        "인천": "Incheon", "인천광역시": "Incheon",
        "대구": "Daegu", "대구광역시": "Daegu",
        "대전": "Daejeon", "대전광역시": "Daejeon",
        "광주": "Gwangju", "광주광역시": "Gwangju",
        "울산": "Ulsan", "울산광역시": "Ulsan",
        "세종": "Sejong", "세종시": "Sejong",
        "제주": "Jeju", "제주도": "Jeju", "제주시": "Jeju"
    }
    
    loc_clean = unicodedata.normalize('NFC', location).strip()
    if loc_clean in korean_mapping:
        loc_clean = korean_mapping[loc_clean]
        
    # 1. Geocoding to get latitude & longitude
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={loc_clean}&count=1&language=ko"
    try:
        geo_resp = httpx.get(geo_url, timeout=10.0).json()
        results = geo_resp.get("results")
        if not results:
            return f"Error: Location '{location}' not found."
        
        loc_data = results[0]
        name = loc_data.get("name")
        country = loc_data.get("country", "")
        lat = loc_data.get("latitude")
        lon = loc_data.get("longitude")
        
        # 2. Fetch current weather conditions
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m&timezone=auto"
        w_resp = httpx.get(weather_url, timeout=10.0).json()
        current = w_resp.get("current")
        if not current:
            return f"Error: Failed to retrieve weather data for '{name}'."
        
        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        feels_like = current.get("apparent_temperature")
        precip = current.get("precipitation")
        wind = current.get("wind_speed_10m")
        code = current.get("weather_code")
        
        # WMO Weather Interpretation Codes
        weather_desc = {
            0: "맑음",
            1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
            45: "안개", 48: "침적 안개",
            51: "가벼운 이슬비", 53: "보통 이슬비", 55: "짙은 이슬비",
            61: "가벼운 비", 63: "보통 비", 65: "강한 비",
            71: "가벼운 눈", 73: "보통 눈", 75: "강한 눈",
            80: "가벼운 소나기", 81: "보통 소나기", 82: "강한 소나기",
            95: "뇌우", 96: "우박을 동반한 가벼운 뇌우", 99: "우박을 동반한 강한 뇌우"
        }
        desc = weather_desc.get(code, "알 수 없음")
        
        output = (
            f"지역: {name} ({country})\n"
            f"현재 기온: {temp}°C (체감 온도: {feels_like}°C)\n"
            f"습도: {humidity}%\n"
            f"날씨 상태: {desc}\n"
            f"강수량: {precip}mm\n"
            f"풍속: {wind} km/h"
        )
        return output
    except Exception as e:
        return f"Error fetching weather data: {str(e)}"

def list_google_calendar_events(time_min_str: str = None, time_max_str: str = None) -> str:
    """
    구글 캘린더에서 다가오는 일정 목록을 조회합니다.

    Args:
        time_min_str: 조회 시작 시각 (ISO 8601 포맷, 예: '2026-06-12T00:00:00Z'). 생략 시 현재 시각 기준.
        time_max_str: 조회 종료 시각 (ISO 8601 포맷, 예: '2026-06-12T23:59:59Z'). 옵션.

    Returns:
        조회된 일정을 일목요연하게 정리한 텍스트 또는 연동 안내 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('calendar', 'v3', credentials=creds)

        if not time_min_str:
            time_min_str = datetime.datetime.utcnow().isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min_str,
            timeMax=time_max_str,
            singleEvents=True,
            orderBy='startTime',
            maxResults=10
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return "구글 캘린더에 등록된 일정이 없습니다."

        lines = ["📅 <b>구글 캘린더 일정 목록:</b>"]
        for event in events:
            summary = event.get('summary', '제목 없음')
            summary_esc = html.escape(summary)
            start = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
            end = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
            
            # Format times for display (e.g. 2026-06-11 14:30)
            def format_time(t_str):
                if not t_str: return ""
                if 'T' in t_str:
                    return t_str.replace('T', ' ')[:16]
                return t_str # Date only
                
            start_f = format_time(start)
            end_f = format_time(end)
            time_range = f"{start_f} ~ {end_f}" if start_f and end_f else start_f
            
            desc = event.get('description', '')
            desc_str = f"\n  - 설명: {html.escape(desc)}" if desc else ""
            loc = event.get('location', '')
            loc_str = f"\n  - 장소: {html.escape(loc)}" if loc else ""
            
            lines.append(f"• <b>{summary_esc}</b> ({time_range}){loc_str}{desc_str}")

        return "\n".join(lines)
    except google_auth.GoogleAuthRequiredError as ae:
        # Re-raise so agent/main can catch it for authorization button flow
        raise ae
    except Exception as e:
        return f"구글 캘린더 일정을 조회하는 도중 오류가 발생했습니다: {str(e)}"

def normalize_iso_datetime(dt_str: str) -> str:
    """
    Normalizes a datetime string to ISO 8601 format (YYYY-MM-DDTHH:MM:SS) expected by Google Calendar API.
    Handles spaces, missing seconds, timezone suffixes, and formats like YYYY-MM-DD HH:MM.
    """
    if not dt_str:
        return dt_str
        
    dt_str = dt_str.strip()
    
    # 1. Replace space with T
    if " " in dt_str and "T" not in dt_str:
        dt_str = dt_str.replace(" ", "T")
        
    # 2. Add time component if just date
    if re.match(r'^\d{4}-\d{2}-\d{2}$', dt_str):
        dt_str = f"{dt_str}T00:00:00"
        
    # 3. Add seconds if missing (e.g. YYYY-MM-DDTHH:MM)
    match_no_seconds = re.match(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})([+-]\d{2}:?\d{2}|Z)?$', dt_str)
    if match_no_seconds:
        date_part = match_no_seconds.group(1)
        time_part = match_no_seconds.group(2)
        tz_part = match_no_seconds.group(3) or ""
        dt_str = f"{date_part}T{time_part}:00{tz_part}"
        
    return dt_str

def create_google_calendar_event(summary: str, start_time_iso: str, end_time_iso: str, description: str = None, location: str = None) -> str:
    start_time_iso = normalize_iso_datetime(start_time_iso)
    end_time_iso = normalize_iso_datetime(end_time_iso)
    """
    구글 캘린더에 새로운 일정을 등록합니다.

    Args:
        summary: 일정 제목 (예: '프로젝트 개발 미팅'). 필수.
        start_time_iso: 시작 일시 (ISO 8601 포맷, 예: '2026-06-12T14:00:00'). 필수.
        end_time_iso: 종료 일시 (ISO 8601 포맷, 예: '2026-06-12T15:00:00'). 필수.
        description: 일정 상세 설명 (옵션).
        location: 미팅 장소 또는 화상 회의 주소 (옵션).

    Returns:
        일정 등록 완료 성공 메시지 또는 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('calendar', 'v3', credentials=creds)

        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time_iso,
                'timeZone': 'Asia/Seoul',
            },
            'end': {
                'dateTime': end_time_iso,
                'timeZone': 'Asia/Seoul',
            },
        }

        if description:
            event['description'] = description
        if location:
            event['location'] = location

        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return f"Success: 구글 캘린더에 일정 '{summary}'이(가) 등록되었습니다. (링크: {created_event.get('htmlLink')})"
    except google_auth.GoogleAuthRequiredError as ae:
        raise ae
    except Exception as e:
        return f"구글 캘린더 일정을 생성하는 도중 오류가 발생했습니다: {str(e)}"

def list_unread_emails(max_results: int = 5) -> str:
    """
    지메일(Gmail)에서 읽지 않은 최근 이메일 목록을 조회합니다.

    Args:
        max_results: 최대 조회할 이메일 수. 기본값은 5개.

    Returns:
        이메일 발신인, 제목, 날짜, 요약 본문을 담은 리스트 텍스트 또는 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('gmail', 'v1', credentials=creds)

        results = service.users().messages().list(userId='me', q='is:unread', maxResults=max_results).execute()
        messages = results.get('messages', [])

        if not messages:
            return "읽지 않은 이메일이 없습니다."

        lines = ["📩 <b>읽지 않은 지메일 목록:</b>"]
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From', 'Subject', 'Date']).execute()
            
            headers = msg_data.get('payload', {}).get('headers', [])
            sender = "알 수 없는 발신자"
            subject = "제목 없음"
            date = ""
            for h in headers:
                if h['name'] == 'From':
                    sender = h['value']
                elif h['name'] == 'Subject':
                    subject = h['value']
                elif h['name'] == 'Date':
                    date = h['value']

            snippet = msg_data.get('snippet', '')
            sender_esc = html.escape(sender)
            subject_esc = html.escape(subject)
            snippet_esc = html.escape(snippet)
            
            lines.append(
                f"• <b>보낸 사람</b>: {sender_esc}\n"
                f"  <b>제목</b>: {subject_esc}\n"
                f"  <b>날짜</b>: {date}\n"
                f"  <b>내용 요약</b>: {snippet_esc}...\n"
            )

        return "\n".join(lines)
    except google_auth.GoogleAuthRequiredError as ae:
        raise ae
    except Exception as e:
        return f"지메일을 조회하는 도중 오류가 발생했습니다: {str(e)}"

def send_email_via_gmail(to_email: str, subject: str, body: str) -> str:
    """
    지메일(Gmail)을 통해 새로운 이메일을 발송합니다.

    Args:
        to_email: 수신자 이메일 주소 (예: 'recipient@example.com'). 필수.
        subject: 이메일 제목. 필수.
        body: 이메일 본문 내용. 필수.

    Returns:
        이메일 발송 완료 메시지 또는 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('gmail', 'v1', credentials=creds)

        message = MIMEText(body)
        message['to'] = to_email
        message['subject'] = subject
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw_message}).execute()
        return f"Success: {to_email} 주소로 이메일 발송을 완료했습니다."
    except google_auth.GoogleAuthRequiredError as ae:
        raise ae
    except Exception as e:
        return f"이메일을 발송하는 도중 오류가 발생했습니다: {str(e)}"

def search_emails_via_gmail(query: str, max_results: int = 5) -> str:
    """
    지메일(Gmail)에서 특정 키워드나 발신자를 기준으로 이메일을 검색합니다.

    Args:
        query: 지메일 검색어 (예: 'from:boss', 'meeting', 'project update'). 필수.
        max_results: 최대 검색 결과 개수. 기본값은 5개.

    Returns:
        검색된 이메일 목록 텍스트 또는 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('gmail', 'v1', credentials=creds)

        results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
        messages = results.get('messages', [])

        if not messages:
            return f"검색어 '{query}'에 해당하는 이메일을 찾지 못했습니다."

        lines = [f"🔍 <b>지메일 검색 결과 (검색어: '{html.escape(query)}'):</b>"]
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From', 'Subject', 'Date']).execute()
            
            headers = msg_data.get('payload', {}).get('headers', [])
            sender = "알 수 없는 발신자"
            subject = "제목 없음"
            date = ""
            for h in headers:
                if h['name'] == 'From':
                    sender = h['value']
                elif h['name'] == 'Subject':
                    subject = h['value']
                elif h['name'] == 'Date':
                    date = h['value']

            snippet = msg_data.get('snippet', '')
            sender_esc = html.escape(sender)
            subject_esc = html.escape(subject)
            snippet_esc = html.escape(snippet)
            
            lines.append(
                f"• <b>보낸 사람</b>: {sender_esc}\n"
                f"  <b>제목</b>: {subject_esc}\n"
                f"  <b>날짜</b>: {date}\n"
                f"  <b>내용 요약</b>: {snippet_esc}...\n"
            )

        return "\n".join(lines)
    except google_auth.GoogleAuthRequiredError as ae:
        raise ae
    except Exception as e:
        return f"지메일을 검색하는 도중 오류가 발생했습니다: {str(e)}"

def add_gmail_alert_filter(filter_type: str, value: str) -> str:
    """
    지메일 알림을 받을 필터링 규칙(보낸 사람 이메일 주소 또는 제목 키워드)을 등록합니다.
    이 규칙에 매칭되는 이메일이 오면 백그라운드에서 실시간 텔레그램 알림을 발송합니다.

    Args:
        filter_type: 필터 종류. 'sender'(발신자 이메일 주소) 또는 'keyword'(제목 키워드)만 가능. 필수.
        value: 필터링할 구체적인 값 (예: 'boss@company.com' 또는 '긴급'). 필수.

    Returns:
        규칙이 성공적으로 추가되었다는 확인 메시지.
    """
    if filter_type not in ['sender', 'keyword']:
        return "Error: filter_type은 'sender'(보낸 사람 이메일) 또는 'keyword'(제목 키워드) 중 하나여야 합니다."
    if not value:
        return "Error: value(필터링할 값)는 비어 있을 수 없습니다."
        
    chat_id = current_chat_id.get()
    database.add_gmail_filter(chat_id, filter_type, value)
    
    type_ko = "발신자" if filter_type == "sender" else "제목 키워드"
    return f"Success: 지메일 알림 대상 필터에 {type_ko} '{value}'이(가) 등록되었습니다."

def delete_gmail_alert_filter(filter_type: str, value: str) -> str:
    """
    등록된 지메일 알림 필터링 규칙을 삭제합니다.

    Args:
        filter_type: 필터 종류. 'sender' 또는 'keyword'. 필수.
        value: 삭제할 필터링 값 (예: 'boss@company.com' 또는 '긴급'). 필수.

    Returns:
        규칙 삭제 성공 혹은 실패 메시지.
    """
    if filter_type not in ['sender', 'keyword']:
        return "Error: filter_type은 'sender' 또는 'keyword' 중 하나여야 합니다."
        
    chat_id = current_chat_id.get()
    success = database.delete_gmail_filter(chat_id, filter_type, value)
    
    if success:
        type_ko = "발신자" if filter_type == "sender" else "제목 키워드"
        return f"Success: 지메일 알림 대상 필터에서 {type_ko} '{value}'이(가) 삭제되었습니다."
    else:
        return f"Error: 등록된 필터 중 '{filter_type}' 타입의 '{value}' 값을 찾을 수 없습니다."

def list_gmail_alert_filters() -> str:
    """
    현재 등록되어 있는 모든 지메일 알림 필터 목록을 조회합니다.

    Returns:
        현재 등록된 지메일 필터 리스트 텍스트.
    """
    chat_id = current_chat_id.get()
    filters = database.get_gmail_filters(chat_id)
    
    if not filters:
        return "현재 등록된 지메일 알림 대상 필터가 없습니다. 자연어로 'test@example.com 에서 온 메일 알림 등록해줘' 처럼 요청하여 등록해 보세요!"
        
    lines = ["📋 <b>지메일 실시간 알림 필터 목록:</b>"]
    for f in filters:
        type_ko = "발신자" if f['filter_type'] == "sender" else "제목 키워드"
        lines.append(f"• [{type_ko}] <code>{f['value']}</code>")
        
    return "\n".join(lines)

def set_user_setting_tool(key: str, value: str) -> str:
    """
    사용자의 개인화 설정(아침 브리핑 시각, 날씨 조회용 거주 지역 등)을 설정하거나 변경합니다.

    Args:
        key: 설정할 항목 이름. 'briefing_time'(브리핑 시각) 또는 'location'(날씨용 거주지)만 가능. 필수.
        value: 설정할 값.
               - briefing_time인 경우: 'HH:MM' 24시간 형식 (예: '07:30' 또는 '08:00').
               - location인 경우: 영어 도시명 (예: 'Seoul', 'Busan', 'Tokyo'). 필수.

    Returns:
        설정 변경 완료 확인 메시지.
    """
    if key not in ['briefing_time', 'location']:
        return "Error: key는 'briefing_time' 또는 'location'만 설정 가능합니다."
        
    chat_id = current_chat_id.get()
    
    # 값 검증
    if key == 'briefing_time':
        try:
            parts = value.split(':')
            if len(parts) != 2: raise ValueError()
            h = int(parts[0])
            m = int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59): raise ValueError()
            value = f"{h:02d}:{m:02d}"
        except ValueError:
            return "Error: briefing_time은 올바른 24시간 형식의 'HH:MM' (예: '07:30')이어야 합니다."
    elif key == 'location':
        if not value:
            return "Error: location 값은 비어있을 수 없습니다."
            
    database.save_user_setting(chat_id, key, value)
    
    key_ko = "아침 브리핑 시각" if key == "briefing_time" else "날씨 조회 지역"
    return f"Success: 사용자의 {key_ko}이(가) '{value}'(으)로 성공적으로 설정되었습니다."

def get_user_settings_tool() -> str:
    """
    현재 사용자의 개인화 설정(브리핑 시각 및 날씨 조회 지역) 상태를 조회합니다.

    Returns:
        현재 유저 설정 값을 포맷팅한 정보 메시지.
    """
    chat_id = current_chat_id.get()
    settings = database.get_user_settings(chat_id)
    
    return (
        f"📋 <b>현재 개인화 비서 설정 정보:</b>\n"
        f"• <b>아침 브리핑 시각</b>: <code>{settings['briefing_time']}</code>\n"
        f"• <b>날씨 조회 거주지</b>: <code>{settings['location']}</code>"
    )

def delete_google_calendar_event(event_id: str) -> str:
    """
    구글 캘린더에서 특정 일정을 삭제합니다.
    일정을 삭제하려면 먼저 일정 목록을 조회(list_google_calendar_events)하여 해당 일정의 event_id를 획득해야 합니다.

    Args:
        event_id: 삭제할 일정의 고유 ID (예: 'abc123xyz...'). 필수.

    Returns:
        일정 삭제 성공 또는 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('calendar', 'v3', credentials=creds)

        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return f"Success: 구글 캘린더에서 ID가 '{event_id}'인 일정을 성공적으로 삭제했습니다."
    except google_auth.GoogleAuthRequiredError as ae:
        raise ae
    except Exception as e:
        return f"구글 캘린더 일정을 삭제하는 도중 오류가 발생했습니다: {str(e)}"

def update_google_calendar_event(
    event_id: str, 
    summary: str = None, 
    start_time_iso: str = None, 
    end_time_iso: str = None, 
    description: str = None, 
    location: str = None
) -> str:
    """
    구글 캘린더의 기존 일정을 수정(업데이트)합니다.
    일정을 수정하려면 먼저 일정 목록을 조회(list_google_calendar_events)하여 해당 일정의 event_id를 획득해야 합니다.

    Args:
        event_id: 수정할 일정의 고유 ID (예: 'abc123xyz...'). 필수.
        summary: 변경할 일정 제목 (옵션).
        start_time_iso: 변경할 시작 일시 (ISO 8601 포맷, 예: '2026-06-12T14:00:00') (옵션).
        end_time_iso: 변경할 종료 일시 (ISO 8601 포맷, 예: '2026-06-12T15:00:00') (옵션).
        description: 변경할 상세 설명 (옵션).
        location: 변경할 장소 (옵션).

    Returns:
        일정 수정 완료 성공 메시지 또는 오류 메시지.
    """
    try:
        chat_id = current_chat_id.get()
        creds = google_auth.get_google_credentials(chat_id)
        service = build('calendar', 'v3', credentials=creds)

        event = service.events().get(calendarId='primary', eventId=event_id).execute()

        if summary:
            event['summary'] = summary
        if start_time_iso:
            start_time_iso = normalize_iso_datetime(start_time_iso)
            event['start'] = {'dateTime': start_time_iso, 'timeZone': 'Asia/Seoul'}
        if end_time_iso:
            end_time_iso = normalize_iso_datetime(end_time_iso)
            event['end'] = {'dateTime': end_time_iso, 'timeZone': 'Asia/Seoul'}
        if description is not None:
            event['description'] = description
        if location is not None:
            event['location'] = location

        updated_event = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
        return f"Success: 구글 캘린더 일정 '{updated_event.get('summary')}'(이)가 성공적으로 수정되었습니다."
    except google_auth.GoogleAuthRequiredError as ae:
        raise ae
    except Exception as e:
        return f"구글 캘린더 일정을 수정하는 도중 오류가 발생했습니다: {str(e)}"

# News Operations & Crawling Tools
def add_news_keyword_tool(keyword: str) -> str:
    """
    사용자의 주요 뉴스 개인화 피드를 위한 관심 뉴스 키워드를 등록합니다.
    이 키워드들은 실시간 맞춤 뉴스 조회 및 아침 브리핑에 연동됩니다.

    Args:
        keyword: 등록할 관심 뉴스 키워드 (예: '인공지능', '부동산', '주식'). 필수.

    Returns:
        성공 여부 확인 메시지.
    """
    if not keyword:
        return "Error: 키워드는 비어 있을 수 없습니다."
    chat_id = current_chat_id.get()
    success = database.add_news_keyword(chat_id, keyword.strip())
    if success:
        return f"Success: 뉴스 관심 키워드에 '{keyword}'이(가) 추가되었습니다."
    else:
        return f"Info: 뉴스 관심 키워드 '{keyword}'은(는) 이미 등록되어 있습니다."

def delete_news_keyword_tool(keyword: str) -> str:
    """
    등록된 관심 뉴스 키워드를 삭제합니다.

    Args:
        keyword: 삭제할 관심 뉴스 키워드. 필수.

    Returns:
        성공 여부 확인 메시지.
    """
    if not keyword:
        return "Error: 키워드는 비어 있을 수 없습니다."
    chat_id = current_chat_id.get()
    success = database.delete_news_keyword(chat_id, keyword.strip())
    if success:
        return f"Success: 뉴스 관심 키워드에서 '{keyword}'이(가) 삭제되었습니다."
    else:
        return f"Error: 등록된 뉴스 관심 키워드 중 '{keyword}'을(를) 찾을 수 없습니다."

def list_news_keywords_tool() -> str:
    """
    현재 등록되어 있는 뉴스 관심 키워드 목록을 조회합니다.

    Returns:
        관심 키워드 리스트 텍스트.
    """
    chat_id = current_chat_id.get()
    keywords = database.get_news_keywords(chat_id)
    if not keywords:
        return "현재 등록된 뉴스 관심 키워드가 없습니다. 자연어로 '뉴스 키워드에 AI 추가해줘'와 같이 요청해 보세요!"
    return f"📋 <b>등록된 뉴스 관심 키워드 목록:</b>\n" + "\n".join([f"• {kw}" for kw in keywords])

def fetch_news_article_text(url: str) -> str:
    """
    주어진 뉴스 기사 URL에서 본문 텍스트를 크롤링하여 정제합니다.
    User-Agent 헤더를 위장하여 차단을 방지하고 BeautifulSoup로 광고 등을 제거합니다.
    """
    import urllib.request
    from bs4 import BeautifulSoup
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        # Timeout set to 8 seconds
        with urllib.request.urlopen(req, timeout=8.0) as response:
            html = response.read()
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
            script.decompose()
            
        # Try to find main article content wrappers by common CSS classes/IDs
        content_selectors = [
            'article', 'div#articleBodyContents', 'div#articleBody', 'div.article_body', 
            'div.news_post_body', 'div#newsct_article', 'div.story-content', 'div.article-body'
        ]
        
        body_text = ""
        for sel in content_selectors:
            target = soup.select_one(sel)
            if target:
                body_text = target.get_text()
                break
                
        # If no common wrappers matched, fallback to soup.get_text()
        if not body_text.strip():
            body_text = soup.get_text()
            
        # Clean text: normalize spaces
        lines = (line.strip() for line in body_text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Filter out very short lines (often layout remnants) and merge
        text = "\n".join(chunk for chunk in chunks if len(chunk) > 20)
        
        # Limit text length to around 3000 chars for LLM safety
        return text[:3000].strip()
        
    except Exception as e:
        raise e

def parse_news_results(search_res: str) -> list[dict]:
    """Parses web_search raw output into structured dictionary list."""
    articles = []
    current = {}
    for line in search_res.split("\n"):
        line = line.strip()
        if line.startswith("Title:"):
            if current and 'title' in current and 'href' in current:
                articles.append(current)
            current = {'title': line[6:].strip()}
        elif line.startswith("Link:") and current:
            current['href'] = line[5:].strip()
        elif line.startswith("Snippet:") and current:
            current['body'] = line[8:].strip()
    if current and 'title' in current and 'href' in current:
        articles.append(current)
    return articles


def is_individual_article_url(url: str) -> bool:
    """
    Checks if a URL refers to an individual news article rather than
    a portal category, list page, or personal/wiki/docs website.
    """
    if not url or not isinstance(url, str) or not url.strip().lower().startswith(('http://', 'https://')):
        return False
        
    blacklist_domains = [
        'blog.naver.com', 'tistory.com', 'egloos.com', 'namu.wiki', 
        'wikipedia.org', 'wikidocs.net', 'blogspot.com', 'velog.io', 
        'github.io', 'brunch.co.kr', 'cafe.naver.com', 'cafe.daum.net',
        'clien.net', 'dcinside.com', 'ppomppu.co.kr', 'ruliweb.com',
        'fmkorea.com', 'inven.co.kr', 'slrclub.com', 'todayhumor.co.kr',
        'mlbpark.donga.com'
    ]
    blacklist_patterns = [
        'news.naver.com/main/main',
        'news.daum.net/tech', 'news.daum.net/politics', 'news.daum.net/economic', 'news.daum.net/society', 'news.daum.net/foreign', 'news.daum.net/culture',
        'news.nate.com/it', 'news.nate.com/politics', 'news.nate.com/economy', 'news.nate.com/society', 'news.nate.com/world', 'news.nate.com/recent',
        'www.hankyung.com/all-news',
        'www.asiae.co.kr/list',
        'www.hani.co.kr/arti/science_general', 'www.hani.co.kr/arti/politics', 'www.hani.co.kr/arti/economy',
        'www.yna.co.kr/news',
        '/list/', '/category/', '/section/', '/all-news', '/recent'
    ]
    url_lower = url.lower()
    
    # Check domain blacklist
    parsed_url = urlparse(url)
    netloc = parsed_url.netloc.lower()
    for d in blacklist_domains:
        if d in netloc:
            return False
            
    # Check blacklist patterns
    for pattern in blacklist_patterns:
        if pattern in url_lower:
            return False
            
    # Check indicators of article
    if any(p in url_lower for p in ['/article/', '/view/', '/v/', '/read/', 'article.html', 'view.html', 'news.naver.com/mnews/article']):
        return True
        
    # Match 6+ digit numbers in path or query
    if re.search(r'/\d{6,}', url_lower) or re.search(r'id=\d{6,}', url_lower):
        return True
        
    if url_lower.endswith(('.html', '.htm', '.shtml')):
        return True
        
    path = parsed_url.path.strip('/')
    if not path or len(path.split('/')) <= 1:
        return False
        
    return True


def remove_duplicates_by_title(articles: list[dict]) -> list[dict]:
    """Filters out duplicate articles by normalizing and checking title similarity."""
    if not articles:
        return []
    seen_simplified = set()
    unique = []
    for art in articles:
        title = art.get('title', '')
        # Remove brackets like [종합], (상보), etc.
        cleaned_title = re.sub(r'\[.*?\]|\(.*?\)', '', title)
        # Keep only alphanumeric characters
        simplified = "".join(c for c in cleaned_title if c.isalnum()).strip()
        if not simplified:
            simplified = title
        if simplified not in seen_simplified:
            seen_simplified.add(simplified)
            unique.append(art)
    return unique


def parse_and_filter_news_results(search_res: str) -> list[dict]:
    """Parses web_search raw output, filters for article URLs, and removes duplicates."""
    articles = parse_news_results(search_res)
    filtered = [art for art in articles if is_individual_article_url(art['href'])]
    filtered = remove_duplicates_by_title(filtered)
    return filtered


async def fetch_and_filter_keyword_news(kw: str, delay: float = 0.0) -> list[dict]:
    """Helper to fetch and filter articles for a news keyword with 24h limit and 1w fallback."""
    if delay > 0:
        await asyncio.sleep(delay)
        
    query_text = f"{kw} 뉴스"
    loop = asyncio.get_running_loop()
    # Try 24h limit first
    search_res = await loop.run_in_executor(
        None,
        lambda: web_search(query_text, timelimit='d', max_results=20)
    )
    articles = parse_and_filter_news_results(search_res)
    
    # Filter sent news
    try:
        chat_id = current_chat_id.get()
        if chat_id:
            sent_urls = database.get_recent_sent_news_urls(chat_id, days_limit=3)
            fresh_articles = [art for art in articles if art['href'].strip() not in sent_urls]
            if fresh_articles:
                articles = fresh_articles
    except Exception:
        pass
    
    # Fallback to 1 week limit if no articles
    if not articles or "No web search results found" in search_res:
        search_res = await loop.run_in_executor(
            None,
            lambda: web_search(query_text, timelimit='w', max_results=20)
        )
        articles = parse_and_filter_news_results(search_res)
        
        # Filter sent news
        try:
            chat_id = current_chat_id.get()
            if chat_id:
                sent_urls = database.get_recent_sent_news_urls(chat_id, days_limit=3)
                fresh_articles = [art for art in articles if art['href'].strip() not in sent_urls]
                if fresh_articles:
                    articles = fresh_articles
        except Exception:
            pass
        
    return articles


async def fetch_and_filter_category_news(query_text: str, delay: float = 0.0) -> list[dict]:
    """Helper to fetch and filter articles for a news category with 24h limit and 1w fallback."""
    if delay > 0:
        await asyncio.sleep(delay)
        
    loop = asyncio.get_running_loop()
    # Try 24h limit first
    search_res = await loop.run_in_executor(
        None,
        lambda: web_search(query_text, timelimit='d', max_results=20)
    )
    articles = parse_and_filter_news_results(search_res)
    
    # Filter sent news
    try:
        chat_id = current_chat_id.get()
        if chat_id:
            sent_urls = database.get_recent_sent_news_urls(chat_id, days_limit=3)
            fresh_articles = [art for art in articles if art['href'].strip() not in sent_urls]
            if fresh_articles:
                articles = fresh_articles
    except Exception:
        pass
    
    # Fallback to 1 week limit if no articles
    if not articles or "No web search results found" in search_res:
        search_res = await loop.run_in_executor(
            None,
            lambda: web_search(query_text, timelimit='w', max_results=20)
        )
        articles = parse_and_filter_news_results(search_res)
        
        # Filter sent news
        try:
            chat_id = current_chat_id.get()
            if chat_id:
                sent_urls = database.get_recent_sent_news_urls(chat_id, days_limit=3)
                fresh_articles = [art for art in articles if art['href'].strip() not in sent_urls]
                if fresh_articles:
                    articles = fresh_articles
        except Exception:
            pass
        
    return articles



def add_dday_tool(title: str, target_date: str) -> str:
    """
    Creates and registers a new D-Day or Anniversary event.

    Args:
        title: The name of the event (e.g., 'My Birthday', 'Project Deadline'). This is required.
        target_date: The target date of the event in 'YYYY-MM-DD' format (e.g., '2026-07-20'). This is required.

    Returns:
        A confirmation message indicating if the D-Day was registered successfully.
    """
    if not title:
        return "Error: Event title is required."
    if not target_date:
        return "Error: Target date is required."
        
    try:
        datetime.datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return "Error: Target date must be in YYYY-MM-DD format (e.g. 2026-06-30)."
        
    chat_id = current_chat_id.get()
    dday_id = database.add_dday(chat_id, title, target_date)
    return f"Success: Registered D-Day '{title}' on {target_date} with ID {dday_id}."

def list_ddays_tool() -> str:
    """
    Lists all registered D-Days and Anniversaries for the user, showing days remaining.

    Returns:
        A formatted HTML list of all D-Days with their remaining days (e.g. D-5, D-Day, D+3),
        or a message indicating no D-Days are registered.
    """
    chat_id = current_chat_id.get()
    ddays = database.get_ddays(chat_id)
    if not ddays:
        return "등록된 D-Day 또는 기념일이 없습니다."
        
    kst_tz = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(kst_tz).date()
    
    lines = ["📅 <b>등록된 D-Day 및 기념일 목록</b>\n"]
    for d in ddays:
        try:
            target_dt = datetime.datetime.strptime(d['target_date'], "%Y-%m-%d").date()
            diff = (target_dt - today).days
            if diff > 0:
                dday_str = f"D-{diff}"
            elif diff == 0:
                dday_str = "D-Day"
            else:
                dday_str = f"D+{abs(diff)}"
                
            lines.append(f"• <b>{html.escape(d['title'])}</b> (<code>{d['target_date']}</code>): <code>{dday_str}</code> [ID: <code>{d['id']}</code>]")
        except Exception as e:
            lines.append(f"• <b>{html.escape(d['title'])}</b> (<code>{d['target_date']}</code>): 에러 (ID: <code>{d['id']}</code>)")
            
    return "\n".join(lines)

def delete_dday_tool(dday_id: int) -> str:
    """
    Deletes a registered D-Day or Anniversary event by its ID.

    Args:
        dday_id: The unique integer ID of the D-Day to delete. This is required.

    Returns:
        A confirmation message indicating if the D-Day was successfully deleted.
    """
    if not dday_id:
        return "Error: D-Day ID is required."
        
    success = database.delete_dday(dday_id)
    if success:
        return f"Success: Deleted D-Day with ID {dday_id}."
    else:
        return f"Error: D-Day with ID {dday_id} not found."


def propose_travel_itinerary(destination: str, start_date: str, end_date: str, events: list[dict]) -> str:
    """
    AI가 설계한 맞춤 여행 일정을 사용자에게 제안하고 검토할 수 있도록 임시 저장합니다.
    사용자가 동의하면 구글 캘린더에 일괄 등록 버튼을 생성할 수 있습니다.

    Args:
        destination: 여행 목적지 도시명 (예: 'Tokyo', 'Paris'). 필수.
        start_date: 여행 시작일 (YYYY-MM-DD 포맷, 예: '2026-07-15'). 필수.
        end_date: 여행 종료일 (YYYY-MM-DD 포맷, 예: '2026-07-17'). 필수.
        events: 일자별 세부 일정 객체 리스트. 필수.
                각 객체 필수 키: 'summary'(일정 제목), 'start_time_iso'(시작 일시 ISO 8601 포맷, 예: '2026-07-15T10:00:00'), 'end_time_iso'(종료 일시 ISO 8601 포맷, 예: '2026-07-15T11:30:00').
                선택 키: 'description'(세부 설명), 'location'(구체적인 위치).

    Returns:
        성공 여부 및 임시 저장된 플랜 ID 정보 메시지.
    """
    import json
    if not destination or not start_date or not end_date or not events:
        return "Error: destination, start_date, end_date, events는 필수 입력값입니다."
        
    chat_id = current_chat_id.get()
    events_json = json.dumps(events, ensure_ascii=False)
    
    try:
        plan_id = database.save_pending_travel_plan(chat_id, destination, start_date, end_date, events_json)
        return f"Success: 여행 일정을 임시 저장했습니다. (플랜 ID: {plan_id}). 사용자가 아래 [📅 구글 캘린더에 전체 등록] 버튼을 누르면 이 일정들이 캘린더에 일괄 등록됩니다."
    except Exception as e:
        return f"Error: 여행 일정을 임시 저장하는 데 실패했습니다. {str(e)}"






