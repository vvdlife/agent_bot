import asyncio
import logging
import datetime
import html
from datetime import timezone, timedelta
from googleapiclient.discovery import build
from src.config import Config
from src import database, google_auth, tools
from src.main import send_safe_message

logger = logging.getLogger(__name__)

# 한국 표준시(KST) 설정
KST = timezone(timedelta(hours=9))

def format_event_time(start_str: str, end_str: str) -> str:
    """일정 시작 및 종료 시간을 친절한 포맷으로 변환합니다."""
    if not start_str:
        return "시간 정보 없음"
    
    # 올데이 일정 (예: 2026-06-11)
    if 'T' not in start_str:
        return f"{start_str} (하루 종일)"
        
    try:
        # ISO 8601 파싱 (Z 접미사 고려)
        # Python 3.11+ 에서는 Z 파싱을 완벽히 지원하지만 하위 호환성을 위해 처리
        start_clean = start_str.replace('Z', '+00:00')
        dt_start = datetime.datetime.fromisoformat(start_clean).astimezone(KST)
        
        start_f = dt_start.strftime("%m월 %d일 %p %I:%M").replace("AM", "오전").replace("PM", "오후")
        
        if end_str:
            end_clean = end_str.replace('Z', '+00:00')
            dt_end = datetime.datetime.fromisoformat(end_clean).astimezone(KST)
            # 같은 날인지 확인
            if dt_start.date() == dt_end.date():
                end_f = dt_end.strftime("%p %I:%M").replace("AM", "오전").replace("PM", "오후")
            else:
                end_f = dt_end.strftime("%m월 %d일 %p %I:%M").replace("AM", "오전").replace("PM", "오후")
            return f"{start_f} ~ {end_f}"
        
        return start_f
    except Exception as e:
        logger.error(f"Error formatting event time (start: {start_str}, end: {end_str}): {e}")
        return start_str

