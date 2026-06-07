"""
daily_briefing.py
매일 아침 7시(KST) GitHub Actions에서 실행되는 주식 브리핑 스크립트
"""

import os
import re
import json
import time
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TODAY_KST = datetime.now(KST)
TODAY_STR  = TODAY_KST.strftime("%Y-%m-%d")
TODAY_YMD  = TODAY_KST.strftime("%Y%m%d")

# ── 환경변수 ────────────────────────────────────────────────────────
KAKAO_ACCESS_TOKEN  = os.environ["KAKAO_ACCESS_TOKEN"]   # 카카오 액세스 토큰
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]    # Claude API 키

# ── 보유 종목 ───────────────────────────────────────────────────────
KRX_STOCKS = [
    {"name": "제주반도체",        "krx": "214150", "yf": "214150.KS", "search": "제주반도체"},
    {"name": "HD현대에너지솔루션", "krx": "322000", "yf": "322000.KS", "search": "HD현대에너지솔루션"},
    {"name": "HLB",              "krx": "028300", "yf": "028300.KS", "search": "HLB 주식"},
    {"name": "클래시스",          "krx": "214190", "yf": "214190.KS", "search": "클래시스"},
]

# 해외 종목이 있다면 여기에 추가
OVERSEAS_STOCKS = []  # 예: [{"name": "Apple", "yf": "AAPL"}]


# ════════════════════════════════════════════════════════════════════
# 1. 주가 조회
# ════════════════════════════════════════════════════════════════════

def get_recent_biz_day(delta=0):
    """가장 최근 영업일(토·일 제외) 반환. delta=-1이면 그 전날."""
    d = TODAY_KST - timedelta(days=delta)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch_price_pykrx(krx_code: str):
    """pykrx로 당일 종가 + 전일 대비 조회"""
    try:
        from pykrx import stock

        today = get_recent_biz_day()
        prev  = get_recent_biz_day(1)

        df_today = stock.get_market_ohlcv_by_date(today, today, krx_code)
        df_prev  = stock.get_market_ohlcv_by_date(prev,  prev,  krx_code)

        if df_today.empty:
            return None

        close_today = int(df_today["종가"].iloc[-1])
        close_prev  = int(df_prev["종가"].iloc[-1]) if not df_prev.empty else None

        change_amt  = close_today - close_prev if close_prev else None
        change_rate = round((change_amt / close_prev) * 100, 2) if close_prev else None

        return {
            "price":      close_today,
            "prev_close": close_prev,
            "change_amt": change_amt,
            "change_rate": change_rate,
            "source":     "pykrx",
        }
    except Exception as e:
        print(f"  [pykrx 오류] {krx_code}: {e}")
        return None


def fetch_price_yfinance(yf_ticker: str):
    """yfinance fallback"""
    try:
        import yfinance as yf

        tk   = yf.Ticker(yf_ticker)
        info = tk.fast_info

        price = info.last_price
        prev  = info.previous_close

        if price is None:
            hist  = tk.history(period="2d")
            if hist.empty:
                return None
            price = hist["Close"].iloc[-1]
            prev  = hist["Close"].iloc[-2] if len(hist) >= 2 else price

        change_amt  = price - prev
        change_rate = round((change_amt / prev) * 100, 2)

        return {
            "price":       round(price),
            "prev_close":  round(prev),
            "change_amt":  round(change_amt),
            "change_rate": change_rate,
            "source":      "yfinance",
        }
    except Exception as e:
        print(f"  [yfinance 오류] {yf_ticker}: {e}")
        return None


def collect_prices():
    results = []
    for s in KRX_STOCKS:
        print(f"주가 조회: {s['name']}")
        data = fetch_price_pykrx(s["krx"]) or fetch_price_yfinance(s["yf"])
        results.append({"name": s["name"], **(data or {"price": None})})
        time.sleep(0.3)

    for s in OVERSEAS_STOCKS:
        print(f"주가 조회(해외): {s['name']}")
        data = fetch_price_yfinance(s["yf"])
        results.append({"name": s["name"], **(data or {"price": None})})

    return results


