import logging
import json
from google import genai
from google.genai import types
from src.config import Config
from src import database, tools, google_auth

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """
당신은 사용자의 일정, 할 일, 노트를 관리하고 지원하는 프리미엄 개인 생산성 AI 에이전트(비서)입니다.
항상 친절하고 일목요연하며 전문적인 비서의 톤앤매너(해요체/하십시오체)를 한국어로 시종일관 유지하세요.

답변 설계 규칙 (Conversational UX Guidelines):
1. **두괄식 핵심 답변 우선 (Core Answer First)**: 
   - 답변의 첫 1~2줄에 사용자가 묻는 핵심 답변, 결과 요약 또는 수행 완료 여부를 명확히 배치하세요.
   - 예: "네, 말씀하신 할 일을 추가했습니다." 혹은 "현재 서울의 날씨 정보입니다."
   - "사용자가 요청하신 ~를 처리하겠습니다" 같은 불필요한 로봇형 서두 문장은 절대 금지합니다.
   
2. **시각적 구조화 및 이모지 기능적 활용 (Visual Hierarchy & Functional Emojis)**:
   - 정보를 전달할 때는 불필요하게 긴 문장 서술을 피하고, 항목별 요약 리스트 형태로 시각화하세요.
   - 리스트 및 주요 섹션 시작점에는 기능에 맞는 대표 이모지(🌡️, 🌦️, 📋, 📝, 🔍, 📰, ⚠️, 💡)를 배치하여 사용자가 폰 화면에서 1초 만에 스캔할 수 있게 하세요.
   - 리스트 항목은 최대 5개로 제한하고, 각 항목의 설명은 최대 2문장을 넘지 않게 하세요.

3. **개인 비서다운 상황 판단 조언 (Agentic Advice)**:
   - 조회된 날씨 데이터나 할 일 상태를 판단하여 상황에 맞는 실용적이고 따뜻한 개인 비서 한 줄 조언을 덧붙이세요.
   - 날씨 예시: 기온이 낮거나 바람이 불면 가벼운 외투(🧥) 챙기기 권유, 강수량이 있으면 우산(☔) 챙기기 권유.
   - 할 일 예시: 마감이 지난 시급한 태스크가 있다면 우선 처리를 부드럽게 권유.

4. **도구 선택 및 최적화**:
   - 날씨/기온 요청 시 ➡️ 반드시 `get_current_weather` 도구를 실행하세요.
   - 웹 검색 요청 시 ➡️ `web_search`를 사용하되, 검색어(query)는 명사형 핵심 키워드(예: "아이폰16 출시일", "오늘 경제 뉴스")로 압축하여 검색하세요.
   - 구글 캘린더 일정 조회 요청 시 ➡️ `list_google_calendar_events` 도구를 사용하세요.
   - 구글 캘린더 일정 등록 요청 시 ➡️ `create_google_calendar_event` 도구를 사용하세요. 시작 및 종료 일시는 반드시 ISO 8601 포맷(예: 'YYYY-MM-DDTHH:MM:SS') 문자열 형식이어야 합니다. 사용자가 "내일 오후 3시"와 같이 상대적인 시간으로 요청하면 현재 시각(2026-06-11T11:20:00+09:00)을 기준으로 실제 날짜와 시간을 계산하여 전달하세요.
   - 구글 캘린더 일정 삭제 요청 시 ➡️ `delete_google_calendar_event` 도구를 사용하세요. (삭제하기 위해 먼저 일정 목록을 조회하여 event_id를 알아내야 합니다.)
   - 구글 캘린더 일정 수정 요청 시 ➡️ `update_google_calendar_event` 도구를 사용하세요. (수정하기 위해 먼저 일정 목록을 조회하여 event_id를 알아내야 합니다.)
   - 읽지 않은 지메일 메일 목록/요약 요청 시 ➡️ `list_unread_emails` 도구를 사용하세요.
   - 지메일 이메일 발송 요청 시 ➡️ `send_email_via_gmail` 도구를 사용하세요.
   - 지메일 이메일 검색 요청 시 ➡️ `search_emails_via_gmail` 도구를 사용하세요.
   - 지메일 중요 메일 실시간 알림 규칙(필터) 등록 요청 시 ➡️ `add_gmail_alert_filter` 도구를 사용하세요. (filter_type은 'sender' 또는 'keyword'여야 함)
   - 지메일 알림 규칙(필터) 삭제 요청 시 ➡️ `delete_gmail_alert_filter` 도구를 사용하세요.
   - 지메일 알림 규칙(필터) 목록 조회 요청 시 ➡️ `list_gmail_alert_filters` 도구를 사용하세요.
   - 개인 설정(아침 브리핑 시각, 날씨용 거주지) 변경 요청 시 ➡️ `set_user_setting_tool` 도구를 사용하세요. (key는 'briefing_time' 또는 'location'이어야 함)
   - 개인 설정 조회 요청 시 ➡️ `get_user_settings_tool` 도구를 사용하세요.
   - 개인 뉴스 관심 키워드 추가 요청 시 ➡️ `add_news_keyword_tool` 도구를 사용하세요.
   - 개인 뉴스 관심 키워드 삭제 요청 시 ➡️ `delete_news_keyword_tool` 도구를 사용하세요.
   - 개인 뉴스 관심 키워드 목록 조회 요청 시 ➡️ `list_news_keywords_tool` 도구를 사용하세요.
   - 지출 내역 등록 요청 시 (예: "식비 15000원 썼어" 또는 "오늘 마트 3만원 지출") ➡️ 반드시 `add_expense_tool` 도구를 사용하세요. 지출 내역에 따라 카테고리(식비, 교통비, 쇼핑 등)를 AI가 지능적으로 매핑해야 합니다.
   - 지출 통계 및 요약 보고 요청 시 (예: "이번 달 지출 요약해줘") ➡️ `get_expense_summary_tool` 도구를 사용하세요.
   - 등록된 지출 삭제 요청 시 ➡️ `delete_expense_tool` 도구를 사용하세요.
   - D-Day 및 기념일 등록 요청 시 ➡️ `add_dday_tool` 도구를 사용하세요. 현재 날짜가 2026-06-16T15:57:00+09:00임을 참고하여 "내일", "다음주 화요일", "10월 25일" 등의 상대적/절대적 날짜를 정확한 YYYY-MM-DD 형식으로 계산하여 전달하세요.
   - D-Day 및 기념일 목록 조회 요청 시 ➡️ `list_ddays_tool` 도구를 사용하세요.
   - D-Day 및 기념일 삭제 요청 시 ➡️ `delete_dday_tool` 도구를 사용하세요. (삭제하기 위해 먼저 목록을 조회하거나 사용자에게 확인해서 D-Day ID를 알아내야 합니다.)
   - 메모 저장 요청 시 ➡️ `save_note` 도구를 사용하세요.
   - 메모 검색 및 조회 요청 시 ➡️ `search_notes` 도구를 사용하세요.
   - 특정 메모 삭제 요청 시 ➡️ `delete_local_note` 도구를 사용하세요. (ID가 필요하므로 필요한 경우 먼저 메모 검색을 하거나 ID를 물어보세요.)
   - 메모 전체 삭제 또는 비우기 요청 시 ➡️ `clear_all_local_notes` 도구를 사용하세요.
   - 여행 일정 플래닝 및 등록 요청 시 (예: "7월 15일부터 3일간 도쿄 여행 계획 짜줘") ➡️ 다음 프로세스를 준수하십시오:
      1. 가고 싶은 특정 명소(랜드마크)나 선호하는 여행 스타일(힐링, 맛집 투어, 액티비티, 쇼핑 등)을 먼저 물어보세요. (예: "도쿄 여행이군요! 혹시 꼭 가고 싶으신 명소나 선호하는 여행 스타일이 있으신가요?")
      2. 사용자가 선호를 답하면, 여행 목적지의 날씨 정보를 제공하세요:
         - 여행 시점이 7일 이내라면 `get_current_weather` 도구로 실시간 날씨를 조회하세요.
         - 7일보다 먼 미래라면 `web_search` 도구를 사용해 해당 월의 목적지 평균 기후 정보(기온, 강수량 등)를 요약해 제공하세요.
      3. 웹 검색(`web_search`)을 활용해 사용자의 스타일과 동선에 최적화된 여행 일정을 시간대별/일자별로 작성해 준 뒤, 사용자 동의 하에 구글 캘린더에 일괄 등록할 수 있도록 `propose_travel_itinerary` 도구를 실행하십시오. (이 도구는 DB에 일정을 임시 저장하고 등록용 버튼을 띄우는 역할을 합니다.)
   - 일반 대화 및 질의응답 시 ➡️ 1줄 요약(Summary)과 2~3문장의 상세 설명(Details)으로 구분하여 전달하세요.


5. **HTML 출력 및 마크다운 금지 규칙 (HTML Formatting & Markdown Restriction)**:
   - 텔레그램 메시지 전송 시 HTML 파서를 사용하므로, 답변 내의 모든 텍스트 서식(굵게, 기울임, 코드 등)은 반드시 HTML 태그만을 사용해야 합니다. 마크다운 기호(예: **, *, _, ` 등)는 절대로 사용하지 마세요.
   - 사용 가능한 HTML 태그 목록:
     - <b>텍스트</b> : 굵은 글씨 (헤더, 강조 키워드, 중요 정보 등)
     - <i>텍스트</i> : 기울임 글씨 (주석, 예시 등)
     - <code>텍스트</code> : 코드 형식 (메모 ID, 날씨 값, 숫자 등)
     - <pre>텍스트</pre> : 여러 줄 코드 또는 고정 폭 서식
     - <a href="링크">텍스트</a> : 하이퍼링크
   - **절대 금지 태그**: <ul>, <li>, <ol>, <br>, <p>, <div> 등의 목록, 문단 및 줄바꿈 태그는 절대로 사용하지 마세요.
     - 줄바꿈이 필요할 때는 태그 대신 실제 줄바꿈 문자(엔터)를 사용하세요.
     - 리스트 항목을 출력할 때는 <li> 대신 동그라미 기호(•)나 이모지를 사용하세요.
   - 이 외의 태그는 사용하지 마세요. 태그를 열었으면 반드시 닫는 태그를 제공해야 합니다.
   - 답변 텍스트 중에 HTML 태그에 해당하지 않는 문자인 <, >, &는 반드시 각각 &lt;, &gt;, &amp;로 치환해서 출력하십시오.

6. **철저한 한국어 답변 규칙 (Strict Korean Language)**:
   - 소스코드나 필수불가결한 고유 명사/원형 데이터를 제외하고, 모든 답변과 제공하는 데이터는 철저히 한국어(한글)로 변환하거나 번역해서 제공해야 합니다.
   - 도구 실행 결과(예: 웹 검색 결과의 영어 본문/스니펫, 날씨 기상 용어 등)가 영어로 되어 있는 경우, 이를 그대로 출력하지 말고 자연스러운 한국어로 번역/요약해서 사용자에게 답변하세요.

7. **음성 메모/메시지 수신 시 처리 및 저장 규칙 (Voice Processing & Auto-Categorization)**:
   - 사용자가 음성 메시지(또는 음성 명령)를 보내면, 먼저 음성 내용을 전사(STT)하고 맥락을 분석하여 **구글 캘린더 관련 기능(등록, 조회, 수정, 삭제)**, **지메일 관련 기능(조회, 발송, 검색, 필터 관리)**, **할 일(Task)**, **메모(Note)** 등 에이전트가 지원하는 모든 적합한 도구를 자유롭게 결정하여 호출해야 합니다.
   - **할 일 및 메모로 분류 시 저장 포맷 규칙**:
     - 음성 내용이 구체적인 할 일 기록이거나 메모 저장인 경우에만 아래 포맷 규칙을 적용하십시오.
     - `title`: 음성 내용의 핵심을 한 줄로 명확하게 요약한 텍스트. (단, `create_local_task` 도구에만 전달하며, `save_note`에는 `title` 매개변수가 없으므로 전달하지 마십시오.)
     - `description`(`create_local_task`용) 또는 `content`(`save_note`용): 다음 포맷을 엄격히 따라 작성하십시오. HTML 태그 외의 마크다운 기호는 절대 사용하면 안 됩니다.
       (만약 `save_note`용 `content`인 경우, 맨 첫 줄에 <b>[제목] 한 줄 핵심 요약</b>을 반드시 포함하여 작성하십시오.)
       <b>[AI 요약]</b>
       • (요약 첫 번째 줄)
       • (요약 두 번째 줄)
       • (요약 세 번째 줄)

       <b>[음성 전사 본문]</b>
       (전사된 원본 텍스트 전체)
     - `tags` (`save_note`용): 음성 내용의 맥락에 어울리는 1~2개의 태그를 추출하여 쉼표로 구분하여 등록하십시오 (예: "일상, 생각" 또는 "아이디어" 등).
   - 도구 호출을 수행한 후, 사용자에게 결과를 보고할 때는 수행 결과를 한 눈에 볼 수 있는 HTML 카드 형식으로 구조화하여 대화 피드백을 전달하십시오.

8. **지출 기록 및 소비 분석 규칙 (Expense Tracking & Analysis)**:
   - 사용자가 텍스트나 음성으로 지출한 사실을 입력하면, 지능적으로 카테고리를 판단하여 `add_expense_tool`을 호출해야 합니다.
   - 예: "스타벅스 6000원" ➡️ category='식비', amount=6000, description='스타벅스'
   - 지출 등록 결과를 보고할 때는 한 눈에 지출액을 파악할 수 있는 HTML 카드를 생성하고, 하단에 취소 및 분석 관련 인라인 버튼이 나타날 수 있도록 안내하십시오.

9. **D-Day 및 기념일 관리 규칙 (D-Day & Anniversary Management)**:
   - 사용자가 대화나 음성으로 D-Day 또는 기념일 등록을 원할 경우 `add_dday_tool`을 호출하고, 조회 시 `list_ddays_tool`, 삭제 시 `delete_dday_tool`을 알맞게 실행하십시오.
   - D-Day 등록 완료 후 결과를 보고할 때는 등록된 이벤트명과 목표 날짜, 그리고 남은 일수(D-Day) 정보를 직관적으로 출력하고, 하단에 D-Day 목록 조회를 쉽게 할 수 있는 인라인 버튼 `[📅 D-Day 목록 조회]`를 추천하는 멘트를 남기십시오.
"""

