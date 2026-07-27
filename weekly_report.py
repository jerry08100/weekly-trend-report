# -*- coding: utf-8 -*-
"""趨勢周報：抓 RSS -> 篩本週 -> 產 HTML 存檔。純 stdlib，免裝套件。
執行： C:\\Users\\8803\\AppData\\Local\\Programs\\Python\\Python312\\python.exe weekly_report.py
"""
import urllib.request, urllib.error, ssl, sys, os, html, re, urllib.parse, json, zlib
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
        "永續 顧問 公司 新服務",          # 同業動態
        "碳盤查 查證 機構 台灣",          # 同業動態
        "ESG 服務 平台 新產品 上線",      # 同業動態
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
    "永續 / 企業實務": "聚焦『台灣企業的執行面』與『同業動態』：ISO 14064-1／GHG 盤查、碳盤查與查證、"
                       "企業轉型實務。特別加重同業（其他永續顧問／碳盤查查證機構／ESG 服務業者）"
                       "推出的新服務、新產品、新合作——這是主管最關注的方向，若清單有相關項目請優先著墨。"
                       "少談國際宏觀情勢。",
    "AI / 企業應用": "聚焦『台灣企業的 AI 應用』：可用的新工具、企業導入 AI Agent 提升工作效率的趨勢與案例。"
                     "少談國際大廠模型軍備競賽等宏觀新聞。",
}
# KICKER：主題 -> 英文 mono 標籤（雙語層級，質感來源）。
KICKER = {
    "永續 / 企業實務": "SUSTAINABILITY · CORPORATE PRACTICE",
    "AI / 企業應用": "AI · ENTERPRISE ADOPTION",
}
# PEERS：觀察名單（主管很在意「同業有做」）。每主題會針對名單額外查詢並強制產「同業動態」節。
PEERS = ["USPACE", "台塑", "中油", "肯譯", "機場快線", "銀行業", "車商", "產險業"]
# 各主題給觀察名單加的查詢字尾（決定往哪個面向抓該對象的動態）。
PEER_SUFFIX = {
    "永續 / 企業實務": "永續 淨零 碳",
    "AI / 企業應用": "AI 數位轉型",
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
        peer_q = [f"{p} {PEER_SUFFIX.get(topic, '')}".strip() for p in PEERS]  # 觀察名單針對性查詢
        urls = ([gnews_url(q) for q in QUERIES.get(topic, [])]
                + [gnews_url(q) for q in peer_q]
                + FEEDS.get(topic, []))
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
        + "請用繁體中文輸出：\n"
        "1. gist：3~5 個關鍵詞（用「、」分隔），一眼概括本週該主題在講什麼，給側欄導覽用。"
        "例如「碳費新規、SBTi 2.0、淨零人才」。\n"
        "2. sections：分 3~4 個小節，各給有意義的 heading（例如「政策與法規」「產業趨勢」「企業實務」）。"
        f"其中『必須』有一節 heading 設為「同業動態」，專門講這份觀察名單做了什麼：{'、'.join(PEERS)}。"
        "有相關新聞就點名『哪個對象做了什麼具體動作』並附引用；名單中沒出現在本週新聞的對象就不用提，"
        "若整份都沒有名單相關動態，該節就據實寫「本週觀察名單無明顯相關動作」。這節是主管最看重的，寫具體。\n"
        "3. 每個小節 body 約 150~230 字連貫段落（不要條列），全篇合計約 600~900 字，把左欄寫得充實。\n"
        "4. 內容要有深度：不只描述新聞，還要補充相關『產業背景知識』（法規要點、標準內涵、盤查/查證常識），"
        "並著重解讀『同業動態』（其他永續顧問／碳查證機構／ESG 服務業者／AI 服務商在做什麼、代表什麼趨勢）——這是主管最看重的。\n"
        "5. 有分析、有觀點，串出脈絡，不要流水帳；讀者想深入會自己點連結。\n"
        "5b. 文字要白話、口語、好懂，像資深同事直接跟你講重點——不要文謅謅的書面腔、不要成語堆砌與冗長修飾。"
        "專有名詞（如 SBTi、AppSec）第一次出現用一句白話解釋它是什麼。句子盡量短、直接。\n"
        "6. 在提到具體事件/法規/數據處，於該句尾用 [[編號]] 標引用來源，可多個如 [[3]][[7]]；編號即下方新聞編號。\n"
        "7. 產業背景知識可用你的既有常識補充，但『具體事件、公司、數字』只能根據提供的標題，不得杜撰。"
        "若清單相關動態少，就把該主題的產業背景與同業趨勢講得更完整來補足篇幅。\n\n"
        "新聞清單：\n" + "\n".join(lines)
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "gist": {"type": "string"},
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
                "required": ["gist", "sections"],
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


# ── 三個純色主題（solid，不用漸層）。切換改 ACTIVE_THEME 或跑 --theme=NAME。──
THEMES = {
    "forest": {  # 森林綠 · 暖米白 · 銅金
        "paper": "#F4F2EA", "card": "#FBFAF4", "ink": "#1E2A24", "ink2": "#5E6B62",
        "line": "#DCE0D4", "line2": "#E9EBE1", "brand": "#2F5D4F",
        "accent": "#B0682B", "shadow": "34,59,51",
        "side_bg": "#223B33", "side_fg": "#D7E0D6", "side_fg2": "#A7B6AA",
        "side_line": "rgba(255,255,255,.14)", "side_active": "rgba(176,104,43,.20)",
        "side_date": "#EAD9C2", "side_grp": "#8FB09A", "side_brand": "#FFFFFF",
    },
    "navy": {  # 藏青 · 冷灰 · 琥珀（財經顧問感）
        "paper": "#F1F4F8", "card": "#FBFCFE", "ink": "#1B2432", "ink2": "#586477",
        "line": "#D5DCE6", "line2": "#E6EAF1", "brand": "#234E7D",
        "accent": "#C0892E", "shadow": "27,43,68",
        "side_bg": "#1B2B44", "side_fg": "#D3DCEA", "side_fg2": "#9FACC2",
        "side_line": "rgba(255,255,255,.14)", "side_active": "rgba(192,137,46,.22)",
        "side_date": "#ECDCBB", "side_grp": "#93A6C4", "side_brand": "#FFFFFF",
    },
    "graphite": {  # 石墨中性 · 暖灰 · 松綠（極簡單色）
        "paper": "#F3F3F0", "card": "#FBFBF9", "ink": "#23231F", "ink2": "#63635C",
        "line": "#DEDDD7", "line2": "#EAE9E3", "brand": "#3A3A34",
        "accent": "#1F7A6B", "shadow": "35,35,31",
        "side_bg": "#2A2A24", "side_fg": "#DAD9D2", "side_fg2": "#A6A59C",
        "side_line": "rgba(255,255,255,.13)", "side_active": "rgba(31,122,107,.24)",
        "side_date": "#CFE3DE", "side_grp": "#9E9D93", "side_brand": "#FFFFFF",
    },
}
ACTIVE_THEME = "navy"

STATIC_CSS = """
*{box-sizing:border-box}
body{font-family:"Segoe UI","Microsoft JhengHei","PingFang TC",sans-serif;margin:0;
  padding:0;color:var(--ink);background:var(--paper);line-height:1.75;font-size:18px;
  -webkit-font-smoothing:antialiased}
a{color:inherit}
.kicker{font-family:var(--mono);font-size:11.5px;letter-spacing:.22em;text-transform:uppercase;
  color:var(--accent);font-weight:600}
/* ── 版面骨架 ── */
.layout{display:flex;gap:0;align-items:stretch;min-height:100vh}
.side{flex:0 0 296px;position:sticky;top:0;align-self:flex-start;height:100vh;overflow:auto;
  background:var(--side-bg);color:var(--side-fg);padding:32px 26px}
.main{flex:1 1 auto;min-width:0;padding:44px clamp(30px,4vw,64px)}
.report{max-width:960px;margin:0 auto;padding:36px 28px}
@media(max-width:760px){.layout{flex-direction:column}.side{flex:none;width:100%;height:auto;
  position:static;padding:22px}.main{padding:28px 22px}}
/* ── 側欄 ── */
.side-title{margin-bottom:24px;padding-bottom:18px;border-bottom:1px solid var(--side-line)}
.side-title a{text-decoration:none;display:block}
.side-title .brand{font-size:22px;font-weight:800;color:var(--side-brand);letter-spacing:.02em;margin-top:7px}
.side .kicker{color:var(--side-grp)}
.day{font-family:var(--mono);font-size:14px;letter-spacing:.04em;color:var(--side-date);
  font-weight:700;margin:24px 0 8px;display:flex;align-items:center;gap:8px}
.day::before{content:"";width:7px;height:7px;background:var(--accent);border-radius:50%;flex:none}
.day.active{color:var(--accent)}
.wk{display:block;text-decoration:none;padding:8px 11px;margin:2px 0 2px 15px;border-radius:7px;
  border-left:2px solid var(--side-line);transition:background .15s}
.wk:hover{background:rgba(255,255,255,.06)}
.wk:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.wk-topic{display:block;font-size:14px;color:var(--side-fg);font-weight:600}
.wk-s{display:block;color:var(--side-fg2);font-size:12.5px;line-height:1.5;margin-top:2px}
/* ── 報頭 ── */
.masthead{margin-bottom:8px}
.masthead h1{font-size:clamp(32px,4vw,46px);font-weight:800;letter-spacing:-.01em;
  line-height:1.08;margin:8px 0 0}
.masthead .issue{font-family:var(--mono);font-size:13px;letter-spacing:.1em;color:var(--ink2);
  margin-top:12px}
.rule{height:2px;background:var(--ink);margin:22px 0 4px}
/* ── 主題區塊 ── */
.section{margin-top:56px}
.section h2{font-size:clamp(24px,2.4vw,32px);font-weight:800;letter-spacing:-.01em;
  margin:6px 0 0;line-height:1.15}
.section h2 .count{font-family:var(--mono);font-size:15px;font-weight:600;color:var(--ink2);
  letter-spacing:.02em;margin-left:10px}
.section .hairline{height:1px;background:var(--line);margin:14px 0 24px}
/* ── 兩欄：左情勢文、右參考來源 ── */
.cols{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:48px;align-items:start}
@media(max-width:1080px){.cols{grid-template-columns:1fr;gap:0}}
h4{font-size:22px;letter-spacing:0;color:var(--brand);margin:28px 0 12px;font-weight:800;
  line-height:1.3;display:flex;align-items:center;gap:9px}
h4::before{content:"";flex:none;width:5px;height:22px;background:var(--accent);border-radius:2px}
.col-body>h4:first-child{margin-top:0}
.body{font-size:19px;color:var(--ink);line-height:1.98;text-align:justify}
.body p{margin:0 0 16px}
sup{line-height:0}
sup a{font-family:var(--mono);font-size:11.5px;color:var(--accent);text-decoration:none;
  font-weight:700;padding:0 1px}
sup a:hover{text-decoration:underline}
/* ── 參考來源（右欄）── */
.col-refs{position:sticky;top:24px}
@media(max-width:1080px){.col-refs{position:static;margin-top:28px}}
.refs .kicker{display:block;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.refs ol{list-style:none;counter-reset:r;padding:0;margin:0}
.refs li{counter-increment:r;position:relative;padding:10px 0 10px 38px;font-size:14.5px;
  color:var(--ink2);border-bottom:1px solid var(--line2);line-height:1.5}
.refs li::before{content:counter(r);position:absolute;left:0;top:10px;width:24px;text-align:center;
  font-family:var(--mono);font-size:12px;font-weight:700;color:var(--accent)}
.refs a{color:var(--ink);text-decoration:none;font-weight:600}
.refs a:hover{color:var(--brand);text-decoration:underline}
.refs .src{display:block;color:var(--ink2);font-weight:400;font-family:var(--mono);font-size:11.5px;margin-top:2px}
/* ── 純清單（無 AI 時）── */
.item{padding:10px 0;font-size:16px;border-bottom:1px solid var(--line2)}
.item a{color:var(--ink);text-decoration:none;font-weight:600}
.item a:hover{color:var(--brand)}
.item .date{color:var(--ink2);font-family:var(--mono);font-size:12px;margin-left:8px}
.note{color:var(--ink2);font-size:16px}
/* ── 資料來源面板 ── */
.srcpanel{position:fixed;top:18px;right:18px;z-index:30;font-size:14px}
.srcpanel>summary{list-style:none;cursor:pointer;background:var(--card);color:var(--brand);
  border:1px solid var(--line);padding:9px 16px;border-radius:999px;font-weight:600;
  box-shadow:0 6px 18px rgba(var(--shadow),.12);display:flex;align-items:center;gap:7px}
.srcpanel>summary::-webkit-details-marker{display:none}
.srcpanel>summary::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent)}
.srcpanel[open]>summary{color:var(--ink)}
.srcpanel>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sp-body{position:absolute;right:0;margin-top:10px;width:min(400px,88vw);max-height:74vh;
  overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;
  box-shadow:0 18px 44px rgba(var(--shadow),.16);padding:18px}
.sp-topic{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--brand);font-weight:700;margin:16px 0 6px}
.sp-topic:first-child{margin-top:0}
.s-row{margin:5px 0 0;color:var(--ink2);line-height:1.7;font-size:13.5px}
.s-row b{color:var(--ink);font-weight:600}
.s-row a{color:var(--brand);text-decoration:none;word-break:break-all}
.s-row a:hover{text-decoration:underline}
/* ── 頁尾 ── */
.meta{color:var(--ink2);font-size:12.5px;margin-top:48px;padding-top:16px;
  border-top:1px solid var(--line);font-family:var(--mono);letter-spacing:.02em;line-height:1.7}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def build_css(theme_name=None):
    t = THEMES.get(theme_name or ACTIVE_THEME, THEMES["forest"])
    root = (":root{"
            f'--paper:{t["paper"]};--card:{t["card"]};--ink:{t["ink"]};--ink2:{t["ink2"]};'
            f'--line:{t["line"]};--line2:{t["line2"]};--brand:{t["brand"]};--accent:{t["accent"]};'
            f'--shadow:{t["shadow"]};--side-bg:{t["side_bg"]};--side-fg:{t["side_fg"]};'
            f'--side-fg2:{t["side_fg2"]};--side-line:{t["side_line"]};--side-active:{t["side_active"]};'
            f'--side-date:{t["side_date"]};--side-grp:{t["side_grp"]};--side-brand:{t["side_brand"]};'
            '--mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;}')
    return root + STATIC_CSS


def page(title, inner, theme=None):
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<title>{html.escape(title)}</title><style>{build_css(theme)}</style></head><body>\n'
            f'{inner}\n</body></html>')


def build_report(data):
    """跑 AI 摘要，組出結構化 report dict（同時給 HTML/JSON/未來 RAG 用）。"""
    topics = []
    for topic, rows in data.items():
        items = []
        for it in rows:
            t, src = split_source(it["title"])
            items.append({
                "title": t, "source": src, "link": it["link"],
                "date": it["date"].strftime("%Y-%m-%d") if it["date"] else "",
            })
        dg = gemini_digest(topic, rows)
        secs = (dg.get("sections") if dg else []) or []
        for s in secs:                                   # 存乾淨版（去 AI 尾端雜字）
            s["body"] = clean_body(s.get("body", ""))
        topics.append({
            "topic": topic,
            "gist": (dg.get("gist") if dg else "") or "",
            "sections": secs,
            "items": items,
            "sources": {"queries": QUERIES.get(topic, []), "feeds": FEEDS.get(topic, [])},
        })
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": sum(len(v) for v in data.values()),
        "topics": topics,
    }


def tkey(topic):
    """主題 -> 穩定的 HTML 錨點 id（crc32 跨進程穩定，避免中文）。"""
    latin = re.sub(r"[^a-zA-Z0-9]", "", topic)[:8]
    return latin or ("t" + str(zlib.crc32(topic.encode("utf-8"))))


def snippet(tp, limit=48):
    """側欄導覽用：優先 AI 給的關鍵詞 gist，否則退回情勢文首段/首則標題。"""
    if tp.get("gist"):
        return tp["gist"].strip()
    if tp.get("sections"):
        txt = re.sub(r"\[\[\d+\]\]", "", tp["sections"][0].get("body", ""))
    elif tp.get("items"):
        txt = tp["items"][0]["title"]
    else:
        txt = ""
    txt = re.sub(r"\s+", "", txt).strip()
    return (txt[:limit] + "…") if len(txt) > limit else txt


def clean_body(text):
    """去掉 AI 偶爾多吐的尾端雜字（如 Check. / ``` / 說明性收尾）。"""
    t = (text or "").strip()
    t = re.sub(r"\s*(check\.?|done\.?|完成。?|```+)\s*$", "", t, flags=re.IGNORECASE)
    return t.strip()


