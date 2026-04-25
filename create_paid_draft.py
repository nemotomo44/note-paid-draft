#!/usr/bin/env python3
"""
note.com 有料記事 下書き自動作成スクリプト
- Googleトレンド・note人気記事をリアルタイム分析
- Groq API でタイトル・本文を生成
- Playwright で note.com に直接下書き保存
"""

import os
import re
import sys
import time
import random
import logging
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOGS_DIR / f"paid_draft_{datetime.date.today()}.log", encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
NOTE_SESSION      = os.getenv("NOTE_SESSION", "")
NOTE_EMAIL        = os.getenv("NOTE_EMAIL", "")
NOTE_PASSWORD     = os.getenv("NOTE_PASSWORD", "")
NOTE_USER_URLNAME = os.getenv("NOTE_USER_URLNAME", "moya_4")

NOTE_PROFILE_FOOTER = """

---

**この記事を書いた人はこちら**

https://note.com/moya_4/n/ncbf98a80d02e
"""

POSTED_TITLES_FILE = BASE_DIR / "posted_titles.json"


# =============================================================
# 投稿済みタイトル管理
# =============================================================

def load_posted_titles() -> list:
    """過去に投稿したタイトル一覧を読み込む"""
    if POSTED_TITLES_FILE.exists():
        try:
            import json
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("titles", [])
        except Exception as e:
            logger.warning(f"posted_titles.json 読み込みエラー: {e}")
    return []


def save_posted_title(title: str, note_id: str):
    """投稿したタイトルを記録する"""
    import json
    titles = load_posted_titles()
    entry = {
        "title": title,
        "note_id": note_id,
        "posted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    # 最新を先頭に追加（最大100件保持）
    titles.insert(0, entry)
    titles = titles[:100]
    data = {
        "total": len(titles),
        "last_updated": datetime.datetime.now().isoformat(),
        "titles": titles,
    }
    with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"投稿タイトルを記録しました: {title}")


def build_past_titles_text(posted_titles: list) -> str:
    """過去タイトル一覧をプロンプト用テキストに整形"""
    if not posted_titles:
        return ""
    lines = ["【過去に投稿済みのタイトル（重複・類似NG）】"]
    lines.append("以下のタイトルと同じテーマ・切り口・表現は避けてください。\n")
    for entry in posted_titles[:30]:  # 直近30件を渡す
        lines.append(f"- {entry['title']}")
    lines.append("")
    return "\n".join(lines)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

TITLE_PROMPT = """あなたはプロのコピーライターです。
以下の条件を満たすnoteタイトルを1つだけ作成してください。

【目的】
noteの「全体ビュー数」「フォロワー数」「スキ」「コメント数」を増やすこと

【タイトル要件】
- 読者が「お金を払ってでも読みたい」と思える価値を感じさせるタイトル
- SEOでも検索されやすいキーワードを自然に含める
- 表現は親しみやすさ or 共感重視、堅苦しすぎない
- 読者が続きを読みたくなるような問い・具体性・メリットを含める
- 毎回異なる切り口・視点・語尾・問いかけの形式を使うこと

{past_titles}

{trend_info}

タイトルのみを1行で出力してください。説明文は不要です。"""

