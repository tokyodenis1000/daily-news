"""
每日英文新聞 → 網頁版(給 GitHub Pages 用)
抓一篇 VOA Learning English 長文 → 逐段翻成繁體中文 → 輸出 docs/index.html
DeepL key 從環境變數 DEEPL_API_KEY 讀取(GitHub Secrets),不會寫進網頁。
"""
import datetime as dt
import html
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import feedparser
import requests
import trafilatura
from deep_translator import GoogleTranslator

# ---------- 設定 ----------
# 我還沒有確認 VOA Learning English 的文章 RSS 網址,所以先留空,
# 程式會自動偵測網站的 RSS / sitemap。若你找到 RSS 網址,貼在這裡:
#   ("VOA Learning English", "https://.../rss.xml"),
FEEDS = []
SITE_HOME = "https://learningenglish.voanews.com"
MIN_WORDS = 250
MAX_WORDS = 900
PER_FEED = 15
MAX_TRIES = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
STATE = DOCS / "state.json"
DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "").strip()


# ---------- 翻譯 ----------
def deepl_batch(texts):
    out = [""] * len(texts)
    if not DEEPL_KEY or not texts:
        return out
    host = "api-free.deepl.com" if DEEPL_KEY.endswith(":fx") else "api.deepl.com"
    for s in range(0, len(texts), 40):
        chunk = texts[s:s + 40]
        for target in ("ZH-HANT", "ZH"):
            try:
                r = requests.post(f"https://{host}/v2/translate",
                                  headers={"Authorization": f"DeepL-Auth-Key {DEEPL_KEY}"},
                                  json={"text": chunk, "source_lang": "EN", "target_lang": target},
                                  timeout=30)
                if r.status_code == 200:
                    for k, t in enumerate(r.json()["translations"]):
                        out[s + k] = t["text"]
                    break
                if r.status_code != 400:
                    break
            except Exception:
                break
    return out


def google(text):
    for wait in (0, 1.5, 3):
        time.sleep(wait)
        try:
            o = GoogleTranslator(source="en", target="zh-TW").translate(text[:4500])
            if o:
                return o
        except Exception:
            pass
    return ""


def translate_all(texts):
    res = deepl_batch(texts)
    for i, t in enumerate(res):
        if not t:
            res[i] = google(texts[i])
            time.sleep(0.6)
    return res