def render_article(topic, sections, items):
    """摘要內文的 [[編號]] 轉引用上標；參考來源列出本週『全部』項目（編號 = 項目序號）。
    AI 是讀全部新聞寫的，故全部即參考來源，不再另分精選/全展開。"""
    tk = tkey(topic)

    def repl(m):
        i = int(m.group(1))
        if not (0 <= i < len(items)):
            return ""
        n = i + 1                                    # 編號 = 項目序號（對齊下方清單）
        return f'<sup><a href="#ref-{tk}-{n}">[{n}]</a></sup>'

    body_parts = []                                  # 左欄：情勢文
    for sec in sections:
        body_parts.append(f'<h4>{html.escape(sec.get("heading", ""))}</h4>')
        body = re.sub(r"\[\[(\d+)\]\]", repl, html.escape(clean_body(sec.get("body", ""))))
        paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        body_parts.append('<div class="body">' + "".join(f"<p>{p}</p>" for p in paras) + "</div>")
    refs = ['<div class="refs"><span class="kicker">References · 參考來源'
            f'（{len(items)} 則）</span><ol>']            # 右欄：全部來源
    for n, it in enumerate(items, 1):
        refs.append(f'<li id="ref-{tk}-{n}"><a href="{html.escape(it["link"])}" '
                    f'target="_blank">{html.escape(it["title"])}</a>'
                    f'<span class="src">{html.escape(it["source"])} {it["date"]}</span></li>')
    refs.append('</ol></div>')
    return (f'<div class="cols"><div class="col-body">{"".join(body_parts)}</div>'
            f'<aside class="col-refs">{"".join(refs)}</aside></div>')