ARTICLE_PROMPT_TEMPLATE = """あなたは「もやし」というペンネームのnoteライターです。
鉄道会社で働きながら、AIと副業ライティングで人生を再出発させた30代です。
以下のタイトルに合わせた高品質なnote有料記事を、もやしの口調で執筆してください。

タイトル: {title}

{trend_info}

【キャラクター設定】
- 名前: もやし（鉄道マン×WEBライター）
- 一人称: 「私」
- 読者との距離感: 友達に話しかけるような親しみやすさ。でも馴れ馴れしすぎない
- 視点: 「普通のサラリーマンが実際に試してみた」リアルな体験者目線
- 得意な表現: 「〜を卒業！」「知ってますか？」「正直に言うと」「これ、ヤバいです」「ぶっちゃけ」

【文体ルール】（必ず守ること）
- 書き出しは「知っていますか？」「正直に言うと〜」「〜って、ありませんか？」など読者に語りかける一文から始める
- 難しい言葉・ビジネス敬語は使わない。話し言葉に近いテンポで書く
- 「〜なんです」「〜でした」「〜ですよ」「〜だったりします」など柔らかい語尾を使う
- キャッチーな表現を積極的に使う（例:「神」「沼る」「ぶっちゃけ」「秒で」「爆速」「やばい」）
- 読者の悩みを「あるある」として共感してから、解決策を提示する流れ

【記事の構成ルール】
記事は「無料部分」と「有料部分」の2段構成にしてください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【無料部分】2,000文字以上（必ず守ること）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- もやし自身のリアルな失敗談・葛藤・転換点をストーリー形式で丁寧に語る（800文字以上）
- 読者が「あ、私のことだ」と感じる具体的な悩みの描写（数字・状況・感情を込めて）
- 「この記事を読めば○○できる」という明確なベネフィットを、体験ベースで説得力をもって提示
- 有料部分で明かすノウハウの「予告」をして、続きを読みたくさせる引きで終わる
- 見出しは2〜3個設け、各章をしっかり書き込む

出力後に以下のマーカーを1行で挿入してください：
---PAID_BOUNDARY---

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【有料部分】3,000文字以上（必ず守ること）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 見出しは4〜6個設け、各章300〜800字程度
- 各見出しはnoteのマークダウン形式（## 見出しタイトル）で記述
- 必要に応じて中見出し（###）を使ってもよい

【有料部分に必ず含めること】
- 具体的なステップ・手順（数字付きで実践できる内容）
- 実際の数字・金額・期間など具体的なデータや実例
- よくある失敗パターンとその対策（もやし自身の失敗談として語る）
- 初心者でも明日から実践できるアドバイス
- 読者が「これは払う価値があった」と感じるノウハウやインサイト
- リアルなケーススタディ（もやし視点の体験談として具体的に）

【まとめ】
- 記事全体の要点を簡潔にまとめる
- もやし独自の視点と今後の展望
- まとめの最後は「ぜひ試してみてください！」「気になった方はチェックしてみて」など背中を押す一言で締める
- エモーショナルCTA（感謝＋共感＋スキ・フォロー・コメントへのやさしい行動喚起）を1〜2文

【共通ルール】
- 一人称は「私」に統一
- 表形式は使わない（noteで表示が崩れるため）
- アフィリエイトリンクは含めない
- 太字は**単語や短いフレーズのみ**（記号・改行を含めない、太字の後スペース1つ）
- 区切り線は不要

【出力形式】
無料部分の本文
---PAID_BOUNDARY---
有料部分の本文
---TAGS---
#タグ1
#タグ2
#タグ3
#タグ4
#タグ5"""


# =============================================================
# リアルタイムトレンド取得
# =============================================================

def get_google_trends() -> dict:
    """Googleトレンドから副業・note関連の急上昇キーワードを取得"""
    result = {"rising": [], "top": []}
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="ja-JP", tz=540, timeout=(10, 30))

        keywords = ["副業", "AI副業", "note", "フリーランス"]
        pytrends.build_payload(keywords, timeframe="now 7-d", geo="JP")
        related = pytrends.related_queries()

        for kw in keywords:
            if kw in related:
                rising_df = related[kw].get("rising")
                top_df    = related[kw].get("top")
                if rising_df is not None and not rising_df.empty:
                    result["rising"].extend(rising_df["query"].head(3).tolist())
                if top_df is not None and not top_df.empty:
                    result["top"].extend(top_df["query"].head(2).tolist())

        # 重複除去
        result["rising"] = list(dict.fromkeys(result["rising"]))[:8]
        result["top"]    = list(dict.fromkeys(result["top"]))[:6]
        logger.info(f"Googleトレンド急上昇: {result['rising']}")
        logger.info(f"Googleトレンド人気: {result['top']}")

    except Exception as e:
        logger.warning(f"Googleトレンド取得エラー（スキップ）: {e}")

    return result