def get_agent_client():
    Config.validate()
    return genai.Client(api_key=Config.GEMINI_API_KEY)

def clean_chat_history(history: list[types.Content]) -> list[types.Content]:
    """
    Sanitizes chat history to ensure strict alternation of roles ('user' and 'model')
    and correct sequencing of function calls and responses. It converts orphan
    function_call and function_response parts to plain text to prevent API validation errors.
    """
    if not history:
        return []

    # Discard leading model turns since the conversation must start with a 'user' turn.
    # Discarding them first ensures any now-orphaned function_response at the beginning
    # is correctly processed and converted to text in Step 1.
    start_idx = 0
    while start_idx < len(history) and history[start_idx].role == "model":
        start_idx += 1
    history = history[start_idx:]
    
    # Filter out empty or invalid parts to prevent 400 INVALID_ARGUMENT from the API server
    sanitized_history = []
    for content in history:
        valid_parts = []
        for p in content.parts:
            # Check if at least one valid oneof field has data
            has_valid_field = (
                getattr(p, 'text', None) is not None or
                getattr(p, 'inline_data', None) is not None or
                getattr(p, 'function_call', None) is not None or
                getattr(p, 'function_response', None) is not None or
                getattr(p, 'file_data', None) is not None or
                getattr(p, 'executable_code', None) is not None or
                getattr(p, 'code_execution_result', None) is not None
            )
            if has_valid_field:
                valid_parts.append(p)
        if valid_parts:
            content.parts = valid_parts
            sanitized_history.append(content)
            
    history = sanitized_history
    
    if not history:
        return []

    # Step 1: Identify and convert orphan function calls and responses
    n = len(history)
    for i in range(n):
        content = history[i]
        
        # Check for function_call in model turns
        if content.role == "model":
            has_fc = any(p.function_call for p in content.parts)
            if has_fc:
                is_paired = False
                if i + 1 < n:
                    next_content = history[i + 1]
                    if next_content.role == "user":
                        if any(p.function_response for p in next_content.parts):
                            is_paired = True
                
                if not is_paired:
                    # Convert function_call parts to text parts
                    new_parts = []
                    for p in content.parts:
                        if p.function_call:
                            args_str = json.dumps(p.function_call.args, ensure_ascii=False) if p.function_call.args else ""
                            new_parts.append(types.Part.from_text(
                                text=f"[도구 호출: {p.function_call.name} {args_str}]"
                            ))
                        else:
                            new_parts.append(p)
                    content.parts = new_parts
                else:
                    # Strip any text parts to keep it as a pure function_call turn
                    content.parts = [p for p in content.parts if p.function_call]
                    
        # Check for function_response in user turns
        elif content.role == "user":
            has_fr = any(p.function_response for p in content.parts)
            if has_fr:
                is_paired = False
                if i - 1 >= 0:
                    prev_content = history[i - 1]
                    if prev_content.role == "model":
                        if any(p.function_call for p in prev_content.parts):
                            is_paired = True
                            
                if not is_paired:
                    # Convert function_response parts to text parts
                    new_parts = []
                    for p in content.parts:
                        if p.function_response:
                            res_str = json.dumps(p.function_response.response, ensure_ascii=False) if p.function_response.response else ""
                            new_parts.append(types.Part.from_text(
                                text=f"[도구 실행 결과: {res_str}]"
                            ))
                        else:
                            new_parts.append(p)
                    content.parts = new_parts
                else:
                    # Strip any text parts to keep it as a pure function_response turn
                    content.parts = [p for p in content.parts if p.function_response]

    # Step 2: Enforce strict role alternation
    clean_history = []
    for content in history:
        if not clean_history:
            if content.role == "user":
                clean_history.append(content)
            continue
            
        last = clean_history[-1]
        
        # Merge or alternate consecutive same roles
        if content.role == last.role:
            if content.role == "user":
                # Always insert a dummy model response to alternate roles for consecutive user turns.
                # This prevents merging distinct query turns from different messages.
                clean_history.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="요청하신 내용을 확인했습니다.")]
                ))
                clean_history.append(content)
            else:
                last.parts.extend(content.parts)
            continue
            
        # Alternating roles: since orphans are converted, they are guaranteed to be valid
        if content.role == "user" and last.role == "model":
            clean_history.append(content)
        elif content.role == "model" and last.role == "user":
            clean_history.append(content)
            
    return clean_history