# ════════════════════════════════════════════════════════════════════
# 2. 뉴스 수집
# ════════════════════════════════════════════════════════════════════

def parse_pub_date(entry) -> datetime | None:
    """feedparser entry의 published 필드를 KST datetime으로 변환"""
    if not hasattr(entry, "published_parsed") or entry.published_parsed is None:
        return None
    return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)


def fetch_google_news(query: str, max_items=3):
    """Google News RSS에서 오늘(KST) 발행 뉴스 최대 max_items개 반환"""
    url = (
        f"https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query + ' when:1d')}"
        f"&hl=ko&gl=KR&ceid=KR:ko"
    )
    try:
        feed  = feedparser.parse(url)
        today = TODAY_KST.date()
        items = []
        for entry in feed.entries:
            pub = parse_pub_date(entry)
            if pub and pub.date() != today:
                continue
            items.append({
                "title":  entry.title,
                "link":   entry.link,
                "source": getattr(entry, "source", {}).get("title", ""),
                "pub":    pub.strftime("%H:%M") if pub else "",
            })
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        print(f"  [뉴스 오류] {query}: {e}")
        return []


def fetch_yf_news(yf_ticker: str, name: str, max_items=3):
    """yfinance Ticker.news로 해외 종목 뉴스 수집 + Claude로 한글 번역"""
    try:
        import yfinance as yf

        tk    = yf.Ticker(yf_ticker)
        news  = tk.news or []
        today = TODAY_KST.date()
        items = []

        for n in news:
            pub_ts = n.get("providerPublishTime", 0)
            pub_dt = datetime.fromtimestamp(pub_ts, tz=KST)
            if pub_dt.date() != today:
                continue
            items.append({
                "title_en": n.get("title", ""),
                "link":     n.get("link", ""),
                "source":   n.get("publisher", ""),
                "pub":      pub_dt.strftime("%H:%M"),
            })
            if len(items) >= max_items:
                break

        # Claude API로 영문 헤드라인 일괄 번역
        if items:
            titles = [it["title_en"] for it in items]
            translated = translate_headlines(titles)
            for i, it in enumerate(items):
                it["title"] = translated[i] if i < len(translated) else it["title_en"]

        return items
    except Exception as e:
        print(f"  [yf뉴스 오류] {yf_ticker}: {e}")
        return []


