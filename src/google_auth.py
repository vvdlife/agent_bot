import os
import json
import logging
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import google.auth.transport.requests
from src.config import Config
from src import database

# Allow scope changes (e.g. cumulative scopes) during OAuth token exchange
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://mail.google.com/'
]

class GoogleAuthError(Exception):
    """Base exception for Google Auth errors."""
    pass

class GoogleAuthRequiredError(GoogleAuthError):
    """Exception raised when the user is not authenticated."""
    pass

class GoogleConfigError(GoogleAuthError):
    """Exception raised when Google credentials file is missing or invalid."""
    pass

def get_google_credentials(chat_id: int) -> Credentials:
    """
    Retrieves Google credentials from database, refreshes if expired,
    and returns google.oauth2.credentials.Credentials.
    Raises GoogleAuthRequiredError if not found.
    """
    creds_json = database.get_user_credentials(chat_id)
    if not creds_json:
        raise GoogleAuthRequiredError("구글 연동 정보가 없습니다. /login 명령어로 연동을 시작해 주세요.")
        
    try:
        creds = Credentials.from_authorized_user_info(json.loads(creds_json), SCOPES)
    except Exception as e:
        logger.error(f"Failed to load credentials from DB for chat {chat_id}: {e}")
        raise GoogleAuthRequiredError("구글 연동 정보가 올바르지 않습니다. 다시 로그인해 주세요.")

    # Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            logger.info(f"Refreshing Google credentials for chat {chat_id}...")
            request = google.auth.transport.requests.Request()
            creds.refresh(request)
            database.save_user_credentials(chat_id, creds.to_json())
            logger.info(f"Successfully refreshed and saved Google credentials for chat {chat_id}.")
        except Exception as e:
            logger.error(f"Failed to refresh Google credentials for chat {chat_id}: {e}", exc_info=True)
            raise GoogleAuthRequiredError("구글 연동 정보 만료 및 자동 갱신에 실패했습니다. 다시 로그인해 주세요.")
            
    return creds

