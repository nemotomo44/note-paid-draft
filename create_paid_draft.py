#!/usr/bin/env python3
"""
note.com 有料記事 下書き自動作成スクリプト
Groq API でタイトル・本文を生成し、Playwright で note.com に直接下書き保存する。
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

【テーマ例】
・note副業
・AI副業
・初心者におすすめの副業
・再現性のある副業
・副業の稼ぎ方

タイトルのみを1行で出力してください。説明文は不要です。"""

ARTICLE_PROMPT_TEMPLATE = """あなたは「もやし」というペンネームのnoteライターです。
鉄道会社で働きながら、AIと副業ライティングで人生を再出発させた30代です。
以下のタイトルに合わせた高品質なnote有料記事を、もやしの口調で執筆してください。

タイトル: {title}

{popular_ref}

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


def get_popular_note_articles(keyword: str, max_articles: int = 3) -> list:
    """note.comの人気記事を取得して参考情報として返す"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    articles = []
    search_urls = [
        f"https://note.com/search?q={requests.utils.quote(keyword)}&context=note&mode=trending",
        f"https://note.com/search?q={requests.utils.quote('副業 note')}&context=note&mode=trending",
    ]
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "ja-JP,ja;q=0.9",
    }

    for search_url in search_urls:
        if len(articles) >= max_articles:
            break
        try:
            time.sleep(random.uniform(1.0, 2.0))
            resp = requests.get(search_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("a[href*='/n/']")
            for card in cards:
                if len(articles) >= max_articles:
                    break
                title_el = card.select_one("h3, h2, [class*='title' i]")
                art_title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:60]
                if art_title and len(art_title) > 5:
                    articles.append({"title": art_title[:80]})
        except Exception as e:
            logger.debug(f"note人気記事取得エラー: {e}")

    logger.info(f"note人気記事参考: {len(articles)}件取得")
    return articles


def generate_title() -> str:
    logger.info("タイトルを生成中...")
    title = call_groq_api(TITLE_PROMPT).strip().splitlines()[0].strip()
    logger.info(f"生成タイトル: {title}")
    return title


def generate_article(title: str) -> tuple:
    logger.info("note人気記事を参考情報として取得中...")
    popular_articles = get_popular_note_articles(title)

    popular_ref = ""
    if popular_articles:
        popular_ref = "【note人気記事の参考情報】\n以下はnote.comで人気を集めている記事のタイトルです。\nタイトルの付け方・語り口・構成の成功パターンを参考にしてください。\n\n"
        for i, a in enumerate(popular_articles, 1):
            popular_ref += f"参考{i}: {a['title']}\n"

    logger.info("本文を生成中（有料部分3,000文字以上）...")
    prompt = ARTICLE_PROMPT_TEMPLATE.format(title=title, popular_ref=popular_ref)
    content = call_groq_api(prompt)

    # 無料部分・有料部分・タグを分割
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

    paid_len = len(paid_body)
    logger.info(f"有料部分文字数: {paid_len}文字")
    logger.info(f"ハッシュタグ: {tags[:5]}")
    return free_body, paid_body, tags[:5]


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

    def _type_content(page, selector: str, text: str):
        """指定セレクタに本文を入力する"""
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

            # ⑤ 有料ラインは手動設定（自動挿入なし）
            logger.info("有料ラインは手動でnoteエディタから設定してください。")

            time.sleep(1)
            _ss(page, "03_paid_line")

            # ⑥ 有料部分を入力
            logger.info("有料部分を入力中...")
            try:
                page.keyboard.press("Enter")
                time.sleep(0.5)
            except Exception:
                pass

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
                        # 末尾に移動
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
            _ss(page, "04_paid_body")

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
            _ss(page, "05_saved")
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


def main():
    logger.info("=" * 60)
    logger.info("note 有料記事 下書き作成開始")
    logger.info(f"実行時刻: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    if not GROQ_API_KEY:
        logger.error("GEMINI_API_KEY (Groq APIキー) が設定されていません")
        sys.exit(1)
    if not NOTE_SESSION and not (NOTE_EMAIL and NOTE_PASSWORD):
        logger.error("NOTE_SESSION または NOTE_EMAIL+NOTE_PASSWORD が必要です")
        sys.exit(1)

    title = generate_title()
    free_body, paid_body, tags = generate_article(title)
    note_id = save_draft_to_note(title, free_body, paid_body, tags)

    if not note_id:
        logger.error("下書き保存に失敗しました")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("完了！")
    logger.info(f"タイトル: {title}")
    logger.info(f"下書きURL: https://note.com/{NOTE_USER_URLNAME}/n/{note_id}")
    logger.info("※ 有料ラインが自動挿入されなかった場合はnoteエディタで手動設定してください")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