def render_sources_panel(report):
    """右上角單一按鈕：展開看『所有主題』抓了哪些 Google News 關鍵字與直連 RSS。"""
    blocks = []
    for tp in report["topics"]:
        src = tp.get("sources") or {"queries": QUERIES.get(tp["topic"], []),
                                    "feeds": FEEDS.get(tp["topic"], [])}
        queries, feeds = src.get("queries", []), src.get("feeds", [])
        if not queries and not feeds:
            continue
        rows = [f'<div class="sp-topic">{html.escape(tp["topic"])}</div>']
        if queries:
            kws = "、".join(html.escape(q) for q in queries)
            rows.append(f'<div class="s-row"><b>Google News 關鍵字</b>：{kws}</div>')
        if feeds:
            links = "、".join(f'<a href="{html.escape(u)}" target="_blank">{html.escape(u)}</a>'
                              for u in feeds)
            rows.append(f'<div class="s-row"><b>直連 RSS</b>：{links}</div>')
        blocks.append("".join(rows))
    if not blocks:
        return ""
    return ('<details class="srcpanel"><summary>資料來源</summary>'
            '<div class="sp-body">' + "".join(blocks) + '</div></details>')


def render_report_body(report):
    """單份周報的內文（不含 <html> 外殼），供存檔頁、首頁、email 共用。"""
    parts = ['<header class="masthead">'
             '<div class="kicker">Weekly Intelligence Briefing</div>'
             '<h1>趨勢周報</h1>'
             f'<div class="issue">ISSUE {report["date"]} · 產業情勢週報</div>'
             '</header><div class="rule"></div>']
    for tp in report["topics"]:
        items = tp["items"]
        tk = tkey(tp["topic"])
        kick = KICKER.get(tp["topic"], "")
        parts.append(f'<section class="section" id="{tk}">')
        if kick:
            parts.append(f'<div class="kicker">{html.escape(kick)}</div>')
        parts.append(f'<h2>{html.escape(tp["topic"])}'
                     f'<span class="count">{len(items)} 則</span></h2>'
                     '<div class="hairline"></div>')
        if not items:
            parts.append('<p class="note">本週無相關動態。</p></section>')
            continue
        if tp["sections"]:                           # 有 AI：摘要 + 全部項目當參考來源
            parts.append(render_article(tp["topic"], tp["sections"], items))
        else:                                        # 無 AI：直接列全部
            for it in items:
                parts.append(f'''<div class="item">
<a href="{html.escape(it["link"])}" target="_blank">{html.escape(it["title"])}</a>
<span class="date">{html.escape(it["source"])} {it["date"]}</span></div>''')
        parts.append('</section>')
    parts.append(f'<p class="meta">自動產生於 {report["generated_at"]}　·　'
                 '資料來源 Google News RSS 等公開來源　·　AI 整理僅供參考，引用請以原文為準</p>')
    return "\n".join(parts)


