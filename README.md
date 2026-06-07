# 📈 Daily Stock Briefing

매일 아침 7시(KST) 포트폴리오 주가 + 뉴스를 정리해 카카오톡으로 받는 자동화 시스템.

---

## 전체 구조

```
GitHub Actions (매일 07:00 KST)
  └─ briefing.py 실행
       ├─ pykrx / yfinance  →  주가 조회
       ├─ Google News RSS   →  국내 뉴스
       ├─ yfinance.news     →  해외 뉴스 (있는 경우)
       └─ Claude API        →  브리핑 생성 + 번역
  └─ briefings/YYYY-MM-DD.md  →  GitHub에 커밋
  └─ 카카오톡 메모챗         →  200자 요약 전송
```

---

## 셋업 가이드

### Step 1 — 이 저장소 만들기

1. GitHub에서 **새 private 저장소** 생성 (이름 예: `stock-briefing`)
2. 아래 파일들을 업로드:
   - `briefing.py`
   - `kakao_refresh.py`
   - `requirements.txt`
   - `.github/workflows/daily_briefing.yml`
   - `.github/workflows/kakao_refresh.yml`

---

### Step 2 — 카카오 REST API 키 발급

1. [카카오 디벨로퍼스](https://developers.kakao.com) 접속 → 로그인
2. **내 애플리케이션** → **애플리케이션 추가하기**
   - 앱 이름: `StockBriefing` (자유롭게)
3. 생성된 앱 클릭 → **앱 키** 탭 → **REST API 키** 복사 (32자리)
4. 좌측 메뉴 **카카오 로그인** → 활성화 ON
   - REST API Key 수정 > 클라이언트 시크릿, 카카오 로그인과 비즈니스 인증 모두 OFF 확인.
6. **카카오 로그인** → **동의항목** → `카카오톡 메시지 전송` 체크
7. **카카오 로그인** → **Redirect URI** 추가: `https://localhost`

---

### Step 3 — 카카오 토큰 최초 발급

아래 URL을 브라우저에서 열어 카카오 계정으로 로그인:

```
https://kauth.kakao.com/oauth/authorize?client_id=YOUR_REST_API_KEY&redirect_uri=https://localhost&response_type=code&scope=talk_message
```

> `YOUR_REST_API_KEY` 를 Step 2에서 복사한 키로 교체

로그인 후 브라우저 주소창에 `https://localhost/?code=XXXXXX` 형태로 리다이렉트 됨.  
`code=` 뒤 값(인가 코드)을 복사.

터미널에서 아래 명령 실행 (curl):

```bash
curl -X POST https://kauth.kakao.com/oauth/token \
  -d "grant_type=authorization_code" \
  -d "client_id=YOUR_REST_API_KEY" \
  -d "redirect_uri=https://localhost" \
  -d "code=인가코드"
```

응답 JSON에서 `access_token`과 `refresh_token` 두 값을 복사.

---

### Step 4 — Anthropic API 키 발급

1. [console.anthropic.com](https://console.anthropic.com) 접속 > 최소 $5부터 구매해야함.
2. **API Keys** → **Create Key** → 키 복사

---

### Step 5 — GitHub Secrets 등록

저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름          | 값                              |
|---------------------|---------------------------------|
| `KAKAO_ACCESS_TOKEN` | Step 3의 `access_token`         |
| `KAKAO_REFRESH_TOKEN`| Step 3의 `refresh_token`        |
| `KAKAO_REST_API_KEY` | Step 2의 REST API 키            |
| `ANTHROPIC_API_KEY`  | Step 4의 Claude API 키          |
| `GH_PAT`             | GitHub → Settings → Developer settings → Personal access tokens → Tokens(classic) → `repo` 권한으로 생성 |

---

### Step 6 — 수동 테스트 실행

저장소 → **Actions** → **📈 Daily Stock Briefing** → **Run workflow**

정상 실행되면:
- `briefings/YYYY-MM-DD.md` 파일이 생성됨
- 카카오톡 메모챗에 요약이 도착함

---

### Step 7 — 자동 스케줄 확인

`daily_briefing.yml`의 cron 설정:
```yaml
- cron: "0 22 * * *"   # UTC 22:00 = KST 07:00
```

매일 아침 7시에 자동 실행됩니다. ✅

---

## 종목 변경 방법

`briefing.py` 상단의 `KRX_STOCKS` 리스트를 수정:

```python
KRX_STOCKS = [
    {"name": "종목명", "krx": "종목코드6자리", "yf": "종목코드.KS", "search": "뉴스검색어"},
    ...
]
```

KOSDAQ 종목은 `"yf": "종목코드.KQ"` 로 변경.

---

## 파일 구조

```
stock-briefing/
├─ briefing.py                        # 메인 스크립트
├─ kakao_refresh.py                   # 카카오 토큰 자동 갱신
├─ requirements.txt
├─ briefings/
│   ├─ 2026-06-07.md                  # 자동 생성되는 브리핑
│   └─ ...
└─ .github/workflows/
    ├─ daily_briefing.yml             # 매일 07:00 KST 실행
    └─ kakao_refresh.yml              # 5시간마다 토큰 갱신
```
