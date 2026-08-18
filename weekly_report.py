# -*- coding: utf-8 -*-
"""趨勢周報：抓 RSS -> 篩本週 -> 產 HTML 存檔。純 stdlib，免裝套件。
執行： C:\\Users\\8803\\AppData\\Local\\Programs\\Python\\Python312\\python.exe weekly_report.py
"""
import urllib.request, urllib.error, ssl, sys, os, html, re, urllib.parse, json, zlib, time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
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
    "政府補助 / 計畫": [
        "企業 AI 轉型 補助 計畫 申請",
        "中小企業 數位轉型 補助 申請 期限",
        "淨零 永續 轉型 補助 企業 計畫",
        "研發 補助 SBIR 企業 受理 申請",
        "產業 升級 輔導 補助 開放申請 金額",
    ],
    "企業獎項 / 競賽": [
        "企業 永續獎 ESG獎 報名 徵件",
        "AI 獎 企業 報名 徵選",
        "數位轉型 獎 企業 報名",
        "永續 獎項 徵件 開始 截止",
    ],
}
# OFFICIAL_HTML：政府補助主題的官方入口（非新聞）。(清單頁, 網域, 內文連結樣式)。
# 註：.gov.tw 站在 GitHub Actions(美國) 可能被擋，抓不到會自動略過、退回新聞查詢。
OFFICIAL_HTML = {
    "政府補助 / 計畫": [
        ("https://www.sbir.org.tw/news", "https://www.sbir.org.tw", r"/news/main_content\?id=\d+"),
    ],
}
# FEEDS：主題 -> 直連 RSS。真實媒體來源，比 Google News 多兩樣東西：完整摘要、真原文網址
# （Google News 只給標題 + 跳轉連結）。2026-07-30 實測活著的；工商時報/數位時代/環境資訊中心/
# CSRone/天下CSR/經濟部/國發會 皆 403 或 404，已排除。
FEEDS = {
    "永續 / 企業實務": [
        "https://esg.gvm.com.tw/rss",                            # ESG遠見（免費內容站）
        "https://feeds.feedburner.com/rsscna/finance",           # 中央社 財經（免費）
    ],
    "AI / 企業應用": [
        "https://www.ithome.com.tw/rss",                         # iThome（免費）
        "https://technews.tw/feed/",                             # 科技新報（免費）
        "https://www.inside.com.tw/feed/rss",                    # Inside（免費）
        "https://feeds.feedburner.com/rsscna/technology",        # 中央社 科技（免費）
    ],
    "政府補助 / 計畫": [],
    "企業獎項 / 競賽": [],
}
# PAYWALL：付費牆媒體。這些來源讀者點進去看不到全文，一律不當來源（Google News 依標題尾
# 「- 媒體」名比對剔除）。要增減直接改。
PAYWALL = ("工商時報", "經濟日報", "天下雜誌", "天下", "遠見雜誌", "商業周刊", "商周",
           "今周刊", "財訊", "鏡週刊", "彭博", "Bloomberg", "華爾街日報", "WSJ",
           "日經", "Nikkei", "金融時報", "哈佛商業評論", "MoneyDJ")
# 所有來源（含 Google News 查詢結果）一律用主題關鍵字濾。原因實測：綜合型 RSS 會灌股市/EPS、
# Google News 查詢會飄（「AI 工具 企業」撈回美股特報）、連 ESG遠見 這種主題站也發西班牙野火、
# 東京短褲之類與企業實務無關的稿。寧可少而準。
FEED_KEYWORDS = {
    "永續 / 企業實務": ["永續", "ESG", "淨零", "減碳", "碳排", "碳費", "碳權", "碳盤查", "碳足跡",
                        "溫室氣體", "盤查", "查證", "綠電", "再生能源", "循環經濟", "氣候變遷",
                        "CBAM", "SBTi", "TCFD", "IFRS S1", "IFRS S2"],
    "AI / 企業應用": ["AI", "人工智慧", "生成式", "大型語言模型", "LLM", "Agent", "智慧製造",
                      "數位轉型", "自動化", "機器學習", "Copilot", "ChatGPT", "Gemini", "算力"],
    "政府補助 / 計畫": ["補助", "獎補助", "計畫申請", "受理申請", "開放申請", "申請期限",
                        "輔導計畫", "徵求提案", "SBIR", "TIIP", "轉型基金", "低利貸款"],
    "企業獎項 / 競賽": ["徵件", "報名", "獎項", "評選", "選拔", "表揚"],
}
# FOCUS：主題 -> 給 AI 的聚焦提示，決定摘要口味。
FOCUS = {
    "永續 / 企業實務": "聚焦『台灣企業的執行面』與『同業動態』：ISO 14064-1／GHG 盤查、碳盤查與查證、"
                       "企業轉型實務。特別加重同業（其他永續顧問／碳盤查查證機構／ESG 服務業者）"
                       "推出的新服務、新產品、新合作——這是主管最關注的方向，若清單有相關項目請優先著墨。"
                       "少談國際宏觀情勢。",
    "AI / 企業應用": "聚焦『台灣企業的 AI 應用』：可用的新工具、企業導入 AI Agent 提升工作效率的趨勢與案例。"
                     "少談國際大廠模型軍備競賽等宏觀新聞。",
    "政府補助 / 計畫": "聚焦『企業可申請、與 AI／數位轉型、永續淨零轉型、研發或產業升級相關』的政府補助與計畫。"
                       "每個點出——哪個部會、計畫名稱、補助對象、補助金額、申請期限。"
                       "排除國旅、農業、家戶節電、個人消費性等與企業轉型無關的補助。",
    "企業獎項 / 競賽": "聚焦『企業可報名爭取的獎項／競賽』，主題為永續／ESG、AI、數位轉型。"
                       "每個點出——主辦單位、獎項名稱、報名對象、獎勵或獎項類別、報名截止日。"
                       "只列企業能報名的，排除個人獎、學生競賽、消費性抽獎。",
}
# KICKER：主題 -> 英文 mono 標籤（雙語層級，質感來源）。
KICKER = {
    "永續 / 企業實務": "SUSTAINABILITY · CORPORATE PRACTICE",
    "AI / 企業應用": "AI · ENTERPRISE ADOPTION",
    "政府補助 / 計畫": "GOVERNMENT GRANTS · FUNDING PROGRAMS",
    "企業獎項 / 競賽": "AWARDS · COMPETITIONS",
}
# COMPETITORS：同業／競爭對手名單 -> 內文「同業動態」那節只寫這些。
# CLIENTS：甲方客戶名單 -> 內文「客戶動態」那節只寫這些。
# 兩份都會拿去查新聞、都會標紅。要增減直接改名單，程式自動查詢與標記。
# 分法經使用者確認（2026-07-30）：機場接送／租車／車隊／停車服務＝同業，
# 台塑／中油／金融業＝甲方客戶。
# 2026-08-14 使用者校正：按全鋒三條業務線歸位（下方註解只是分類說明，程式吃的是同一份平的
# list——每個名字各產一組搜尋 query，不需要分組資料結構）。
#   機場接送：USPACE（旗下 UGO 有機場接送）、機場快線、大都會車隊、台灣大車隊、
#             順鋒機場接送、Uber、肯譯
#   道路救援：行遍天下          ← 本次補上，先前整條業務線都沒有同業，周報永遠報不到
#   車輛租賃：漢聲租車
# 移除「車商」：不是公司名，查詢會撈回一堆賣車新聞；原本就已被 WATCH_HL 排除標紅。
COMPETITORS = ["USPACE", "機場快線", "大都會車隊", "台灣大車隊", "順鋒機場接送",
               "漢聲租車", "Uber", "肯譯", "行遍天下"]