def get_popular_note_articles(max_articles: int = 5) -> list:
    """note.comの人気記事をタイトル・スキ数・概要つきで取得"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    articles = []
    search_queries = ["副業", "AI 副業", "note 収益化", "在宅 副業"]
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "ja-JP,ja;q=0.9",
    }

    for query in search_queries:
        if len(articles) >= max_articles:
            break
        url = f"https://note.com/search?q={requests.utils.quote(query)}&context=note&mode=trending"
        try:
            time.sleep(random.uniform(1.5, 2.5))
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            for card in soup.select("a[href*='/n/']"):
                if len(articles) >= max_articles:
                    break
                # タイトル
                title_el = card.select_one("h3, h2, [class*='title' i]")
                title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:60]
                if not title or len(title) < 5:
                    continue
                # 概要
                desc_el = card.select_one("p, [class*='desc' i], [class*='body' i]")
                desc = desc_el.get_text(strip=True)[:120] if desc_el else ""
                # スキ数
                like_el = card.select_one("[class*='like' i], [class*='Like'], [class*='count' i]")
                likes = like_el.get_text(strip=True) if like_el else ""

                articles.append({
                    "title": title[:80],
                    "desc": desc,
                    "likes": likes,
                })
        except Exception as e:
            logger.debug(f"note人気記事取得エラー: {e}")

    # 重複タイトル除去
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)

    logger.info(f"note人気記事: {len(unique)}件取得")
    return unique


def build_trend_info() -> str:
    """トレンド情報をプロンプト用テキストに整形する"""
    google_trends = get_google_trends()
    note_articles  = get_popular_note_articles()

    lines = ["【リアルタイムトレンド情報】（本日取得）"]
    lines.append("※ 以下のトレンドを参考にタイトル・記事内容に自然に反映してください。\n")

    # Googleトレンド
    if google_trends["rising"]:
        lines.append("■ Googleトレンド 急上昇ワード（過去7日・日本）")
        for i, kw in enumerate(google_trends["rising"], 1):
            lines.append(f"  {i}. {kw}")
        lines.append("")

    if google_trends["top"]:
        lines.append("■ Googleトレンド 人気ワード")
        for i, kw in enumerate(google_trends["top"], 1):
            lines.append(f"  {i}. {kw}")
        lines.append("")

    # note人気記事
    if note_articles:
        lines.append("■ note 人気記事（トレンド順）")
        for i, a in enumerate(note_articles, 1):
            line = f"  {i}. 【{a['likes'] or 'スキ多数'}】{a['title']}"
            if a["desc"]:
                line += f"\n     概要: {a['desc']}"
            lines.append(line)
        lines.append("")

    if len(lines) <= 3:
        return ""  # トレンド情報なし

    lines.append("上記トレンドを踏まえ、今まさに読者が求めているテーマ・言葉を自然に盛り込んでください。")
    return "\n".join(lines)


# =============================================================
# Groq API
# =============================================================

def call_groq_api(prompt: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8000,
        "temperature": 0.8,
    }
    for wait in [10, 30, 60]:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code == 429:
                logger.warning(f"Groq API レート制限。{wait}秒後にリトライ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Groq API エラー: {e}")
            time.sleep(wait)
    raise RuntimeError("Groq API の呼び出しに失敗しました")


# =============================================================
# 記事生成
# =============================================================

def generate_title(trend_info: str, past_titles: list) -> str:
    logger.info("タイトルを生成中...")
    past_titles_text = build_past_titles_text(past_titles)
    prompt = TITLE_PROMPT.format(trend_info=trend_info, past_titles=past_titles_text)
    title = call_groq_api(prompt).strip().splitlines()[0].strip()
    logger.info(f"生成タイトル: {title}")
    return title


def generate_article(title: str, trend_info: str) -> tuple:
    logger.info("本文を生成中（無料2,000文字以上・有料3,000文字以上）...")
    prompt = ARTICLE_PROMPT_TEMPLATE.format(title=title, trend_info=trend_info)
    content = call_groq_api(prompt)

    free_body = ""
    paid_body = ""
    tags = []
    PAID_MARKER = "≪≪以降、有料パート≫≫"

    if "---PAID_BOUNDARY---" in content:
        parts = content.split("---PAID_BOUNDARY---", 1)
        free_body = parts[0].strip()
        remainder = PAID_MARKER + "\n\n" + parts[1].strip()
    else:
        free_body = ""
        remainder = PAID_MARKER + "\n\n" + content.strip()

    if "---TAGS---" in remainder:
        paid_parts = remainder.split("---TAGS---", 1)
        paid_body = paid_parts[0].strip()
        for line in paid_parts[1].strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                tags.append(stripped.lstrip("#").strip())
    else:
        paid_body = remainder.strip()

    paid_body += NOTE_PROFILE_FOOTER

    logger.info(f"無料部分: {len(free_body)}文字")
    logger.info(f"有料部分: {len(paid_body)}文字")
    logger.info(f"ハッシュタグ: {tags[:5]}")
    return free_body, paid_body, tags[:5]


# =============================================================
# note.com 下書き保存（Playwright）
# =============================================================

def save_draft_to_note(title: str, free_body: str, paid_body: str, tags: list) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright がインストールされていません")
        return None

    if not NOTE_SESSION and not (NOTE_EMAIL and NOTE_PASSWORD):
        logger.error("NOTE_SESSION または NOTE_EMAIL+NOTE_PASSWORD が必要です")
        return None

    logger.info("Playwrightでnoteに下書き保存中...")

    def _ss(page, label: str):
        try:
            p = LOGS_DIR / f"{label}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=str(p), full_page=True)
        except Exception:
            pass

    def _type_content(page, selector: str, text: str) -> bool:
        els = page.query_selector_all(selector)
        target = None
        for candidate in els:
            ph = candidate.get_attribute("data-placeholder") or ""
            if "タイトル" not in ph and candidate.is_visible():
                target = candidate
                break
        if not target and els:
            target = els[-1]
        if target:
            target.click()
            time.sleep(0.5)
            page.evaluate(
                "(args) => { const els = document.querySelectorAll(args.sel); let el = null; for(let e of els){ const ph = e.getAttribute('data-placeholder')||''; if(!ph.includes('タイトル') && e.offsetParent !== null){ el=e; break; } } if(!el && els.length) el=els[els.length-1]; if(el){ el.focus(); document.execCommand('insertText', false, args.txt); } }",
                {"sel": selector, "txt": text}
            )
            return True
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=random.choice(_USER_AGENTS),
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        page = context.new_page()

        try:
            # ① 認証
            if NOTE_SESSION.strip():
                context.add_cookies([{
                    "name": "_note_session_v5",
                    "value": NOTE_SESSION.strip(),
                    "domain": "note.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }])
                page.goto("https://note.com", wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
            else:
                page.goto("https://note.com/login", wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                for sel in ['input[type="email"]', 'input[type="text"]']:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=3000):
                            el.fill(NOTE_EMAIL)
                            break
                    except Exception:
                        continue
                try:
                    page.locator('button[type="submit"]').first.click()
                except Exception:
                    page.keyboard.press("Enter")
                time.sleep(2)
                try:
                    page.wait_for_selector('input[type="password"]', timeout=8000)
                    page.locator('input[type="password"]').first.fill(NOTE_PASSWORD)
                except Exception:
                    pass
                try:
                    page.locator('button[type="submit"]').first.click()
                except Exception:
                    page.keyboard.press("Enter")
                try:
                    page.wait_for_url(lambda u: "login" not in u, timeout=20000)
                except Exception:
                    pass
                time.sleep(3)
                if "login" in page.url:
                    logger.error("ログイン失敗")
                    return None

            # ② 新規エディタ
            page.goto("https://note.com/notes/new", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            _ss(page, "01_editor")

            # ③ タイトル入力
            title_entered = False
            for sel in [
                'div[data-placeholder="タイトル"]',
                'div[data-placeholder*="タイトル"]',
                'textarea[placeholder*="タイトル"]',
                'h1[contenteditable="true"]',
                '[class*="title" i][contenteditable="true"]',
            ]:
                try:
                    el = page.wait_for_selector(sel, timeout=3000)
                    if el and el.is_visible():
                        el.click()
                        time.sleep(0.3)
                        page.keyboard.press("Control+a")
                        page.keyboard.press("Delete")
                        page.keyboard.type(title, delay=30)
                        title_entered = True
                        logger.info(f"タイトル入力完了（{sel}）")
                        break
                except Exception:
                    continue

            if not title_entered:
                try:
                    el = page.locator('[contenteditable="true"]').first
                    el.click()
                    page.keyboard.press("Control+a")
                    page.keyboard.press("Delete")
                    page.keyboard.type(title, delay=30)
                    title_entered = True
                except Exception as e:
                    logger.warning(f"タイトル入力失敗: {e}")

            time.sleep(1)
            page.keyboard.press("Enter")
            time.sleep(0.5)

            # ④ 無料部分を入力
            logger.info("無料部分を入力中...")
            body_entered = False
            for sel in ['.ProseMirror', 'div[contenteditable="true"][class*="editor" i]']:
                if _type_content(page, sel, free_body):
                    body_entered = True
                    logger.info("無料部分入力完了")
                    break

            if not body_entered:
                try:
                    els = page.locator('[contenteditable="true"]').all()
                    target = els[1] if len(els) > 1 else (els[0] if els else None)
                    if target:
                        target.click()
                        target.type(free_body[:4000])
                        body_entered = True
                except Exception as e:
                    logger.warning(f"無料部分入力失敗: {e}")

            time.sleep(2)
            _ss(page, "02_free_body")

            # ⑤ 有料ラインは手動設定
            logger.info("有料ラインは手動でnoteエディタから設定してください。")

            # ⑥ 有料部分を入力
            logger.info("有料部分を入力中...")
            page.keyboard.press("Enter")
            time.sleep(0.5)

            paid_entered = False
            for sel in ['.ProseMirror', 'div[contenteditable="true"][class*="editor" i]']:
                try:
                    els = page.query_selector_all(sel)
                    if not els:
                        continue
                    target = None
                    for candidate in els:
                        ph = candidate.get_attribute("data-placeholder") or ""
                        if "タイトル" not in ph and candidate.is_visible():
                            target = candidate
                            break
                    if not target and els:
                        target = els[-1]
                    if target:
                        target.click()
                        page.keyboard.press("Control+End")
                        time.sleep(0.3)
                        page.keyboard.press("Enter")
                        time.sleep(0.3)
                        page.evaluate(
                            "(txt) => { document.execCommand('insertText', false, txt); }",
                            paid_body[:8000]
                        )
                        time.sleep(1)
                        paid_entered = True
                        logger.info("有料部分入力完了")
                        break
                except Exception:
                    continue

            if not paid_entered:
                try:
                    els = page.locator('[contenteditable="true"]').all()
                    target = els[1] if len(els) > 1 else (els[0] if els else None)
                    if target:
                        target.click()
                        page.keyboard.press("Control+End")
                        target.type(paid_body[:4000])
                        paid_entered = True
                except Exception as e:
                    logger.warning(f"有料部分入力失敗: {e}")

            time.sleep(3)
            _ss(page, "03_paid_body")

            # ⑦ 下書き保存
            logger.info("下書きを保存中...")
            for btn_text in ["下書き保存", "保存", "下書きとして保存"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(btn_text)).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        logger.info(f"「{btn_text}」ボタンをクリック")
                        time.sleep(2)
                        break
                except Exception:
                    continue
            else:
                page.keyboard.press("Control+s")
                time.sleep(2)

            time.sleep(3)
            _ss(page, "04_saved")
            current_url = page.url
            note_id_match = re.search(r"/notes?/([a-zA-Z0-9]+)", current_url)
            note_id = note_id_match.group(1) if note_id_match else "unknown"
            logger.info(f"下書き保存完了: https://note.com/{NOTE_USER_URLNAME}/n/{note_id}")
            return note_id

        except Exception as e:
            logger.error(f"Playwright エラー: {e}")
            _ss(page, "error")
            return None
        finally:
            browser.close()


# =============================================================
# メイン処理
# =============================================================

def main():
    logger.info("=" * 60)
    logger.info("note 有料記事 下書き作成開始")
    logger.info(f"実行時刻: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY が設定されていません")
        sys.exit(1)
    if not NOTE_SESSION and not (NOTE_EMAIL and NOTE_PASSWORD):
        logger.error("NOTE_SESSION または NOTE_EMAIL+NOTE_PASSWORD が必要です")
        sys.exit(1)

    # Step1: リアルタイムトレンド＆過去タイトル取得
    logger.info("[Step 1/3] トレンド取得・過去タイトル確認中...")
    trend_info   = build_trend_info()
    past_titles  = load_posted_titles()
    logger.info(f"過去投稿タイトル数: {len(past_titles)}件")

    # Step2: タイトル・記事生成
    logger.info("[Step 2/3] タイトル・記事を生成中...")
    title = generate_title(trend_info, past_titles)
    free_body, paid_body, tags = generate_article(title, trend_info)

    # Step3: noteに下書き保存
    logger.info("[Step 3/3] noteに下書き保存中...")
    note_id = save_draft_to_note(title, free_body, paid_body, tags)

    if not note_id:
        logger.error("下書き保存に失敗しました")
        sys.exit(1)

    # 投稿済みタイトルを記録
    save_posted_title(title, note_id)

    logger.info("=" * 60)
    logger.info("完了！")
    logger.info(f"タイトル: {title}")
    logger.info(f"下書きURL: https://note.com/{NOTE_USER_URLNAME}/n/{note_id}")
    logger.info("※ noteエディタで有料ラインを手動設定してから公開してください")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