def translate_headlines(titles: list[str]) -> list[str]:
    """Claude API로 영문 헤드라인을 한글로 번역"""
    prompt = (
        "다음 영문 주식 뉴스 헤드라인을 자연스러운 한국어로 번역해줘. "
        "JSON 배열로만 응답해. 예: [\"번역1\", \"번역2\"]\n\n"
        + json.dumps(titles, ensure_ascii=False)
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        text = resp.json()["content"][0]["text"]
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception as e:
        print(f"  [번역 오류] {e}")
        return titles


def collect_news():
    news_map = {}
    for s in KRX_STOCKS:
        print(f"뉴스 수집: {s['name']}")
        news_map[s["name"]] = fetch_google_news(s["search"])
        time.sleep(0.5)

    for s in OVERSEAS_STOCKS:
        print(f"뉴스 수집(해외): {s['name']}")
        news_map[s["name"]] = fetch_yf_news(s["yf"], s["name"])

    return news_map


# ════════════════════════════════════════════════════════════════════
# 3. 브리핑 마크다운 생성 (Claude API)
# ════════════════════════════════════════════════════════════════════

def build_prompt(prices: list, news_map: dict) -> str:
    price_lines = []
    for p in prices:
        if p.get("price"):
            sign = "▲" if (p.get("change_amt") or 0) > 0 else "▼"
            rate = p.get("change_rate", 0) or 0
            price_lines.append(
                f"- {p['name']}: {p['price']:,}원  {sign} {abs(p.get('change_amt',0)):,}원 ({rate:+.2f}%)"
            )
        else:
            price_lines.append(f"- {p['name']}: 조회 실패")

    news_lines = []
    for name, items in news_map.items():
        news_lines.append(f"\n### {name}")
        if not items:
            news_lines.append("- 오늘 뉴스 없음")
        for it in items:
            news_lines.append(f"- [{it['title']}]({it['link']})  ({it.get('source','')} {it.get('pub','')})")

    return f"""
오늘({TODAY_STR}) 주식 포트폴리오 데이터를 정리해서 일일 브리핑 마크다운을 작성해줘.

## 주가 데이터
{chr(10).join(price_lines)}

## 뉴스 데이터
{''.join(news_lines)}

## 출력 형식 (이 형식을 정확히 따라줘)
---
# 📈 일일 포트폴리오 브리핑 — {TODAY_STR}

## 💹 가격 요약
| 종목 | 현재가 | 전일대비 | 등락률 |
|------|--------|---------|--------|
(표 채워줘)

## 📰 종목별 뉴스
(종목별로 뉴스 정리, 링크 포함)

## 🔑 오늘의 핵심 한 줄
(전체 포트폴리오 흐름을 1문장으로)

## ⚡ 오늘의 액션
(구체적 투자 행동 제안 1~2개, 매수/매도/관망 등)
---

그리고 마지막에 아래 형식으로 카카오톡용 200자 이내 요약도 붙여줘:

KAKAO_MSG_START
(200자 이내 요약. 이모지 포함. 핵심 주가 + 주요 뉴스 1줄 + 액션)
KAKAO_MSG_END
"""


def generate_briefing(prices, news_map) -> tuple[str, str]:
    """Claude API로 브리핑 생성. (markdown, kakao_msg) 반환"""
    print("브리핑 생성 중 (Claude API)...")
    prompt = build_prompt(prices, news_map)

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    full_text = resp.json()["content"][0]["text"]

    # 카카오 메시지 추출
    kakao_match = re.search(r"KAKAO_MSG_START\n([\s\S]*?)\nKAKAO_MSG_END", full_text)
    kakao_msg   = kakao_match.group(1).strip() if kakao_match else full_text[:200]

    # 마크다운에서 KAKAO 블록 제거
    markdown = re.sub(r"\nKAKAO_MSG_START[\s\S]*?KAKAO_MSG_END", "", full_text).strip()

    return markdown, kakao_msg


# ════════════════════════════════════════════════════════════════════
# 4. 카카오톡 메모챗 전송
# ════════════════════════════════════════════════════════════════════

def send_kakao_memo(text: str):
    """카카오톡 나에게 보내기 (메모챗)"""
    print("카카오톡 메모챗 전송 중...")
    resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={
            "Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "template_object": json.dumps({
                "object_type": "text",
                "text":        text[:200],
                "link":        {"web_url": "", "mobile_web_url": ""},
            })
        },
        timeout=10,
    )
    if resp.status_code == 200 and resp.json().get("result_code") == 0:
        print("  ✅ 카카오톡 전송 성공")
    else:
        print(f"  ❌ 카카오톡 전송 실패: {resp.status_code} {resp.text}")


# ════════════════════════════════════════════════════════════════════
# 5. 메인
# ════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'='*50}")
    print(f"  일일 주식 브리핑 시작 — {TODAY_STR} 07:00 KST")
    print(f"{'='*50}\n")

    # 주가 수집
    prices   = collect_prices()

    # 뉴스 수집
    news_map = collect_news()

    # 브리핑 생성
    markdown, kakao_msg = generate_briefing(prices, news_map)

    # 마크다운 파일 저장
    out_dir  = "briefings"
    os.makedirs(out_dir, exist_ok=True)
    out_path = f"{out_dir}/{TODAY_STR}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"\n브리핑 저장 완료: {out_path}")

    # 카카오톡 전송
    send_kakao_memo(kakao_msg)

    print(f"\n{'='*50}")
    print("  완료!")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