def load_all_reports(site_dir):
    """讀 docs/reports/*.json，回傳依日期新到舊的 report list。"""
    rep_dir = os.path.join(site_dir, "reports")
    dates = sorted(
        (f[:-5] for f in os.listdir(rep_dir) if f.endswith(".json")),
        reverse=True) if os.path.isdir(rep_dir) else []
    out = []
    for d in dates:
        try:
            with open(os.path.join(rep_dir, f"{d}.json"), encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            pass
    return out


def build_sidebar(reports, order, base, active_date=None):
    """常駐左側欄：日期第一層、主題(AI/永續)第二層。base 控制連結相對路徑
    （首頁在 root 用 'reports/'，週頁在 reports/ 內用 ''）。"""
    home = "index.html" if base == "reports/" else "../index.html"  # 首頁在 root、週頁在 reports/
    side = ['<aside class="side"><div class="side-title">'
            f'<a href="{home}"><span class="kicker">Archive · 歷史</span>'
            '<span class="brand">趨勢周報</span></a></div>']
    rank = {t: i for i, t in enumerate(order)}
    for rep in reports:                              # 已依日期新到舊
        d = rep["date"]
        side.append(f'<div class="day{" active" if d == active_date else ""}">{d}</div>')
        for tp in sorted(rep["topics"], key=lambda x: rank.get(x["topic"], 99)):
            tk = tkey(tp["topic"])
            side.append(
                f'<a class="wk" href="{base}{d}.html#{tk}">'
                f'<span class="wk-topic">{html.escape(tp["topic"])}</span>'
                f'<span class="wk-s">{html.escape(snippet(tp))}</span></a>')
    side.append("</aside>")
    return "".join(side)


def compose_page(report, sidebar_html, theme=None):
    """側欄 + 右上角資料來源按鈕 + 該份周報全文，組成完整頁。"""
    inner = (f'{render_sources_panel(report)}\n<div class="layout">\n{sidebar_html}\n'
             f'<main class="main">{render_report_body(report)}</main>\n</div>')
    return page(f'趨勢周報 {report["date"]}', inner, theme)


def send_mail(html_body, report):
    """有設 env 就寄；沒設就跳過。Gmail SMTP -> 收件者(可 Outlook)。"""
    import smtplib
    from email.message import EmailMessage
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PW")
    to = os.environ.get("MAIL_TO")
    if not (user and pw and to):
        print("[mail] 未設 GMAIL_USER/GMAIL_APP_PW/MAIL_TO，跳過寄信")
        return
    site = os.environ.get("SITE_URL")        # 設了就在信末附網頁連結
    body = html_body
    if site:
        body = body.replace("</body>",
                            f'<p class="meta">線上看：<a href="{html.escape(site)}">{html.escape(site)}</a></p></body>')
    msg = EmailMessage()
    msg["Subject"] = f"趨勢周報 {report['date']}（{report['total']} 則）"
    msg["From"] = user
    msg["To"] = to
    msg.set_content("此信為 HTML 格式，請用支援 HTML 的信箱檢視。")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] 已寄至 {to}")