def process_message(chat_id: int, user_message_text: str = None, file_path: str = None, mime_type: str = None) -> tuple[str, list[str]]:
    # Set context variables for tools
    tools.current_chat_id.set(chat_id)
    
    logger.info(f"Agent received task for chat {chat_id} (Text: '{user_message_text}', File: {file_path is not None})")
    client = get_agent_client()
    
    # 1. Save the incoming message representation to database
    if file_path:
        db_text = "[음성 메시지 수신]"
    else:
        db_text = user_message_text or ""
        
    user_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=db_text)]
    )
    database.save_chat_message(chat_id, "user", user_content.model_dump_json())
    logger.info(f"Saved user message in database history.")
    
    # 2. Load recent conversation history for context
    raw_history = database.get_chat_history(chat_id, limit=20)
    history = []
    for row in raw_history:
        try:
            content_obj = types.Content.model_validate_json(row['content'])
            history.append(content_obj)
        except Exception:
            # Fallback if DB has raw text
            history.append(types.Content(
                role=row['role'],
                parts=[types.Part.from_text(text=row['content'])]
            ))
    logger.info(f"Loaded {len(history)} messages of history context.")
    
    # Sanitize and fix the history structure to prevent role alternation/validation errors
    history = clean_chat_history(history)
    logger.info(f"Sanitized history length: {len(history)}")
            
    # Upload file to Gemini Files API if present and replace the last content parts
    file_ref = None
    if file_path and len(history) > 0:
        try:
            logger.info(f"Uploading file {file_path} to Gemini Files API...")
            file_ref = client.files.upload(file=file_path)
            voice_instruction = (
                "이 음성 메시지를 분석하여 다음 규칙에 따라 도구를 호출해 주세요:\n"
                "1. 사용자의 의도를 분석하여 구글 캘린더(일정 등록/조회/수정/삭제), 지메일(메일 조회/발송/검색/필터), 날씨 조회, 지출 등록(add_expense_tool)/통계 조회(get_expense_summary_tool) 등 에이전트가 제공하는 모든 도구 중 가장 적합한 도구를 실행하십시오.\n"
                "   - 만약 사용자가 돈을 썼다고 말하면(예: \"오늘 커피에 6천원 지출\"), AI가 지능적으로 카테고리를 추론하여 add_expense_tool을 호출해 주십시오.\n"
                "2. 만약 할 일(create_local_task) 또는 메모(save_note)를 등록하는 경우, 다음 포맷 규칙을 엄격히 준수하십시오 (HTML 태그만 사용, 마크다운 금지):\n"
                "   - title: 음성 내용의 한 줄 핵심 요약 (create_local_task 전용, save_note에는 없음)\n"
                "   - description(할 일) 또는 content(메모):\n"
                "     (메모인 경우 맨 첫 줄에 <b>[제목] 한 줄 핵심 요약</b>을 꼭 삽입하십시오)\n"
                "     <b>[AI 요약]</b>\n"
                "     • 요약 1\n"
                "     • 요약 2\n"
                "     • 요약 3\n\n"
                "     <b>[음성 전사 본문]</b>\n"
                "     (전사된 원본 텍스트 전체)\n"
                "   - tags(메모인 경우): 맥락에 맞는 1~2개 쉼표 구분 태그\n"
                "3. 도구 실행을 마치고 한국어로 친절하게 수행 결과를 답변하십시오."
            )
            
            # Replace the last history item (the user message placeholder) with the multimodal elements
            history[-1] = types.Content(
                role="user",
                parts=[
                    types.Part(
                        file_data=types.FileData(
                            file_uri=file_ref.uri,
                            mime_type=file_ref.mime_type
                        )
                    ),
                    types.Part.from_text(text=voice_instruction)
                ]
            )
            logger.info("Voice file uploaded to Gemini Files API successfully.")
        except Exception as e:
            logger.error(f"Failed to upload voice file to Gemini: {e}", exc_info=True)
            return f"음성 파일을 처리하는 중 오류가 발생했습니다: {str(e)}", []
            
    # List of available tools
    available_tools = [
        tools.create_local_task,
        tools.list_local_tasks,
        tools.complete_local_task,
        tools.save_note,
        tools.search_notes,
        tools.delete_local_note,
        tools.clear_all_local_notes,
        tools.web_search,
        tools.get_current_weather,  # Dedicated weather tool
        tools.list_google_calendar_events,
        tools.create_google_calendar_event,
        tools.list_unread_emails,
        tools.send_email_via_gmail,
        tools.search_emails_via_gmail,
        tools.add_gmail_alert_filter,
        tools.delete_gmail_alert_filter,
        tools.list_gmail_alert_filters,
        tools.set_user_setting_tool,
        tools.get_user_settings_tool,
        tools.delete_google_calendar_event,
        tools.update_google_calendar_event,
        tools.add_news_keyword_tool,
        tools.delete_news_keyword_tool,
        tools.list_news_keywords_tool,
        tools.add_expense_tool,
        tools.delete_expense_tool,
        tools.get_expense_summary_tool,
        tools.add_dday_tool,
        tools.list_ddays_tool,
        tools.delete_dday_tool,
        tools.propose_travel_itinerary
    ]
    
    # 3. Call Gemini API
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=available_tools,
        temperature=0.7
    )
    
    try:
        logger.info("Calling Gemini API generating response...")
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=history,
            config=config
        )
        logger.info("Gemini API call returned.")
        
        # 4. Handle and save response history
        tools_executed = []
        if response.automatic_function_calling_history:
            logger.info(f"Automatic Tool Execution triggered. {len(response.automatic_function_calling_history)} turns executed.")
            # Only save the newly generated turns (excluding the input history passed to the API)
            new_turns = response.automatic_function_calling_history[len(history):]
            for content in new_turns:
                for part in content.parts:
                    if part.function_call:
                        logger.info(f"  [Tool Call] Name: '{part.function_call.name}', Args: {part.function_call.args}")
                        tools_executed.append(part.function_call.name)
                    elif part.function_response:
                        logger.info(f"  [Tool Response] Name: '{part.function_response.name}', Result: {part.function_response.response}")
                database.save_chat_message(chat_id, content.role, content.model_dump_json())
            # Save the final text response from the model to maintain strict role alternation
            if response.candidates and len(response.candidates) > 0:
                final_content = response.candidates[0].content
                database.save_chat_message(chat_id, "model", final_content.model_dump_json())
            final_text = response.text
        else:
            logger.info("Gemini replied directly without calling tools.")
            model_content = response.candidates[0].content
            database.save_chat_message(chat_id, "model", model_content.model_dump_json())
            final_text = response.text
            
        logger.info("Saved response history in database.")
        return final_text or "죄송합니다, 답변을 생성하지 못했습니다.", tools_executed
        
    except google_auth.GoogleAuthRequiredError as ae:
        logger.warning(f"Google auth required for chat {chat_id}: {ae}")
        raise ae
    except Exception as e:
        logger.error(f"Error during Gemini API generation or tool call: {e}", exc_info=True)
        return f"죄송합니다. 에이전트 처리 중 오류가 발생했습니다: {str(e)}", []
    finally:
        # Clean up the file from Google Cloud storage if uploaded
        if file_ref:
            try:
                logger.info(f"Deleting uploaded file {file_ref.name} from Gemini storage...")
                client.files.delete(name=file_ref.name)
                logger.info("Voice file deleted from Gemini storage.")
            except Exception as e:
                logger.warning(f"Failed to delete uploaded file from Gemini storage: {e}")


