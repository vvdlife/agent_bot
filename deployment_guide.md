# 우분투 서버 Docker 배포 가이드 (Ubuntu Deployment Guide)

이 문서는 정리된 코드를 기반으로 우분투 서버에서 도커(Docker)를 이용해 텔레그램 에이전트 봇을 처음부터 구동하는 절차를 설명합니다.

---

## 1. 사전 준비 사항
* 우분투 서버에 **Docker** 및 **Docker Compose**가 설치되어 있어야 합니다.
* 아래 두 가지 자격증명이 필요합니다:
  1. **텔레그램 봇 토큰 (`TELEGRAM_BOT_TOKEN`)**
  2. **구글 제미나이 API 키 (`GEMINI_API_KEY`)**
  3. **구글 Cloud OAuth 클라이언트 JSON 파일 (`google_credentials.json`)**

---

## 2. 배포 절차

### 1단계: 깃허브에서 코드 클론
우분투 서버 터미널에서 프로젝트를 클론할 디렉토리로 이동한 후 아래 명령어를 실행합니다.
```bash
git clone https://github.com/vvdlife/agent_bot.git
cd agent_bot
```

### 2단계: 필수 폴더 생성
데이터베이스 및 구글 로그인 세션 파일이 마운트될 `data` 폴더를 생성합니다.
```bash
mkdir -p data
```

### 3단계: 환경 설정 파일 (`.env`) 생성
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 아래 템플릿 내용을 입력합니다.
```bash
nano .env
```
**`.env` 파일 내용:**
```text
# 텔레그램 봇 토큰 (BotFather에게 발급받은 토큰)
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE

# 구글 제미나이 API 키 (Google AI Studio 발급)
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE

# 데이터베이스 및 구글 자격증명 경로 (도커 내부 경로 기준이므로 유지)
DATABASE_PATH=data/agent.db
GOOGLE_CREDENTIALS_PATH=data/google_credentials.json

# 접근을 허용할 텔레그램 사용자 ID 또는 username (쉼표로 구분)
ALLOWED_USERS=YOUR_TELEGRAM_USER_ID_HERE
```
*작성 후 `Ctrl + O` -> `Enter` -> `Ctrl + X`를 눌러 저장하고 나옵니다.*

### 4단계: 구글 API 인증 파일 배치
확보해 두신 구글 OAuth 클라이언트 JSON 파일의 이름을 `google_credentials.json`으로 지정하여 `data/` 폴더 내부에 위치시킵니다.
* 방법 예시: 로컬에서 SFTP/SCP 등을 이용해 전송하거나, 파일 내용을 복사해 서버에서 만듭니다.
```bash
nano data/google_credentials.json
# 복사한 JSON 파일 내용을 붙여넣고 저장합니다.
```

### 5단계: 스크립트 실행 권한 부여 및 봇 구동
실행 스크립트에 권한을 부여하고 봇을 백그라운드 컨테이너로 시작합니다.
```bash
chmod +x start.sh stop.sh
./start.sh
```

---

## 3. 봇 상태 확인 및 관리

* **실시간 로그 확인:**
  구동 스크립트 실행 시 로그 스트리밍이 자동 시작됩니다. 로그 화면을 빠져나오려면 `Ctrl + C`를 누르시면 됩니다. (봇 프로세스는 백그라운드에서 계속 동작합니다.)
* **프로세스 중지:**
  ```bash
  ./stop.sh
  ```
* **수동 로그 조회:**
  ```bash
  docker compose logs -f
  ```

---

## 4. 구글 API 연동 시 리디렉션 처리 방법 (OAuth Fallback)

텔레그램 봇에서 `/login` 명령어를 입력하면 구글 인증 링크가 제공됩니다.
1. 링크를 타고 브라우저에서 인증을 완료하면 브라우저 주소창이 `http://localhost:8080/?state=...&code=...` 형태로 리다이렉트됩니다.
2. 서버가 로컬에 실행 중이 아니기 때문에 브라우저 화면에는 **"사이트에 연결할 수 없음"** 또는 오류 화면이 표시되는 것이 정상입니다.
3. 당황하지 마시고, **브라우저 주소창의 전체 URL 주소를 그대로 복사**합니다.
4. 복사한 전체 URL(또는 `code=` 뒤의 코드값)을 **텔레그램 봇 채팅 창에 메시지로 전송**합니다.
5. 봇이 해당 URL에서 인증 코드를 파싱하여 구글 로그인을 정상 완료 처리합니다. (서버 포트를 외부에 노출할 필요가 없어 안전합니다.)