# 客戶端要的是「整個金融業」的 AI／永續動態（所有金控，不只富邦），用業別詞當查詢撈全體，
# 具體公司名靠 INDUSTRY_TAIL 標紅（富邦金控、國泰金控、台新金控…都自動命中）。
CLIENTS = ["台塑", "中油", "金控", "銀行", "產險"]
CLIENT_DESC = ("台塑、中油，以及『整個金融業』——所有金控／銀行／產險業者"
               "（不限名單，點名具體公司，如富邦金控、國泰金控、台新金控）")
# ALIASES：同一家公司的其他寫法也要標紅。AI 有時會擴寫成全名（富邦產險 -> 富邦產物保險
# 股份有限公司），沒列進來就標不到。
ALIASES = {
    "台塑": ["台灣塑膠", "台塑集團"],
    "中油": ["台灣中油", "中國石油"],
    "Uber": ["優步"],
    "USPACE": ["優勢泊車"],
}
# WATCH_HL：內文/來源清單要標紅的對象 = 同業 + 客戶 + 別名。過短或太通用的詞不標
# （「車商」「銀行業」「產險業」會誤標到無關句子），只標具體公司名。
WATCH_HL = ([w for w in COMPETITORS + CLIENTS if len(w) >= 3 and w not in ("銀行業", "車商", "產險業")]
            + [a for w in COMPETITORS + CLIENTS for a in ALIASES.get(w, [])])
# 同類業者也標紅：名單只列得出已知對象，但新聞常出現名單外的同業／甲方（台新金控、裕隆、
# 新光產險…）。用「公司名長相」補抓——2~6 個中文字接上業別字尾。「銀行業」「產險業」這種
# 沒有公司名前綴的通稱不會命中，不會誤標。
INDUSTRY_TAIL = ("產物保險", "產險", "金控", "金融控股", "銀行", "車隊", "租車",
                 "機場接送", "停車", "客運", "運輸", "汽車")
# 「XX汽車」樣式會誤中「電動汽車」「自駕汽車」這類技術詞——前綴是這些通用詞就不標。
_GENERIC_PREFIX = {"電動", "自駕", "無人", "新能源", "燃油", "油電", "二手", "進口",
                   "國產", "網路", "數位", "傳統", "智慧", "共享"}
_WATCH_RE = re.compile(
    "|".join([re.escape(w) for w in sorted(WATCH_HL, key=len, reverse=True)]
             + [rf"[一-龥]{{2,6}}(?:{'|'.join(INDUSTRY_TAIL)})"]),
    re.IGNORECASE)


_WATCH_SET = set(WATCH_HL)
_PARTICLES = set("的了與和及並而從在對向由為跟讓也就仍已如據按含")


def _watch_repl(m):
    """業別字尾比對是貪婪的，會把前面的虛詞吃進來（「與新光金控」「從澳洲聯邦銀行」）。
    名單精確命中不動；字尾命中的把開頭虛詞剝到 <mark> 外，公司名前綴至少保留 2 字
    （保護「和泰產險」這類以虛詞字開頭的真公司名）。"""
    t = m.group(0)
    if t in _WATCH_SET:
        return f'<mark class="watch">{t}</mark>'
    tail = next((x for x in sorted(INDUSTRY_TAIL, key=len, reverse=True)
                 if t.endswith(x)), "")
    name = t[:len(t) - len(tail)]
    lead = ""
    while len(name) > 2 and name[0] in _PARTICLES:
        lead += name[0]
        name = name[1:]
    if name in _GENERIC_PREFIX:                          # 「電動汽車」是技術詞不是公司
        return t
    return f'{lead}<mark class="watch">{name}{tail}</mark>'


def mark_watch(frag):
    """把同業／客戶（含同類業者）標紅。作用在『已 escape 且已轉好連結』的 HTML 片段上；
    比對的都是公司名，不會出現在 href 屬性裡（錨點是 #ref-xxx-N），不會咬壞標籤。"""
    return _WATCH_RE.sub(_watch_repl, frag)
# 各主題給觀察名單加的查詢字尾（決定往哪個面向抓該對象的動態）。
WATCH_SUFFIX = {
    "永續 / 企業實務": "永續 淨零 碳",
    "AI / 企業應用": "AI 數位轉型",
}
# GRANT_TOPICS：這些主題產「條列卡片」（不寫長文）。含政府補助與企業獎項。
GRANT_TOPICS = {"政府補助 / 計畫", "企業獎項 / 競賽"}
AWARD_TOPICS = {"企業獎項 / 競賽"}       # 卡片語意換成獎項（主辦/獎項/對象/獎勵/報名截止）
# STANDING：主題 -> 固定卡片清單（已查證，永遠顯示、不靠 AI）。要增減改這裡。
STANDING = {
    "政府補助 / 計畫": [
        # 只放『現正開放、隨時可報名』的常態計畫；限時梯次(CITD/以大帶小)會過期，走「本週相關動態」。
        {"agency": "數位發展部 數位產業署", "program": "臺灣雲市集 TCloud 數位轉型點數",
         "target": "中小微企業（採購雲端／數位工具）", "amount": "補助點數 3 萬元，抵最高 50% 費用",
         "status": "常態", "url": "https://www.tcloud.gov.tw/"},
        {"agency": "經濟部 中小及新創企業署", "program": "SBIR 小型企業創新研發計畫",
         "target": "中小企業、新創（創新研發）", "amount": "依計畫類型，個案／跨域補助不等",
         "status": "常態（隨到隨受理）", "url": "https://sbir.org.tw/"},
        {"agency": "經濟部 產業發展署", "program": "產業升級創新平台輔導計畫（TIIP）",
         "target": "企業／產業聯盟（前瞻技術研發）", "amount": "依計畫審定",
         "status": "常態（至經費用罄）", "url": "https://eii.nat.gov.tw/tiip/"},
    ],
    "企業獎項 / 競賽": [
        # 年度徵件的企業獎項（永續／AI／數位轉型）。徵件期間見各官網。
        {"agency": "台灣永續能源研究基金會 TAISE", "program": "TCSA 台灣企業永續獎（含 AI 賦能永續獎）",
         "target": "台灣企業、政府機關", "amount": "ESG 綜合／單項／報告書／傑出人士",
         "fee": "單項每項約 NT$12,600", "status": "每年約 11～1 月徵件（下屆見官網）",
         "url": "https://tcsaward.org.tw/"},
        {"agency": "遠見雜誌", "program": "遠見 ESG 企業永續獎",
         "target": "企業（ESG 永續）", "amount": "榮譽獎項",
         "fee": "每組約 NT$15,750", "status": "每年約上半年徵件（下屆見官網）",
         "url": "https://event.gvm.com.tw/esg/"},
        {"agency": "天下雜誌", "program": "天下永續公民獎（ESG）",
         "target": "企業（ESG 永續）", "amount": "榮譽獎項",
         "fee": "需報名費（見簡章）", "status": "每年約 5～7 月徵件（下屆見官網）",
         "url": "https://csr.cw.com.tw/esgaward/"},
        {"agency": "哈佛商業評論 台灣", "program": "數位轉型鼎革獎",
         "target": "企業、醫療機構（數位轉型＋永續）", "amount": "榮譽獎項",
         "fee": "見官網簡章", "status": "每年約 3～6 月徵件（下屆見官網）",
         "url": "https://event.hbrtaiwan.com/hbrdx/"},
        {"agency": "國家發展委員會", "program": "國家永續發展獎",
         "target": "企業、機關、團體、學校", "amount": "國家級榮譽",
         "fee": "免費（政府主辦）", "status": "依年度公告徵件（見官網）",
         "url": "https://ncsdaward.ndc.gov.tw/"},
    ],
}
# TAB_LABEL：頂部分頁按鈕的短標籤。
TAB_LABEL = {
    "永續 / 企業實務": "永續",
    "AI / 企業應用": "AI",
    "政府補助 / 計畫": "補助",
    "企業獎項 / 競賽": "獎項",
}