def generate_auth_url(chat_id: int) -> tuple[str, Flow, str]:
    """
    Generates Google OAuth authorization URL.
    Returns (auth_url, flow, state).
    Raises GoogleConfigError if credentials file is missing.
    """
    creds_path = Config.GOOGLE_CREDENTIALS_PATH
    if not os.path.exists(creds_path):
        raise GoogleConfigError(
            f"Google Cloud OAuth 클라이언트 비밀번호 파일('{creds_path}')이 존재하지 않습니다. "
            "개발자 가이드에 따라 파일을 배치하고 .env에 경로를 올바르게 설정해 주세요."
        )
        
    try:
        # Build flow
        redirect_uri = f"http://localhost:{Config.GOOGLE_REDIRECT_PORT}/"
        flow = Flow.from_client_secrets_file(
            creds_path,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        # Generate URL
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        return auth_url, flow, state
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {e}", exc_info=True)
        raise GoogleConfigError(f"OAuth 클라이언트 설정 파일 로드 실패: {str(e)}")

# Keep track of active redirect servers to prevent duplicates
_active_servers = {}
_active_servers_lock = threading.Lock()

# Keep track of active OAuth flows for manual verification bypass
_active_flows = {}
_active_flows_lock = threading.Lock()

def exchange_code_for_credentials(chat_id: int, flow: Flow, code: str) -> Credentials:
    """
    Exchanges authorization code for credentials and saves to the database.
    """
    flow.fetch_token(code=code)
    creds = flow.credentials
    database.save_user_credentials(chat_id, creds.to_json())
    logger.info(f"Google credentials successfully saved for chat {chat_id} via exchange.")
    return creds

def cleanup_auth_session(chat_id: int):
    """
    Clean up active redirect server and flow context for this chat_id.
    """
    # Shutdown redirect server if running
    with _active_servers_lock:
        if chat_id in _active_servers:
            logger.info(f"Shutting down redirect server for chat {chat_id}...")
            try:
                server = _active_servers[chat_id]
                threading.Thread(target=server.shutdown, daemon=True).start()
            except Exception as e:
                logger.warning(f"Failed to shutdown redirect server: {e}")
            del _active_servers[chat_id]
            
    # Clear flow session
    with _active_flows_lock:
        if chat_id in _active_flows:
            del _active_flows[chat_id]

def handle_manual_callback(chat_id: int, url_or_code: str) -> bool:
    """
    Manually extracts the state and code from the provided URL or code,
    exchanges it for credentials using the active flow, and saves to DB.
    Returns True if successful, raises ValueError/GoogleAuthError otherwise.
    """
    # 1. Parse URL to extract code and state
    code = None
    state = None
    
    url_or_code_clean = url_or_code.strip()
    if url_or_code_clean.startswith(("http://", "https://", "localhost", "127.0.0.1")):
        try:
            parsed = urlparse(url_or_code_clean)
            query = parse_qs(parsed.query)
            code = query.get('code', [None])[0]
            state = query.get('state', [None])[0]
        except Exception as e:
            raise ValueError(f"URL 파싱 중 오류가 발생했습니다: {str(e)}")
    else:
        # Just raw code
        code = url_or_code_clean

    if not code:
        raise ValueError("입력된 내용에서 인증 코드(code)를 찾을 수 없습니다. 주소창 주소를 전체 복사해 주세요.")

    # 2. Retrieve active flow data
    with _active_flows_lock:
        flow_data = _active_flows.get(chat_id)

    if not flow_data:
        raise GoogleAuthRequiredError("활성화된 로그인 세션을 찾을 수 없습니다. 다시 /login 명령어를 실행한 후 시도해 주세요.")

    flow, expected_state = flow_data

    # 3. Verify state if present (highly recommended for CSRF protection)
    if state and state != expected_state:
        raise ValueError("보안 토큰(state)이 일치하지 않습니다. 올바른 링크인지 확인해 주세요.")

    # 4. Exchange code and save
    try:
        exchange_code_for_credentials(chat_id, flow, code)
        # Session cleanup
        cleanup_auth_session(chat_id)
        return True
    except Exception as e:
        logger.error(f"Manual credentials exchange failed for chat {chat_id}: {e}", exc_info=True)
        raise GoogleAuthError(f"구글 연동 처리 중 오류 발생: {str(e)}")

def start_redirect_server(chat_id: int, flow: Flow, expected_state: str, bot, loop):
    """
    Starts the local redirect web server in a background thread.
    Shuts down any existing server for this chat_id first.
    """
    cleanup_auth_session(chat_id)

    # Register active flow for manual callback fallback
    with _active_flows_lock:
        _active_flows[chat_id] = (flow, expected_state)

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Suppress default server log to stdout/stderr
            logger.debug(format % args)

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            
            # 1. Verify state
            state = query.get('state', [None])[0]
            if not state or state != expected_state:
                self.send_response(400)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(b"<h1>Error: Invalid state parameter.</h1>")
                return

            # 2. Extract code
            code = query.get('code', [None])[0]
            if not code:
                self.send_response(400)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(b"<h1>Error: Authorization code not found.</h1>")
                return

            # 3. Exchange code for credentials
            try:
                exchange_code_for_credentials(chat_id, flow, code)

                # Render a beautiful HTML success page
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                
                success_html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Google Account Linked</title>
                    <meta charset="utf-8">
                    <style>
                        body {
                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                            background-color: #f3f4f6;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                        }
                        .card {
                            background: white;
                            padding: 2.5rem;
                            border-radius: 16px;
                            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
                            text-align: center;
                            max-width: 400px;
                        }
                        h1 { color: #10b981; margin-top: 0; font-size: 1.8rem; }
                        p { color: #4b5563; font-size: 1rem; line-height: 1.5; }
                        .emoji { font-size: 3rem; margin-bottom: 1rem; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div class="emoji">🔑</div>
                        <h1>Google 연동 성공!</h1>
                        <p>구글 계정이 성공적으로 연동되었습니다.<br>이 브라우저 창을 닫고 텔레그램 메신저로 돌아가셔도 좋습니다.</p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode('utf-8'))

                # Send confirmation telegram message asynchronously
                asyncio.run_coroutine_threadsafe(
                    send_telegram_notification(chat_id, bot),
                    loop
                )

                # Clean up session (this will shutdown server in another thread)
                cleanup_auth_session(chat_id)

            except Exception as e:
                logger.error(f"Error exchanging OAuth code: {e}", exc_info=True)
                self.send_response(500)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(f"<h1>Error: {str(e)}</h1>".encode('utf-8'))

    # Start the server
    server_address = ('', Config.GOOGLE_REDIRECT_PORT)
    try:
        httpd = HTTPServer(server_address, CallbackHandler)
        with _active_servers_lock:
            _active_servers[chat_id] = httpd
            
        logger.info(f"OAuth Redirect server started on port {Config.GOOGLE_REDIRECT_PORT} for chat {chat_id}")
        
        # Run httpd.serve_forever in a daemon thread so it does not block the main process
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
    except Exception as e:
        logger.error(f"Failed to start OAuth Redirect server: {e}", exc_info=True)
        raise GoogleAuthError(f"리디렉션 서버 구동 실패 (포트 {Config.GOOGLE_REDIRECT_PORT}가 사용 중인지 확인하세요): {str(e)}")

async def send_telegram_notification(chat_id: int, bot):
    """Helper to send telegram notification when OAuth succeeds."""
    try:
        from src.main import send_safe_message, get_main_keyboard
        text = (
            "🔑 <b>구글 계정 연동이 성공적으로 완료되었습니다!</b>\n\n"
            "이제 자연어로 캘린더 일정을 조회/추가하고, 지메일을 관리해보세요.\n"
            "- 예시: <i>\"내일 내 일정 알려줘\"</i>, <i>\"지메일 읽지 않은 메일 요약해줘\"</i>"
        )
        await send_safe_message(
            chat_id=chat_id,
            bot=bot,
            text=text,
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        logger.info(f"Sent Telegram confirmation to chat {chat_id}")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
