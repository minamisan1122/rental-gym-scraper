from __future__ import annotations
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import random
from datetime import datetime

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_AVAILABLE = True
except ImportError:
    _CFFI_AVAILABLE = False

# ── 検索条件 ──────────────────────────────────────────────────
RENT_MAX_MAN  = 20    # 家賃上限20万円
AREA_MIN_SQM  = 30    # 30㎡以上
AREA_MAX_SQM  = 80    # 80㎡未満

# ── 環境変数 ──────────────────────────────────────────────────
CHATWORK_API_TOKEN = os.environ.get("CHATWORK_API_TOKEN")
CHATWORK_ROOM_ID   = os.environ.get("CHATWORK_ROOM_ID", "258471022")
DATA_DIR           = os.environ.get("DATA_DIR", ".")
SEEN_FILE          = os.path.join(DATA_DIR, "seen_properties.json")
BATCH_FILE         = os.path.join(DATA_DIR, "current_batch.json")
SCRAPERAPI_KEY     = os.environ.get("SCRAPERAPI_KEY")

# Chromeバージョンをランダムローテーションしてフィンガープリントを分散させる
CHROME_PROFILES = [
    ("chrome110", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"),
    ("chrome116", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome131", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
]

def get_headers(ua: str) -> dict:
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

HEADERS = get_headers(CHROME_PROFILES[3][1])

# ── エリア定義（バッチ分割） ────────────────────────────────────
# 1回のセッションで6エリアを超えるとReese84にブロックされるため4グループに分割。
# 3時間おきに1グループずつ処理し、全19エリアを12時間で1巡する。
# (エリア名, ベースURL, 徒歩分数上限, 家賃下限)
BATCHES = [
    # バッチ0: 埼玉北部・中部（6エリア）
    [
        ("ふじみ野駅", "https://www.athome.co.jp/rent_store/saitama/fujimino-st",        10, None),
        ("和光市駅",   "https://www.athome.co.jp/rent_store/saitama/wako-st",            10, None),
        ("熊谷駅",     "https://www.athome.co.jp/rent_store/saitama/kumagaya-st",        10, None),
        ("春日部駅",   "https://www.athome.co.jp/rent_store/saitama/kasukabe-st",        10, None),
        ("川越駅",     "https://www.athome.co.jp/rent_store/saitama/kawagoe-st",         10, None),
        ("浦和駅",     "https://www.athome.co.jp/rent_store/saitama/urawa-st",           10, None),
    ],
    # バッチ1: 埼玉南部・八王子・東京東部（6エリア）
    [
        ("所沢駅",     "https://www.athome.co.jp/rent_store/saitama/tokorozawa-st",      10, None),
        ("南越谷駅",   "https://www.athome.co.jp/rent_store/saitama/minamikoshigaya-st", 10, None),
        ("志木駅",     "https://www.athome.co.jp/rent_store/saitama/shiki-st",           10, None),
        ("八王子駅",   "https://www.athome.co.jp/rent_store/tokyo/hachioji-st",          10, None),
        ("錦糸町駅",   "https://www.athome.co.jp/rent_store/tokyo/kinshicho-st",          7, None),
        ("小岩駅",     "https://www.athome.co.jp/rent_store/tokyo/koiwa-st",              7, None),
    ],
    # バッチ2: 東京東部・北関東（5エリア）
    [
        ("新小岩駅",   "https://www.athome.co.jp/rent_store/tokyo/shinkoiwa-st",          7, None),
        ("高崎駅",     "https://www.athome.co.jp/rent_store/gunma/takasaki-st",          15, None),
        ("水戸駅",     "https://www.athome.co.jp/rent_store/ibaraki/mito-st",            15, None),
        ("研究学園駅", "https://www.athome.co.jp/rent_store/ibaraki/kenkyugakuen-st",    15, None),
        ("宇都宮駅",   "https://www.athome.co.jp/rent_store/tochigi/utsunomiya-st",      15, None),
    ],
    # バッチ3: 東京西部（2エリア）
    [
        ("調布駅",     "https://www.athome.co.jp/rent_store/tokyo/chofu-st",             10, None),
        ("府中駅",     "https://www.athome.co.jp/rent_store/tokyo/fuchu-st",             10, None),
    ],
]
NUM_BATCHES = len(BATCHES)


# ══════════════════════════════════════════════════════════════
#  共通ユーティリティ
# ══════════════════════════════════════════════════════════════

def load_batch() -> int:
    if os.path.exists(BATCH_FILE):
        with open(BATCH_FILE) as f:
            return json.load(f).get("batch", 0)
    return 0

def save_batch(batch_num: int):
    with open(BATCH_FILE, "w") as f:
        json.dump({"batch": batch_num}, f)

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

def init_session():
    """curl_cffiが使えない場合のフォールバック用（何もしない）"""
    pass

def _is_blocked(content: str) -> bool:
    return "reeseSkipExpiration" in content or 'noindex,nofollow' in content

def fetch(url: str, session=None, impersonate: str = "chrome124", ua: str = "") -> str | None:
    """curl_cffiでChrome TLSフィンガープリントを偽装してアットホームのbot検知を回避する。"""
    headers = get_headers(ua) if ua else HEADERS
    if _CFFI_AVAILABLE:
        try:
            s = session or cffi_requests.Session()
            r = s.get(url, headers=headers, timeout=30, impersonate=impersonate)
            if r.status_code == 404:
                print(f"  404 スキップ: {url}")
                return None
            r.raise_for_status()
            if _is_blocked(r.text):
                return "__BLOCKED__"
            return r.text
        except Exception as e:
            print(f"  fetch error (cffi): {e}")
            return None
    # フォールバック: 通常requests
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 404:
            print(f"  404 スキップ: {url}")
            return None
        r.raise_for_status()
        if len(r.text) < 30000 or _is_blocked(r.text):
            return "__BLOCKED__"
        return r.text
    except Exception as e:
        print(f"  fetch error: {e}")
        return None


def fetch_scraperapi(url: str) -> str | None:
    """ScraperAPI経由でフェッチ（PerimeterX/Reese84 bot検知回避）"""
    if not SCRAPERAPI_KEY:
        print("  ⚠ SCRAPERAPI_KEY が未設定")
        return None
    try:
        r = requests.get(
            "http://api.scraperapi.com",
            params={"api_key": SCRAPERAPI_KEY, "url": url, "premium": "true"},
            timeout=60,
        )
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  ScraperAPI error: {e}")
        return None

def fetch_with_retry(url: str, impersonate: str, ua: str) -> str | None:
    """ブロック検出時に別プロファイルで1回リトライする。"""
    session = cffi_requests.Session() if _CFFI_AVAILABLE else None
    result = fetch(url, session=session, impersonate=impersonate, ua=ua)
    if result == "__BLOCKED__":
        retry_profile, retry_ua = random.choice(CHROME_PROFILES)
        wait = random.uniform(100, 140)
        print(f"  ブロック検出 → {wait:.0f}秒待機後に {retry_profile} でリトライ: {url}")
        time.sleep(wait)
        retry_session = cffi_requests.Session() if _CFFI_AVAILABLE else None
        result = fetch(url, session=retry_session, impersonate=retry_profile, ua=retry_ua)
        if result == "__BLOCKED__":
            print(f"  リトライもブロック → スキップ: {url}")
            return None
    return result

def parse_rent(text: str) -> float | None:
    # 「賃料」「月額」「家賃」ラベルの直後の金額を優先（敷金・礼金・坪単価を誤取得しない）
    m = re.search(r"(?:賃料|月額|家賃)[^\d]*([\d.]+)\s*万円", text)
    if m:
        return float(m.group(1))
    # ラベルなしの場合はフォールバック
    m = re.search(r"([\d.]+)\s*万円", text)
    if m:
        return float(m.group(1))
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

def send_chatwork(msg: str) -> bool:
    success = True
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
                success = False
        except Exception as e:
            print(f"  Chatwork error: {e}")
            success = False
    return success

def format_message(area_name: str, prop: dict) -> str:
    if prop.get("rent_man") is not None:
        r = prop["rent_man"]
        rent = f"{r:.1f}万円/月" if r != int(r) else f"{int(r)}万円/月"
    else:
        rent = "不明"
    area  = f"{prop['area_sqm']}㎡" if prop.get("area_sqm") else "不明"
    walk  = f"徒歩{prop['walk_min']}分" if prop.get("walk_min") else "不明"
    name  = prop.get("name") or "（名称不明）"
    url   = prop.get("url", "")

    return (
        f"[toall]\n[info][title]🏋 新着賃貸【{area_name}エリア】\n{name}[/title]\n"
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

PROP_ID_RE = re.compile(r"/rent_store/(\d{8,12})/")

def scrape_area(area_name: str, base_url: str, walk_limit: int,
                today_str: str, seen: dict, is_first_run: bool,
                rent_min: int | None = None) -> int:
    notified = 0

    for page in range(1, 4):
        url = f"{base_url}/list/" if page == 1 else f"{base_url}/list/?page={page}"

        html = fetch_scraperapi(url)
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
            key = f"athome_rs_{prop_id}"

            if key in seen:
                continue

            found_new = True

            if is_first_run:
                seen[key] = today_str
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

            # 家賃: athomeは整数部と小数部を別spanに分割するため separator='' で結合取得
            rent_man: float | None = None
            price_span = card.find("span", class_="red")
            if price_span:
                price_text = price_span.get_text(strip=True)
                m = re.search(r"([\d.]+)\s*万円", price_text)
                if m:
                    rent_man = float(m.group(1))

            text = card.get_text(separator=" ", strip=True)
            area_sqm = parse_area(text)
            if rent_man is None:
                rent_man = parse_rent(text)
            walk_min = parse_walk(text)

            # 面積フィルタ（取得できた場合のみ）
            if area_sqm is not None:
                if area_sqm < AREA_MIN_SQM or area_sqm >= AREA_MAX_SQM:
                    seen[key] = today_str
                    print(f"    面積NG: {area_sqm}㎡")
                    continue

            # 徒歩分数フィルタ（不明な場合もスキップ）
            if walk_min is None:
                seen[key] = today_str
                print(f"    徒歩NG: 徒歩分数不明のためスキップ")
                continue
            if walk_min > walk_limit:
                seen[key] = today_str
                print(f"    徒歩NG: {walk_min}分（上限{walk_limit}分）")
                continue

            # 家賃フィルタ（取得できた場合のみ）
            if rent_man is not None and rent_man > RENT_MAX_MAN:
                seen[key] = today_str
                print(f"    家賃NG（上限超過）: {rent_man}万円")
                continue
            if rent_min is not None and rent_man is not None and rent_man < rent_min:
                seen[key] = today_str
                print(f"    家賃NG（下限未満）: {rent_man}万円")
                continue

            # 物件名
            name = ""
            for tag in card.find_all(["h2","h3","h4","h5","p"]):
                t = tag.get_text(strip=True)
                if t and len(t) > 3:
                    name = t
                    break

            detail_url = f"https://www.athome.co.jp/rent_store/{prop_id}/"

            prop = {
                "url":      detail_url,
                "name":     name,
                "rent_man": rent_man,
                "area_sqm": area_sqm,
                "walk_min": walk_min,
            }

            if send_chatwork(format_message(area_name, prop)):
                seen[key] = today_str  # 送信成功後にのみseen登録
                print(f"  ✅ {area_name} {name[:30]} → 通知送信")
                notified += 1
            else:
                print(f"  ⚠ 通知失敗（次回再試行）: {area_name} {name[:30]}")
            time.sleep(1)

        if not found_new:
            break

        time.sleep(random.uniform(10, 20))

    return notified


# ══════════════════════════════════════════════════════════════
#  メイン
# ══════════════════════════════════════════════════════════════

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")

    current_batch = load_batch()
    next_batch = (current_batch + 1) % NUM_BATCHES
    areas = BATCHES[current_batch]

    print(f"=== Ratジム賃貸スクレイパー 開始 {today_str} ===")
    print(f"バッチ {current_batch + 1}/{NUM_BATCHES}（{len(areas)}エリア）→ 次回はバッチ{next_batch + 1}")

    init_session()

    seen = load_seen()
    is_first_run = len(seen) == 0
    if is_first_run:
        print("【初回実行】物件IDを登録するのみ（通知なし）")

    total = 0
    for area_name, base_url, walk_limit, rent_min in areas:
        print(f"\n== {area_name} ==")
        total += scrape_area(area_name, base_url, walk_limit, today_str, seen, is_first_run,
                             rent_min)

    print(f"\n通知合計: {total}件")
    save_seen(seen)
    save_batch(next_batch)
    print("完了")


if __name__ == "__main__":
    main()
