# -*- coding: utf-8 -*-
"""趨勢周報：抓 RSS -> 篩本週 -> 產 HTML 存檔。純 stdlib，免裝套件。
執行： C:\\Users\\8803\\AppData\\Local\\Programs\\Python\\Python312\\python.exe weekly_report.py
"""
import urllib.request, urllib.error, ssl, sys, os, html, re, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ── 來源設定 ──
# QUERIES：主題 -> 關鍵字清單，走 Google News RSS（繁中、永遠在線、免維護死連結）。想調主題改這裡。
QUERIES = {
    "ESG / 永續": ["ESG 永續", "淨零 碳排", "永續報告 企業"],
    "AI 趨勢 / 產品": ["AI 人工智慧 趨勢", "AI 產品 發表", "生成式AI 應用"],
}
# FEEDS：主題 -> 直連 RSS（補充深度來源）。無則留空。
FEEDS = {
    "ESG / 永續": ["https://esg.gvm.com.tw/rss"],                                    # ESG遠見
    "AI 趨勢 / 產品": ["https://techcrunch.com/category/artificial-intelligence/feed/"],
}

DAYS = 7                       # 抓幾天內
CAP = 30                       # 每主題最多留幾則
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (weekly-report-bot)"
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE   # ponytail: 關 SSL 驗證圖方便；正式對外服務別這樣


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=_ctx) as r:
        return r.read()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:                                    # RSS: RFC822
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:                                    # Atom: ISO8601
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def strip_html(s, limit=180):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s).strip()
    return (s[:limit] + "…") if len(s) > limit else s


def parse_feed(xml_bytes):
    """回傳 [(title, link, date, summary)]，吃 RSS 與 Atom。"""
    root = ET.fromstring(xml_bytes)
    tag = lambda e: e.tag.split("}")[-1]    # 去 namespace
    out = []
    # RSS: channel/item ; Atom: feed/entry
    items = [e for e in root.iter() if tag(e) in ("item", "entry")]
    for it in items:
        d = {"title": "", "link": "", "date": None, "summary": ""}
        for c in it:
            t = tag(c)
            if t == "title":
                d["title"] = (c.text or "").strip()
            elif t == "link":
                d["link"] = (c.text or c.get("href") or "").strip()
            elif t in ("pubDate", "published", "updated", "date"):
                if d["date"] is None:
                    d["date"] = parse_date(c.text)
            elif t in ("description", "summary", "content"):
                if not d["summary"]:
                    d["summary"] = strip_html(c.text)
        out.append(d)
    return out


def gnews_url(q):
    qs = urllib.parse.quote(q)
    return f"https://news.google.com/rss/search?q={qs}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def norm(t):
    return re.sub(r"\s+", "", (t or "")).lower()[:40]   # 去空白+截頭當去重鍵


def collect():
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    result, log = {}, []
    topics = set(QUERIES) | set(FEEDS)
    for topic in topics:
        urls = [gnews_url(q) for q in QUERIES.get(topic, [])] + FEEDS.get(topic, [])
        rows, seen = [], set()
        for u in urls:
            label = u if u.startswith("http") else u
            try:
                items = parse_feed(fetch(u))
            except Exception as e:
                log.append(f"[skip] {label[:60]} -> {type(e).__name__}")
                continue
            kept = 0
            for it in items:
                dt = it["date"]
                if dt is not None and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt is not None and dt < cutoff:      # 有日期且過期 -> 丟
                    continue
                key = norm(it["title"])
                if not key or key in seen:              # 去重（跨查詢重複很多）
                    continue
                seen.add(key)
                it["date"] = dt
                rows.append(it)
                kept += 1
            log.append(f"[ok]   {label[:60]} -> {kept} 則")
        rows.sort(key=lambda r: r["date"] or cutoff, reverse=True)
        result[topic] = rows[:CAP]
    return result, log


def build_html(data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>趨勢周報 {now}</title><style>
body{{font-family:"Segoe UI","Microsoft JhengHei",sans-serif;max-width:820px;margin:0 auto;padding:24px;color:#1a1a1a;line-height:1.6}}
h1{{font-size:24px;border-bottom:3px solid #2563eb;padding-bottom:8px}}
h2{{font-size:19px;margin-top:32px;color:#2563eb}}
.item{{padding:12px 0;border-bottom:1px solid #eee}}
.item a{{font-weight:600;color:#111;text-decoration:none;font-size:16px}}
.item a:hover{{color:#2563eb}}
.date{{color:#888;font-size:13px;margin-left:6px}}
.sum{{color:#555;font-size:14px;margin-top:4px}}
.meta{{color:#999;font-size:12px;margin-top:24px}}
</style></head><body>
<h1>📊 趨勢周報 <span class="date">{now}</span></h1>"""]
    for topic, rows in data.items():
        parts.append(f"<h2>{html.escape(topic)}（{len(rows)} 則）</h2>")
        if not rows:
            parts.append('<p class="sum">本週無新資料。</p>')
        for it in rows:
            ds = it["date"].strftime("%m/%d") if it["date"] else ""
            parts.append(f'''<div class="item">
<a href="{html.escape(it["link"])}" target="_blank">{html.escape(it["title"])}</a>
<span class="date">{ds}</span>
<div class="sum">{html.escape(it["summary"])}</div></div>''')
    parts.append(f'<p class="meta">自動產生於 {now} · 純本機測試資料版</p></body></html>')
    return "\n".join(parts)


def send_mail(html_body, total):
    """有設 env 就寄；沒設就跳過。Gmail SMTP -> 收件者(可 Outlook)。"""
    import smtplib
    from email.message import EmailMessage
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PW")
    to = os.environ.get("MAIL_TO")
    if not (user and pw and to):
        print("[mail] 未設 GMAIL_USER/GMAIL_APP_PW/MAIL_TO，跳過寄信")
        return
    msg = EmailMessage()
    msg["Subject"] = f"趨勢周報 {datetime.now():%Y-%m-%d}（{total} 則）"
    msg["From"] = user
    msg["To"] = to
    msg.set_content("此信為 HTML 格式，請用支援 HTML 的信箱檢視。")
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] 已寄至 {to}")


def main():
    data, log = collect()
    print("\n".join(log))
    body = build_html(data)
    total = sum(len(v) for v in data.values())
    fname = "周報_" + datetime.now().strftime("%Y%m%d") + ".html"
    path = os.path.join(OUT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"\n共 {total} 則 -> {path}")
    send_mail(body, total)
    return path


if __name__ == "__main__":
    main()
