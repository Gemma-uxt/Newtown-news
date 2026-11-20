"""
3기 신도시 / 광명시흥 / 의왕군포안산 뉴스 수집기
- Google 뉴스 RSS에서 최근 기사를 모아
- 이미 보낸 기사는 빼고
- 메일로 보내고, 웹페이지(docs/)용 기록도 남깁니다.
외부 패키지 없이 파이썬 기본 기능만 씁니다.
"""
import json
import os
import smtplib
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from html import escape

# ───────── 여기만 고치면 키워드를 바꿀 수 있어요 ─────────
TOPICS = {
    "3기 신도시": ['"3기 신도시"', '"3기신도시"'],
    "광명시흥": ['광명시흥 신도시', '"광명시흥지구"', '"광명시흥"'],
    "의왕군포안산": ['"의왕군포안산"', '의왕 군포 안산 신도시'],
    "용량시장": ['"용량시장"'],
    "히트펌프": ['"히트펌프"'],
    "전극보일러": ['"전극보일러"'],
}
# 제목에 이 단어가 들어간 기사는 뺍니다 (가전제품 광고성 기사 등)
EXCLUDE = {
    "히트펌프": ["건조기", "세탁기", "에어컨", "특가", "할인"],
}
SIMILAR = 0.5                # 제목이 이 정도 이상 겹치면 같은 기사로 봅니다(0~1)
LOOKBACK_HOURS = 30          # 최근 몇 시간 기사까지 볼지
MAX_PER_TOPIC = 30           # 주제별 최대 기사 수
ARCHIVE_DAYS = 60            # 웹페이지에 남겨둘 기간(일)
# ─────────────────────────────────────────────────────────

KST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(BASE, "seen.json")
ARCHIVE_FILE = os.path.join(BASE, "docs", "archive.json")


def fetch_rss(query):
    q = urllib.parse.quote(f"{query} when:2d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        time.sleep(3 if attempt == 0 else 20 * attempt)   # 너무 빨리 요청하면 구글이 막아서 쉬어가며 요청
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise


def parse_rss(xml_bytes):
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        pub = it.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub).astimezone(KST) if pub else None
        except Exception:
            dt = None
        # 구글 뉴스 제목 끝의 " - 언론사" 떼기
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)].strip()
        if title and link:
            items.append({"title": title, "link": link, "source": src, "dt": dt})
    return items


def norm(title):
    return "".join(ch for ch in title.lower() if ch.isalnum())


def grams(title):
    t = norm(title)
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def similar(a, b):
    return len(a & b) / len(a | b) >= SIMILAR if a and b else False


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def collect(seen):
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    seen_grams = [grams(t) for t in seen]
    picked = []                      # 이번에 고른 기사들 (주제 넘어서도 중복 확인)
    result = {}
    for topic, queries in TOPICS.items():
        bucket = []
        for q in queries:
            try:
                items = parse_rss(fetch_rss(q))
            except Exception as e:
                print(f"[경고] '{q}' 가져오기 실패: {e}", file=sys.stderr)
                continue
            for a in items:
                if a["dt"] and a["dt"] < cutoff:
                    continue
                if any(w in a["title"] for w in EXCLUDE.get(topic, [])):
                    continue
                g = grams(a["title"])
                if any(similar(g, x) for x in seen_grams):
                    continue          # 지난번에 이미 보낸 것과 비슷
                dup = next((p for p in picked if similar(g, p["g"])), None)
                if dup:
                    if a["source"] and a["source"] not in dup["others"] and a["source"] != dup["source"]:
                        dup["others"].append(a["source"])
                    continue          # 이번에 고른 것과 비슷 → 언론사 이름만 추가
                a["g"], a["others"] = g, []
                picked.append(a)
                bucket.append(a)
        bucket.sort(key=lambda a: a["dt"] or now, reverse=True)
        result[topic] = bucket[:MAX_PER_TOPIC]
    return result


def build_html(result, today):
    total = sum(len(v) for v in result.values())
    parts = [
        '<div style="font-family:Apple SD Gothic Neo,Malgun Gothic,sans-serif;max-width:680px;color:#222">',
        f'<h2 style="margin:0 0 4px">신도시·에너지 뉴스 브리핑</h2>',
        f'<div style="color:#888;font-size:13px;margin-bottom:18px">{today} · 새 기사 {total}건</div>',
    ]
    for topic, arts in result.items():
        parts.append(
            f'<h3 style="border-left:4px solid #2a6df4;padding-left:8px;margin:22px 0 8px">'
            f'{escape(topic)} <span style="color:#888;font-weight:normal">({len(arts)})</span></h3>'
        )
        if not arts:
            parts.append('<div style="color:#aaa;font-size:14px">새 기사 없음</div>')
            continue
        parts.append('<ul style="padding-left:18px;margin:0">')
        for a in arts:
            when = a["dt"].strftime("%m/%d %H:%M") if a["dt"] else ""
            more = f' · 비슷한 기사 {len(a["others"])}건' if a["others"] else ""
            parts.append(
                f'<li style="margin:6px 0;line-height:1.45">'
                f'<a href="{escape(a["link"])}" style="color:#1a1a1a;text-decoration:none">{escape(a["title"])}</a>'
                f' <span style="color:#999;font-size:12px">{escape(a["source"])} {when}{more}</span></li>'
            )
        parts.append("</ul>")
    web = os.environ.get("WEB_URL")
    if web:
        parts.append(f'<p style="margin-top:24px;font-size:13px"><a href="{escape(web)}">지난 기사 모아보기 →</a></p>')
    parts.append("</div>")
    return "".join(parts), total


def send_mail(html, subject):
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not (user and pw):
        print("[안내] 메일 설정(GMAIL_USER, GMAIL_APP_PASSWORD)이 없어 메일은 건너뜁니다.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    print(f"메일 발송 완료 → {to}")


def update_archive(result, today):
    archive = load_json(ARCHIVE_FILE, [])
    for topic, arts in result.items():
        for a in arts:
            archive.append({
                "date": today,
                "topic": topic,
                "title": a["title"],
                "link": a["link"],
                "source": a["source"],
                "similar": len(a["others"]),
                "time": a["dt"].strftime("%Y-%m-%d %H:%M") if a["dt"] else "",
            })
    limit = (datetime.now(KST) - timedelta(days=ARCHIVE_DAYS)).strftime("%Y-%m-%d")
    archive = [x for x in archive if x["date"] >= limit]
    os.makedirs(os.path.dirname(ARCHIVE_FILE), exist_ok=True)
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=1)


def main():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    seen = load_json(SEEN_FILE, [])
    result = collect(seen)
    html, total = build_html(result, today)

    send_mail(html, f"[신도시·에너지 뉴스] {today} 새 기사 {total}건")

    for arts in result.values():
        for a in arts:
            seen.append(norm(a["title"]))
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-3000:], f, ensure_ascii=False)
    update_archive(result, today)
    print(f"완료: 새 기사 {total}건")


if __name__ == "__main__":
    main()