def rebuild_site(site):
    """用現成的 reports/*.json 重畫整個網站（首頁 + 每週存檔頁）。不抓新聞、不呼叫 AI。"""
    reports = load_all_reports(site)
    if not reports:
        print("[render] 沒有任何 reports/*.json，先正常跑一次產資料")
        return
    order = [tp["topic"] for tp in reports[0]["topics"]]      # 主題順序依最新一份
    reps = os.path.join(site, "reports")
    for rep in reports:                                      # 每週存檔頁（同目錄相對連結）
        side = build_sidebar(reports, order, base="", active_date=rep["date"])
        with open(os.path.join(reps, f'{rep["date"]}.html'), "w", encoding="utf-8") as f:
            f.write(compose_page(rep, side))
    idx_side = build_sidebar(reports, order, base="reports/", active_date=reports[0]["date"])
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(compose_page(reports[0], idx_side))
    print(f"[render] 重畫 {len(reports)} 週 -> {site}")


def main():
    site = os.path.join(OUT_DIR, "docs")
    os.makedirs(os.path.join(site, "reports"), exist_ok=True)

    # --render-only：只用舊 JSON 重畫（改樣式/版面用，秒出、零 API）
    if "--render-only" in sys.argv:
        rebuild_site(site)
        return site

    data, log = collect()
    print("\n".join(log))
    report = build_report(data)
    d = report["date"]
    with open(os.path.join(site, "reports", f"{d}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)   # 結構化資料（未來 AI RAG 讀這個）

    rebuild_site(site)                                        # 重畫全站
    print(f"共 {report['total']} 則")

    mail_html = page(f"趨勢周報 {d}", f'<div class="report">{render_report_body(report)}</div>')
    send_mail(mail_html, report)                             # 寄無側欄窄欄版
    return site


if __name__ == "__main__":
    main()