DAYS = 7                       # 抓幾天內
CAP = 30                       # 每主題最多留幾則
SIM = 0.72                     # 標題相似度 >= 此值視為同事件（跨媒體合併）
DIRECT_QUOTA = 12              # CAP 內保留給直連媒體的名額（它們才有摘要/正文，別被熱度擠掉）
BODY_MAX = 6                   # 每主題最多抓幾篇原文正文餵 AI（只抓直連 RSS 的真實網址）
BODY_CHARS = 700               # 每篇正文取前幾字
MAIL_FROM_HOUR = 7             # 幾點後才准寄信（週一有多班接力，太早的那班先讓過，別在清晨吵人）
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (weekly-report-bot)"
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE   # ponytail: 關 SSL 驗證圖方便；正式對外服務別這樣


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
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
    # when:{DAYS}d 是關鍵：不加的話 Google News 會塞回大量幾個月前的舊聞
    # （實測窄查詢回 75~100 則裡本週內只有 0~1 則），本週新聞被舊聞擠光。
    qs = urllib.parse.quote(f"{q} when:{DAYS}d")
    return f"https://news.google.com/rss/search?q={qs}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def norm(t):
    return re.sub(r"\s+", "", (t or "")).lower()[:40]   # 去空白+截頭當去重鍵


def is_paywall(title):
    """Google News 標題尾「- 媒體」若是付費牆媒體，回 True（不當來源）。"""
    _, src = split_source(title)
    if not src:
        return False
    return any(p.lower() in src.lower() for p in PAYWALL)


_KW_CACHE = {}


def kw_hit(topic, text):
    """綜合型 RSS 的主題關鍵字過濾。英數關鍵字要求詞界（避免 TAIWAN 命中 AI）。"""
    kws = FEED_KEYWORDS.get(topic)
    if not kws:
        return True
    rx = _KW_CACHE.get(topic)
    if rx is None:
        parts = [(rf"\b{re.escape(k)}\b" if k.isascii() else re.escape(k)) for k in kws]
        rx = _KW_CACHE[topic] = re.compile("|".join(parts), re.IGNORECASE)
    return bool(rx.search(text or ""))


def dkey(title):
    """去重比對用鍵：拆掉「- 媒體」尾巴、去標點空白。"""
    t, _ = split_source(title)
    return re.sub(r"[\s\W_]+", "", t).lower()


def absorb(prev, it):
    """把重複的那則併進既有那則：報導家數 +1、留較長摘要、優先留真實媒體網址。
    同一篇文章常有 Google News 版（只有標題）與直連版（有摘要／可抓正文），要留直連版那份資料。"""
    prev["dupes"] = prev.get("dupes", 1) + 1
    if len(it.get("summary") or "") > len(prev.get("summary") or ""):
        prev["summary"] = it["summary"]
    if it.get("direct") and not prev.get("direct"):
        prev["link"], prev["direct"] = it["link"], True


def merge_same_event(rows):
    """同事件跨媒體合併：標題相似度 >= SIM 視為同一則，留摘要最長者並記報導家數。
    家數本身是熱度信號，會餵給 AI。
    ponytail: O(n²) 比對，n 約 200 上限、字串 40 字內，跑一次不到 0.2 秒；n 破千再換 minhash。"""
    kept = []
    for it in rows:
        k1 = dkey(it["title"])
        for k in kept:
            if SequenceMatcher(None, k1, k["_dk"]).ratio() >= SIM:
                absorb(k, it)
                break
        else:
            it["_dk"], it["dupes"] = k1, it.get("dupes", 1)
            kept.append(it)
    for k in kept:
        k.pop("_dk", None)
    return kept


def useful_summary(title, summary, limit=280):
    """RSS 摘要有時只是標題複述，那種丟掉，省 prompt 也避免誤導。"""
    s = re.sub(r"\s+", " ", summary or "").strip()
    if len(s) < 30:
        return ""
    if SequenceMatcher(None, dkey(s[:40]), dkey(title or "")[:40]).ratio() >= 0.85:
        return ""
    return s[:limit]


def fetch_body(url):
    """抓原文正文前幾段。通用做法：砍掉 script/nav 等，取夠長的 <p> 串起來。失敗回空字串。"""
    try:
        raw = fetch(url, timeout=10)
    except Exception:
        return ""
    txt = raw.decode("utf-8", "ignore")
    txt = re.sub(r"(?is)<(script|style|nav|header|footer|aside|figure)[^>]*>.*?</\1>", " ", txt)
    paras = [strip_html(p, 400) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", txt)]
    paras = [p for p in paras if len(p) >= 40]          # 濾掉版權、標籤等短句
    return " ".join(paras)[:BODY_CHARS]


def scrape_official(url, base, pat):
    """從官方補助入口的清單頁抓內文連結（title, link），當作 items 餵給 AI。"""
    noise = ("說明會", "推廣", "核定名單", "得獎", "名單", "工作坊", "座談",
             "研討", "花絮", "公布", "成果", "頒獎", "課程", "研習")
    txt = fetch(url).decode("utf-8", "ignore")
    out, seen = [], set()
    for a, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', txt, re.S):
        if not re.search(pat, a):
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t))).strip()
        if len(title) < 6 or any(k in title for k in noise):   # 濾掉活動/名單類雜訊
            continue
        link = a if a.startswith("http") else base + a
        if link in seen:
            continue
        seen.add(link)
        out.append({"title": title, "link": link, "date": None, "summary": ""})
    return out[:25]


