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
KAKAO_ACCESS_TOKEN = os.environ.get("KAKAO_ACCESS_TOKEN", "")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

# ── 보유 종목 (정확한 종목코드 및 거래소 반영) ──────────────────────
KRX_STOCKS = [
    {"name": "제주반도체",           "krx": "080220", "yf": "080220.KQ", "search": "제주반도체"},
    {"name": "HD현대에너지솔루션",    "krx": "322000", "yf": "322000.KS", "search": "HD현대에너지솔루션"},
    {"name": "HLB",                  "krx": "028300", "yf": "028300.KQ", "search": "HLB 주식"},
    {"name": "클래시스",             "krx": "214150", "yf": "214150.KQ", "search": "클래시스"},
]

OVERSEAS_STOCKS = []  # 해외 종목 추가 시: [{"name": "Apple", "yf": "AAPL"}]


# ════════════════════════════════════════════════════════════════════
# 1. 주가 조회
# ════════════════════════════════════════════════════════════════════

def get_recent_biz_day(offset=0):
    """가장 최근 영업일 기준으로 offset일 전 영업일 반환"""
    d = TODAY_KST.date()
    count = 0
    while True:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        if count == offset:
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
        count += 1


def fetch_price_pykrx(krx_code: str):
    """pykrx로 당일 종가 + 전일 대비 조회 (로그인 불필요)"""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import logging
        logging.disable(logging.CRITICAL)
        from pykrx import stock
        logging.disable(logging.NOTSET)

        today = get_recent_biz_day(0)
        prev  = get_recent_biz_day(1)

        df_today = stock.get_market_ohlcv_by_date(today, today, krx_code)
        df_prev  = stock.get_market_ohlcv_by_date(prev,  prev,  krx_code)

        if df_today.empty:
            return None

        close_today = int(df_today["종가"].iloc[-1])
        close_prev  = int(df_prev["종가"].iloc[-1]) if not df_prev.empty else None

        change_amt  = (close_today - close_prev) if close_prev else None
        change_rate = round((change_amt / close_prev) * 100, 2) if close_prev else None

        return {
            "price":       close_today,
            "prev_close":  close_prev,
            "change_amt":  change_amt,
            "change_rate": change_rate,
            "source":      "pykrx",
        }
    except Exception as e:
        print(f"  [pykrx 오류] {krx_code}: {e}")
        return None


def fetch_price_yfinance(yf_ticker: str):
    """yfinance fallback"""
    try:
        import yfinance as yf

        tk   = yf.Ticker(yf_ticker)
        hist = tk.history(period="5d")

        if hist.empty or len(hist) < 1:
            return None

        close_today = hist["Close"].iloc[-1]
        close_prev  = hist["Close"].iloc[-2] if len(hist) >= 2 else None

        change_amt  = (close_today - close_prev) if close_prev is not None else None
        change_rate = round((change_amt / close_prev) * 100, 2) if close_prev else None

        return {
            "price":       round(close_today),
            "prev_close":  round(close_prev) if close_prev else None,
            "change_amt":  round(change_amt) if change_amt is not None else None,
            "change_rate": change_rate,
            "source":      "yfinance",
        }
    except Exception as e:
        print(f"  [yfinance 오류] {yf_ticker}: {e}")
        return None


def collect_prices():
    results = []
    for s in KRX_STOCKS:
        print(f"주가 조회: {s['name']} ({s['krx']})")
        data = fetch_price_pykrx(s["krx"]) or fetch_price_yfinance(s["yf"])
        results.append({"name": s["name"], "krx": s["krx"], **(data or {"price": None})})
        time.sleep(0.5)

    for s in OVERSEAS_STOCKS:
        print(f"주가 조회(해외): {s['name']}")
        data = fetch_price_yfinance(s["yf"])
        results.append({"name": s["name"], **(data or {"price": None})})

    return results


# ════════════════════════════════════════════════════════════════════
# 2. 뉴스 수집
# ════════════════════════════════════════════════════════════════════

def parse_pub_date(entry):
    if not hasattr(entry, "published_parsed") or entry.published_parsed is None:
        return None
    return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)


def fetch_google_news(query: str, max_items=3):
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
            # 제목에서 언론사 부분 제거 (Google RSS는 "제목 - 언론사" 형식)
            title = re.sub(r'\s*-\s*[^-]+$', '', entry.title).strip()
            source = getattr(entry, "source", {}).get("title", "")
            items.append({
                "title":  title,
                "link":   entry.link,
                "source": source,
                "pub":    pub.strftime("%H:%M") if pub else "",
            })
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        print(f"  [뉴스 오류] {query}: {e}")
        return []


def collect_news():
    news_map = {}
    for s in KRX_STOCKS:
        print(f"뉴스 수집: {s['name']}")
        news_map[s["name"]] = fetch_google_news(s["search"])
        time.sleep(0.5)
    return news_map


# ════════════════════════════════════════════════════════════════════
# 3. 브리핑 마크다운 생성
# ════════════════════════════════════════════════════════════════════

def change_str(data: dict) -> str:
    amt  = data.get("change_amt")
    rate = data.get("change_rate")
    if amt is None:
        return "N/A"
    sign = "▲" if amt > 0 else ("▼" if amt < 0 else "-")
    return f"{sign} {abs(amt):,}원 ({rate:+.2f}%)"