async def filter_articles_by_category_or_keyword(articles: list[dict], target: str, is_category: bool = True) -> list[dict]:
    """
    Filters a list of articles using Gemini 2nd pass validation.
    Only articles strictly matching the category or keyword are returned.
    """
    if not articles:
        return []
        
    client = get_agent_client()
    
    if is_category:
        prompt = (
            f"당신은 뉴스 편집 비서입니다. 아래 기사 리스트에서 오직 [{target}] 분야에만 정합하게 부합하는 기사를 최대 4개 골라내어 주세요.\n"
            f"정치 기사(예: 대통령, 국회, 정당 등)나 연예 기사는 [{target}]가 아니면 배제해야 합니다.\n"
            f"출력은 반드시 JSON array 형식이어야 하며, 각 객체는 원래 리스트의 'title', 'href', 'body' 값을 정확히 보존해야 합니다.\n"
            f"JSON 마크다운 기호(```json)를 사용하지 말고 순수 JSON 문자열만 리턴하세요.\n\n"
            f"기사 리스트:\n{json.dumps(articles, ensure_ascii=False)}"
        )
    else:
        prompt = (
            f"당신은 뉴스 편집 비서입니다. 아래 기사 리스트에서 관심 키워드인 [{target}]와(과) 실제로 밀접하게 연관된 기사만 골라내어 주세요.\n"
            f"단순히 키워드가 스니펫이나 제목에 우연히 한 번 들어갔을 뿐 기사의 주 내용이 키워드와 무관한 기사는 제외해야 합니다.\n"
            f"출력은 반드시 JSON array 형식이어야 하며, 각 객체는 원래 리스트의 'title', 'href', 'body' 값을 정확히 보존해야 합니다.\n"
            f"JSON 마크다운 기호(```json)를 사용하지 말고 순수 JSON 문자열만 리턴하세요.\n\n"
            f"기사 리스트:\n{json.dumps(articles, ensure_ascii=False)}"
        )
        
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt
            )
        )
        
        ai_res_text = response.text.strip()
        if ai_res_text.startswith("```"):
            # Remove markdown fences
            lines = ai_res_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            ai_res_text = "\n".join(lines).strip()
            
        filtered_articles = json.loads(ai_res_text)
        if isinstance(filtered_articles, list):
            return filtered_articles
    except Exception as e:
        logger.warning(f"Failed to filter articles with AI: {e}. Returning original articles.")
        
    return articles

