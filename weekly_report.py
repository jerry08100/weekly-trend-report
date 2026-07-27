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


def split_source(title):
    """Google News 標題常是「標題 - 媒體」，拆出乾淨標題與來源。"""
    m = re.match(r"^(.*)\s[-–—]\s([^-–—]{1,20})$", title or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return (title or "").strip(), ""


def gemini_digest(topic, rows):
    """呼叫 Gemini 產本週摘要。回 {overview, highlights:[{idx,note}]}；無 key 或失敗回 None。"""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not rows:
        return None
    lines = []
    for i, it in enumerate(rows):
        t, src = split_source(it["title"])
        lines.append(f"[{i}] {t}" + (f"（{src}）" if src else ""))
    prompt = (
        f"你是產業趨勢分析師。以下是本週「{topic}」相關新聞標題（附編號）。\n"
        "請用繁體中文：\n"
        "1. overview：2~4 句總結本週該領域的重點趨勢。\n"
        "2. highlights：挑最重要的 5~8 則，每則給該則的 idx 與一句話說明為何值得看。\n"
        "只根據下列標題判斷，不要杜撰。\n\n" + "\n".join(lines)
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "overview": {"type": "string"},
                    "highlights": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "idx": {"type": "integer"},
                                "note": {"type": "string"},
                            },
                            "required": ["idx", "note"],
                        },
                    },
                },
                "required": ["overview", "highlights"],
            },
        },
    }
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-flash-latest:generateContent?key=" + key)
    try:
        import json
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60, context=_ctx) as r:
            resp = json.loads(r.read())
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:
        print(f"[ai] {topic} 摘要失敗，回退純清單 -> {type(e).__name__}: {e}")
        return None


def build_html(data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>趨勢周報 {now}</title><style>
body{{font-family:"Segoe UI","Microsoft JhengHei",sans-serif;max-width:820px;margin:0 auto;padding:24px;color:#1a1a1a;line-height:1.6}}
h1{{font-size:24px;border-bottom:3px solid #2563eb;padding-bottom:8px}}
h2{{font-size:19px;margin-top:36px;color:#2563eb;border-bottom:1px solid #e5e7eb;padding-bottom:6px}}
h3{{font-size:15px;color:#374151;margin:20px 0 8px}}
.overview{{background:#f0f6ff;border-left:4px solid #2563eb;padding:12px 16px;border-radius:6px;font-size:15px;color:#1e3a5f}}
.hl{{padding:10px 0;border-bottom:1px solid #eee}}
.hl a{{font-weight:600;color:#111;text-decoration:none;font-size:16px}}
.hl a:hover{{color:#2563eb}}
.src{{color:#9ca3af;font-size:12px;margin-left:6px}}
.note{{color:#555;font-size:14px;margin-top:3px}}
.item{{padding:7px 0;font-size:14px}}
.item a{{color:#374151;text-decoration:none}}
.item a:hover{{color:#2563eb}}
.date{{color:#9ca3af;font-size:12px;margin-left:6px}}
details{{margin-top:12px}}
summary{{cursor:pointer;color:#6b7280;font-size:13px}}
.meta{{color:#999;font-size:12px;margin-top:32px;border-top:1px solid #eee;padding-top:12px}}
</style></head><body>
<h1>📊 趨勢周報 <span class="src">{now}</span></h1>"""]
    for topic, rows in data.items():
        parts.append(f"<h2>{html.escape(topic)}（{len(rows)} 則）</h2>")
        if not rows:
            parts.append('<p class="note">本週無新資料。</p>')
            continue
        dg = gemini_digest(topic, rows)
        if dg:
            parts.append(f'<p class="overview">{html.escape(dg.get("overview", ""))}</p>')
            parts.append("<h3>🔍 本週重點</h3>")
            for h in dg.get("highlights", []):
                i = h.get("idx")
                if not isinstance(i, int) or not (0 <= i < len(rows)):
                    continue
                it = rows[i]
                t, src = split_source(it["title"])
                ds = it["date"].strftime("%m/%d") if it["date"] else ""
                parts.append(f'''<div class="hl">
<a href="{html.escape(it["link"])}" target="_blank">{html.escape(t)}</a>
<span class="src">{html.escape(src)} {ds}</span>
<div class="note">{html.escape(h.get("note", ""))}</div></div>''')
            parts.append('<details><summary>展開全部 {} 則</summary>'.format(len(rows)))
        # 完整清單（有 AI 時收在 details 內，無 AI 時直接列）
        for it in rows:
            t, src = split_source(it["title"])
            ds = it["date"].strftime("%m/%d") if it["date"] else ""
            parts.append(f'''<div class="item">
<a href="{html.escape(it["link"])}" target="_blank">{html.escape(t)}</a>
<span class="date">{html.escape(src)} {ds}</span></div>''')
        if dg:
            parts.append('</details>')
    parts.append(f'<p class="meta">自動產生於 {now} · 資料來源：Google News RSS 等公開來源 · AI 摘要僅供參考</p></body></html>')
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
