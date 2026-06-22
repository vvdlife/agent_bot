import os
import sys
import json
import html
from functools import wraps

# Add the project root directory to sys.path to allow running this script directly
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import logging
from logging.handlers import RotatingFileHandler
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.request import HTTPXRequest
from telegram.error import TelegramError
from src.config import Config
from src import database, agent, tools, quiz_helper

# Enable logging to console and rotating file
log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# Rotating file handler
log_dir = os.path.dirname(Config.DATABASE_PATH)
log_file = os.path.join(log_dir, "agent.log") if log_dir else "agent.log"
try:
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
except Exception as e:
    print(f"Failed to initialize file logger: {e}", file=sys.stderr)

logger = logging.getLogger(__name__)

def get_main_keyboard():
    """Create the persistent bottom keyboard menu."""
    keyboard = [
        [KeyboardButton("📋 나의 할 일"), KeyboardButton("📅 오늘의 일정")],
        [KeyboardButton("📩 새 메일 요약"), KeyboardButton("🌦️ 실시간 날씨")],
        [KeyboardButton("⚙️ 비서 설정"), KeyboardButton("📰 주요 뉴스")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def clean_unsupported_html(text: str) -> str:
    """
    Cleans and converts unsupported HTML tags (like <ul>, <li>, <br>)
    to Telegram-supported equivalents (newlines and bullet points).
    """
    if not text:
        return ""
        
    # Preprocess <a> tags: normalize quotes to double quotes and safely escape raw ampersands in URLs.
    import re
    import html as html_lib
    
    def replace_href(match):
        url = match.group(2)
        url_dec = html_lib.unescape(url)
        url_enc = html_lib.escape(url_dec).replace('"', '&quot;').replace("'", '&#x27;')
        return f'<a href="{url_enc}">'
        
    text = re.sub(r'<a\s+href=(["\'])(.*?)\1>', replace_href, text)

    # Replace list items with bullets
    text = text.replace("<li>", "• ").replace("</li>", "\n")
    # Remove list wrapper tags
    text = text.replace("<ul>", "").replace("</ul>", "")
    text = text.replace("<ol>", "").replace("</ol>", "")
    # Convert line breaks and paragraph tags
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("<p>", "").replace("</p>", "\n")
    text = text.replace("<div>", "").replace("</div>", "\n")
    
    # Normalize lines to strip trailing spaces
    lines = text.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    text = "\n".join(cleaned_lines)
    
    # Compress multiple newlines
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
        
    return text.strip()

async def send_safe_message(
    chat_id=None,
    bot=None,
    update=None,
    query=None,
    text="",
    reply_markup=None,
    parse_mode="HTML"
):
    """
    Sends or edits a Telegram message safely using HTML. If it fails, falls back to plain text.
    """
    # Pre-process text to convert unsupported HTML tags if parse_mode is HTML
    if parse_mode == "HTML" and text:
        text = await clean_unsupported_html(text)
    try:
        if query:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update and update.message:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif bot and chat_id:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            logger.error("send_safe_message: Missing target context to send message.")
    except TelegramError as e:
        logger.warning(f"Telegram error with parse_mode={parse_mode}: {e}. Retrying as plain text...")
        # Strip common HTML tags
        plain_text = text
        for tag in ["<b>", "</b>", "<i>", "</i>", "<code>", "</code>", "<pre>", "</pre>"]:
            plain_text = plain_text.replace(tag, "")
        plain_text = plain_text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        
        try:
            if query:
                await query.edit_message_text(text=plain_text, reply_markup=reply_markup)
            elif update and update.message:
                await update.message.reply_text(text=plain_text, reply_markup=reply_markup)
            elif bot and chat_id:
                await bot.send_message(chat_id=chat_id, text=plain_text, reply_markup=reply_markup)
        except Exception as retry_err:
            if "Chat not found" in str(retry_err) or "Forbidden" in str(retry_err):
                logger.warning(f"Failed plain text fallback for chat {chat_id}: {retry_err} (Chat not found or bot blocked)")
            else:
                logger.error(f"Failed plain text fallback: {retry_err}", exc_info=True)


def is_user_allowed(update: Update) -> bool:
    if not Config.ALLOWED_USERS:
        return True
    
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
        
    user_id = str(user.id)
    chat_id = str(chat.id)
    username = user.username.lower() if user.username else ""
    
    if user_id in Config.ALLOWED_USERS or chat_id in Config.ALLOWED_USERS:
        return True
        
    if username:
        if username in Config.ALLOWED_USERS or f"@{username}" in Config.ALLOWED_USERS:
            return True
            
    return False


def whitelist_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not is_user_allowed(update):
            chat_id = update.effective_chat.id if update.effective_chat else None
            user = update.effective_user
            user_info = f"ID: {user.id}" if user else "Unknown"
            username_info = f"username: {user.username}" if user and user.username else ""
            logger.warning(f"Unauthorized access attempt by user ({user_info}, {username_info}) in chat {chat_id}")
            
            text = "⚠️ <b>접근 권한이 없습니다.</b>\n\n이 봇은 승인된 사용자만 이용할 수 있도록 보안 설정되어 있습니다. 봇 관리자에게 본인의 Telegram Chat ID나 Username 등록을 요청하세요."
            if update.callback_query:
                await update.callback_query.answer(text="접근 권한이 없습니다.", show_alert=True)
            elif update.message:
                await update.message.reply_text(text, parse_mode="HTML")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


@whitelist_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    logger.info(f"=== /start Command Received from Chat {chat_id} ({user}) ===")
    await update.message.reply_text(
        f"안녕하세요, {user}님! 저는 당신의 개인 비서 AI 에이전트입니다.\n"
        f"일정 관리, 할 일 추가, 메모 저장 등을 자연스럽게 말하듯이 요청해주세요.\n\n"
        f"예시:\n"
        f"- '내일 오후 3시 회의 준비하기 할 일로 등록해줘'\n"
        f"- '할 일 목록 보여줘'\n"
        f"- '오늘 일기: 텔레그램 봇을 성공적으로 개발함 (태그: 일상)'\n"
        f"- '일상 태그가 달린 노트 찾아줘'",
        reply_markup=get_main_keyboard()
    )
    logger.info("Sent welcome message with main keyboard.")

@whitelist_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    chat_id = update.effective_chat.id
    logger.info(f"=== /help Command Received from Chat {chat_id} ===")
    await update.message.reply_text(
        "지원하는 기능 및 명령어:\n"
        "/start - 에이전트 대화 시작\n"
        "/help - 도움말 출력\n"
        "/tasks - 현재 진행 중인 할 일 버튼식으로 관리\n"
        "/settings - 아침 브리핑, 지메일 알림 등 설정 관리\n"
        "/login - 구글 계정(캘린더/지메일) 연동\n"
        "/logout - 구글 계정 연동 해제\n\n"
        "자연어로 다음과 같이 입력해보세요:\n"
        "- 할 일 추가: '우유 사기 할 일로 등록해줘'\n"
        "- 할 일 목록 확인: '할 일 리스트 보여줘'\n"
        "- 실시간 검색: '오늘 서울 날씨 어때?'\n"
        "- 구글 캘린더 일정 조회: '내일 내 일정 보여줘'\n"
        "- 구글 캘린더 일정 등록: '내일 오후 3시 프로젝트 미팅 일정 등록해줘'\n"
        "- 읽지 않은 지메일 요약: '지메일 안읽은 메일 요약해줘'\n"
        "- 이메일 발송: 'test@example.com으로 이메일 보내줘'\n"
        "- 음성 명령: 음성 메시지로 요청을 녹음해 보세요!",
        reply_markup=get_main_keyboard()
    )
    logger.info("Sent help message.")

@whitelist_only
async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display pending tasks with inline buttons for completion."""
    chat_id = update.effective_chat.id
    logger.info(f"=== /tasks Command Received from Chat {chat_id} ===")
    tasks = database.list_tasks(status="pending")
    
    if not tasks:
        logger.info("No pending tasks found. Sending empty state UI.")
        keyboard = [[InlineKeyboardButton(text="🔄 새로고침", callback_data="refresh")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            "📋 <b>진행 중인 할 일이 없습니다.</b> 👍\n\n"
            "할 일을 등록하려면 자연어로 봇에게 채팅을 보내보세요!\n"
            "예시:\n"
            "- <i>\"우유 사기 할 일 추가해줘\"</i>\n"
            "- <i>\"내일 아침 9시 치과 예약 등록하기\"</i>\n\n"
            "등록하신 후 아래 [🔄 새로고침] 버튼을 누르면 목록이 업데이트됩니다."
        )
        await send_safe_message(
            update=update,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return
    
    logger.info(f"Found {len(tasks)} pending tasks. Displaying task list.")
    keyboard = []
    text_lines = ["📋 <b>현재 진행 중인 할 일 목록:</b>\n"]
    for idx, t in enumerate(tasks, start=1):
        title_esc = html.escape(t['title'])
        desc_esc = f" - {html.escape(t['description'])}" if t['description'] else ""
        due_esc = f" (기한: {html.escape(t['due_date'])})" if t['due_date'] else ""
        text_lines.append(f"• <b>#{idx}</b>: {title_esc}{desc_esc}{due_esc}")
        
        button = InlineKeyboardButton(
            text=f"✅ #{idx} 완료",
            callback_data=f"complete:{t['id']}"
        )
        keyboard.append([button])
    
    keyboard.append([InlineKeyboardButton(text="🔄 새로고침", callback_data="refresh")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_safe_message(
        update=update,
        text="\n".join(text_lines),
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    logger.info("Task list sent.")

@whitelist_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle task completion and list refreshing from inline buttons."""
    query = update.callback_query
    
    # Answer callback query to remove loading spinner.
    # Some callbacks provide custom alerts/toasts, so we only answer here if not handled individually.
    data = query.data
    custom_callbacks = ["dday_clear", "news_cat:", "news_my", "travel_apply:", "quiz_start:", "quiz_ans:", "quiz_add_more:", "quiz_review_start"]
    if not any(data.startswith(c) for c in custom_callbacks):
        await query.answer()
        
    chat_id = query.message.chat_id
    logger.info(f"=== Callback Query Received: '{data}' ===")
    
    # 1. Expense operations
    if data.startswith("expense_del:"):
        expense_id = int(data.split(":")[1])
        logger.info(f"Request to delete expense ID {expense_id}")
        success = database.delete_expense(expense_id)
        if success:
            keyboard = [[InlineKeyboardButton(text="📊 소비 분석 조회", callback_data="expense_stat")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await send_safe_message(
                query=query,
                text="❌ <b>지출 내역을 성공적으로 삭제했습니다!</b>",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            await send_safe_message(
                query=query,
                text="❌ <b>지출 내역 삭제 실패 (이미 삭제되었거나 존재하지 않음)</b>",
                parse_mode="HTML"
            )
            
    elif data == "expense_stat":
        logger.info(f"Callback expense_stat for chat {chat_id}")
        tools.current_chat_id.set(chat_id)
        try:
            summary_report = tools.get_expense_summary_tool()
            keyboard = [[InlineKeyboardButton(text="🔄 소비 분석 새로고침", callback_data="expense_stat")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await send_safe_message(
                query=query,
                text=summary_report,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            await send_safe_message(
                query=query,
                text=f"❌ 소비 분석 조회 중 오류 발생: {str(e)}",
                parse_mode="HTML"
            )

    # 2. Complete Task
    elif data.startswith("complete:"):
        task_id = int(data.split(":")[1])
        logger.info(f"Request to complete task ID {task_id}")
        success = database.complete_task(task_id)
        if success:
            logger.info(f"Task ID {task_id} completed successfully.")
            keyboard = [[InlineKeyboardButton(text="🔄 목록 새로고침", callback_data="refresh")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await send_safe_message(
                query=query,
                text="✅ <b>할 일을 완료 처리했습니다!</b>",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            logger.warning(f"Task ID {task_id} could not be completed (not found or already completed).")
            await send_safe_message(
                query=query,
                text="❌ <b>할 일을 완료 처리하지 못했습니다. (이미 완료되었거나 존재하지 않음)</b>",
                parse_mode="HTML"
            )
            
    # 2. Refresh Task List
    elif data == "refresh":
        logger.info("Request to refresh task list.")
        tasks = database.list_tasks(status="pending")
        
        if not tasks:
            logger.info("Refresh: No tasks found. Displaying empty state.")
            keyboard = [[InlineKeyboardButton(text="🔄 새로고침", callback_data="refresh")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                "📋 <b>진행 중인 할 일이 없습니다.</b> 👍\n\n"
                "할 일을 등록하려면 자연어로 봇에게 채팅을 보내보세요!\n"
                "예시:\n"
                "- <i>\"우유 사기 할 일 추가해줘\"</i>\n"
                "- <i>\"내일 아침 9시 치과 예약 등록하기\"</i>\n\n"
                "등록하신 후 아래 [🔄 새로고침] 버튼을 누르면 목록이 업데이트됩니다."
            )
            await send_safe_message(
                query=query,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            return
            
        logger.info(f"Refresh: Found {len(tasks)} tasks. Displaying task list.")
        keyboard = []
        text_lines = ["📋 <b>현재 진행 중인 할 일 목록:</b>\n"]
        for idx, t in enumerate(tasks, start=1):
            title_esc = html.escape(t['title'])
            desc_esc = f" - {html.escape(t['description'])}" if t['description'] else ""
            due_esc = f" (기한: {html.escape(t['due_date'])})" if t['due_date'] else ""
            text_lines.append(f"• <b>#{idx}</b>: {title_esc}{desc_esc}{due_esc}")
            
            button = InlineKeyboardButton(
                text=f"✅ #{idx} 완료",
                callback_data=f"complete:{t['id']}"
            )
            keyboard.append([button])
            
        keyboard.append([InlineKeyboardButton(text="🔄 새로고침", callback_data="refresh")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await send_safe_message(
            query=query,
            text="\n".join(text_lines),
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    # 2.5 Refresh Weekly Report
    elif data == "refresh_report":
        logger.info(f"Callback refresh_report for chat {chat_id}")
        try:
            from src.reports import generate_weekly_report_image
            image_path = generate_weekly_report_image(chat_id)
            
            keyboard = [[InlineKeyboardButton("🔄 보고서 갱신", callback_data="refresh_report")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            caption = (
                "📊 <b>주간 분석 리포트 업데이트 완료</b> 📈\n\n"
                "실시간 데이터를 반영하여 주간 리포트가 갱신되었습니다."
            )
            
            await query.edit_message_media(
                media=InputMediaPhoto(media=open(image_path, 'rb'), caption=caption, parse_mode="HTML"),
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to refresh weekly report: {e}", exc_info=True)
            await query.message.reply_text(f"❌ 리포트 갱신 중 오류가 발생했습니다: {str(e)}")

    # 3. Refresh Weather Report
    elif data == "refresh_weather":
        logger.info("Refreshing weather...")
        raw_history = database.get_chat_history(chat_id, limit=15)
        location = "Seoul"
        for row in reversed(raw_history):
            try:
                content_json = json.loads(row['content'])
                for part in content_json.get('parts', []):
                    if part.get('function_call') and part['function_call']['name'] == 'get_current_weather':
                        location = part['function_call']['args']['location']
                        break
            except Exception:
                pass
        
        weather_info = tools.get_current_weather(location)
        keyboard = [[InlineKeyboardButton("🔄 날씨 새로고침", callback_data="refresh_weather")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            f"🌦️ <b>실시간 날씨 정보 업데이트 완료</b>\n\n"
            f"{html.escape(weather_info)}"
        )
        await send_safe_message(
            query=query,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    # 4. Refresh Web News Search
    elif data == "refresh_news":
        logger.info("Refreshing news...")
        raw_history = database.get_chat_history(chat_id, limit=15)
        query_text = "오늘 주요 뉴스"
        for row in reversed(raw_history):
            try:
                content_json = json.loads(row['content'])
                for part in content_json.get('parts', []):
                    if part.get('function_call') and part['function_call']['name'] == 'web_search':
                        query_text = part['function_call']['args']['query']
                        break
            except Exception:
                pass
        
        search_res = tools.web_search(query_text)
        keyboard = [[InlineKeyboardButton("📰 관련 뉴스 더 검색", callback_data="refresh_news")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            f"🔍 <b>실시간 검색 결과 업데이트 완료</b>\n\n"
            f"{html.escape(search_res)}"
        )
        await send_safe_message(
            query=query,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    # 5. Refresh Google Calendar events
    elif data == "refresh_calendar":
        logger.info("Refreshing Google Calendar events...")
        tools.current_chat_id.set(chat_id)
        try:
            calendar_info = tools.list_google_calendar_events()
            keyboard = [[InlineKeyboardButton("📅 일정 새로고침", callback_data="refresh_calendar")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                f"📅 <b>실시간 일정 정보 업데이트 완료</b>\n\n"
                f"{calendar_info}"
            )
            await send_safe_message(
                query=query,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            from src import google_auth
            if isinstance(e, google_auth.GoogleAuthRequiredError) or "GoogleAuthRequiredError" in str(e):
                await query.message.reply_text(
                    "🔒 구글 계정 연동 세션이 만료되었습니다. /login 명령어로 다시 로그인해 주세요."
                )
            else:
                await send_safe_message(
                    query=query,
                    text=f"❌ 일정 업데이트 중 오류 발생: {str(e)}",
                    parse_mode="HTML"
                )

    # 6. Refresh Gmail unread emails
    elif data == "refresh_emails":
        logger.info("Refreshing Gmail emails...")
        tools.current_chat_id.set(chat_id)
        try:
            email_info = tools.list_unread_emails()
            keyboard = [[InlineKeyboardButton("📩 메일 새로고침", callback_data="refresh_emails")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                f"📩 <b>실시간 메일 정보 업데이트 완료</b>\n\n"
                f"{email_info}"
            )
            await send_safe_message(
                query=query,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            from src import google_auth
            if isinstance(e, google_auth.GoogleAuthRequiredError) or "GoogleAuthRequiredError" in str(e):
                await query.message.reply_text(
                    "🔒 구글 계정 연동 세션이 만료되었습니다. /login 명령어로 다시 로그인해 주세요."
                )
            else:
                await send_safe_message(
                    query=query,
                    text=f"❌ 메일 업데이트 중 오류 발생: {str(e)}",
                    parse_mode="HTML"
                )

    # 7. Route to prompt Search Notes
    elif data == "prompt_search_notes":
        await query.message.reply_text(
            "🔍 검색하고 싶은 메모의 키워드를 입력해 주세요 (예: '아이디어' 또는 '일기')."
        )

    # 8. Settings callback handlers
    elif data == "auth_logout":
        logger.info(f"Callback auth_logout for chat {chat_id}")
        success = database.delete_user_credentials(chat_id)
        if success:
            await query.message.reply_text("🔓 <b>구글 계정 연동이 해제되었습니다.</b>", parse_mode="HTML")
        else:
            await query.message.reply_text("ℹ️ 연동된 구글 계정이 없습니다.")
        await render_settings_ui(chat_id=chat_id, query=query, bot=context.bot)
        
    elif data == "auth_login":
        logger.info(f"Callback auth_login for chat {chat_id}")
        from src import google_auth
        try:
            auth_url, flow, state = google_auth.generate_auth_url(chat_id)
            loop = asyncio.get_running_loop()
            google_auth.start_redirect_server(chat_id, flow, state, context.bot, loop)
            
            keyboard = [[InlineKeyboardButton("🔑 Google 계정 연동하기", url=auth_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                "🔒 <b>구글 계정 연동 링크가 생성되었습니다.</b>\n\n"
                "아래 버튼을 눌러 연동을 완료해 주세요!"
            )
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed callback auth_login flow: {e}", exc_info=True)
            await query.message.reply_text(f"❌ 구글 로그인 페이지 생성 중 오류: {str(e)}")
            
    elif data == "info_briefing":
        text = (
            "⏰ <b>일일 브리핑 설정 방법</b>\n\n"
            "비서 봇에게 대화로 직접 설정을 지시할 수 있습니다:\n"
            "• <b>시간 변경:</b> <i>\"아침 브리핑 시간 오전 7시 40분으로 설정해줘\"</i>\n"
            "• <b>거주지(날씨) 변경:</b> <i>\"날씨 거주지 부산으로 설정해줘\"</i>\n\n"
            "설정을 변경하시면 다음날 브리핑부터 반영됩니다."
        )
        await query.message.reply_text(text, parse_mode="HTML")
        
    elif data == "info_filters":
        text = (
            "📩 <b>지메일 중요 메일 필터 설정 방법</b>\n\n"
            "특정 발신자나 키워드가 포함된 메일만 중요 알림을 받도록 필터를 설정할 수 있습니다. 봇에게 대화로 요청해 보세요:\n"
            "• <b>키워드 추가:</b> <i>\"지메일 알림 필터에 '긴급' 키워드 추가해줘\"</i>\n"
            "• <b>발신자 추가:</b> <i>\"지메일 알림 필터에 boss@work.com 발신자 등록해줘\"</i>\n"
            "• <b>필터 삭제:</b> <i>\"지메일 알림 필터에서 '긴급' 키워드 삭제해줘\"</i>\n\n"
            "설정 완료 후 설정 갱신 버튼을 누르면 목록이 업데이트됩니다."
        )
        await query.message.reply_text(text, parse_mode="HTML")
        
    elif data == "refresh_settings":
        logger.info(f"Callback refresh_settings for chat {chat_id}")
        await render_settings_ui(chat_id=chat_id, query=query, bot=context.bot)

    elif data == "dday_list":
        logger.info(f"Callback dday_list for chat {chat_id}")
        tools.current_chat_id.set(chat_id)
        dday_html = tools.list_ddays_tool()
        keyboard = [
            [
                InlineKeyboardButton("🔄 목록 새로고침", callback_data="dday_list"),
                InlineKeyboardButton("⚙️ 설정 탭 이동", callback_data="refresh_settings")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await send_safe_message(
            query=query,
            text=dday_html,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    elif data == "dday_clear":
        logger.info(f"Callback dday_clear for chat {chat_id}")
        count = database.clear_ddays(chat_id)
        await query.answer(f"총 {count}개의 D-Day가 삭제되었습니다.", show_alert=True)
        await render_settings_ui(chat_id=chat_id, query=query, bot=context.bot)


    elif data.startswith("news_cat:"):
        category = data.split(":")[1]
        logger.info(f"Callback news_cat '{category}' for chat {chat_id}")
        
        category_queries = {
            "economy": "오늘 주요 경제 뉴스 기사",
            "tech": "오늘 주요 IT 과학 테크 뉴스 기사",
            "politics": "오늘 주요 정치 뉴스 기사",
            "world": "오늘 주요 국제 뉴스 기사",
            "entertainment": "오늘 주요 스포츠 연예 뉴스 기사"
        }
        category_names = {
            "economy": "경제", "tech": "IT/테크", "politics": "정치", "world": "국제", "entertainment": "스포츠/연예"
        }
        
        query_text = category_queries.get(category, "오늘 주요 뉴스 기사")
        cat_name = category_names.get(category, "일반")
        
        await query.answer(text=f"📰 [실시간 {cat_name}] 뉴스를 불러오고 있습니다...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            articles = await tools.fetch_and_filter_category_news(query_text)
                
            if not articles:
                await query.message.reply_text(f"❌ '{cat_name}' 카테고리에서 뉴스를 찾지 못했습니다.")
                return
                
            # Perform AI 2nd pass category validation
            filtered_articles = await agent.filter_articles_by_category_or_keyword(articles, cat_name, is_category=True)
            if not filtered_articles:
                logger.info(f"AI category validation filtered out all articles. Falling back to unfiltered list.")
                filtered_articles = articles
                
            text_lines = [f"💼 <b>실시간 [{cat_name}] 뉴스 브리핑</b>\n"]
            buttons = []
            
            # Show up to 4 articles
            for idx, art in enumerate(filtered_articles[:4], start=1):
                if not art.get('href') or not art['href'].startswith(('http://', 'https://')):
                    continue
                art_id = database.save_news_article_cache(art['href'], art['title'])
                title_esc = html.escape(art['title'])
                snippet_esc = html.escape(art['body']) if 'body' in art else ""
                
                text_lines.append(f"<b>{idx}. <a href=\"{art['href']}\">{title_esc}</a></b>\n  <i>{snippet_esc[:120]}...</i>\n")
                
                buttons.append(
                    InlineKeyboardButton(f"🔗 #{idx} 원문", url=art['href'])
                )
                
            # Chunk buttons into 2-column rows
            keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
            keyboard.append([InlineKeyboardButton("🔄 갱신", callback_data=f"news_cat:{category}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await send_safe_message(
                query=query,
                text="\n".join(text_lines),
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error fetching category news {category}: {e}", exc_info=True)
            await query.message.reply_text(f"❌ 뉴스 조회 중 오류 발생: {str(e)}")
            
    elif data == "news_my":
        logger.info(f"Callback news_my for chat {chat_id}")
        keywords = database.get_news_keywords(chat_id)
        if not keywords:
            await query.message.reply_text(
                "ℹ️ <b>등록된 뉴스 관심 키워드가 없습니다.</b>\n\n"
                "비서 봇에게 대화로 <i>\"뉴스 키워드에 '부동산' 추가해줘\"</i> 와 같이 요청해서 관심사를 먼저 등록해 보세요!",
                parse_mode="HTML"
            )
            return
            
        await query.answer(text="🌟 맞춤 키워드에 대한 최신 뉴스들을 검색 중입니다...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            loop = asyncio.get_running_loop()
            
            # Limit to top 3 keywords to avoid timeout / quota issues
            target_kws = keywords[:3]
            
            # Concurrently fetch and filter articles for each keyword
            tasks_list = [tools.fetch_and_filter_keyword_news(kw) for kw in target_kws]
            results = await asyncio.gather(*tasks_list, return_exceptions=True)
            
            async def dummy_empty():
                return []
            
            # Concurrently run AI filtering for each keyword's articles
            filter_tasks = []
            for idx, kw in enumerate(target_kws):
                res = results[idx]
                if isinstance(res, Exception) or not res:
                    filter_tasks.append(dummy_empty())
                    continue
                filter_tasks.append(agent.filter_articles_by_category_or_keyword(res, kw, is_category=False))
                
            filtered_results = await asyncio.gather(*filter_tasks, return_exceptions=True)
            
            text_lines = ["🌟 <b>나의 관심 키워드 맞춤 뉴스 피드</b>\n"]
            buttons = []
            btn_idx = 1
            
            for idx, kw in enumerate(target_kws):
                res = results[idx]
                
                # Check if search failed completely or got an exception
                if isinstance(res, Exception):
                    text_lines.append(f"🔍 <b>키워드: #{kw}</b>\n  (뉴스 검색에 오류가 발생했습니다.)\n")
                    continue
                    
                articles = filtered_results[idx]
                if isinstance(articles, Exception) or not articles:
                    # fallback to unfiltered (but URL-filtered) results if AI filtering failed or returned empty
                    articles = res if not isinstance(res, Exception) else []
                    
                text_lines.append(f"🔍 <b>키워드: #{kw}</b>")
                if not articles:
                    text_lines.append("  (최근 뉴스가 없습니다.)\n")
                    continue
                    
                # Show top 2 articles per keyword
                for art in articles[:2]:
                    if not art.get('href') or not art['href'].startswith(('http://', 'https://')):
                        continue
                    art_id = database.save_news_article_cache(art['href'], art['title'])
                    title_esc = html.escape(art['title'])
                    snippet_esc = html.escape(art['body']) if 'body' in art else ""
                    
                    text_lines.append(f"  • <b><a href=\"{art['href']}\">{title_esc}</a></b>\n    <i>{snippet_esc[:90]}...</i>")
                    
                    buttons.append(
                        InlineKeyboardButton(f"🔗 #{btn_idx} 원문", url=art['href'])
                    )
                    btn_idx += 1
                text_lines.append("")
                
            # Chunk buttons into 2-column rows
            keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
            keyboard.append([InlineKeyboardButton("🔄 맞춤 뉴스 갱신", callback_data="news_my")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await send_safe_message(
                query=query,
                text="\n".join(text_lines),
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in news_my: {e}", exc_info=True)
            await query.message.reply_text(f"❌ 맞춤 뉴스 조회 중 오류 발생: {str(e)}")
            
    elif data == "info_news_settings":
        text = (
            "📰 <b>뉴스 개인화 설정 방법</b>\n\n"
            "비서 봇에게 대화로 직접 관심 키워드를 추가하거나 삭제할 수 있습니다:\n"
            "• <b>키워드 추가:</b> <i>\"뉴스 키워드에 '인공지능' 추가해줘\"</i>\n"
            "• <b>키워드 삭제:</b> <i>\"뉴스 키워드에서 '부동산' 삭제해줘\"</i>\n"
            "• <b>키워드 조회:</b> <i>\"뉴스 키워드 목록 보여줘\"</i>\n\n"
            "맞춤 키워드 뉴스는 <b>[🌟 맞춤 뉴스]</b> 메뉴나 아침 브리핑 발송 시 함께 포함되어 보고됩니다."
        )
        await query.message.reply_text(text, parse_mode="HTML")

    elif data.startswith("travel_apply:"):
        plan_id = int(data.split(":")[1])
        logger.info(f"Request to bulk register travel plan ID {plan_id} to Google Calendar")
        await query.answer("구글 캘린더에 일정을 등록하고 있습니다...")
        
        plan = database.get_pending_travel_plan(plan_id)
        if not plan:
            await query.message.reply_text("❌ <b>해당 여행 계획을 찾을 수 없거나 이미 삭제되었습니다.</b>", parse_mode="HTML")
            return
            
        try:
            events = json.loads(plan['events_json'])
        except Exception as e:
            await query.message.reply_text(f"❌ <b>일정 데이터 파싱 오류: {str(e)}</b>", parse_mode="HTML")
            return
            
        if not events:
            await query.message.reply_text("❌ <b>등록할 일정이 없습니다.</b>", parse_mode="HTML")
            return

        from src import google_auth
        from googleapiclient.discovery import build
        try:
            creds = google_auth.get_google_credentials(chat_id)
            service = build('calendar', 'v3', credentials=creds)
        except google_auth.GoogleAuthRequiredError:
            await query.message.reply_text("🔒 <b>구글 계정 연동 세션이 없거나 만료되었습니다.</b>\n/login 명령어로 로그인을 먼저 완료한 후 다시 시도해 주세요.")
            return
        except Exception as e:
            await query.message.reply_text(f"❌ 구글 서비스 빌드 중 오류 발생: {str(e)}")
            return

        success_count = 0
        failed_count = 0
        for ev in events:
            try:
                event_body = {
                    'summary': ev.get('summary', '여행 일정'),
                    'start': {
                        'dateTime': ev.get('start_time_iso'),
                        'timeZone': 'Asia/Seoul',
                    },
                    'end': {
                        'dateTime': ev.get('end_time_iso'),
                        'timeZone': 'Asia/Seoul',
                    },
                }
                if ev.get('description'):
                    event_body['description'] = ev.get('description')
                if ev.get('location'):
                    event_body['location'] = ev.get('location')
                    
                service.events().insert(calendarId='primary', body=event_body).execute()
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to register travel event {ev.get('summary')}: {e}")
                failed_count += 1

        result_text = (
            f"📅 <b>구글 캘린더 일괄 등록 완료!</b>\n\n"
            f"• 목적지: <b>{plan['destination']}</b>\n"
            f"• 기간: <code>{plan['start_date']} ~ {plan['end_date']}</code>\n"
            f"• 성공: <code>{success_count}개</code>\n"
        )
        if failed_count > 0:
            result_text += f"• 실패: <code>{failed_count}개</code>\n"
            
        result_text += "\n구글 캘린더 앱 또는 웹에서 일정이 잘 들어갔는지 확인해 보세요!"
        
        keyboard = [[InlineKeyboardButton("📅 일정 새로고침", callback_data="refresh_calendar")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await send_safe_message(
            query=query,
            text=result_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    # 9. Quiz Operations
    elif data.startswith("quiz_start:"):
        session_id = int(data.split(":")[1])
        logger.info(f"Quiz starting for session ID {session_id}")
        await query.answer("퀴즈를 불러오고 있습니다...")
        
        session_data = database.get_quiz_session(session_id)
        if not session_data:
            await query.message.reply_text("❌ <b>유효하지 않은 퀴즈 세션입니다.</b>", parse_mode="HTML")
            return
            
        await render_quiz_question(query, context, session_id, 0, session_data)
        
    elif data.startswith("quiz_ans:"):
        parts = data.split(":")
        session_id = int(parts[1])
        choice = int(parts[2])
        
        session_data = database.get_quiz_session(session_id)
        if not session_data or session_data["status"] == "completed":
            await query.answer("이미 완료되었거나 유효하지 않은 퀴즈입니다.")
            return
            
        questions = json.loads(session_data["questions_json"])
        curr_idx = session_data["current_index"]
        score = session_data["score"]
        is_current_failed = session_data.get("is_current_failed", 0)
        
        if curr_idx >= len(questions):
            await query.answer("이미 모든 문제를 푸셨습니다.")
            return
            
        # Answer to remove loading spinner for selection
        await query.answer()
        
        q = questions[curr_idx]
        correct = q["correct_option"]
        is_correct = (choice == correct)
        option_letters = ["A", "B", "C", "D"]
        
        if is_correct:
            # [점수 산정 규칙] 첫 시도에 맞춘 경우에만 점수(score)를 1점 부여합니다.
            # 이전에 한 번이라도 틀렸었다면(is_current_failed == 1) 성적에 반영하지 않고 기존 점수를 유지합니다.
            new_score = score + 1 if is_current_failed == 0 else score
            next_idx = curr_idx + 1
            
            # [오답 복습 처리] 만약 현재 진행 중인 세션이 '오답 노트 복습' 모드라면, 정답을 맞춘 문제를 오답 노출 리스트에서 제거합니다.
            if session_data["title"] == "오답 노트 복습":
                database.remove_incorrect_note_by_text(chat_id, q["question"])
            
            # 다음 문제로 넘어가므로 DB에 다음 문제 인덱스를 저장하고, 재시도 오답 여부(is_current_failed)는 다시 0으로 초기화합니다.
            database.update_quiz_session(session_id, next_idx, new_score, "active", is_current_failed=0)
            
            # 정답 해설 메시지 작성 (사용자가 정답 확인을 즉시 할 수 있도록 버튼 없이 메시지만 보냅니다.)
            text = (
                f"📖 <b>[{html.escape(session_data['title'])}] 퀴즈 채점</b>\n\n"
                f"<b>Q {curr_idx+1}. {html.escape(q['question'])}</b>\n"
                f"선택한 답안: {option_letters[choice]}. {html.escape(q['options'][choice])}\n\n"
                f"✅ <b>정답입니다!</b>\n\n"
                f"💡 <b>해설:</b> {html.escape(q['explanation'])}"
            )
            
            await send_safe_message(
                chat_id=query.message.chat_id,
                bot=context.bot,
                text=text,
                reply_markup=None,
                parse_mode="HTML"
            )
            
            # DB가 업데이트되었으므로 세션 정보를 최신 상태로 새로 조회해옵니다.
            updated_session = database.get_quiz_session(session_id)
            
            # [곧바로 다음 문제 출력 / 10번째 결과 화면 전환 처리]
            # 다음 문제가 남아있다면, 별도 '풀기' 버튼 없이 곧바로 다음 문제 화면을 출력합니다.
            # 10번째(혹은 마지막) 문제라면 곧바로 결과 화면(추가 풀기를 물어보는 버튼 탑재)을 출력합니다.
            if next_idx < len(questions):
                await render_quiz_question(query, context, session_id, next_idx, updated_session)
            else:
                await render_quiz_result(query, context, session_id, updated_session)
        else:
            # [오답 노트 자동 수집] 해당 문제를 처음 틀렸을 때(is_current_failed == 0)만 가중치를 1 올리거나 신규 등록하여 중복을 방지합니다.
            if is_current_failed == 0:
                opt_json = json.dumps(q["options"], ensure_ascii=False)
                database.add_or_increment_incorrect_note(
                    chat_id=chat_id,
                    title=session_data["title"],
                    question_text=q["question"],
                    options_json=opt_json,
                    correct_option=correct,
                    explanation=q["explanation"]
                )
            
            # [오답 처리 규칙] 틀린 경우 점수나 인덱스는 그대로 두고, 오답 이력(is_current_failed = 1)만 표시하여 DB에 저장합니다.
            database.update_quiz_session(session_id, curr_idx, score, "active", is_current_failed=1)
            
            text = (
                f"📖 <b>[{html.escape(session_data['title'])}] 복습 퀴즈</b>\n\n"
                f"<b>Q {curr_idx+1}. {html.escape(q['question'])}</b>\n"
                f"선택한 답안: {option_letters[choice]}. {html.escape(q['options'][choice])}\n\n"
                f"❌ <b>오답입니다.</b> 다른 답을 다시 선택해 보세요!\n"
            )
            
            # 정답을 맞출 때까지 동일한 문제의 보기 버튼 A, B, C, D를 다시 보여줍니다.
            keyboard = []
            for idx, opt in enumerate(q["options"]):
                button = InlineKeyboardButton(text=option_letters[idx], callback_data=f"quiz_ans:{session_id}:{idx}")
                keyboard.append(button)
            reply_markup = InlineKeyboardMarkup([keyboard])
            
            await send_safe_message(
                chat_id=query.message.chat_id,
                bot=context.bot,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        
    elif data.startswith("quiz_next:"):
        parts = data.split(":")
        session_id = int(parts[1])
        session_data = database.get_quiz_session(session_id)
        if not session_data:
            await query.message.reply_text("❌ 유효하지 않은 퀴즈 세션입니다.")
            return
        await render_quiz_question(query, context, session_id, session_data["current_index"], session_data)
        
    elif data.startswith("quiz_result:"):
        parts = data.split(":")
        session_id = int(parts[1])
        session_data = database.get_quiz_session(session_id)
        if not session_data:
            await query.message.reply_text("❌ 유효하지 않은 퀴즈 세션입니다.")
            return
            
        await render_quiz_result(query, context, session_id, session_data)
        
    elif data.startswith("quiz_add_more:"):
        # [추가 퀴즈 생성 콜백] 사용자가 완료 후 10문제 추가 풀기를 눌렀을 때 실행됩니다.
        parts = data.split(":")
        session_id = int(parts[1])
        logger.info(f"Adding 10 more questions for session ID {session_id}")
        await query.answer("추가 퀴즈를 생성하고 있습니다. 잠시만 기다려 주세요... ⏳")
        
        session_data = database.get_quiz_session(session_id)
        if not session_data:
            await query.message.reply_text("❌ <b>유효하지 않은 퀴즈 세션입니다.</b>", parse_mode="HTML")
            return
            
        # 데이터베이스에 백업해 두었던 원본 분석 본문 텍스트를 불러옵니다.
        source_content = session_data.get("source_content")
        if not source_content or not source_content.strip():
            await query.message.reply_text("❌ <b>원본 콘텐츠를 찾을 수 없어 추가 퀴즈 생성이 불가합니다.</b>", parse_mode="HTML")
            return
            
        title = session_data["title"]
        existing_questions = json.loads(session_data["questions_json"])
        
        # 텔레그램 채팅창에 '입력 중(typing)' 상태를 표시하여 AI 생성 지연 대기 인지를 돕습니다.
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Gemini AI를 호출하여 기존에 출제된 문제 리스트와 겹치지 않는 새로운 10문제를 비동기로 생성합니다.
        new_questions = await asyncio.get_running_loop().run_in_executor(
            None, agent.generate_additional_quiz, title, source_content, existing_questions
        )
        
        if not new_questions:
            await query.message.reply_text("❌ <b>추가 퀴즈 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.</b>", parse_mode="HTML")
            return
            
        # 기존 질문 리스트의 끝에 새로 만들어진 질문 목록을 합칩니다 (Append)
        merged_questions = existing_questions + new_questions
        merged_json = json.dumps(merged_questions, ensure_ascii=False)
        
        # 퀴즈 인덱스는 기존 문제의 개수(예: 10개였다면 인덱스 10 즉 11번째 문제)를 가리키게 하고,
        # 세션 상태를 다시 'active'로 변경하며, 문제 목록 JSON을 덮어쓰고, 재시도 오답 여부(is_current_failed)는 0으로 리셋하여 갱신합니다.
        current_idx = len(existing_questions)
        database.update_quiz_session(session_id, current_idx, session_data["score"], "active", merged_json, is_current_failed=0)
        
        await query.message.reply_text(
            f"🎯 <b>새로운 퀴즈 {len(new_questions)}문항 추가 완료!</b>\n"
            f"이어서 문제를 풀어보세요. (현재 문제 번호: Q {current_idx+1})",
            parse_mode="HTML"
        )
        
        # 갱신된 데이터를 기반으로 사용자에게 새로 생긴 첫 번째 문제(Q 11)를 출제합니다.
        updated_session = database.get_quiz_session(session_id)
        await render_quiz_question(query, context, session_id, current_idx, updated_session)
        
    elif data == "quiz_review_start":
        # [오답 복습 시작 콜백] 사용자가 설정화면 등에서 '오답 복습' 버튼을 눌렀을 때 실행됩니다.
        logger.info(f"Callback quiz_review_start for chat {chat_id}")
        
        # 1. 현재 데이터베이스에 등록된 사용자의 오답 개수를 가져옵니다.
        incorrect_count = database.get_incorrect_notes_count(chat_id)
        if incorrect_count == 0:
            # 오답이 없는 경우, 텔레그램 상단에 '틀린 문제가 없습니다!' 알림을 띄우고 리턴합니다.
            await query.answer("틀린 문제가 없습니다! 👏", show_alert=True)
            return
            
        # 2. 로딩 스피너를 제거하고 사용자에게 준비 알림을 띄웁니다.
        await query.answer("오답 노트를 바탕으로 복습 퀴즈를 생성하고 있습니다... ⏳")
        
        # 3. 데이터베이스에서 틀린 횟수가 높은 가중치 위주로 최대 5개의 오답 문항을 임의 추출합니다.
        notes = database.get_incorrect_notes_for_review(chat_id, limit=5)
        
        # 4. 추출된 오답 데이터를 퀴즈 세션용 질문 JSON 구조로 매핑하여 빌드합니다.
        review_questions = []
        for n in notes:
            try:
                options = json.loads(n["options_json"])
            except Exception:
                options = []
            review_questions.append({
                "question": n["question_text"],
                "options": options,
                "correct_option": n["correct_option"],
                "explanation": n["explanation"]
            })
            
        review_questions_json = json.dumps(review_questions, ensure_ascii=False)
        
        # 5. 타이틀을 '오답 노트 복습'으로 지정하고, 원본 source_content가 없으므로 None으로 전달하여 복습 세션을 생성합니다.
        session_id = database.create_quiz_session(chat_id, "오답 노트 복습", review_questions_json, None)
        
        # 6. 새로 생성된 복습 세션 데이터를 조회한 뒤 첫 번째 오답(Q 1) 문제를 화면에 렌더링합니다.
        session_data = database.get_quiz_session(session_id)
        await render_quiz_question(query, context, session_id, 0, session_data)
        
    elif data == "quiz_history":
        logger.info(f"Callback quiz_history for chat {chat_id}")
        completed = database.get_completed_quizzes(chat_id, limit=5)
        if not completed:
            text = "📖 <b>완료한 퀴즈 이력이 없습니다.</b>\n\n링크를 전송해 복습 퀴즈를 풀어보세요!"
        else:
            text_lines = ["📖 <b>최근 퀴즈 학습 완료 이력 (최대 5건):</b>\n"]
            for idx, q in enumerate(completed, start=1):
                title_esc = html.escape(q['title'])
                try:
                    total_q = len(json.loads(q['questions_json']))
                except Exception:
                    total_q = 3
                text_lines.append(f"• <b>{idx}. {title_esc}</b>\n  성적: <code>{q['score']}/{total_q}</code> ({q['created_at'][:10]})")
            text = "\n".join(text_lines)
            
        keyboard = [[InlineKeyboardButton("⚙️ 설정 탭 이동", callback_data="refresh_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await send_safe_message(
            query=query,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def handle_google_auth_required(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Generates OAuth URL, starts redirect server, and sends login button to user."""
    from src import google_auth
    try:
        auth_url, flow, state = google_auth.generate_auth_url(chat_id)
        loop = asyncio.get_running_loop()
        google_auth.start_redirect_server(chat_id, flow, state, context.bot, loop)
        
        keyboard = [[InlineKeyboardButton("🔑 Google 계정 연동하기", url=auth_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            "🔒 <b>구글 계정 연동이 필요한 서비스입니다.</b>\n\n"
            "아래 버튼을 눌러 연동을 완료한 뒤 다시 요청해 주세요!\n\n"
            "💡 <b>원격 서버 배포 환경 팁</b>\n"
            "우분투 서버나 Docker 구동 상태에서 연동 완료 후 브라우저 화면에 <i>'사이트에 연결할 수 없음'</i> 에러가 발생하더라도 당황하지 마세요.\n"
            "브라우저 주소창의 에러가 난 **실패 주소 전체(http://localhost:8080/?...)**를 복사해서 이 대화방에 그대로 채팅으로 보내(전송) 주시면 자동으로 즉시 연동 완료 처리됩니다."
        )
        await send_safe_message(
            update=update,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except google_auth.GoogleConfigError as ce:
        logger.error(f"Google config missing for chat {chat_id}: {ce}")
        await update.message.reply_text(
            f"❌ 구글 연동 서비스 설정이 완료되지 않았습니다:\n\n{str(ce)}"
        )
    except Exception as e:
        logger.error(f"Failed to initialize auth flow: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ 구글 로그인 페이지 생성 중 오류가 발생했습니다: {str(e)}"
        )

@whitelist_only
async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send OAuth authorization link and start background redirect server."""
    chat_id = update.effective_chat.id
    logger.info(f"=== /login Command Received from Chat {chat_id} ===")
    await handle_google_auth_required(update, context, chat_id)

@whitelist_only
async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect Google credentials and remove from database."""
    chat_id = update.effective_chat.id
    logger.info(f"=== /logout Command Received from Chat {chat_id} ===")
    
    from src import database
    success = database.delete_user_credentials(chat_id)
    
    if success:
        text = "🔓 <b>구글 계정 연동이 해제되었습니다.</b>\n\n저장된 로그인 정보가 안전하게 파기되었습니다."
    else:
        text = "ℹ️ 연동된 구글 계정이 없습니다."
        
    await send_safe_message(
        update=update,
        text=text,
        parse_mode="HTML"
    )
    logger.info(f"Logout completed for chat {chat_id}. Success: {success}")

async def render_settings_ui(chat_id: int, update: Update = None, query = None, bot = None) -> None:
    """Helper to render or edit the settings message UI."""
    settings = database.get_user_settings(chat_id)
    briefing_time = settings.get("briefing_time", "08:00")
    location = settings.get("location", "Seoul")
    
    # Get gmail filters
    filters_list = database.get_gmail_filters(chat_id)
    filters_str = ""
    if filters_list:
        for idx, f in enumerate(filters_list, start=1):
            ftype = "발신자" if f['filter_type'] == 'sender' else "키워드"
            filters_str += f"\n  • #{idx} [{ftype}] {html.escape(f['value'])}"
    else:
        filters_str = "\n  (등록된 필터가 없습니다.)"
        
    # Get news keywords
    news_kw_list = database.get_news_keywords(chat_id)
    news_kw_str = ", ".join(news_kw_list) if news_kw_list else "(등록된 키워드가 없습니다.)"
    news_kw_str_esc = html.escape(news_kw_str)
        
    # Check Google authentication status
    creds = database.get_user_credentials(chat_id)
    google_status = "🟢 연동됨" if creds else "🔴 연동 안 됨"
    
    # Get ddays count
    ddays = database.get_ddays(chat_id)
    ddays_count = len(ddays)
    
    # Calculate monthly expense total
    import datetime
    today = datetime.date.today()
    start_date = today.replace(day=1).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    expenses = database.get_expenses(chat_id, start_date, end_date)
    total_spent = sum(item['amount'] for item in expenses) if expenses else 0
    
    # 데이터베이스에서 누적 완료된 퀴즈 개수와 평균 정답률 통계를 가져옵니다.
    quiz_stats = database.get_completed_quiz_stats(chat_id)
    total_quizzes = quiz_stats["total_quizzes"]
    avg_rate = quiz_stats["avg_rate"]
    
    # [오답 노트 연동] 현재 복습용 오답 노트 데이터베이스에 보관 중인 문제 개수를 조회합니다.
    incorrect_count = database.get_incorrect_notes_count(chat_id)
    
    text = (
        "⚙️ <b>개인 비서 AI 에이전트 설정</b>\n\n"
        f"👤 <b>구글 계정 연동 상태:</b> {google_status}\n"
        f"📅 <b>등록된 D-Day:</b> <code>{ddays_count}개</code>\n"
        f"📊 <b>이번 달 누적 지출액:</b> <code>{total_spent:,}원</code>\n"
        f"📖 <b>퀴즈 학습 이력:</b> <code>{total_quizzes}회 완료 (평균 정답률: {avg_rate:.1f}%)</code>\n"
        f"❌ <b>보관된 오답 문제:</b> <code>{incorrect_count}개</code>\n\n"
        f"⏰ <b>일일 브리핑 설정:</b>\n"
        f"  • 브리핑 시간: <code>{briefing_time}</code>\n"
        f"  • 날씨 지역: <code>{location}</code>\n\n"
        f"📩 <b>지메일 중요 알림 필터:</b>{filters_str}\n\n"
        f"📰 <b>뉴스 관심 키워드:</b> <code>{news_kw_str_esc}</code>\n\n"
        f"💡 <i>팁: 아래 버튼을 클릭하여 연동 상태를 관리하거나, 봇에게 대화로 직접 설정을 요청하실 수 있습니다.</i>\n"
        f"예: <i>\"뉴스 키워드에 'AI' 추가해줘\"</i>"
    )
    
    keyboard = []
    # Row 1: Google OAuth
    if creds:
        keyboard.append([InlineKeyboardButton("🔓 구글 연동 해제", callback_data="auth_logout")])
    else:
        keyboard.append([InlineKeyboardButton("🔑 구글 계정 연동", callback_data="auth_login")])
        
    # Row 2: D-Day Management
    keyboard.append([
        InlineKeyboardButton("📅 D-Day 목록", callback_data="dday_list"),
        InlineKeyboardButton("❌ D-Day 전체 삭제", callback_data="dday_clear")
    ])
        
    # Row 3: Configurations
    keyboard.append([
        InlineKeyboardButton("⏰ 브리핑 설정", callback_data="info_briefing"),
        InlineKeyboardButton("📩 필터 설정", callback_data="info_filters"),
        InlineKeyboardButton("📰 뉴스 설정", callback_data="info_news_settings")
    ])
    
    # Row 4: Expense / Refresh
    keyboard.append([
        InlineKeyboardButton("📊 소비 분석", callback_data="expense_stat"),
        InlineKeyboardButton("🔄 설정 갱신", callback_data="refresh_settings")
    ])
    
    # Row 5: Quiz History & Incorrect Notes Review
    keyboard.append([
        InlineKeyboardButton("📖 퀴즈 이력", callback_data="quiz_history"),
        InlineKeyboardButton(f"❌ 오답 복습 ({incorrect_count}개)", callback_data="quiz_review_start")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await send_safe_message(
        chat_id=chat_id,
        bot=bot,
        update=update,
        query=query,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

@whitelist_only
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send settings UI to user."""
    chat_id = update.effective_chat.id
    logger.info(f"=== /settings Command (or button) Received from Chat {chat_id} ===")
    await render_settings_ui(chat_id=chat_id, update=update, bot=context.bot)

@whitelist_only
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming voice message and send it to the Gemini agent."""
    chat_id = update.effective_chat.id
    voice = update.message.voice
    logger.info(f"=== Voice Message Received from Chat {chat_id} (File size: {voice.file_size} bytes) ===")
    
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    temp_dir = os.path.join(project_root, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_file_path = os.path.join(temp_dir, f"voice_{chat_id}_{update.message.message_id}.ogg")
    
    try:
        logger.info(f"Downloading voice file {voice.file_id}...")
        voice_file = await voice.get_file()
        await voice_file.download_to_drive(temp_file_path)
        logger.info(f"Downloaded Telegram voice note to {temp_file_path}")
        
        loop = asyncio.get_running_loop()
        logger.info("Sending voice file to Gemini agent...")
        reply_text, tools_run = await loop.run_in_executor(
            None,
            agent.process_message,
            chat_id,
            None,
            temp_file_path,
            "audio/ogg"
        )
        
        # Determine inline keyboards based on executed tools
        keyboard = []
        if "get_current_weather" in tools_run:
            keyboard.append([InlineKeyboardButton("🔄 날씨 새로고침", callback_data="refresh_weather")])
        if "web_search" in tools_run:
            keyboard.append([InlineKeyboardButton("📰 관련 뉴스 더 검색", callback_data="refresh_news")])
        if "create_local_task" in tools_run or "complete_local_task" in tools_run:
            keyboard.append([InlineKeyboardButton("📋 나의 할 일 목록 보기", callback_data="refresh")])
        if "save_note" in tools_run:
            keyboard.append([InlineKeyboardButton("🔍 메모 검색", callback_data="prompt_search_notes")])
        if "list_google_calendar_events" in tools_run or "create_google_calendar_event" in tools_run:
            keyboard.append([InlineKeyboardButton("📅 일정 새로고침", callback_data="refresh_calendar")])
        if "list_unread_emails" in tools_run or "send_email_via_gmail" in tools_run or "search_emails_via_gmail" in tools_run:
            keyboard.append([InlineKeyboardButton("📩 메일 새로고침", callback_data="refresh_emails")])
        if "add_expense_tool" in tools_run:
            import re
            match = re.search(r"with ID (\d+)", reply_text)
            if match:
                expense_id = int(match.group(1))
                keyboard.append([
                    InlineKeyboardButton("📊 소비 분석 조회", callback_data="expense_stat"),
                    InlineKeyboardButton("❌ 지출 삭제", callback_data=f"expense_del:{expense_id}")
                ])
            else:
                keyboard.append([InlineKeyboardButton("📊 소비 분석 조회", callback_data="expense_stat")])
        if "get_expense_summary_tool" in tools_run:
            keyboard.append([InlineKeyboardButton("🔄 소비 분석 새로고침", callback_data="expense_stat")])
        if any(t in tools_run for t in ["add_dday_tool", "delete_dday_tool", "list_ddays_tool"]):
            keyboard.append([
                InlineKeyboardButton("📅 D-Day 목록 조회", callback_data="dday_list"),
                InlineKeyboardButton("⚙️ 설정 탭 이동", callback_data="refresh_settings")
            ])
        if "propose_travel_itinerary" in tools_run:
            import re
            match = re.search(r"플랜 ID: (\d+)", reply_text)
            if match:
                plan_id = int(match.group(1))
                keyboard.append([InlineKeyboardButton("📅 구글 캘린더에 전체 등록", callback_data=f"travel_apply:{plan_id}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        logger.info(f"Replying to chat {chat_id}...")
        await send_safe_message(
            update=update,
            text=reply_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        logger.info("Reply sent successfully.")
        
    except Exception as e:
        from src import google_auth
        if isinstance(e, google_auth.GoogleAuthRequiredError) or "GoogleAuthRequiredError" in str(e):
            await handle_google_auth_required(update, context, chat_id)
            return
            
        logger.error(f"Error processing voice message: {e}", exc_info=True)
        await update.message.reply_text(
            f"음성 메시지를 처리하는 도중 오류가 발생했습니다: {str(e)}"
        )
    finally:
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Cleaned up local temp file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete local temp file {temp_file_path}: {e}")

@whitelist_only
async def weekly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send the weekly report dashboard image."""
    chat_id = update.effective_chat.id
    logger.info(f"Command /report or keyboard request received from chat {chat_id}")
    
    # Send processing message
    processing_msg = await update.message.reply_text("📊 <b>주간 시각 리포트를 생성 중입니다... 잠시만 기다려 주세요.</b> ⏳", parse_mode="HTML")
    
    try:
        from src.reports import generate_weekly_report_image
        # Generate image path
        image_path = generate_weekly_report_image(chat_id)
        
        # Build inline keyboard
        keyboard = [[InlineKeyboardButton("🔄 보고서 갱신", callback_data="refresh_report")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        caption = (
            "📊 <b>주간 분석 리포트 배달 완료</b> 📈\n\n"
            "회장님, 지난 7일간의 지출 흐름과 할 일 현황을 요약한 시각 리포트입니다.\n"
            "추가 분석이나 실시간 데이터 반영을 원하시면 아래 [🔄 보고서 갱신]을 눌러 주세요."
        )
        
        # Delete processing message and send photo
        await processing_msg.delete()
        await update.message.reply_photo(
            photo=open(image_path, 'rb'),
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to generate or send weekly report: {e}", exc_info=True)
        await processing_msg.edit_text(f"❌ 주간 리포트 생성 중 오류가 발생했습니다: {str(e)}")

@whitelist_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming text messages using the Gemini agent."""
    chat_id = update.effective_chat.id
    user_text = update.message.text
    logger.info(f"=== Text Message Received from Chat {chat_id} ===")
    logger.info(f"Text: '{user_text}'")
    
    # 1.0 Detect YouTube or web link URL
    if user_text:
        import re
        urls = re.findall(r'(https?://[^\s]+)', user_text)
        if urls:
            url = urls[0]
            # Exclude manual localhost redirect callback URLs
            if not (("localhost:8080" in url or "127.0.0.1:8080" in url) and "code=" in url):
                logger.info(f"Detected content URL in chat {chat_id}: {url}")
                await process_url_link(update, context, chat_id, url)
                return
                
    # 1.1 Intercept Google OAuth manual callback URLs
    if user_text and ("localhost:8080" in user_text or "127.0.0.1:8080" in user_text) and "code=" in user_text:
        logger.info(f"Detected Google OAuth redirect URL pasted in chat {chat_id}: {user_text}")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        from src import google_auth
        try:
            loop = asyncio.get_running_loop()
            # Run the manual credentials exchange in executor to avoid blocking the async loop
            await loop.run_in_executor(None, google_auth.handle_manual_callback, chat_id, user_text)
            
            # Send confirmation
            confirm_text = (
                "🔑 <b>구글 계정 연동이 성공적으로 완료되었습니다!</b>\n\n"
                "이제 자연어로 캘린더 일정을 조회/추가하고, 지메일을 관리해보세요.\n"
                "- 예시: <i>\"내일 내 일정 알려줘\"</i>, <i>\"지메일 읽지 않은 메일 요약해줘\"</i>"
            )
            await send_safe_message(
                update=update,
                text=confirm_text,
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )
            logger.info(f"Manual Google OAuth link processed successfully for chat {chat_id}")
            return
        except Exception as e:
            logger.error(f"Failed to process manual OAuth link: {e}", exc_info=True)
            await update.message.reply_text(f"❌ 구글 연동 중 오류가 발생했습니다:\n\n{str(e)}")
            return
            
    # 1. Intercept Persistent Bottom Keyboard button clicks
    if user_text == "📋 나의 할 일":
        await tasks_command(update, context)
        return
    elif user_text == "📅 오늘의 일정":
        logger.info(f"Keyboard Intercept: Google Calendar events for chat {chat_id}")
        tools.current_chat_id.set(chat_id)
        try:
            calendar_info = tools.list_google_calendar_events()
            keyboard = [[InlineKeyboardButton("📅 일정 새로고침", callback_data="refresh_calendar")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = f"📅 <b>실시간 일정 정보 조회 완료</b>\n\n{calendar_info}"
            await send_safe_message(update=update, text=text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            from src import google_auth
            if isinstance(e, google_auth.GoogleAuthRequiredError) or "GoogleAuthRequiredError" in str(e):
                await handle_google_auth_required(update, context, chat_id)
            else:
                await update.message.reply_text(f"❌ 일정 조회 중 오류 발생: {str(e)}")
        return
    elif user_text == "🌦️ 실시간 날씨":
        await update.message.reply_text(
            "어느 지역의 날씨를 알려드릴까요? (예: 서울, 도쿄)",
            reply_markup=get_main_keyboard()
        )
        return
    elif user_text == "📩 새 메일 요약":
        logger.info(f"Keyboard Intercept: Gmail unread emails for chat {chat_id}")
        tools.current_chat_id.set(chat_id)
        try:
            email_info = tools.list_unread_emails()
            keyboard = [[InlineKeyboardButton("📩 메일 새로고침", callback_data="refresh_emails")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = f"📩 <b>실시간 안읽은 메일 요약 완료</b>\n\n{email_info}"
            await send_safe_message(update=update, text=text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            from src import google_auth
            if isinstance(e, google_auth.GoogleAuthRequiredError) or "GoogleAuthRequiredError" in str(e):
                await handle_google_auth_required(update, context, chat_id)
            else:
                await update.message.reply_text(f"❌ 메일 요약 중 오류 발생: {str(e)}")
        return
    elif user_text == "⚙️ 비서 설정":
        await settings_command(update, context)
        return
    elif "주간 보고서" in user_text or "주간 리포트" in user_text:
        await weekly_report_command(update, context)
        return
    elif user_text == "📰 주요 뉴스":
        logger.info(f"Keyboard Intercept: Category list for chat {chat_id}")
        keyboard = [
            [
                InlineKeyboardButton("💼 경제", callback_data="news_cat:economy"),
                InlineKeyboardButton("🔬 테크", callback_data="news_cat:tech"),
                InlineKeyboardButton("⚖️ 정치", callback_data="news_cat:politics")
            ],
            [
                InlineKeyboardButton("🌍 국제", callback_data="news_cat:world"),
                InlineKeyboardButton("⚽ 스포츠/연예", callback_data="news_cat:entertainment"),
                InlineKeyboardButton("🌟 맞춤 뉴스", callback_data="news_my")
            ],
            [
                InlineKeyboardButton("⚙️ 뉴스 설정 안내", callback_data="info_news_settings")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            "📰 <b>주요 뉴스 브리핑 카테고리</b>\n\n"
            "보고 싶으신 뉴스 분야를 선택해 주세요.\n"
            "<b>[🌟 맞춤 뉴스]</b>를 선택하시면 사용자가 등록하신 관심 키워드 기반의 뉴스를 수집해 드립니다."
        )
        await send_safe_message(update=update, text=text, reply_markup=reply_markup, parse_mode="HTML")
        return
    elif user_text == "🔍 메모 검색":
        await update.message.reply_text(
            "검색하고 싶은 메모의 키워드를 입력해 주세요 (예: '아이디어' 또는 '일기').",
            reply_markup=get_main_keyboard()
        )
        return
    elif user_text == "❓ 도움말":
        await help_command(update, context)
        return
        
    # 2. General Agent Processing
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        loop = asyncio.get_running_loop()
        logger.info("Sending request to Gemini agent...")
        reply_text, tools_run = await loop.run_in_executor(None, agent.process_message, chat_id, user_text)
        
        # Build context-aware inline action buttons based on what tools Gemini used
        keyboard = []
        if "get_current_weather" in tools_run:
            keyboard.append([InlineKeyboardButton("🔄 날씨 새로고침", callback_data="refresh_weather")])
        if "web_search" in tools_run:
            keyboard.append([InlineKeyboardButton("📰 관련 뉴스 더 검색", callback_data="refresh_news")])
        if "create_local_task" in tools_run or "complete_local_task" in tools_run:
            keyboard.append([InlineKeyboardButton("📋 나의 할 일 목록 보기", callback_data="refresh")])
        if "save_note" in tools_run:
            keyboard.append([InlineKeyboardButton("🔍 메모 검색", callback_data="prompt_search_notes")])
        if "list_google_calendar_events" in tools_run or "create_google_calendar_event" in tools_run:
            keyboard.append([InlineKeyboardButton("📅 일정 새로고침", callback_data="refresh_calendar")])
        if "list_unread_emails" in tools_run or "send_email_via_gmail" in tools_run or "search_emails_via_gmail" in tools_run:
            keyboard.append([InlineKeyboardButton("📩 메일 새로고침", callback_data="refresh_emails")])
        if "add_expense_tool" in tools_run:
            import re
            match = re.search(r"with ID (\d+)", reply_text)
            if match:
                expense_id = int(match.group(1))
                keyboard.append([
                    InlineKeyboardButton("📊 소비 분석 조회", callback_data="expense_stat"),
                    InlineKeyboardButton("❌ 지출 삭제", callback_data=f"expense_del:{expense_id}")
                ])
            else:
                keyboard.append([InlineKeyboardButton("📊 소비 분석 조회", callback_data="expense_stat")])
        if "get_expense_summary_tool" in tools_run:
            keyboard.append([InlineKeyboardButton("🔄 소비 분석 새로고침", callback_data="expense_stat")])
        if any(t in tools_run for t in ["add_dday_tool", "delete_dday_tool", "list_ddays_tool"]):
            keyboard.append([
                InlineKeyboardButton("📅 D-Day 목록 조회", callback_data="dday_list"),
                InlineKeyboardButton("⚙️ 설정 탭 이동", callback_data="refresh_settings")
            ])
        if "propose_travel_itinerary" in tools_run:
            import re
            match = re.search(r"플랜 ID: (\d+)", reply_text)
            if match:
                plan_id = int(match.group(1))
                keyboard.append([InlineKeyboardButton("📅 구글 캘린더에 전체 등록", callback_data=f"travel_apply:{plan_id}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        logger.info(f"Replying to chat {chat_id}...")
        await send_safe_message(
            update=update,
            text=reply_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        logger.info("Reply sent successfully.")
    except Exception as e:
        from src import google_auth
        if isinstance(e, google_auth.GoogleAuthRequiredError) or "GoogleAuthRequiredError" in str(e):
            await handle_google_auth_required(update, context, chat_id)
            return
            
        logger.error(f"Error handling message: {e}", exc_info=True)
        await update.message.reply_text(
            f"오류가 발생했습니다: {str(e)}\n\n"
            f"`.env` 파일에 텔레그램 토큰과 Gemini API 키가 올바르게 설정되었는지 확인해 주세요."
        )

async def post_init(application: Application) -> None:
    """Send a startup message to all active chats in the database and register commands."""
    logger.info("Running post_init startup tasks...")
    
    # Register commands with Telegram
    from telegram import BotCommand
    commands = [
        BotCommand("start", "비서 AI 에이전트 시작 및 안내"),
        BotCommand("help", "사용 방법 및 명령어 목록 안내"),
        BotCommand("tasks", "📋 나의 할 일 목록 확인 및 관리"),
        BotCommand("settings", "⚙️ 비서 서비스 통합 설정 관리"),
        BotCommand("login", "🔑 구글 계정(캘린더/지메일) 연동 로그인"),
        BotCommand("logout", "🔓 구글 계정 연동 해제"),
        BotCommand("report", "📊 주간 종합 분석 리포트 이미지 조회")
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Successfully registered bot command list with Telegram.")
    except Exception as e:
        logger.error(f"Failed to register commands with Telegram: {e}")

    conn = database.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_history'")
        if cursor.fetchone():
            cursor.execute("SELECT DISTINCT chat_id FROM chat_history")
            chat_ids = [row[0] for row in cursor.fetchall()]
        else:
            chat_ids = []
    except Exception as e:
        logger.error(f"Error querying chat IDs for startup: {e}")
        chat_ids = []
    finally:
        conn.close()
        
    logger.info(f"Found {len(chat_ids)} active chats to notify on startup.")
    for chat_id in chat_ids:
        try:
            logger.info(f"Sending startup notification to chat {chat_id}...")
            text = (
                "🤖 <b>개인 비서 AI 에이전트가 온라인 상태가 되었습니다!</b>\n"
                "무엇을 도와드릴까요?\n"
                "- 할 일 목록을 보시려면 아래 키보드의 <code>📋 나의 할 일</code> 버튼을 누르거나 /tasks 를 입력해 주세요."
            )
            await send_safe_message(
                chat_id=chat_id,
                bot=application.bot,
                text=text,
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to send startup message to chat {chat_id}: {e}")

    # Start the Google Calendar background reminders scheduler
    try:
        from src import reminders
        reminders.start_reminder_scheduler(application)
        logger.info("Successfully started Google Calendar background reminders scheduler.")
    except Exception as e:
        logger.error(f"Failed to start Google Calendar reminders scheduler: {e}", exc_info=True)

async def render_quiz_question(query, context, session_id: int, index: int, session_data: dict = None):
    """Helper to render a specific quiz question using inline keyboard buttons."""
    if not session_data:
        session_data = database.get_quiz_session(session_id)
    if not session_data:
        if query and query.message:
            await query.message.reply_text("❌ 유효하지 않은 퀴즈 세션입니다.")
        return
        
    questions = json.loads(session_data["questions_json"])
    if index >= len(questions):
        await render_quiz_result(query, context, session_id, session_data)
        return
        
    q = questions[index]
    title = session_data["title"]
    
    text_lines = [
        f"📖 <b>[{html.escape(title)}] 복습 퀴즈</b>\n",
        f"<b>Q {index+1}. {html.escape(q['question'])}</b>\n"
    ]
    
    keyboard = []
    option_letters = ["A", "B", "C", "D"]
    for idx, opt in enumerate(q["options"]):
        text_lines.append(f"<b>{option_letters[idx]}.</b> {html.escape(opt)}")
        button = InlineKeyboardButton(text=option_letters[idx], callback_data=f"quiz_ans:{session_id}:{idx}")
        keyboard.append(button)
        
    reply_markup = InlineKeyboardMarkup([keyboard])
    
    await send_safe_message(
        chat_id=query.message.chat_id,
        bot=context.bot,
        text="\n".join(text_lines),
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def render_quiz_result(query, context, session_id: int, session_data: dict = None):
    """Helper to render the final results of a quiz."""
    if not session_data:
        session_data = database.get_quiz_session(session_id)
    if not session_data:
        return
        
    questions = json.loads(session_data["questions_json"])
    total = len(questions)
    score = session_data["score"]
    
    rate = (score / total) * 100 if total > 0 else 0
    if rate == 100:
        comment = "🎉 대단합니다! 완벽하게 이해하셨네요! 👍"
    elif rate >= 60:
        comment = "👏 훌륭합니다! 대부분의 핵심을 잘 짚어내셨어요."
    else:
        comment = "💡 좋은 시도였습니다! 다시 한번 학습해 보시는 것을 권장합니다."
        
    text = (
        f"📊 <b>퀴즈 학습 결과 보고</b>\n\n"
        f"📌 <b>제목:</b> {html.escape(session_data['title'])}\n"
        f"🎯 <b>최종 점수:</b> <code>{score} / {total} 문제 정답</code> ({rate:.1f}%)\n\n"
        f"{comment}\n\n"
        f"⚙️ 퀴즈 요약 정보는 <b>비서 설정</b>(/settings)에서 누적 학습 통계로 확인하실 수 있습니다."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("➕ 10문제 추가 풀기", callback_data=f"quiz_add_more:{session_id}")
        ],
        [
            InlineKeyboardButton("⚙️ 설정 보기", callback_data="refresh_settings"),
            InlineKeyboardButton("📖 이력 조회", callback_data="quiz_history")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await send_safe_message(
        chat_id=query.message.chat_id,
        bot=context.bot,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def process_url_link(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str):
    """Helper to extract content from a URL, generate summary + quiz, and display start prompt."""
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    loading_msg = await update.message.reply_text("🔗 <b>링크를 감지했습니다. 본문을 분석하고 요약 및 퀴즈를 생성하는 중입니다...</b> ⏳", parse_mode="HTML")
    
    try:
        if quiz_helper.is_youtube_url(url):
            video_id = quiz_helper.extract_youtube_id(url)
            if not video_id:
                raise ValueError("유튜브 비디오 ID를 추출하지 못했습니다.")
            res = await asyncio.get_running_loop().run_in_executor(None, quiz_helper.fetch_youtube_transcript, video_id)
            title = res["title"]
            content = res["content"]
            is_fallback = res["is_fallback"]
        else:
            res = await asyncio.get_running_loop().run_in_executor(None, quiz_helper.fetch_web_article_text, url)
            title = res["title"]
            content = res["content"]
            is_fallback = False
            
        if not content or not content.strip():
            raise ValueError("본문 내용을 추출할 수 없습니다.")
            
        # Call Gemini model via agent helper
        data = await asyncio.get_running_loop().run_in_executor(None, agent.generate_summary_and_quiz, title, content)
        summary = data.get("summary", "")
        questions = data.get("questions", [])
        
        if not questions:
            raise ValueError("퀴즈 질문이 생성되지 않았습니다.")
            
        questions_json = json.dumps(questions, ensure_ascii=False)
        session_id = database.create_quiz_session(chat_id, title, questions_json, content)
        
        fallback_notice = "\n\n⚠️ <i>(안내) 자막이 제공되지 않아 동영상 메타데이터와 웹 검색을 기반으로 퀴즈를 구성했습니다.</i>" if is_fallback else ""
        text = (
            f"📖 <b>콘텐츠 분석 및 요약 완료</b>\n\n"
            f"📌 <b>제목:</b> {html.escape(title)}\n\n"
            f"{summary}"
            f"{fallback_notice}\n\n"
            f"💡 <i>요약을 확인하셨다면 아래 [📖 퀴즈 시작] 버튼을 눌러 복습 퀴즈를 풀어보세요! (총 10문제)</i>"
        )
        
        keyboard = [[InlineKeyboardButton("📖 퀴즈 시작", callback_data=f"quiz_start:{session_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await loading_msg.delete()
        await send_safe_message(
            update=update,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to process URL {url}: {e}", exc_info=True)
        await loading_msg.edit_text(f"❌ 요약 및 퀴즈 생성에 실패했습니다: {str(e)}")

def main() -> None:
    database.init_db()
    
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        print(f"\n[Error] {e}")
        print("Please edit the .env file in the workspace root and fill in the values.\n")
        return

    request_config = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)

    application = (
        Application.builder()
        .token(Config.TELEGRAM_BOT_TOKEN)
        .request(request_config)
        .post_init(post_init)
        .build()
    )

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("logout", logout_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("report", weekly_report_command))

    # Inline button callback handler
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Voice message handler
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Non-command text message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot
    print("Starting bot... Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