# ---------- 抓文章 ----------
def clean(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def page_links(url, pattern):
    """抓一個網頁,回傳符合 pattern 的連結(絕對網址,去重,保持順序)。"""
    try:
        r = requests.get(url, headers=UA, timeout=20)
        print(f"  {url} -> HTTP {r.status_code}, {len(r.text)} bytes")
        r.raise_for_status()
    except Exception as e:
        print(f"  {url} -> 失敗: {e}")
        return []
    out = []
    for m in re.findall(pattern, r.text):
        u = urllib.parse.urljoin(url, m)
        if u not in out:
            out.append(u)
    return out


def candidates():
    items = []
    # 1) 有填 RSS 就先用
    for name, url in FEEDS:
        try:
            feed = feedparser.parse(url, agent=UA["User-Agent"])
            items += [(name, clean(e.get("title", "")), e["link"])
                      for e in feed.entries[:PER_FEED] if e.get("link")]
        except Exception:
            pass
    # 2) 直接爬 VOA Learning English 首頁與各單元頁,收集文章連結 /a/數字.html
    print("爬取 VOA 首頁與單元頁…")
    sections = page_links(SITE_HOME + "/", r'href="(/z/\d+)"')[:10]
    for page in [SITE_HOME + "/"] + sections:
        for link in page_links(page, r'href="(/a/\d+\.html)"'):
            items.append(("VOA Learning English", "", link))
    seen, out = set(), []
    for it in items:
        if it[2] not in seen:
            seen.add(it[2])
            out.append(it)
    print(f"共找到 {len(out)} 個候選連結")
    return out


def extract(page_html):
    meta = trafilatura.bare_extraction(page_html, include_comments=False, include_tables=False,
                                       favor_precision=True)
    text = (meta.text if meta and hasattr(meta, "text") else (meta or {}).get("text")) or ""
    title = (meta.title if meta and hasattr(meta, "title") else (meta or {}).get("title")) or ""
    paras, words = [], 0
    for p in text.split("\n"):
        p = p.strip()
        if len(p) < 40:
            continue
        n = len(p.split())
        if words + n > MAX_WORDS and paras:
            break
        paras.append(p)
        words += n
    return title, paras, words


def fetch_article(seen):
    tried = 0
    for source, title, link in candidates():
        if link in seen:
            continue
        seen.append(link)
        tried += 1
        try:
            r = requests.get(link, headers=UA, timeout=20)
            r.raise_for_status()
            t2, paras, words = extract(r.text)
        except Exception as e:
            print(f"  跳過 {link}: {e}")
            continue
        print(f"  {link} -> {words} 字")
        if words >= MIN_WORDS:
            return {"source": source, "title": title or t2, "link": link,
                    "paras": paras, "words": words}
        if tried >= MAX_TRIES:
            break
    return None


# ---------- 輸出網頁 ----------
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1e1e2e">
<title>Daily News</title>
<style>
:root{--bg:#1e1e2e;--panel:#2a2a3c;--fg:#e6e6e6;--accent:#7aa2f7;--zh:#8fa1d6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:Arial,sans-serif;line-height:1.55}
header{position:sticky;top:0;background:var(--bg);padding:10px 14px;border-bottom:1px solid #333;z-index:5}
#meta{color:var(--accent);font-size:13px;font-weight:bold;margin-bottom:8px}
.bar button{background:var(--panel);color:var(--fg);border:0;border-radius:6px;padding:8px 12px;margin-right:6px;font-size:14px}
main{padding:14px;max-width:760px;margin:auto;font-size:var(--fs,18px)}
h1{font-size:1.35em;margin:.2em 0}
.en{margin:0 0 .4em}
.zh{color:var(--zh);font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif;font-size:.93em;margin:0 0 1.4em}
.hide-zh .zh{display:none}
.w{border-radius:3px}
.w:active{background:#3b4261}
#sheet{position:fixed;left:0;right:0;bottom:0;background:var(--panel);padding:14px 16px 20px;
 border-top:2px solid var(--accent);transform:translateY(110%);transition:.2s;z-index:10}
#sheet.open{transform:none}
#sheet b{font-size:20px}
#sheet a{color:var(--accent);margin-right:14px}
#tr{margin:6px 0 10px;min-height:1.4em;color:#cfd6f5}
</style></head>
<body>
<header><div id="meta"></div>
<div class="bar"><button id="bz">中文 顯示/隱藏</button><button id="bm">A−</button><button id="bp">A+</button>
<button id="bo">原文</button></div></header>
<main id="main"></main>
<div id="sheet"><b id="word"></b><div id="tr"></div>
<a id="cam" target="_blank">劍橋字典</a><a id="gt" target="_blank">Google 翻譯</a><a href="#" id="close">關閉</a></div>
<script>
const A = __DATA__;
const main = document.getElementById('main');
document.getElementById('meta').textContent = A.date + ' | ' + A.source + ' | ' + A.paras.length + ' 段 · 約 ' + A.words + ' 字';
function wrap(t){ return t.replace(/[A-Za-z][A-Za-z'’-]*/g, m => '<span class="w">'+m+'</span>'); }
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
let h = '<h1>'+wrap(esc(A.title))+'</h1><p class="zh">'+esc(A.zh[0]||'')+'</p>';
A.paras.forEach((p,i)=>{ h += '<p class="en">'+wrap(esc(p))+'</p><p class="zh">'+esc(A.zh[i+1]||'')+'</p>'; });
main.innerHTML = h;
let fs = +localStorage.fs || 18; const setFs=()=>{main.style.setProperty('--fs',fs+'px');localStorage.fs=fs};
setFs();
if(localStorage.hz==='1') main.classList.add('hide-zh');
bz.onclick=()=>{main.classList.toggle('hide-zh');localStorage.hz=main.classList.contains('hide-zh')?'1':'0'};
bm.onclick=()=>{fs=Math.max(13,fs-1);setFs()}; bp.onclick=()=>{fs=Math.min(30,fs+1);setFs()};
bo.onclick=()=>window.open(A.link,'_blank');
const sheet=document.getElementById('sheet');
close.onclick=e=>{e.preventDefault();sheet.classList.remove('open')};
main.addEventListener('click', async e=>{
  const el=e.target.closest('.w'); if(!el) return;
  const w=el.textContent.replace(/[’]/g,"'"); const lw=w.toLowerCase();
  word.textContent=w; tr.textContent='查詢中…';
  cam.href='https://dictionary.cambridge.org/dictionary/english-chinese-traditional/'+encodeURIComponent(lw);
  gt.href='https://translate.google.com/?sl=en&tl=zh-TW&op=translate&text='+encodeURIComponent(w);
  sheet.classList.add('open');
  try{
    const r=await fetch('https://api.mymemory.translated.net/get?q='+encodeURIComponent(lw)+'&langpair=en|zh-TW');
    const j=await r.json(); tr.textContent=(j.responseData&&j.responseData.translatedText)||'(查不到,請點劍橋字典)';
  }catch(_){ tr.textContent='(查詢失敗,請點劍橋字典)'; }
});
</script></body></html>
"""


def main():
    DOCS.mkdir(exist_ok=True)
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    seen = state.get("seen", [])
    article = fetch_article(seen) or state.get("article")
    if not article:
        raise SystemExit("沒有抓到文章:請檢查 FEEDS 或調低 MIN_WORDS")
    if "zh" not in article or article.get("zh_for") != article["link"]:
        zh = translate_all([article["title"]] + article["paras"])
        article["zh"], article["zh_for"] = zh, article["link"]
    article["date"] = dt.date.today().isoformat()
    STATE.write_text(json.dumps({"article": article, "seen": seen[-200:]},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    data = json.dumps(article, ensure_ascii=False).replace("</", "<\\/")
    (DOCS / "index.html").write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
    print("OK:", article["title"], article["words"], "words")


if __name__ == "__main__":
    main()