async def notify_google_auth_required(chat_id: int, bot):
    """Sends a one-time Google OAuth authorization reminder to the user and starts the redirect server."""
    try:
        # Check if already notified to prevent spam
        settings = database.get_user_settings(chat_id)
        if settings.get("google_auth_expiry_notified", 0) == 1:
            logger.info(f"User {chat_id} already notified of Google auth expiry. Skipping reminder.")
            return

        from src import google_auth
        import asyncio
        
        auth_url, flow, state = google_auth.generate_auth_url(chat_id)
        loop = asyncio.get_running_loop()
        google_auth.start_redirect_server(chat_id, flow, state, bot, loop)
        
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [[InlineKeyboardButton("🔑 Google 계정 연동하기", url=auth_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            "🔒 <b>구글 계정 연동 만료 안내</b>\n\n"
            "구글 계정 인증이 만료되었거나 연동 정보가 올바르지 않아 일정을 조회할 수 없습니다.\n"
            "백그라운드 일정 알림 및 메일 모니터링을 지속하시려면 아래 버튼을 눌러 계정을 재연동해 주세요!"
        )
        
        await send_safe_message(
            chat_id=chat_id,
            bot=bot,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        
        # Mark as notified
        database.save_user_setting(chat_id, "google_auth_expiry_notified", "1")
        logger.info(f"Sent Google auth expiry notification to chat {chat_id}.")
        
    except Exception as e:
        logger.error(f"Failed to send Google auth required notification to chat {chat_id}: {e}", exc_info=True)


async def check_and_send_reminders(application):
    """모든 연동된 사용자의 캘린더를 스캔하여 15분 이내 일정을 알림 발송합니다."""
    logger.info("Starting Google Calendar proactive reminders scan...")
    
    # 1. DB에서 연동된 모든 사용자 조회
    try:
        chat_ids = database.get_all_authenticated_users()
    except Exception as e:
        logger.error(f"Failed to fetch authenticated users for Calendar: {e}", exc_info=True)
        return
        
    if not chat_ids:
        logger.debug("No authenticated users found for Calendar reminders.")
        return
        
    # [현재 시각, 현재 시각 + 15분] 윈도우 계산
    now = datetime.datetime.now(timezone.utc)
    lead_time = Config.CALENDAR_REMINDER_LEAD_MINUTES
    time_max_dt = now + timedelta(minutes=lead_time)
    
    # API 요청을 위한 ISO 8601 포맷
    time_min = now.isoformat().replace('+00:00', 'Z')
    time_max = time_max_dt.isoformat().replace('+00:00', 'Z')
    
    for chat_id in chat_ids:
        try:
            # 2. 유저별 구글 자격 증명 획득 및 서비스 빌드
            creds = google_auth.get_google_credentials(chat_id)
            service = build('calendar', 'v3', credentials=creds)
            
            # 3. 일정 범위 이내의 이벤트 가져오기
            events_result = service.events().list(
                calendarId='primary',
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            for event in events:
                event_id = event.get('id')
                if not event_id:
                    continue
                    
                # 4. 중복 발송 여부 체크
                if database.is_reminder_sent(chat_id, event_id):
                    logger.debug(f"Reminder already sent for event {event_id} to chat {chat_id}. Skipping.")
                    continue
                    
                # 5. HTML 알림 템플릿 빌드
                summary = event.get('summary', '제목 없음')
                summary_esc = html.escape(summary)
                start_time = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
                end_time = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
                location = event.get('location', '')
                location_esc = html.escape(location)
                description = event.get('description', '')
                
                formatted_time = format_event_time(start_time, end_time)
                
                # HTML 알림 메시지 구성
                text = (
                    f"🔔 <b>[일정 시작 알림]</b>\n"
                    f"회장님, 곧 시작하는 일정이 있습니다. 준비해 주시기 바랍니다.\n\n"
                    f"• <b>일정명</b>: <code>{summary_esc}</code>\n"
                    f"• <b>시간</b>: <code>{formatted_time}</code>"
                )
                if location:
                    text += f"\n• <b>장소</b>: <code>{location_esc}</code>"
                if description:
                    # 간단하게 120자까지만 표시 (의사결정 반영)
                    desc_short = description[:120] + "..." if len(description) > 120 else description
                    text += f"\n• <b>설명</b>: <code>{html.escape(desc_short)}</code>"
                
                # 6. 알림 발송
                logger.info(f"Sending proactive reminder for event '{summary}' (ID: {event_id}) to chat {chat_id}.")
                await send_safe_message(
                    chat_id=chat_id,
                    bot=application.bot,
                    text=text,
                    parse_mode="HTML"
                )
                
                # 7. 발송 완료 데이터베이스 기록
                database.save_sent_reminder(chat_id, event_id, start_time)
                
        except google_auth.GoogleAuthRequiredError as ae:
            logger.warning(f"Google authorization expired or not found for chat {chat_id}: {ae}")
            await notify_google_auth_required(chat_id, application.bot)
        except Exception as e:
            logger.error(f"Error checking calendar reminders for chat {chat_id}: {e}", exc_info=True)
            
    # 8. 매 폴링 루프마다 오래된 발송 기록 정리 (1일 경과 데이터 정리)
    try:
        database.cleanup_old_reminders(days=1)
    except Exception as e:
        logger.error(f"Failed to cleanup old reminders in database: {e}")

async def check_and_send_email_alerts(application):
    """모든 연동된 사용자의 중요 지메일을 감지하여 알림 발송합니다."""
    logger.info("Starting Gmail proactive alerts scan...")
    
    try:
        chat_ids = database.get_all_authenticated_users()
    except Exception as e:
        logger.error(f"Failed to fetch authenticated users for Gmail: {e}", exc_info=True)
        return
        
    if not chat_ids:
        logger.debug("No authenticated users found for Gmail alerts.")
        return
        
    # 최근 10분 이내 도착한 이메일만 확인하기 위한 타임스탬프 계산 (밀리초)
    poll_seconds = Config.CALENDAR_POLL_INTERVAL_SECONDS
    time_threshold_ms = int((datetime.datetime.now(timezone.utc) - timedelta(seconds=poll_seconds + 120)).timestamp() * 1000)
    
    for chat_id in chat_ids:
        try:
            # 1. 사용자의 필터 규칙 조회
            filters = database.get_gmail_filters(chat_id)
            if not filters:
                continue
                
            # 2. 유저별 구글 자격 증명 획득 및 서비스 빌드
            creds = google_auth.get_google_credentials(chat_id)
            service = build('gmail', 'v1', credentials=creds)
            
            # 3. 안읽은 메시지 목록 가져오기
            results = service.users().messages().list(userId='me', q='is:unread', maxResults=20).execute()
            messages = results.get('messages', [])
            if not messages:
                continue
                
            for msg_summary in messages:
                msg_id = msg_summary['id']
                
                # 4. 중복 발송 여부 체크
                if database.is_email_alert_sent(chat_id, msg_id):
                    continue
                    
                # 5. 메일 상세 정보 로드
                msg_data = service.users().messages().get(
                    userId='me', 
                    id=msg_id, 
                    format='metadata', 
                    metadataHeaders=['From', 'Subject', 'Date']
                ).execute()
                
                # 6. 수신 시각 검증
                internal_date = int(msg_data.get('internalDate', '0'))
                if internal_date < time_threshold_ms:
                    continue
                    
                # 7. 헤더 정보 추출
                headers = msg_data.get('payload', {}).get('headers', [])
                sender = ""
                subject = ""
                for h in headers:
                    if h['name'] == 'From':
                        sender = h['value']
                    elif h['name'] == 'Subject':
                        subject = h['value']
                        
                # 8. 필터 매칭 수행
                matched = False
                matched_rule = None
                
                for f in filters:
                    f_type = f['filter_type']
                    f_val = f['value'].lower()
                    
                    if f_type == 'sender':
                        if f_val in sender.lower():
                            matched = True
                            matched_rule = f"발신자: {f['value']}"
                            break
                    elif f_type == 'keyword':
                        if f_val in subject.lower():
                            matched = True
                            matched_rule = f"키워드: {f['value']}"
                            break
                            
                if not matched:
                    continue
                    
                # 9. 요약 스니펫 및 HTML 메시지 구성
                snippet = msg_data.get('snippet', '내용 없음')
                
                import html
                sender_esc = html.escape(sender)
                subject_esc = html.escape(subject)
                snippet_esc = html.escape(snippet)
                
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                keyboard = [[InlineKeyboardButton("📩 메일 새로고침", callback_data="refresh_emails")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                text = (
                    f"🔔 <b>[중요 메일 알림]</b>\n"
                    f"회장님, 설정하신 필터 조건({matched_rule})에 맞는 새 메일이 도착했습니다.\n\n"
                    f"• <b>보낸 사람</b>: <code>{sender_esc}</code>\n"
                    f"• <b>제목</b>: <code>{subject_esc}</code>\n"
                    f"• <b>요약</b>: <code>{snippet_esc}</code>"
                )
                
                # 10. 알림 발송
                logger.info(f"Sending proactive Gmail alert for message ID {msg_id} to chat {chat_id}.")
                await send_safe_message(
                    chat_id=chat_id,
                    bot=application.bot,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                
                # 11. 발송 완료 기록
                database.save_sent_email_alert(chat_id, msg_id)
                
        except google_auth.GoogleAuthRequiredError as ae:
            logger.warning(f"Google authorization expired or not found for Gmail chat {chat_id}: {ae}")
            await notify_google_auth_required(chat_id, application.bot)
        except Exception as e:
            logger.error(f"Error checking Gmail alerts for chat {chat_id}: {e}", exc_info=True)
            
    try:
        database.cleanup_old_email_alerts(days=7)
    except Exception as e:
        logger.error(f"Failed to cleanup old Gmail alerts in database: {e}")

async def check_and_send_briefings(application):
    """지정된 아침 시각이 지난 경우 오늘의 아침 브리핑(날씨 + 캘린더 일정 + 할 일 마감 + D-Day)을 발송합니다."""
    logger.info("Starting Daily Briefing checks...")
    
    try:
        chat_ids = database.get_all_authenticated_users()
    except Exception as e:
        logger.error(f"Failed to fetch authenticated users for Daily Briefing: {e}", exc_info=True)
        return
        
    if not chat_ids:
        logger.debug("No authenticated users found for Daily Briefing.")
        return
        
    kst_now = datetime.datetime.now(KST)
    today_str = kst_now.strftime("%Y-%m-%d")
    current_time_str = kst_now.strftime("%H:%M")
    
    for chat_id in chat_ids:
        try:
            # 1. 오늘 이미 브리핑을 보냈는지 확인
            if database.is_briefing_sent(chat_id, today_str):
                continue
                
            # 2. 사용자 설정 로드
            settings = database.get_user_settings(chat_id)
            target_time_str = settings.get('briefing_time', '08:00')
            location = settings.get('location', 'Seoul')
            
            # 3. 설정된 브리핑 시각이 지났는지 검증
            if current_time_str < target_time_str:
                continue
                
            logger.info(f"Generating Daily Briefing for chat {chat_id} (Target: {target_time_str}, Location: {location}).")
            tools.current_chat_id.set(chat_id)
            
            # --- 3.5. D-Day 및 기념일 수집 ---
            ddays_text = "진행 중인 D-Day가 없습니다."
            try:
                ddays = database.get_ddays(chat_id)
                if ddays:
                    kst_tz = datetime.timezone(datetime.timedelta(hours=9))
                    today = datetime.datetime.now(kst_tz).date()
                    lines = []
                    for d in ddays:
                        target_dt = datetime.datetime.strptime(d['target_date'], "%Y-%m-%d").date()
                        diff = (target_dt - today).days
                        if diff > 0:
                            dday_str = f"D-{diff}"
                        elif diff == 0:
                            dday_str = "D-Day"
                        else:
                            dday_str = f"D+{abs(diff)}"
                        lines.append(f"• <b>{html.escape(d['title'])}</b>: <code>{dday_str}</code>")
                    ddays_text = "\n".join(lines)
            except Exception as dde:
                logger.error(f"Failed to fetch ddays for briefing: {dde}")
                ddays_text = "D-Day 정보를 불러오는 도중 오류가 발생했습니다."

            # --- 4. 날씨 정보 수집 ---
            try:
                loop = asyncio.get_running_loop()
                weather_info = await loop.run_in_executor(None, tools.get_current_weather, location)
            except Exception as we:
                logger.error(f"Failed to fetch weather for briefing: {we}")
                weather_info = "날씨 정보를 불러오는 데 실패했습니다."
                
            # --- 5. 구글 캘린더 일정 수집 (오늘 00:00:00 ~ 23:59:59 KST) ---
            google_events_text = "오늘 예정된 일정이 없습니다."
            try:
                creds = google_auth.get_google_credentials(chat_id)
                service = build('calendar', 'v3', credentials=creds)
                
                kst_start = datetime.datetime(kst_now.year, kst_now.month, kst_now.day, 0, 0, 0, tzinfo=KST)
                kst_end = datetime.datetime(kst_now.year, kst_now.month, kst_now.day, 23, 59, 59, tzinfo=KST)
                time_min = kst_start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
                time_max = kst_end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
                
                events_result = service.events().list(
                    calendarId='primary',
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                if events:
                    lines = []
                    for ev in events:
                        summary = ev.get('summary', '제목 없음')
                        summary_esc = html.escape(summary)
                        start = ev.get('start', {}).get('dateTime') or ev.get('start', {}).get('date')
                        end = ev.get('end', {}).get('dateTime') or ev.get('end', {}).get('date')
                        formatted_time = format_event_time(start, end)
                        loc = ev.get('location', '')
                        loc_str = f" (장소: {html.escape(loc)})" if loc else ""
                        lines.append(f"• <b>{summary_esc}</b> ({formatted_time}){loc_str}")
                    google_events_text = "\n".join(lines)
            except google_auth.GoogleAuthRequiredError:
                google_events_text = "🔒 구글 계정 연동 정보가 만료되어 일정을 조회할 수 없습니다. /login 으로 갱신해주세요."
            except Exception as ce:
                logger.error(f"Failed to fetch calendar for briefing: {ce}")
                google_events_text = "일정 정보를 불러오는 도중 오류가 발생했습니다."
                
            # --- 6. 로컬 할 일(Tasks) 수집 (오늘 기한 및 마감 기한이 지난 미완료 작업) ---
            tasks_text = "오늘 마감인 할 일이 없습니다."
            try:
                pending_tasks = database.list_tasks(status='pending')
                due_tasks = []
                for t in pending_tasks:
                    due = t.get('due_date')
                    if due:
                        due_day = due[:10]
                        if due_day <= today_str:
                            due_tasks.append(t)
                            
                if due_tasks:
                    lines = []
                    for t in due_tasks:
                        due_label = f" (기한: {html.escape(t['due_date'])})" if t['due_date'] else ""
                        lines.append(f"• <b>[#{t['id']}] {html.escape(t['title'])}</b>{due_label}")
                    tasks_text = "\n".join(lines)
            except Exception as te:
                logger.error(f"Failed to fetch tasks for briefing: {te}")
                tasks_text = "할 일 목록을 불러오는 도중 오류가 발생했습니다."
                
            # --- 6.5. 관심 키워드 뉴스 수집 ---
            news_text = "오늘의 주요 뉴스 정보가 없습니다."
            briefing_news_buttons = []
            try:
                keywords = database.get_news_keywords(chat_id)
                import html as html_lib
                loop = asyncio.get_running_loop()
                
                if keywords:
                    import src.agent as agent_module
                    # 등록된 관심 키워드 기반 뉴스 수집 (최대 3개 키워드 비동기 동시 조회, 백그라운드 엇갈림 딜레이 0.3초)
                    target_kws = keywords[:3]
                    tasks_list = [tools.fetch_and_filter_keyword_news(kw, delay=idx * 0.3) for idx, kw in enumerate(target_kws)]
                    
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
                        filter_tasks.append(agent_module.filter_articles_by_category_or_keyword(res, kw, is_category=False))
                        
                    filtered_results = await asyncio.gather(*filter_tasks, return_exceptions=True)
                    
                    lines = []
                    for idx, kw in enumerate(target_kws):
                        res = results[idx]
                        if isinstance(res, Exception):
                            continue
                        
                        filtered_articles = filtered_results[idx]
                        if isinstance(filtered_articles, Exception) or not filtered_articles:
                            # fallback to URL-filtered list if AI filtering failed or returned empty
                            filtered_articles = res if not isinstance(res, Exception) else []
                            
                        if filtered_articles:
                            # 키워드 당 최고 1개만 요약 노출해 브리핑 카드 길이 조절
                            art = filtered_articles[0]
                            database.add_sent_news(chat_id, art['href'])
                            art_id = database.save_news_article_cache(art['href'], art['title'], art.get('summary'))
                            
                            title_esc = html_lib.escape(art['title'])
                            href_esc = html_lib.escape(art['href'])
                            summary_esc = html_lib.escape(art.get('summary') or '')
                            summary_line = f"\n  <i>{summary_esc}</i>" if summary_esc else ""
                            
                            lines.append(f"• <b>[#{kw}]</b> <a href=\"{href_esc}\">{title_esc}</a>{summary_line}")
                            briefing_news_buttons.append({
                                'label': f"🔗 [#{kw}] 원문",
                                'url': art['href']
                            })
                            briefing_news_buttons.append({
                                'label': f"📝 [#{kw}] 요약/퀴즈",
                                'callback_data': f"news_summarize:{art_id}"
                            })
                    if lines:
                        news_text = "\n".join(lines)
                
                # 관심 키워드가 없거나, 관심 키워드 기사 결과가 0건인 경우 일반 주요 뉴스 폴백
                if not keywords or not briefing_news_buttons:
                    # 등록된 키워드가 없거나 결과가 없으면 일반 "오늘 주요 뉴스" 3건 수집
                    articles = await tools.fetch_and_filter_category_news("오늘 주요 뉴스 기사")
                    if articles:
                        import src.agent as agent_module
                        # 폴백 기사도 AI 필터링 및 요약 단계를 거치도록 적용
                        filtered_articles = await agent_module.filter_articles_by_category_or_keyword(articles, "일반 주요 뉴스", is_category=True)
                        if not filtered_articles:
                            filtered_articles = articles
                            
                        lines = []
                        for idx, art in enumerate(filtered_articles[:3], start=1):
                            database.add_sent_news(chat_id, art['href'])
                            art_id = database.save_news_article_cache(art['href'], art['title'], art.get('summary'))
                            
                            title_esc = html_lib.escape(art['title'])
                            href_esc = html_lib.escape(art['href'])
                            summary_esc = html_lib.escape(art.get('summary') or '')
                            summary_line = f"\n  <i>{summary_esc}</i>" if summary_esc else ""
                            
                            lines.append(f"• <a href=\"{href_esc}\">{title_esc}</a>{summary_line}")
                            briefing_news_buttons.append({
                                'label': f"🔗 주요 뉴스 #{idx} 원문",
                                'url': art['href']
                            })
                            briefing_news_buttons.append({
                                'label': f"📝 뉴스 #{idx} 요약/퀴즈",
                                'callback_data': f"news_summarize:{art_id}"
                            })
                        news_text = "\n".join(lines)
            except Exception as ne:
                logger.error(f"Failed to fetch news for briefing: {ne}")
                news_text = "뉴스 정보를 불러오는 도중 오류가 발생했습니다."
                
            # --- 6.6. 이번 달 누적 지출 정보 수집 ---
            expense_text = "이번 달 지출 내역이 없습니다."
            try:
                start_date = kst_now.replace(day=1).strftime("%Y-%m-%d")
                end_date = kst_now.strftime("%Y-%m-%d")
                expenses = database.get_expenses(chat_id, start_date, end_date)
                total_spent = sum(item['amount'] for item in expenses) if expenses else 0
                expense_text = f"이번 달 누적 지출액은 <b>{total_spent:,}원</b>입니다."
            except Exception as ee:
                logger.error(f"Failed to fetch monthly expenses for briefing: {ee}")
                expense_text = "지출 정보를 불러오는 도중 오류가 발생했습니다."
                
            # --- 6.5 오답 복습 현황 조회 ---
            incorrect_text = "보관된 오답 문제가 없습니다. 👍"
            incorrect_count = 0
            try:
                incorrect_count = database.get_incorrect_notes_count(chat_id)
                if incorrect_count > 0:
                    incorrect_text = f"현재 복습해야 할 오답 문제가 <b>{incorrect_count}개</b> 있습니다. 틀린 문제를 다시 확인해 보세요!"
            except Exception as ee:
                logger.error(f"Failed to fetch incorrect notes count for briefing: {ee}")
                incorrect_text = "오답 정보를 불러오는 도중 오류가 발생했습니다."
                
            # --- 7. HTML 브리핑 메시지 템플릿 조립 ---
            text = (
                f"☀️ <b>[오늘의 아침 브리핑]</b>\n"
                f"회장님, 안녕하십니까! 좋은 아침입니다. 오늘 하루도 보람차게 시작해 보세요. ☕\n\n"
                f"📆 <b>D-Day 알림</b>\n"
                f"{ddays_text}\n\n"
                f"🌦️ <b>오늘의 날씨 ({location})</b>\n"
                f"{weather_info}\n\n"
                f"📅 <b>오늘의 구글 일정</b>\n"
                f"{google_events_text}\n\n"
                f"📋 <b>오늘 마감인 할 일</b>\n"
                f"{tasks_text}\n\n"
                f"📰 <b>오늘의 주요 뉴스</b>\n"
                f"{news_text}\n\n"
                f"💸 <b>이번 달 누적 지출</b>\n"
                f"{expense_text}\n\n"
                f"❌ <b>오답 복습 현황</b>\n"
                f"{incorrect_text}"
            )
            
            # --- 8. 브리핑 발송 및 완료 기록 ---
            logger.info(f"Sending Daily Briefing message to chat {chat_id}...")
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = []
            if briefing_news_buttons:
                row = []
                for btn in briefing_news_buttons:
                    if 'callback_data' in btn:
                        row.append(InlineKeyboardButton(text=btn['label'], callback_data=btn['callback_data']))
                    else:
                        row.append(InlineKeyboardButton(text=btn['label'], url=btn['url']))
                # 가로 2열 배치 청크 분할
                keyboard = [row[i:i + 2] for i in range(0, len(row), 2)]
            
            # [오답 복습 연동] 오답이 존재할 경우 복습 시작 단축 버튼을 키보드 최하단에 신설합니다.
            if incorrect_count > 0:
                keyboard.append([
                    InlineKeyboardButton(text=f"❌ 오답 복습 바로가기 ({incorrect_count}개)", callback_data="quiz_review_start")
                ])
                
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            await send_safe_message(
                chat_id=chat_id,
                bot=application.bot,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            database.save_sent_briefing(chat_id, today_str)
        except Exception as e:
            logger.error(f"Error executing Daily Briefing for chat {chat_id}: {e}", exc_info=True)
            
    try:
        database.cleanup_old_briefings(days=30)
        database.cleanup_old_sent_news(days=30)
    except Exception as e:
        logger.error(f"Failed to cleanup old briefings or sent news: {e}")
        
    try:
        # [용량 최적화] 30일이 경과한 오래된 퀴즈 세션의 원본 본문 텍스트를 정리합니다.
        database.cleanup_old_quiz_sessions(days=30)
        logger.info("Successfully cleaned up old quiz sessions' source content.")
    except Exception as e:
        logger.error(f"Failed to cleanup old quiz sessions: {e}")

async def check_and_send_dday_alerts(application):
    """사용자의 D-Day 목록을 스캔하여 D-3, D-1, D-Day 당일 알림을 푸시 발송합니다."""
    logger.info("Starting D-Day proactive alerts scan...")
    
    try:
        chat_ids = database.get_all_authenticated_users()
    except Exception as e:
        logger.error(f"Failed to fetch authenticated users for D-Day alerts: {e}", exc_info=True)
        return
        
    if not chat_ids:
        logger.debug("No authenticated users found for D-Day alerts.")
        return
        
    kst_now = datetime.datetime.now(KST)
    today_str = kst_now.strftime("%Y-%m-%d")
    current_time_str = kst_now.strftime("%H:%M")
    today = kst_now.date()
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    for chat_id in chat_ids:
        try:
            # 1. 사용자 설정 로드 (브리핑 시간 기준)
            settings = database.get_user_settings(chat_id)
            target_time_str = settings.get('briefing_time', '08:00')
            
            # 2. 브리핑 시간이 지났는지 체크
            if current_time_str < target_time_str:
                continue
                
            # 3. D-Day 목록 조회
            ddays = database.get_ddays(chat_id)
            if not ddays:
                continue
                
            for d in ddays:
                try:
                    target_dt = datetime.datetime.strptime(d['target_date'], "%Y-%m-%d").date()
                    diff = (target_dt - today).days
                    
                    # D-3, D-1, D-Day 당일 체크
                    if diff in [3, 1, 0]:
                        alert_type = f"D-{diff}" if diff > 0 else "D-Day"
                        
                        # 4. 중복 발송 여부 검증
                        if database.is_dday_alert_sent(chat_id, d['id'], alert_type):
                            continue
                            
                        title_esc = html.escape(d['title'])
                        
                        # 5. 메시지 포맷팅
                        if diff > 0:
                            text = (
                                f"🔔 <b>[{alert_type} 알림]</b>\n"
                                f"회장님, 등록하신 D-Day <b>{title_esc}</b> 일정까지 <b>{diff}일</b> 남았습니다!\n"
                                f"• 목표일: <code>{d['target_date']}</code>"
                            )
                        else:
                            text = (
                                f"🔔 <b>[D-Day 알림]</b>\n"
                                f"회장님, 오늘은 등록하신 D-Day <b>{title_esc}</b> 당일입니다! 🎉\n"
                                f"• 목표일: <code>{d['target_date']}</code>"
                            )
                            
                        # 6. 인라인 버튼 구성
                        keyboard = [
                            [
                                InlineKeyboardButton("📅 D-Day 목록", callback_data="dday_list"),
                                InlineKeyboardButton("⚙️ 설정 탭 이동", callback_data="refresh_settings")
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        # 7. 푸시 알림 발송
                        logger.info(f"Sending proactive D-Day alert ({alert_type}) for '{d['title']}' to chat {chat_id}...")
                        await send_safe_message(
                            chat_id=chat_id,
                            bot=application.bot,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                        
                        # 8. 발송 완료 내역 저장
                        database.save_sent_dday_alert(chat_id, d['id'], alert_type)
                except Exception as inner_e:
                    logger.error(f"Error checking single D-Day alert (ID: {d.get('id')}) for chat {chat_id}: {inner_e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error checking D-Day alerts for chat {chat_id}: {e}", exc_info=True)
            
    # 오래된 발송 완료 로그 자동 정리 (30일 초과 건)
    try:
        database.cleanup_old_dday_alerts(days=30)
    except Exception as e:
        logger.error(f"Failed to cleanup old D-Day alerts: {e}")

async def check_and_send_weekly_reports(application):
    """매주 일요일 저녁 8시(KST) 이후에 주간 분석 리포트 이미지를 자동 발송합니다."""
    logger.info("Starting Weekly Report checks...")
    
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT chat_id FROM chat_history")
        chat_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        logger.error(f"Failed to fetch chat IDs for weekly report: {e}", exc_info=True)
        try:
            chat_ids = database.get_all_authenticated_users()
        except Exception:
            chat_ids = []
            
    if not chat_ids:
        logger.debug("No active chats found for weekly report.")
        return
        
    kst_now = datetime.datetime.now(KST)
    
    # KST 기준 일요일인지 확인 (6 = 일요일)
    if kst_now.weekday() != 6:
        return
        
    target_time_str = getattr(Config, 'WEEKLY_REPORT_TIME', '20:00')
    current_time_str = kst_now.strftime("%H:%M")
    if current_time_str < target_time_str:
        return
        
    # 주차 계산 (예: 2026-W25)
    year, week, _ = kst_now.isocalendar()
    report_week = f"{year}-W{week:02d}"
    
    for chat_id in chat_ids:
        try:
            if database.is_weekly_report_sent(chat_id, report_week):
                continue
                
            logger.info(f"Automatically generating and sending weekly report for chat {chat_id} (Week: {report_week})...")
            
            from src.reports import generate_weekly_report_image
            image_path = generate_weekly_report_image(chat_id)
            
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [[InlineKeyboardButton("🔄 보고서 갱신", callback_data="refresh_report")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            caption = (
                f"📊 <b>[{report_week}] 주간 분석 리포트</b> 📈\n\n"
                f"회장님, 이번 한 주 동안의 지출 내역과 할 일 통계를 정리한 리포트 카드입니다.\n"
                f"편안한 일요일 저녁 되시길 바랍니다. ☕"
            )
            
            with open(image_path, 'rb') as photo_file:
                await application.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                
            database.save_sent_weekly_report(chat_id, report_week)
            logger.info(f"Weekly report successfully sent to chat {chat_id}.")
        except Exception as e:
            logger.error(f"Error sending weekly report to chat {chat_id}: {e}", exc_info=True)
            
    try:
        database.cleanup_old_weekly_reports(days=365)
    except Exception as e:
        logger.error(f"Failed to cleanup old weekly reports: {e}")

async def reminder_scheduler_loop(application):
    """지정된 폴링 주기에 따라 무한히 반복 실행되는 스케줄러 태스크 루프입니다."""
    poll_seconds = Config.CALENDAR_POLL_INTERVAL_SECONDS
    logger.info(f"Google Calendar, Gmail & Briefing reminder scheduler loop started. Polling interval: {poll_seconds}s")
    
    # 봇이 가동되자마자 한 번 체크하고 루프 진입
    await check_and_send_reminders(application)
    await check_and_send_email_alerts(application)
    await check_and_send_briefings(application)
    await check_and_send_dday_alerts(application)
    await check_and_send_weekly_reports(application)
    
    while True:
        try:
            await asyncio.sleep(poll_seconds)
            await check_and_send_reminders(application)
            await check_and_send_email_alerts(application)
            await check_and_send_briefings(application)
            await check_and_send_dday_alerts(application)
            await check_and_send_weekly_reports(application)
        except asyncio.CancelledError:
            logger.info("Calendar, Gmail & Briefing reminder scheduler loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in calendar, Gmail & Briefing reminder scheduler loop: {e}", exc_info=True)
            await asyncio.sleep(60)

def start_reminder_scheduler(application):
    """봇 초기화 시 호출되어 백그라운드 asyncio 태스크로 스케줄러 루프를 구동합니다."""
    logger.info("Registering Google Calendar, Gmail & Briefing proactive reminder scheduler...")
    asyncio.create_task(reminder_scheduler_loop(application))