def build_markdown(prices: list, news_map: dict) -> str:
    # 가격 요약 표
    table_rows = []
    for p in prices:
        price_str  = f"{p['price']:,}원" if p.get("price") else "조회 실패"
        change     = change_str(p)
        src        = p.get("source", "")
        table_rows.append(f"| {p['name']} | {price_str} | {change} | {src} |")

    table = "\n".join([
        "| 종목 | 현재가 | 전일대비 | 출처 |",
        "|------|--------|---------|------|",
        *table_rows,
    ])

    # 종목별 뉴스
    news_sections = []
    for name, items in news_map.items():
        lines = [f"### {name}"]
        if not items:
            lines.append("- 오늘 뉴스 없음")
        for it in items:
            pub_str = f" `{it['pub']}`" if it.get("pub") else ""
            src_str = f" _{it['source']}_" if it.get("source") else ""
            lines.append(f"- [{it['title']}]({it['link']}){src_str}{pub_str}")
        news_sections.append("\n".join(lines))

    # 핵심 한 줄 & 액션 (Claude API 있으면 생성, 없으면 기본값)
    key_line = "AI 요약 미설정 (ANTHROPIC_API_KEY를 추가하면 자동 생성됩니다)"
    action   = "보유 종목 뉴스 확인 후 포지션 점검"

    if ANTHROPIC_API_KEY:
        try:
            price_summary = "\n".join([
                f"- {p['name']}: {p.get('price','N/A'):,}원 {change_str(p)}" if p.get('price') else f"- {p['name']}: 조회 실패"
                for p in prices
            ])
            news_summary = "\n".join([
                f"{name}: " + " / ".join([it['title'] for it in items[:2]])
                for name, items in news_map.items() if items
            ])
            prompt = (
                f"오늘({TODAY_STR}) 포트폴리오 현황:\n{price_summary}\n\n"
                f"주요 뉴스:\n{news_summary}\n\n"
                "1) 전체 포트폴리오 흐름을 1문장으로 요약해줘.\n"
                "2) 오늘의 투자 액션 1~2개를 짧게 제안해줘.\n"
                "JSON으로만 응답: {\"key_line\": \"...\", \"action\": \"...\"}"
            )
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp_json = resp.json()
            if "content" not in resp_json:
                print(f"  [Claude API 응답 오류] {resp_json}")
                raise ValueError(f"No content in response: {resp_json}")
            text   = resp_json["content"][0]["text"]
            text   = re.sub(r"```json|```", "", text).strip()
            parsed = json.loads(text)
            key_line = parsed.get("key_line", key_line)
            action   = parsed.get("action", action)
        except Exception as e:
            print(f"  [Claude API 오류] {e}")

    markdown = f"""# 📈 일일 포트폴리오 브리핑 — {TODAY_STR}

## 💹 가격 요약

{table}

## 📰 종목별 뉴스

{chr(10).join(news_sections)}

## 🔑 오늘의 핵심 한 줄

{key_line}

## ⚡ 오늘의 액션

{action}

---
_생성 시각: {TODAY_KST.strftime("%Y-%m-%d %H:%M")} KST_
"""
    return markdown


def build_kakao_msg(prices: list, news_map: dict) -> str:
    lines = [f"📈 {TODAY_STR} 포트폴리오 브리핑", ""]

    # 주가 요약
    lines.append("[ 주가 ]")
    for p in prices:
        if p.get("price"):
            rate = p.get("change_rate", 0) or 0
            sign = "▲" if rate > 0 else ("▼" if rate < 0 else "-")
            lines.append(f"{p['name']}: {p['price']:,}원 {sign}{abs(rate):.2f}%")
        else:
            lines.append(f"{p['name']}: 조회 실패")

    # 종목별 뉴스 전체 (3개씩)
    lines.append("")
    lines.append("[ 뉴스 ]")
    for name, items in news_map.items():
        lines.append(f"\n▶ {name}")
        if not items:
            lines.append("  오늘 뉴스 없음")
        for it in items:
            pub = f" ({it['pub']})" if it.get("pub") else ""
            lines.append(f"  • {it['title']}{pub}")

    msg = "\n".join(lines)
    return msg[:9000]


# ════════════════════════════════════════════════════════════════════
# 4. 카카오톡 메모챗 전송
# ════════════════════════════════════════════════════════════════════

def send_kakao_memo(text: str):
    if not KAKAO_ACCESS_TOKEN:
        print("  [카카오] KAKAO_ACCESS_TOKEN 미설정, 전송 생략")
        return

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
                "text":        text[:9000],
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
    print(f"  일일 주식 브리핑 — {TODAY_STR} 07:00 KST")
    print(f"{'='*50}\n")

    # ── 중복 실행 방지 ───────────────────────────────────────────────
    out_dir  = "briefings"
    out_path = f"{out_dir}/{TODAY_STR}.md"
    if os.path.exists(out_path):
        print(f"⏭️  오늘({TODAY_STR}) 브리핑이 이미 존재합니다. 중복 실행 건너뜀.")
        return

    prices   = collect_prices()
    news_map = collect_news()

    markdown  = build_markdown(prices, news_map)
    kakao_msg = build_kakao_msg(prices, news_map)

    # 마크다운 저장
    out_dir  = "briefings"
    os.makedirs(out_dir, exist_ok=True)
    out_path = f"{out_dir}/{TODAY_STR}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"\n✅ 브리핑 저장: {out_path}")
    print("\n" + "="*50)
    print(markdown)
    print("="*50)

    send_kakao_memo(kakao_msg)
    print("\n완료!")


if __name__ == "__main__":
    main()