def generate_summary_and_quiz(title: str, text: str) -> dict:
    """
    제공된 콘텐츠 본문(텍스트)을 기반으로 Gemini AI를 사용하여
    핵심 요약본(HTML 포맷) 및 최초 10개의 객관식 사지선다형 복습 퀴즈를 생성합니다.
    출력 결과는 'summary'와 'questions' 키를 담은 딕셔너리 객체로 반환됩니다.
    """
    client = get_agent_client()
    
    prompt = (
        f"당신은 개인 비서 에이전트의 교육/학습 지원 모듈입니다.\n"
        f"아래 제공된 콘텐츠 본문(제목: {title})을 분석하여 다음 두 가지를 수행해 주세요:\n\n"
        f"1. **핵심 요약 (HTML 형식)**:\n"
        f"   - 내용을 3~5개의 핵심 요약 리스트(동그라미 기호 • 활용)로 정리해 주세요.\n"
        f"   - 강조하고 싶은 단어는 <b>태그를 사용해 주세요.\n"
        f"   - 마크다운 기호(예: **, *, _, ` 등)는 절대로 사용하지 마세요. 오직 HTML 서식 태그(<b>, <i>, <code>)만 사용 가능합니다.\n"
        f"   - <ul>, <li>, <ol>, <br>, <p>, <div> 태그는 절대 사용하지 마세요. 줄바꿈은 실제 줄바꿈 문자(엔터)를 사용하세요.\n"
        f"   - 요약문 맨 앞에는 '☀️ <b>[핵심 요약]</b>'과 같은 헤더를 달어주세요.\n\n"
        f"2. **객관식 퀴즈 (10문항)**:\n"
        f"   - 콘텐츠 본문의 핵심 내용 및 사실에 기반한 객관식 사지선다형(보기 4개) 퀴즈 10문제를 만들어 주세요.\n"
        f"   - 문제는 학습 및 복습을 돕는 유익하고 직관적인 내용이어야 합니다.\n"
        f"   - 각 문제에 대해 정답 번호(0, 1, 2, 3 중 하나)와 1~2문장의 친절한 정답 해설(explanation)을 포함해 주세요.\n\n"
        f"출력은 반드시 다른 텍스트 설명 없이 순수 JSON 문자열 형식이어야 하며, 루트에 'summary'와 'questions' 키를 가져야 합니다.\n"
        f"JSON 마크다운 기호(```json)를 사용하지 말고 순수 JSON 문자열만 리턴하세요.\n\n"
        f"JSON 스키마 예시:\n"
        f"{{\n"
        f"  \"summary\": \"(HTML 서식의 요약문 내용)\",\n"
        f"  \"questions\": [\n"
        f"    {{\n"
        f"      \"question\": \"1번 문제 내용?\",\n"
        f"      \"options\": [\"보기A\", \"보기B\", \"보기C\", \"보기D\"],\n"
        f"      \"correct_option\": 0,\n"
        f"      \"explanation\": \"1번 문제 해설 내용...\"\n"
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"콘텐츠 본문:\n"
        f"{text}"
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt
        )
        
        ai_res_text = response.text.strip()
        if ai_res_text.startswith("```"):
            lines = ai_res_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            ai_res_text = "\n".join(lines).strip()
            
        result = json.loads(ai_res_text)
        # Ensure we have the required keys
        if "summary" not in result or "questions" not in result:
            raise KeyError("Missing required keys in AI response JSON.")
        return result
    except Exception as e:
        logger.error(f"Failed to generate summary and quiz: {e}", exc_info=True)
        # Fallback dictionary
        return {
            "summary": f"☀️ <b>[요약]</b>\n• {title} 콘텐츠 분석 중 오류가 발생하여 요약본을 제공하지 못했습니다.",
            "questions": [
                {
                    "question": f"'{title}' 동영상/아티클에 대해 이해하셨나요?",
                    "options": ["네, 이해했습니다", "아니오, 어렵네요", "다시 읽어볼래요", "잘 모르겠습니다"],
                    "correct_option": 0,
                    "explanation": "콘텐츠에 관심을 가져주셔서 감사합니다! 복습을 완료해 주세요."
                }
            ]
        }

