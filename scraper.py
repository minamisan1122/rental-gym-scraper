from __future__ import annotations
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
from datetime import datetime

# ── 検索条件 ──────────────────────────────────────────────────
RENT_MAX_MAN  = 20    # 家賃上限20万円
AREA_MIN_SQM  = 20    # 20㎡以上
AREA_MAX_SQM  = 50    # 50㎡未満

# ── 環境変数 ──────────────────────────────────────────────────
CHATWORK_API_TOKEN = os.environ.get("CHATWORK_API_TOKEN")
CHATWORK_ROOM_ID   = os.environ.get("CHATWORK_ROOM_ID", "258471022")
DATA_DIR           = os.environ.get("DATA_DIR", ".")
SEEN_FILE          = os.path.join(DATA_DIR, "seen_properties.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# ── エリア定義 ─────────────────────────────────────────────────
# (エリア名, ベースURL, 徒歩分数)
SEARCH_AREAS = [
    # 1. 奈良駅エリア（JR奈良駅・近鉄奈良駅、徒歩15分）
    ("JR奈良駅",   "https://www.athome.co.jp/chintai/nara/nara-st",          15),
    ("近鉄奈良駅", "https://www.athome.co.jp/chintai/nara/kintetsunara-st",  15),

    # 2. 埼玉エリア（各駅徒歩10分）
    ("ふじみ野駅", "https://www.athome.co.jp/chintai/saitama/fujimino-st",         10),
    ("和光市駅",   "https://www.athome.co.jp/chintai/saitama/wako-st",             10),
    ("上尾駅",     "https://www.athome.co.jp/chintai/saitama/ageo-st",             10),
    ("熊谷駅",     "https://www.athome.co.jp/chintai/saitama/kumagaya-st",         10),
    ("春日部駅",   "https://www.athome.co.jp/chintai/saitama/kasukabe-st",         10),
    ("川越駅",     "https://www.athome.co.jp/chintai/saitama/kawagoe-st",          10),
    ("浦和駅",     "https://www.athome.co.jp/chintai/saitama/urawa-st",            10),
    ("所沢駅",     "https://www.athome.co.jp/chintai/saitama/tokorozawa-st",       10),
    ("南越谷駅",   "https://www.athome.co.jp/chintai/saitama/minamikoshigaya-st",  10),
    ("志木駅",     "https://www.athome.co.jp/chintai/saitama/shiki-st",            10),

    # 3. 八王子エリア（徒歩10分）
    ("八王子駅",   "https://www.athome.co.jp/chintai/tokyo/hachioji-st",           10),
]


# ══════════════════════════════════════════════════════════════
#  共通ユーティリティ
# ══════════════════════════════════════════════════════════════

def load_seen() -> dict:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {}

def save_seen(seen: dict):
    today = datetime.now().date()
    cleaned = {k: v for k, v in seen.items()
               if (today - datetime.strptime(v, "%Y-%m-%d").date()).days < 60}
    with open(SEEN_FILE, "w") as f:
        json.dump(cleaned, f)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def init_session():
    """アットホームトップページでCookieを取得"""
    try:
        r = SESSION.get("https://www.athome.co.jp/", timeout=20)
        print(f"  セッション初期化: {r.status_code}")
        time.sleep(3)
    except Exception as e:
        print(f"  セッション初期化エラー: {e}")

def fetch(url: str) -> str | None:
    try:
        r = SESSION.get(url, timeout=30)
        if r.status_code == 404:
            print(f"  404 スキップ: {url}")
            return None
        r.raise_for_status()
        content = r.content.decode("utf-8", errors="replace")
        # CAPTCHAまたはブロックページの検出
        if len(content) < 30000 or 'noindex,nofollow' in content:
            print(f"  ブロック検出（スキップ）: {url}")
            return None
        return content
    except Exception as e:
        print(f"  fetch error: {e}")
        return None

def parse_rent(text: str) -> int | None:
    m = re.search(r"([\d.]+)\s*万円", text)
    if m:
        return int(float(m.group(1)))
    return None

def parse_area(text: str) -> float | None:
    m = re.search(r"([\d.]+)\s*(?:m[²2]|㎡)", text)
    return float(m.group(1)) if m else None

def parse_walk(text: str) -> int | None:
    m = re.search(r"徒歩\s*(\d+)\s*分", text)
    return int(m.group(1)) if m else None


# ══════════════════════════════════════════════════════════════
#  通知
# ══════════════════════════════════════════════════════════════

CHATWORK_LIMIT = 2000

def split_message(msg: str) -> list[str]:
    if len(msg) <= CHATWORK_LIMIT:
        return [msg]
    parts = []
    while msg:
        parts.append(msg[:CHATWORK_LIMIT])
        msg = msg[CHATWORK_LIMIT:]
    return parts

def send_chatwork(msg: str):
    for part in split_message(msg):
        try:
            r = requests.post(
                f"https://api.chatwork.com/v2/rooms/{CHATWORK_ROOM_ID}/messages",
                headers={"X-ChatWorkToken": CHATWORK_API_TOKEN},
                data={"body": part},
                timeout=10,
            )
            if r.status_code != 200:
                print(f"  Chatwork error: {r.status_code} {r.text}")
        except Exception as e:
            print(f"  Chatwork error: {e}")

def format_message(area_name: str, prop: dict) -> str:
    rent  = f"{prop['rent_man']}万円/月" if prop.get("rent_man") else "不明"
    area  = f"{prop['area_sqm']}㎡" if prop.get("area_sqm") else "不明"
    walk  = f"徒歩{prop['walk_min']}分" if prop.get("walk_min") else "不明"
    name  = prop.get("name") or "（名称不明）"
    url   = prop.get("url", "")

    return (
        f"[info][title]🏋 新着賃貸【{area_name}エリア】\n{name}[/title]\n"
        f"URL：{url}\n\n"
        f"【物件情報】\n"
        f"・家賃　　：{rent}\n"
        f"・面積　　：{area}\n"
        f"・アクセス：{area_name} {walk}\n"
        f"[/info]"
    )


# ══════════════════════════════════════════════════════════════
#  スクレイピング
# ══════════════════════════════════════════════════════════════

PROP_ID_RE = re.compile(r"/chintai/(\d{8,12})/")

def scrape_area(area_name: str, base_url: str, walk_limit: int,
                today_str: str, seen: dict, is_first_run: bool) -> int:
    walk_path = f"{walk_limit}-min"
    notified  = 0

    for page in range(1, 4):
        params = f"menseki_from={AREA_MIN_SQM}&menseki_to={AREA_MAX_SQM - 1}&chinryou_to={RENT_MAX_MAN}"
        if page == 1:
            url = f"{base_url}/{walk_path}/list/?{params}"
        else:
            url = f"{base_url}/{walk_path}/list/?{params}&page={page}"

        html = fetch(url)
        if html is None:
            break

        soup = BeautifulSoup(html, "html.parser")
        prop_links = soup.find_all("a", href=PROP_ID_RE)
        if not prop_links:
            print(f"  物件リンクなし（ページ{page}）")
            break

        print(f"  {area_name} page{page}: {len(prop_links)}件")
        found_new = False

        for link in prop_links:
            href = link.get("href", "")
            if not href.startswith("http"):
                href = "https://www.athome.co.jp" + href
            m = PROP_ID_RE.search(href)
            if not m:
                continue
            prop_id = m.group(1)
            key = f"athome_{prop_id}"

            if key in seen:
                continue

            found_new = True
            seen[key] = today_str

            if is_first_run:
                continue

            # カード情報取得
            card = link
            for parent in link.parents:
                if parent.name in ("li", "article"):
                    card = parent
                    break
                if parent.name == "div":
                    cls = " ".join(parent.get("class", []))
                    if any(kw in cls for kw in ["item","card","property","bukken","result","estate","object"]):
                        card = parent
                        break

            text = card.get_text(separator=" ", strip=True)
            area_sqm = parse_area(text)
            rent_man = parse_rent(text)
            walk_min = parse_walk(text)

            # 面積フィルタ（取得できた場合のみ）
            if area_sqm is not None:
                if area_sqm < AREA_MIN_SQM or area_sqm >= AREA_MAX_SQM:
                    print(f"    面積NG: {area_sqm}㎡")
                    continue

            # 家賃フィルタ（取得できた場合のみ）
            if rent_man is not None and rent_man > RENT_MAX_MAN:
                print(f"    家賃NG: {rent_man}万円")
                continue

            # 物件名
            name = ""
            for tag in card.find_all(["h2","h3","h4","h5","p"]):
                t = tag.get_text(strip=True)
                if t and len(t) > 3:
                    name = t
                    break

            # 詳細URLは /chintai/XXXXXXXXXX/ の形式
            detail_url = f"https://www.athome.co.jp/chintai/{prop_id}/"

            prop = {
                "url":      detail_url,
                "name":     name,
                "rent_man": rent_man,
                "area_sqm": area_sqm,
                "walk_min": walk_min,
            }

            send_chatwork(format_message(area_name, prop))
            print(f"  ✅ {area_name} {name[:30]} → 通知送信")
            notified += 1
            time.sleep(1)

        if not found_new:
            break

        time.sleep(5)

    return notified


# ══════════════════════════════════════════════════════════════
#  メイン
# ══════════════════════════════════════════════════════════════

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"=== Ratジム賃貸スクレイパー 開始 {today_str} ===")

    init_session()

    seen = load_seen()
    is_first_run = len(seen) == 0
    if is_first_run:
        print("【初回実行】物件IDを登録するのみ（通知なし）")

    total = 0
    for area_name, base_url, walk_limit in SEARCH_AREAS:
        print(f"\n== {area_name} ==")
        total += scrape_area(area_name, base_url, walk_limit, today_str, seen, is_first_run)
        time.sleep(8)

    print(f"\n通知合計: {total}件")
    save_seen(seen)
    print("完了")


if __name__ == "__main__":
    main()
