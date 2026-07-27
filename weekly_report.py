# -*- coding: utf-8 -*-
"""趨勢周報：抓 RSS -> 篩本週 -> 產 HTML 存檔。純 stdlib，免裝套件。
執行： C:\\Users\\8803\\AppData\\Local\\Programs\\Python\\Python312\\python.exe weekly_report.py
"""
import urllib.request, urllib.error, ssl, sys, os, html, re, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ── 來源設定 ──
# QUERIES：主題 -> 關鍵字清單，走 Google News RSS（繁中、hl/gl=TW 偏台灣）。想調主題改這裡。
QUERIES = {
    "永續 / 企業實務": [
        "ISO 14064 溫室氣體盤查 企業",
        "企業 淨零 碳盤查 轉型",
        "碳足跡 查證 永續 顧問 服務",
        "永續 服務 台灣 企業",
    ],
    "AI / 企業應用": [
        "企業 導入 AI Agent 生產力",
        "AI 工具 企業 應用 台灣",
        "生成式 AI 企業 導入 效率",
    ],
}
# FEEDS：主題 -> 直連 RSS（補充台灣深度來源）。無則留空。
FEEDS = {
    "永續 / 企業實務": ["https://esg.gvm.com.tw/rss"],                                # ESG遠見（台灣）
    "AI / 企業應用": [],
}
# FOCUS：主題 -> 給 AI 的聚焦提示，決定摘要口味。
FOCUS = {
    "永續 / 企業實務": "聚焦『台灣企業的執行面』：ISO 14064-1／GHG 盤查、碳盤查與查證、"
                       "同業（永續顧問／服務業者）推出的新服務、企業轉型實務。少談國際宏觀情勢。",
    "AI / 企業應用": "聚焦『台灣企業的 AI 應用』：可用的新工具、企業導入 AI Agent 提升工作效率的趨勢與案例。"
                     "少談國際大廠模型軍備競賽等宏觀新聞。",
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
    """呼叫 Gemini 產本週回顧文。回 {sections:[{heading,body}]}，body 內含 [[編號]] 引用標記。
    無 key 或失敗回 None。"""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not rows:
        return None
    lines = []
    for i, it in enumerate(rows):
        t, src = split_source(it["title"])
        lines.append(f"[{i}] {t}" + (f"（{src}）" if src else ""))
    focus = FOCUS.get(topic, "")
    prompt = (
        f"你是資深產業分析師。下面是本週「{topic}」的新聞清單（每則附編號）。\n\n"
        + (f"【聚焦方向】{focus}\n\n" if focus else "")
        + "請用繁體中文寫一段「一分鐘看懂本週情勢」的濃縮精華，要求：\n"
        "1. 只輸出 1 個小節，heading 設為「一分鐘看懂本週情勢」。\n"
        "2. body 約 120~180 字，連貫一段文字（不要條列），只點出最貼合上述聚焦方向的 3~5 個動態，"
        "其餘（尤其國際宏觀新聞）略過。\n"
        "3. 讀者想深入會自己點連結，所以文字要精煉、給大局，不要逐則流水帳。\n"
        "4. 在提到具體事件/法規/數據處，於該句尾用 [[編號]] 標引用來源，可多個如 [[3]][[7]]；編號即下方新聞編號。\n"
        "5. 只根據提供的標題撰寫，不要杜撰未提及的事實或數字。若清單內容與聚焦方向落差大，就據實寫本週相關動態較少。\n\n"
        "新聞清單：\n" + "\n".join(lines)
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["heading", "body"],
                        },
                    },
                },
                "required": ["sections"],
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
h4{{font-size:16px;color:#374151;margin:22px 0 6px}}
.body{{font-size:15px;color:#222;text-align:justify}}
.body p{{margin:8px 0}}
sup a{{color:#2563eb;text-decoration:none;font-weight:600}}
.refs{{margin-top:16px;font-size:13px;color:#444}}
.refs ol{{padding-left:22px;margin:6px 0}}
.refs li{{margin:4px 0}}
.refs a{{color:#374151;text-decoration:none}}
.refs a:hover{{color:#2563eb;text-decoration:underline}}
.src{{color:#9ca3af}}
.item{{padding:6px 0;font-size:13px}}
.item a{{color:#4b5563;text-decoration:none}}
.item a:hover{{color:#2563eb}}
.date{{color:#9ca3af;font-size:12px;margin-left:6px}}
.note{{color:#555;font-size:14px}}
details{{margin-top:14px}}
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
        if dg and dg.get("sections"):
            parts.append(render_article(topic, dg["sections"], rows))
            parts.append('<details><summary>展開本週全部 {} 則</summary>'.format(len(rows)))
        for it in rows:                      # 完整清單（有 AI 時收在 details 內）
            t, src = split_source(it["title"])
            ds = it["date"].strftime("%m/%d") if it["date"] else ""
            parts.append(f'''<div class="item">
<a href="{html.escape(it["link"])}" target="_blank">{html.escape(t)}</a>
<span class="date">{html.escape(src)} {ds}</span></div>''')
        if dg and dg.get("sections"):
            parts.append('</details>')
    parts.append(f'<p class="meta">自動產生於 {now} · 資料來源：Google News RSS 等公開來源 · AI 整理僅供參考，引用請以原文為準</p></body></html>')
    return "\n".join(parts)


def render_article(topic, sections, rows):
    """把小節文字裡的 [[編號]] 轉成論文式引用上標，文末列參考來源。"""
    tkey = re.sub(r"[^a-zA-Z0-9]", "", topic)[:8] or "t"
    order, num_of = [], {}                   # order: 引用順序的 row idx；num_of: idx -> 引用序號

    def repl(m):
        i = int(m.group(1))
        if not (0 <= i < len(rows)):
            return ""
        if i not in num_of:
            order.append(i)
            num_of[i] = len(order)
        n = num_of[i]
        return f'<sup><a href="#ref-{tkey}-{n}">[{n}]</a></sup>'

    out = []
    for sec in sections:
        out.append(f'<h4>{html.escape(sec.get("heading", ""))}</h4>')
        body = html.escape(sec.get("body", ""))
        body = re.sub(r"\[\[(\d+)\]\]", repl, body)      # 先跑 repl 累積引用順序
        paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        out.append('<div class="body">' +
                   "".join(f"<p>{p}</p>" for p in paras) + "</div>")
    if order:
        out.append('<div class="refs"><strong>參考來源</strong><ol>')
        for n, i in enumerate(order, 1):
            it = rows[i]
            t, src = split_source(it["title"])
            ds = it["date"].strftime("%Y/%m/%d") if it["date"] else ""
            out.append(f'<li id="ref-{tkey}-{n}"><a href="{html.escape(it["link"])}" '
                       f'target="_blank">{html.escape(t)}</a> '
                       f'<span class="src">{html.escape(src)} {ds}</span></li>')
        out.append('</ol></div>')
    return "\n".join(out)


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