def collect():
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    result, log = {}, []
    topics = list(QUERIES) + [t for t in FEEDS if t not in QUERIES]  # 保持定義順序(永續→AI→補助)
    for topic in topics:
        watch_q = ([f"{p} {WATCH_SUFFIX[topic]}".strip() for p in COMPETITORS + CLIENTS]
                  if topic in WATCH_SUFFIX else [])      # 只有 WATCH_SUFFIX 主題才查同業＋客戶名單
        urls = ([gnews_url(q) for q in QUERIES.get(topic, [])]
                + [gnews_url(q) for q in watch_q]
                + FEEDS.get(topic, []))
        feedset = set(FEEDS.get(topic, []))             # 直連 RSS：真實網址、摘要有料
        rows, seen = [], {}                             # seen: 去重鍵 -> 已收的那則（碰撞時併進去）
        for u in urls:
            label = u if u.startswith("http") else u
            direct = u in feedset                       # 直連 RSS：有摘要、真原文網址（可抓正文）
            try:
                items = parse_feed(fetch(u))
                if not items and direct:                # 直連站偶發回空 feed（實測 ESG遠見中過一次）
                    time.sleep(2)
                    items = parse_feed(fetch(u))
            except Exception as e:
                log.append(f"[skip] {label[:60]} -> {type(e).__name__}")
                continue
            kept = off_topic = paywalled = 0
            for it in items:
                dt = it["date"]
                if dt is not None and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt is not None and dt < cutoff:      # 有日期且過期 -> 丟
                    continue
                if not direct and is_paywall(it["title"]):   # 付費牆媒體不當來源（讀者看不到全文）
                    paywalled += 1
                    continue
                if not kw_hit(topic, it["title"] + " " + it.get("summary", "")):
                    off_topic += 1                      # 股市/EPS/國際生活稿等與主題無關的
                    continue
                key = norm(it["title"])
                if not key:
                    continue
                it["direct"] = direct
                prev = seen.get(key)
                if prev is not None:                    # 跨查詢/跨來源重複很多，併進既有那則
                    absorb(prev, it)
                    continue
                it["date"] = dt
                it["dupes"] = 1
                seen[key] = it
                rows.append(it)
                kept += 1
            log.append(f"[ok]   {label[:60]} -> {kept} 則"
                       + (f"（濾離題 {off_topic}）" if off_topic else "")
                       + (f"（濾付費 {paywalled}）" if paywalled else ""))
        for url, base, pat in OFFICIAL_HTML.get(topic, []):   # 官方入口（.gov 站雲端可能被擋，失敗略過）
            try:
                offi = scrape_official(url, base, pat)
            except Exception as e:
                log.append(f"[skip] 官方 {url[:44]} -> {type(e).__name__}")
                continue
            kept = 0
            for it in offi:
                key = norm(it["title"])
                if not key or key in seen:
                    continue
                seen[key] = it
                rows.append(it)
                kept += 1
            log.append(f"[ok]   官方 {url[:44]} -> {kept} 則")
        before = len(rows)
        rows = merge_same_event(rows)                   # 同事件跨媒體合併，額度留給真的不同的新聞
        if before != len(rows):
            log.append(f"[dedup] {topic} {before} -> {len(rows)} 則（同事件合併）")
        rows.sort(key=lambda r: (r.get("dupes", 1), r["date"] or cutoff), reverse=True)
        quota = [r for r in rows if r.get("direct")][:DIRECT_QUOTA]   # 直連媒體保底名額
        qid = {id(r) for r in quota}
        rows = (quota + [r for r in rows if id(r) not in qid])[:CAP]
        got = 0                                         # 抓原文正文（只碰真實媒體網址，Google News 是跳轉頁不碰）
        for it in rows:
            if got >= BODY_MAX:
                break
            if not it.get("direct"):
                continue
            body = fetch_body(it["link"])
            if body:
                it["body"] = body
                got += 1
        if got:
            log.append(f"[body] {topic} 取得 {got} 篇原文正文")
        result[topic] = rows
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
        n = it.get("dupes", 1)
        head_line = f"[{i}] {t}" + (f"（{src}）" if src else "")
        if n > 1:
            head_line += f"〔{n} 家媒體報導〕"
        lines.append(head_line)
        detail = it.get("body") or useful_summary(it["title"], it.get("summary", ""))
        if detail:
            lines.append(f"    內容：{detail}")
    focus = FOCUS.get(topic, "")
    head = (f"你是資深產業分析師。下面是本週「{topic}」的新聞清單（每則附編號）。\n"
            "部分項目附「內容」（原文摘要或正文節錄），請優先依內容寫，不要只從標題推測；"
            "標註〔N 家媒體報導〕代表多家跟進、是本週熱度較高的事件，值得多寫幾句。\n\n"
            + (f"【聚焦方向】{focus}\n\n" if focus else ""))
    if topic in AWARD_TOPICS:
        prompt = (head + "請用繁體中文輸出：\n"
            "1. gist：3~5 個關鍵詞，概括本週獎項重點（如「TCSA徵件、遠見ESG獎、AI獎」）。\n"
            "2. grants：把新聞裡『企業可報名的獎項／競賽』整理成條列，每個獎項一個物件，欄位：\n"
            "   - agency：主辦單位（如 TAISE、遠見雜誌、經濟部）\n"
            "   - program：獎項／競賽名稱\n"
            "   - target：報名對象（哪類企業）\n"
            "   - amount：獎勵或獎項類別（如 榮譽獎項、獎金 XX 萬）；沒提就填空字串 \"\"\n"
            "   - deadline：報名截止日，盡量寫成 YYYY-MM-DD；沒提就填空字串 \"\"\n"
            "   - idx：來源新聞編號（整數）\n"
            "【只列這類】企業可報名、主題為永續／ESG、AI、數位轉型的獎項或競賽。\n"
            "【一律排除】個人獎、學生競賽、消費者抽獎、與企業無關的活動。\n"
            "只根據下列來源，不得杜撰獎項或日期；已截止的不要列。本週若沒有符合的，grants 給空陣列。\n"
            "3. sections 給空陣列。\n\n"
            "新聞清單：\n" + "\n".join(lines))
    elif topic in GRANT_TOPICS:
        prompt = (head + "請用繁體中文輸出：\n"
            "1. gist：3~5 個關鍵詞，概括本週補助重點（如「儲能補助、地方SBIR、數位轉型」）。\n"
            "2. grants：把新聞裡的『政府補助／計畫』整理成條列，每個計畫一個物件，欄位：\n"
            "   - agency：主辦部會或單位（如 經濟部、數位發展部、新竹縣政府）\n"
            "   - program：計畫名稱\n"
            "   - target：補助對象（哪類企業或條件；白話）\n"
            "   - amount：從新聞抓出的實際補助金額／比例／上限（例：最高 500 萬、補助 50%、每案 200 萬）。"
            "新聞真的沒提到金額就填空字串 \"\"，不要填「詳見公告」之類的字。\n"
            "   - deadline：申請截止日，盡量寫成 YYYY-MM-DD；新聞沒提就填空字串 \"\"。\n"
            "   - idx：來源新聞編號（整數）\n"
            "【只列這類】企業可申請、且與『AI／數位轉型、永續淨零轉型、研發、產業升級』相關的補助（含地方政府對企業的數位/AI 升級補助）。"
            "金額或期限抓不到沒關係，還是可以列（該欄留空即可）；重點是讓讀者知道有這個補助可申請。\n"
            "【一律排除】國旅、觀光、農業、漁業、家戶節電/光電、個人消費性、獎金競賽等與企業轉型無關的補助。\n"
            "只根據下列來源整理，不得杜撰計畫、金額或期限；不是補助計畫的（純政策論述、評論、說明會、核定名單）不要列。"
            "已截止／已結束的不要列。本週若真的沒有符合的補助，grants 給空陣列。\n"
            "3. sections 給空陣列。\n\n"
            "新聞清單：\n" + "\n".join(lines))
    else:
        prompt = (head + "請用繁體中文輸出：\n"
            "1. gist：3~5 個關鍵詞（用「、」分隔），一眼概括本週該主題在講什麼。\n"
            "2. sections：分小節寫，heading 見下方 2b；沒指定 2b 的主題自己分 3~4 節、給有意義的 heading。\n"
            + ("2b. heading 必須『固定用這四個、照這個順序』：\n"
               "   「政策與法規」— 主管機關、法令、標準、期程要求的變化。\n"
               "   「市場趨勢」— 產業整體走向、技術與商業模式變化、值得注意的做法。\n"
               f"   「同業動態」— 只寫競爭對手：{'、'.join(COMPETITORS)}。\n"
               f"   「客戶動態」— 只寫甲方客戶：{CLIENT_DESC}。\n"
               "【後兩節寫法】點名『哪個對象做了什麼具體動作』（新成立什麼部門、推出什麼服務、"
               "導入什麼系統、拿到什麼認證）並附引用。名稱一律照名單上的寫法照抄，不要擴寫成全名、"
               "不要改簡稱（例：寫「富邦產險」，不要寫「富邦產物保險股份有限公司」）。\n"
               "本週沒有相關新聞的對象，連名字都不要出現；整節都沒有時只寫一句"
               "「本週無明顯相關動作」，不要把名單唸一遍。這兩節是主管最看重的，寫具體。\n"
               "2c. 名單對象出現在前兩節時，也一樣照名單寫法寫（系統會自動標紅）。\n"
               if topic in WATCH_SUFFIX else "")
            + "3. 每個小節 body 約 150~230 字連貫段落（不要條列），全篇合計約 600~900 字。\n"
            "4. 內容要有深度：補充相關『產業背景知識』（法規要點、標準內涵、盤查/查證常識與實務）。\n"
            "5. 有分析、有觀點，串出脈絡，不要流水帳；讀者想深入會自己點連結。\n"
            "5a.【每節結尾必須有一句「所以呢」】點出這件事對台灣企業的實際意義——會被要求什麼、"
            "要提前準備什麼、或代表什麼機會。空泛的「值得持續關注」「應提早因應」不算，"
            "要具體到動作或時間點（例：明年編預算時要把查證費用算進去、供應鏈客戶明年可能開始要求碳足跡資料）。\n"
            "5b. 文字要白話、口語、好懂，像資深同事直接跟你講重點——不要文謅謅的書面腔、不要成語堆砌與冗長修飾。"
            "專有名詞（如 SBTi、AppSec）第一次出現用一句白話解釋。句子盡量短、直接。\n"
            "5c.【禁用套話】不要出現：隨著…的到來、在數位浪潮下、綜觀而言、值得關注、"
            "扮演關鍵角色、勢在必行、迎來新局、更上一層樓。開頭直接講事情，不要鋪陳。\n"
            "5d.【多則有關聯就串起來】同一節若有數則指向同一個趨勢（例：多家金控都在建 AI 治理組織），"
            "合起來講「這代表什麼」，不要一則一句各自表述。\n"
            "6. 在提到具體事件/法規/數據處，於該句尾用 [[編號]] 標引用來源，可多個如 [[3]][[7]]；編號即下方新聞編號。\n"
            "7. 產業背景知識可用你的既有常識補充，但『具體事件、公司、數字』只能根據提供的標題與摘要，不得杜撰。"
            "來源沒寫的數字、日期、金額一律不要寫；不確定的用「據報導」帶過，寧可少講也不要編。\n"
            "8.【交稿前自我檢查】每節是否都有具體對象與動作（而非泛泛而談）？是否有 5a 的「所以呢」？"
            "是否踩到 5c 套話？有的話改掉再輸出。\n\n"
            "新聞清單：\n" + "\n".join(lines))
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
                    "grants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agency": {"type": "string"},
                                "program": {"type": "string"},
                                "target": {"type": "string"},
                                "amount": {"type": "string"},
                                "deadline": {"type": "string"},
                                "idx": {"type": "integer"},
                            },
                            "required": ["agency", "program", "target", "amount", "deadline", "idx"],
                        },
                    },
                },
                "required": ["gist"],
            },
        },
    }
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-flash-latest:generateContent?key=" + key)
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):                             # 5xx/429 暫時性錯誤重試
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60, context=_ctx) as r:
                resp = json.loads(r.read())
            return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                print(f"[ai] {topic} {e.code} 重試 {attempt+1}/2…")
                time.sleep(4 * (attempt + 1))
                continue
            print(f"[ai] {topic} 摘要失敗 -> HTTP {e.code}")
            return None
        except Exception as e:
            print(f"[ai] {topic} 摘要失敗 -> {type(e).__name__}: {e}")
            return None
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
.day-item{border-bottom:1px solid var(--side-line)}
.day-item>summary{list-style:none;cursor:pointer;font-family:var(--mono);font-size:14px;
  letter-spacing:.04em;color:var(--side-date);font-weight:700;padding:12px 0;
  display:flex;align-items:center;gap:9px}