def generate_additional_quiz(title: str, text: str, existing_questions: list) -> list:
    """
    원본 본문 텍스트와 기존에 이미 출제되었던 문제 목록(existing_questions)을 전달받아,
    Gemini AI에게 기존 문제들과 내용이나 개념이 중복되지 않는 새로운 10개의 퀴즈 문항을 추가 생성하도록 요청합니다.
    출력 결과는 생성된 10개의 질문 딕셔너리를 담은 리스트 형태로 반환됩니다.
    """
    client = get_agent_client()
    
    # Format existing questions for prompt context
    formatted_existing = []
    for idx, q in enumerate(existing_questions, start=1):
        formatted_existing.append(f"문제 {idx}: {q['question']}")
    existing_str = "\n".join(formatted_existing) if formatted_existing else "없음"
    
    prompt = (
        f"당신은 개인 비서 에이전트의 교육/학습 지원 모듈입니다.\n"
        f"제공된 콘텐츠 본문(제목: {title})을 바탕으로, 이미 학습한 기존 문제들과 **전혀 겹치지 않는 새로운 객관식 사지선다형(보기 4개) 퀴즈 10문제**를 생성해 주세요.\n\n"
        f"### 기존에 이미 출제된 문제 목록 (이 문제들과 중복되거나 너무 유사한 내용은 출제하지 마세요):\n"
        f"{existing_str}\n\n"
        f"### 요구 사항:\n"
        f"- 기존에 출제된 문제들과 다른 새로운 관점이나 세부 내용, 혹은 깊이 있는 주제를 다루어 주세요.\n"
        f"- 10개의 문제를 생성해야 합니다.\n"
        f"- 각 문제에 대해 정답 번호(0, 1, 2, 3 중 하나)와 1~2문장의 친절한 정답 해설(explanation)을 포함해 주세요.\n\n"
        f"출력은 반드시 다른 설명 텍스트 없이 순수 JSON 배열 형식이어야 하며, 루트에 객체 형태 대신 배열형태의 문제 목록만 있어야 합니다.\n"
        f"JSON 마크다운 기호(```json)를 사용하지 말고 순수 JSON 문자열만 리턴하세요.\n\n"
        f"JSON 스키마 예시:\n"
        f"[\n"
        f"  {{\n"
        f"    \"question\": \"새로운 문제 내용?\",\n"
        f"    \"options\": [\"보기A\", \"보기B\", \"보기C\", \"보기D\"],\n"
        f"    \"correct_option\": 1,\n"
        f"    \"explanation\": \"새로운 문제 해설 내용...\"\n"
        f"  }}\n"
        f"]\n\n"
        f"콘텐츠 본문:\n"
        f"{text}"
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt
        )
        
        ai_res_text = response.text.strip()
        if ai_res_text.startswith("```"):
            lines = ai_res_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            ai_res_text = "\n".join(lines).strip()
            
        result = json.loads(ai_res_text)
        if not isinstance(result, list):
            raise TypeError("Expected a JSON list of questions from AI.")
        return result
    except Exception as e:
        logger.error(f"Failed to generate additional quiz: {e}", exc_info=True)
        # Fallback empty list
        return []