.day-item>summary::-webkit-details-marker{display:none}
.day-item>summary::before{content:"▸";color:var(--accent);font-size:11px;transition:transform .15s}
.day-item[open]>summary::before{transform:rotate(90deg)}
.day-item[open]>summary{color:var(--accent)}
.day-item>summary:hover{color:var(--side-fg)}
.wk{display:block;text-decoration:none;padding:8px 11px;margin:2px 0 2px 16px;border-radius:7px;
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
/* ── 分頁列 ── */
.tabs{display:flex;gap:6px;margin:26px 0 4px;border-bottom:2px solid var(--line);flex-wrap:wrap}
.tab{font-family:inherit;font-size:16px;font-weight:700;color:var(--ink2);background:none;border:0;
  padding:11px 20px;cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;
  border-radius:7px 7px 0 0;transition:color .15s,background .15s}
.tab:hover{color:var(--brand);background:var(--card)}
.tab.active{color:var(--brand);border-bottom-color:var(--accent)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.section.tabbed{display:none;margin-top:26px}
.section.tabbed.active{display:block}
/* ── 補助卡片 ── */
.grants{display:flex;flex-direction:column;gap:14px}
.grant{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
  border-radius:10px;padding:16px 18px;box-shadow:0 4px 14px rgba(var(--shadow),.06)}
.g-top{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.g-agency{font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--brand);font-weight:700}
.g-src{font-family:var(--mono);font-size:12px;color:var(--accent);text-decoration:none;white-space:nowrap}
.g-src:hover{text-decoration:underline}
.g-name{font-size:18px;font-weight:800;color:var(--ink);margin:6px 0 12px;line-height:1.35}
.g-meta{display:grid;grid-template-columns:1fr;gap:7px;font-size:15px;color:var(--ink2)}
@media(min-width:560px){.g-meta{grid-template-columns:1fr 1fr}}
.g-meta b{color:var(--brand);font-weight:600;margin-right:8px}
.g-hint{font-size:13px;color:var(--ink2);margin:-4px 0 14px}
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
/* ── 同業／客戶標記：主管關注對象，內文與來源清單一律標紅 ── */
mark.watch{background:rgba(179,38,30,.09);color:#A32118;font-weight:700;padding:0 3px;
  border-radius:3px;box-decoration-break:clone;-webkit-box-decoration-break:clone}
.refs mark.watch{background:none;padding:0}
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
                # summary/body 一起存進 JSON：未來 RAG 讀得到內容，不只標題
                "summary": useful_summary(it["title"], it.get("summary", "")),
                "body": it.get("body", ""),
                "dupes": it.get("dupes", 1),
            })
        dg = gemini_digest(topic, rows)
        secs = (dg.get("sections") if dg else []) or []
        for s in secs:                                   # 存乾淨版（去 AI 尾端雜字）
            s["body"] = clean_body(s.get("body", ""))
        topics.append({
            "topic": topic,
            "gist": (dg.get("gist") if dg else "") or "",
            "sections": secs,
            "grants": (dg.get("grants") if dg else []) or [],
            "items": items,
            "sources": {"queries": QUERIES.get(topic, []), "feeds": FEEDS.get(topic, [])},
        })
    return {
        "date": report_date(),
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
        link = items[i].get("link", "")
        if link:                                     # 點引用直接開原文（新分頁）
            return (f'<sup><a href="{html.escape(link)}" target="_blank" '
                    f'rel="noopener" title="開啟原文">[{n}]</a></sup>')
        return f'<sup><a href="#ref-{tk}-{n}">[{n}]</a></sup>'

    body_parts = []                                  # 左欄：情勢文
    for sec in sections:
        body_parts.append(f'<h4>{html.escape(sec.get("heading", ""))}</h4>')
        body = re.sub(r"\[\[(\d+)\]\]", repl, html.escape(clean_body(sec.get("body", ""))))
        body = mark_watch(body)                      # 同業／客戶名稱標紅（主管關注）
        paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        body_parts.append('<div class="body">' + "".join(f"<p>{p}</p>" for p in paras) + "</div>")
    return (f'<div class="cols"><div class="col-body">{"".join(body_parts)}</div>'
            f'<aside class="col-refs">{build_refs(tk, items)}</aside></div>')


def build_refs(tk, items):
    """右欄參考來源清單（編號 = 項目序號，對齊內文 [n]）。"""
    out = ['<div class="refs"><span class="kicker">References · 參考來源'
           f'（{len(items)} 則）</span><ol>']
    for n, it in enumerate(items, 1):
        hot = f'　·　{it["dupes"]} 家報導' if it.get("dupes", 1) > 1 else ""
        title = mark_watch(html.escape(it["title"]))     # 來源清單也標紅，掃一眼就看到同業/客戶
        out.append(f'<li id="ref-{tk}-{n}"><a href="{html.escape(it["link"])}" '
                   f'target="_blank">{title}</a>'
                   f'<span class="src">{html.escape(it["source"])} {it["date"]}{hot}</span></li>')
    out.append('</ol></div>')
    return "".join(out)


def deadline_passed(text):
    """從期限字串抓日期，早於今天回 True（用來濾掉已截止的補助）。抓不到日期回 False（保留）。"""
    s = (text or "").strip()
    m = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", s)
    if m:
        y, mo, dd = map(int, m.groups())
    else:
        m = re.search(r"(\d{1,2})\s*[/月]\s*(\d{1,2})", s)   # 無年份 -> 當今年
        if not m:
            return False
        mo, dd = map(int, m.groups())
        y = datetime.now().year
    try:
        return datetime(y, mo, dd).date() < datetime.now().date()
    except ValueError:
        return False


def _grant_card(agency, program, target, amount, amount_label, date_label, date_val,
                link, cite_label, fee=None):
    cite = (f'<a class="g-src" href="{html.escape(link)}" target="_blank">{cite_label} ↗</a>'
            if link else "")
    meta = (f'<span><b>對象</b>{html.escape(target or "—")}</span>'
            f'<span><b>{amount_label}</b>{html.escape(amount or "—")}</span>')
    if fee is not None:
        meta += f'<span><b>報名費</b>{html.escape(fee or "見官網")}</span>'
    if date_label:
        meta += f'<span><b>{date_label}</b>{html.escape(date_val or "—")}</span>'
    return ('<div class="grant">'
            f'<div class="g-top"><span class="g-agency">{html.escape(agency)}</span>{cite}</div>'
            f'<div class="g-name">{html.escape(program)}</div>'
            f'<div class="g-meta">{meta}</div></div>')


def render_grants(topic, grants, items):
    """卡片主題（補助／獎項）：固定清單 + 本週新聞動態（AI 抓、濾過期）。滿版。"""
    award = topic in AWARD_TOPICS
    std_head = "可報名獎項" if award else "常態可申請計畫"
    std_hint = ("以下為年度徵件的企業獎項，實際徵件期間與報名方式見各獎項官網。"
                if award else
                "以下為現正開放、隨時可報名的計畫（無固定截止）；限時補助見下方「本週相關動態」。")
    cite = "獎項官網" if award else "官方網站"
    news_head = "本週獎項動態" if award else "本週相關動態"
    news_date = "報名截止" if award else "期限"

    amt_label = "獎勵" if award else "補助"
    out = [f'<h4>{std_head}</h4><p class="g-hint">{std_hint}</p><div class="grants">']
    for g in STANDING.get(topic, []):                   # 固定清單（官方連結，永遠顯示）
        out.append(_grant_card(g["agency"], g["program"], g["target"], g["amount"],
                               amt_label, "受理", g.get("status", ""), g["url"], cite,
                               fee=g.get("fee") if award else None))
    out.append('</div>')

    news = []
    for g in grants or []:
        if deadline_passed(g.get("deadline", "")):
            continue
        i = g.get("idx")
        link = items[i]["link"] if isinstance(i, int) and 0 <= i < len(items) else ""
        news.append(_grant_card(g.get("agency", ""), g.get("program", ""), g.get("target", ""),
                                g.get("amount", "").strip(), amt_label, news_date,
                                g.get("deadline", "").strip() or "詳見公告", link, "來源"))
    if news:
        out.append(f'<h4>{news_head}</h4><div class="grants">' + "".join(news) + '</div>')
    return "".join(out)


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


def render_report_body(report, tabs=False):
    """單份周報內文。tabs=True 時頂部三分頁、一次顯示一個主題（網頁用）；
    tabs=False 時全部展開（email 用，信箱不跑 JS）。"""
    parts = ['<header class="masthead">'
             '<div class="kicker">Weekly Intelligence Briefing</div>'
             '<h1>趨勢周報</h1>'
             f'<div class="issue">ISSUE {report["date"]} · 產業情勢週報</div>'
             '</header><div class="rule"></div>']
    if tabs:                                          # 分頁列
        btns = "".join(
            f'<button class="tab" data-t="{tkey(tp["topic"])}">'
            f'{html.escape(TAB_LABEL.get(tp["topic"], tp["topic"]))}</button>'
            for tp in report["topics"])
        parts.append(f'<nav class="tabs">{btns}</nav>')
    for tp in report["topics"]:
        items = tp["items"]
        tk = tkey(tp["topic"])
        kick = KICKER.get(tp["topic"], "")
        cls = "section tabbed" if tabs else "section"
        parts.append(f'<section class="{cls}" id="{tk}">')
        if kick:
            parts.append(f'<div class="kicker">{html.escape(kick)}</div>')
        parts.append(f'<h2>{html.escape(tp["topic"])}</h2>'
                     '<div class="hairline"></div>')
        if tp.get("grants") or tp["topic"] in GRANT_TOPICS:   # 補助：條列卡片
            parts.append(render_grants(tp["topic"], tp.get("grants", []), items))
        elif tp["sections"]:                                  # 有 AI 長文
            parts.append(render_article(tp["topic"], tp["sections"], items))
        elif items:                                           # 無 AI：直接列全部
            for it in items:
                parts.append(f'''<div class="item">
<a href="{html.escape(it["link"])}" target="_blank">{html.escape(it["title"])}</a>
<span class="date">{html.escape(it["source"])} {it["date"]}</span></div>''')
        else:
            parts.append('<p class="note">本週無相關動態。</p>')
        parts.append('</section>')
    parts.append(f'<p class="meta">自動產生於 {report["generated_at"]}　·　'
                 '資料來源 Google News RSS 等公開來源　·　AI 整理僅供參考，引用請以原文為準</p>')
    if tabs:                                          # 分頁切換 + 錨點深連結
        parts.append(
            "<script>(function(){var t=[].slice.call(document.querySelectorAll('.tab'));"
            "function s(id){document.querySelectorAll('.section.tabbed').forEach(function(x){"
            "x.classList.toggle('active',x.id===id)});t.forEach(function(b){"
            "b.classList.toggle('active',b.dataset.t===id)});}"
            "t.forEach(function(b){b.onclick=function(){s(b.dataset.t);"
            "history.replaceState(null,'','#'+b.dataset.t)};});"
            "var ids=t.map(function(b){return b.dataset.t;});var h=location.hash.slice(1);"
            "s(ids.indexOf(h)>=0?h:ids[0]);})();</script>")
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
    for rep in reports:                              # 已依日期新到舊；每個日期一列、點開展三類（預設全收合）
        d = rep["date"]
        side.append(f'<details class="day-item"><summary class="day">{d}</summary>')
        for tp in sorted(rep["topics"], key=lambda x: rank.get(x["topic"], 99)):
            tk = tkey(tp["topic"])
            side.append(
                f'<a class="wk" href="{base}{d}.html#{tk}">'
                f'<span class="wk-topic">{html.escape(tp["topic"])}</span>'
                f'<span class="wk-s">{html.escape(snippet(tp))}</span></a>')
        side.append("</details>")
    side.append("</aside>")
    return "".join(side)


def compose_page(report, sidebar_html, theme=None):
    """側欄 + 右上角資料來源按鈕 + 該份周報全文，組成完整頁。"""
    inner = (f'{render_sources_panel(report)}\n<div class="layout">\n{sidebar_html}\n'
             f'<main class="main">{render_report_body(report, tabs=True)}</main>\n</div>')
    return page(f'趨勢周報 {report["date"]}', inner, theme)


def render_email(report, site):
    """精簡信件：每主題只給關鍵詞 + 一句摘要 + 連結；不列全部來源，讀者自己上網站看。"""
    d = report["date"]
    P = ['<div style="font-family:\'Microsoft JhengHei\',Arial,sans-serif;max-width:640px;'
         'margin:0 auto;color:#1b2432">'
         f'<h1 style="font-size:22px;border-bottom:3px solid #234E7D;padding-bottom:8px;margin:0 0 4px">'
         f'趨勢周報 <span style="color:#94A3B8;font-size:15px">{d}</span></h1>'
         '<p style="color:#586477;font-size:14px;margin:6px 0 18px">本週重點摘要，點主題看完整內容。</p>']
    for tp in report["topics"]:
        tk = tkey(tp["topic"])
        link = f"{site}#{tk}" if site else "#"
        P.append(f'<h2 style="font-size:17px;color:#234E7D;margin:20px 0 4px">'
                 f'{html.escape(tp["topic"])}</h2>')
        if tp.get("gist"):
            P.append(f'<p style="margin:2px 0;color:#8a6d3b;font-size:13px">'
                     f'{html.escape(tp["gist"])}</p>')
        if tp.get("sections"):                           # 一句摘要（第一節開頭）
            s = re.sub(r"\[\[\d+\]\]", "", clean_body(tp["sections"][0].get("body", "")))
            s = re.split(r"[。\n]", s.strip())[0]
            if s:
                P.append(f'<p style="margin:6px 0;font-size:15px;line-height:1.7">{html.escape(s)}。</p>')
        elif tp["topic"] in GRANT_TOPICS:
            n = len(STANDING.get(tp["topic"], []))
            P.append(f'<p style="margin:6px 0;font-size:14px;color:#586477">'
                     f'常態可申請 {n} 項＋本週動態，詳見網站。</p>')
        P.append(f'<a href="{html.escape(link)}" style="color:#234E7D;font-weight:600;'
                 f'text-decoration:none;font-size:14px">看完整內容 →</a>')
    if site:
        P.append(f'<div style="margin-top:28px;padding-top:16px;border-top:1px solid #dfe3ea">'
                 f'<a href="{html.escape(site)}" style="display:inline-block;background:#234E7D;'
                 f'color:#fff;padding:11px 22px;border-radius:6px;text-decoration:none;'
                 f'font-weight:600">開啟完整週報網站</a></div>')
    P.append("</div>")
    return "".join(P)


def send_mail(report):
    """有設 env 就寄精簡版；沒設就跳過。Gmail SMTP -> 收件者(可 Outlook)。"""
    import smtplib
    from email.message import EmailMessage
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PW")
    to = os.environ.get("MAIL_TO")
    if not (user and pw and to):
        print("[mail] 未設 GMAIL_USER/GMAIL_APP_PW/MAIL_TO，跳過寄信")
        return False
    site = os.environ.get("SITE_URL")
    msg = EmailMessage()
    msg["Subject"] = f"趨勢周報 {report['date']}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content("此信為 HTML 格式，請用支援 HTML 的信箱檢視。完整內容：" + (site or ""))
    msg.add_alternative(render_email(report, site), subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] 已寄至 {to}")
    return True


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


def iso_week(dstr):
    y, m, dd = map(int, dstr.split("-"))
    return datetime(y, m, dd).isocalendar()[:2]              # (ISO 年, 週)


def report_date():
    """周報日期一律掛該週『週一』：週日備稿算隔天，週二/三補跑也蓋回同一支檔（天然一週一份）。"""
    n = datetime.now()
    d = n + timedelta(days=1) if n.weekday() == 6 else n - timedelta(days=n.weekday())
    return d.strftime("%Y-%m-%d")


def complete(rep):
    """『完整』週報：每個有新聞的非補助主題都有 AI 內容。"""
    return all(tp["topic"] in GRANT_TOPICS or tp.get("sections") or not tp["items"]
               for tp in rep["topics"])


def week_report(site):
    """本週已備好的完整週報；沒有或不完整回 None。"""
    cur = iso_week(report_date())
    for rep in load_all_reports(site):
        if iso_week(rep["date"]) == cur and complete(rep):
            return rep
    return None


def mark_mailed(site, rep):
    """在 JSON 上蓋『已寄』戳記，讓同週後續班次不會重寄（原子寫入，不冒清空原檔的險）。"""
    rep["mailed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = os.path.join(site, "reports", f'{rep["date"]}.json')
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


def selftest():
    """離線自檢（不連網、不燒 API）：關鍵字濾、同事件合併、標紅、摘要複述、期限判斷。
    跑法：python weekly_report.py --selftest"""
    assert kw_hit("AI / 企業應用", "台積電導入生成式 AI 提升良率")
    assert not kw_hit("AI / 企業應用", "TAIWAN 加權指數收紅，台股量能放大")   # AI 不可命中 TAIWAN
    assert kw_hit("永續 / 企業實務", "金管會擴大碳盤查查證範圍")
    assert not kw_hit("永續 / 企業實務", "某公司法說會 EPS 創新高")
    rows = [{"title": "富邦產險導入 AI 理賠 - 視傳媒", "summary": "短", "link": "a"},
            {"title": "富邦產險導入 AI 理賠系統 - 中央社", "summary": "x" * 80,
             "link": "b", "direct": True},
            {"title": "完全不相干的另一則新聞 - 經濟日報", "summary": "", "link": "c"}]
    m = merge_same_event(rows)
    assert len(m) == 2, f"同事件沒合併：{[r['title'] for r in m]}"
    assert m[0]["dupes"] == 2, m[0]
    assert m[0]["link"] == "b" and m[0]["direct"], "應改留真實媒體網址"
    assert len(m[0]["summary"]) == 80, "應留較長的摘要"
    assert '<mark class="watch">富邦產險</mark>' in mark_watch("富邦產險宣布導入")
    assert '<mark class="watch">USPACE</mark>' in mark_watch("USPACE 新成立 AI 部門")
    assert '<mark class="watch">富邦產物保險</mark>' in mark_watch("富邦產物保險股份有限公司導入")
    assert mark_watch("與新光金控合作") == '與<mark class="watch">新光金控</mark>合作', "虛詞要剝到 mark 外"
    assert mark_watch("從澳洲聯邦銀行來看") == '從<mark class="watch">澳洲聯邦銀行</mark>來看'
    assert '<mark class="watch">和泰產險</mark>' in mark_watch("和泰產險推新保單"), "真公司名開頭字不可被剝"
    assert '<mark class="watch">國泰金控</mark>' in mark_watch("國泰金控導入AI"), "名單外金控也要標"
    assert '<mark class="watch">裕隆汽車</mark>' in mark_watch("裕隆汽車開放參訪"), "車商也要標"
    assert '<mark class="watch">和泰汽車</mark>' in mark_watch("和泰汽車導入AI")
    assert mark_watch("電動汽車市場升溫") == "電動汽車市場升溫", "技術詞不是公司，不標"
    assert mark_watch("數位銀行浪潮來襲") == "數位銀行浪潮來襲"
    assert mark_watch("車商大打折扣戰") == "車商大打折扣戰", "太通用的詞不該標"
    assert useful_summary("標題就是這一句話沒別的", "標題就是這一句話沒別的") == ""
    assert deadline_passed("2020-01-01") and not deadline_passed("2099-12-31")
    assert "when%3A7d" in gnews_url("測試"), "Google News 查詢必須限時間窗"
    assert datetime.strptime(report_date(), "%Y-%m-%d").weekday() == 0, "周報日期必須掛週一"
    ai, grant = "AI / 企業應用", "政府補助 / 計畫"
    assert not complete({"topics": [{"topic": ai, "items": [1], "sections": []}]}), "有新聞沒 AI 內容＝不完整"
    assert complete({"topics": [{"topic": ai, "items": [1], "sections": [{"body": "x"}]}]})
    assert complete({"topics": [{"topic": ai, "items": [], "sections": []}]}), "沒新聞不算殘"
    assert complete({"topics": [{"topic": grant, "items": [1], "sections": []}]}), "補助主題本來就沒 sections"
    print("[selftest] OK")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return None
    site = os.path.join(OUT_DIR, "docs")
    reps = os.path.join(site, "reports")
    os.makedirs(reps, exist_ok=True)

    # --render-only：只用舊 JSON 重畫（改樣式/版面用，秒出、零 API）
    if "--render-only" in sys.argv:
        rebuild_site(site)
        return site

    force = "--force" in sys.argv
    report = None if force else week_report(site)             # 週日已備好 -> 週一這班只寄信，不重抓
    if report is None:
        data, log = collect()
        print("\n".join(log))
        report = build_report(data)
        d = report["date"]
        for old in os.listdir(reps):                          # 清同週舊檔(舊命名的殘檔)，保一週一份
            base = old.rsplit(".", 1)[0]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", base) and base != d and iso_week(base) == iso_week(d):
                os.remove(os.path.join(reps, old))
        with open(os.path.join(reps, f"{d}.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)  # 結構化資料（未來 AI RAG 讀這個）
        rebuild_site(site)                                    # 重畫全站
        print(f"共 {report['total']} 則")
    else:
        print(f"[ready] 本週 {report['date']} 內容已備好，不重抓")

    if "--prepare" in sys.argv:                               # 週日班：只備稿，寄信留給週一
        print("[mail] --prepare 只備稿，不寄信")
        return site
    if report.get("mailed"):
        print(f"[mail] 本週已於 {report['mailed']} 寄出，跳過")
        return site
    if not complete(report) and datetime.now().weekday() < 2:  # 殘的別寄爛信，等週二/三補跑
        print("[mail] 本週週報不完整，等下一班補跑再寄")
        return site
    if not force and datetime.now().hour < MAIL_FROM_HOUR:    # 排程可能被 GitHub 提早叫醒
        print(f"[mail] 現在 {datetime.now():%H:%M} 早於 {MAIL_FROM_HOUR} 點，等下一班再寄")
        return site
    if send_mail(report):                                    # 寄精簡版（摘要+連結）
        mark_mailed(site, report)                            # 真的寄出去才蓋戳，沒寄成下一班要重試
    return site


if __name__ == "__main__":
    main()
