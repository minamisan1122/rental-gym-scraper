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
HOMES_INIT_FILE    = os.path.join(DATA_DIR, "homes_initialized.json")
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
# ScraperAPI移行後、bot検知リスクは低減。34エリアを2バッチで3時間おきに処理。
# アットホームとHOME'Sの2サイトを同じ条件で巡回する。
# (エリア名, アットホームURL, 徒歩分数上限, 家賃下限, HOME'Sパス, HOME'Sの対象駅名)
# HOME'Sパス: /chintai/tempo/ 以下。駅コードはHOME'S固有のため sitemap・沿線ページで確認済み。
# HOME'Sの対象駅名: HOME'Sは1物件に複数駅を併記するため、この駅の徒歩分数だけを採用する。
#                  Noneは市区指定エリア（駅指定なし）で、最寄り駅の徒歩分数を採用する。
BATCHES = [
    # バッチ0: 東京・神奈川・愛知（17エリア・全て徒歩10分）
    [
        ("錦糸町駅",   "https://www.athome.co.jp/rent_store/tokyo/kinshicho-st",         10, None, "tokyo/kinshicho_00207-st",        "錦糸町"),
        ("小岩駅",     "https://www.athome.co.jp/rent_store/tokyo/koiwa-st",             10, None, "tokyo/koiwa_01928-st",            "小岩"),
        ("新小岩駅",   "https://www.athome.co.jp/rent_store/tokyo/shinkoiwa-st",         10, None, "tokyo/shinkoiwa_01929-st",        "新小岩"),
        ("蒲田駅",     "https://www.athome.co.jp/rent_store/tokyo/kamata-st",            10, None, "tokyo/kamata_00605-st",           "蒲田"),
        ("練馬駅",     "https://www.athome.co.jp/rent_store/tokyo/nerima-st",            10, None, "tokyo/nerima_04788-st",           "練馬"),
        ("赤羽駅",     "https://www.athome.co.jp/rent_store/tokyo/akabane-st",           10, None, "tokyo/akabane_00533-st",          "赤羽"),
        ("調布駅",     "https://www.athome.co.jp/rent_store/tokyo/chofu-st",             10, None, "tokyo/chofu_04941-st",            "調布"),
        ("中央林間駅", "https://www.athome.co.jp/rent_store/kanagawa/chuorinkan-st",     10, None, "kanagawa/chuorinkan_05030-st",    "中央林間"),
        ("新横浜駅",   "https://www.athome.co.jp/rent_store/kanagawa/shinyokohama-st",   10, None, "kanagawa/shinyokohama_00012-st",  "新横浜"),
        ("武蔵小杉駅", "https://www.athome.co.jp/rent_store/kanagawa/musashikosugi-st",  10, None, "kanagawa/musashikosugi_00657-st", "武蔵小杉"),
        ("登戸駅",     "https://www.athome.co.jp/rent_store/kanagawa/noborito-st",       10, None, "kanagawa/noborito_00664-st",      "登戸"),
        ("たまプラーザ駅", "https://www.athome.co.jp/rent_store/kanagawa/tamaplaza-st",  10, None, "kanagawa/tamaplaza_05103-st",     "たまプラーザ"),
        ("戸塚駅",     "https://www.athome.co.jp/rent_store/kanagawa/totsuka-st",        10, None, "kanagawa/totsuka_00559-st",       "戸塚"),
        ("橋本駅",     "https://www.athome.co.jp/rent_store/kanagawa/hashimoto-st",      10, None, "kanagawa/hashimoto_00728-st",     "橋本"),
        ("湘南台駅",   "https://www.athome.co.jp/rent_store/kanagawa/shonandai-st",      10, None, "kanagawa/shonandai_05037-st",     "湘南台"),
        ("大和駅",     "https://www.athome.co.jp/rent_store/kanagawa/yamato-st",         10, None, "kanagawa/yamato_05033-st",        "大和"),
        ("金山駅",     "https://www.athome.co.jp/rent_store/aichi/kanayama-st",          10, None, "aichi/kanayama_02071-st",         "金山"),
    ],
    # バッチ1: 神奈川・埼玉・北関東・北海道・関西（17エリア）
    [
        ("大宮駅",     "https://www.athome.co.jp/rent_store/saitama/omiya-st",           10, None, "saitama/omiya_00040-st",          "大宮"),
        ("南越谷駅",   "https://www.athome.co.jp/rent_store/saitama/minamikoshigaya-st", 10, None, "saitama/minamikoshigaya_00699-st", "南越谷"),
        ("上大岡駅",   "https://www.athome.co.jp/rent_store/kanagawa/kamiooka-st",       10, None, "kanagawa/kamiooka_05163-st",      "上大岡"),
        ("溝の口駅",   "https://www.athome.co.jp/rent_store/kanagawa/musashimizonokuchi-st", 10, None, "kanagawa/mizonokuchi_05098-st", "溝の口"),
        ("新百合ヶ丘駅","https://www.athome.co.jp/rent_store/kanagawa/shinyurigaoka-st",  10, None, "kanagawa/shinyurigaoka_05007-st", "新百合ヶ丘"),
        ("水戸駅",     "https://www.athome.co.jp/rent_store/ibaraki/mito-st",            15, None, "ibaraki/mito_00141-st",           "水戸"),
        ("研究学園駅", "https://www.athome.co.jp/rent_store/ibaraki/kenkyugakuen-st",    15, None, "ibaraki/kenkyugakuen_09944-st",   "研究学園"),
        ("宇都宮駅",   "https://www.athome.co.jp/rent_store/tochigi/utsunomiya-st",      15, None, "tochigi/utsunomiya_00042-st",     "宇都宮"),
        ("豊平区",       "https://www.athome.co.jp/rent_office/hokkaido/sapporo_toyohira-city", 10, None, "hokkaido/sapporo_toyohira-city", None),
        ("豊平公園駅",   "https://www.athome.co.jp/rent_store/hokkaido/toyohirakoen-st",        15, None, "hokkaido/toyohirakoen_07567-st", "豊平公園"),
        ("豊中駅",       "https://www.athome.co.jp/rent_store/osaka/toyonaka-st",               10, None, "osaka/toyonaka_06138-st",     "豊中"),
        ("谷町四丁目駅", "https://www.athome.co.jp/rent_store/osaka/tanimachiyonchome-st",      10, None, "osaka/tanimachiyonchome_06498-st", "谷町四丁目"),
        ("天王寺駅",     "https://www.athome.co.jp/rent_store/osaka/tennoji-st",                10, None, "osaka/tennoji_00280-st",      "天王寺"),
        ("枚方市駅",     "https://www.athome.co.jp/rent_store/osaka/hirakatashi-st",            10, None, "osaka/hirakatashi_06037-st",  "枚方市"),
        ("堺東駅",       "https://www.athome.co.jp/rent_store/osaka/sakaihigashi-st",           10, None, "osaka/sakaihigashi_05968-st", "堺東"),
        ("JR奈良駅",     "https://www.athome.co.jp/rent_store/nara/nara-st",                    15, None, "nara/nara_03277-st",          "奈良"),
        ("近鉄奈良駅",   "https://www.athome.co.jp/rent_store/nara/kintetsunara-st",            15, None, "nara/kintetsunara_05827-st",  "近鉄奈良"),
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

def load_homes_init() -> set:
    """HOME'S巡回済みエリア。既存物件の一斉通知を防ぐため、エリア単位で初回登録を管理する。"""
    if os.path.exists(HOMES_INIT_FILE):
        with open(HOMES_INIT_FILE) as f:
            return set(json.load(f))
    return set()

def save_homes_init(areas: set):
    with open(HOMES_INIT_FILE, "w") as f:
        json.dump(sorted(areas), f, ensure_ascii=False)

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
    return "reeseSkipExpiration" in content or "認証中" in content

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


# ブロック時はathomeが認証中ページ、HOME'SがAWS WAFの数KBページを返す。
# 正常な一覧ページは100KB以上あるので、極端に小さい応答もブロック扱いにする。
MIN_VALID_BYTES = 5000
_NOT_FOUND = object()

def _looks_blocked(text: str) -> bool:
    return len(text) < MIN_VALID_BYTES or _is_blocked(text)

def _scraperapi_once(url: str, premium: bool) -> "str | object | None":
    """戻り値: HTML / _NOT_FOUND(404) / None(通信失敗)"""
    label = "premium" if premium else "標準"
    params = {"api_key": SCRAPERAPI_KEY, "url": url}
    if premium:
        params["premium"] = "true"
    try:
        r = requests.get("http://api.scraperapi.com", params=params, timeout=60)
        if r.status_code == 404:
            print(f"  404 スキップ: {url}")
            return _NOT_FOUND  # ページ切れ等。リトライしても無駄なので即終了する
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  ScraperAPI error ({label}): {e}")
        return None

def fetch_scraperapi(url: str, retries: int = 3, wait: int = 15) -> str | None:
    """まず標準プロキシで取得し、ブロックされた時だけpremiumに切り替える。

    ScraperAPIは標準1クレジット・premium10クレジット。2026-08-03の実測では
    athome・HOME'Sとも標準で15/15成功したため、premiumは保険に回して消費を1/10にする。
    """
    if not SCRAPERAPI_KEY:
        print("  ⚠ SCRAPERAPI_KEY が未設定")
        return None
    for attempt in range(1, retries + 1):
        # ① 標準プロキシ（1クレジット）
        text = _scraperapi_once(url, premium=False)
        if text is _NOT_FOUND:
            return None
        if text and not _looks_blocked(text):
            return text
        if text:
            print(f"  標準プロキシでブロック検出 → premiumで再試行: {url}")

        # ② 標準がだめな時だけpremium（10クレジット）
        text = _scraperapi_once(url, premium=True)
        if text is _NOT_FOUND:
            return None
        if text:
            if _looks_blocked(text):
                print(f"  ⚠ premiumでもブロックの可能性（{len(text)}文字）: {url}")
            return text

        if attempt < retries:
            time.sleep(wait)
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

def format_message(area_name: str, prop: dict, source: str = "アットホーム") -> str:
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
        f"・掲載元　：{source}\n"
        f"・家賃　　：{rent}\n"
        f"・面積　　：{area}\n"
        f"・アクセス：{area_name} {walk}\n"
        f"[/info]"
    )


# ══════════════════════════════════════════════════════════════
#  スクレイピング
# ══════════════════════════════════════════════════════════════

PROP_ID_RE = re.compile(r"/(rent_(?:store|office))/(\d{8,12})/")

def scrape_area(area_name: str, base_url: str, walk_limit: int,
                today_str: str, seen: dict, is_first_run: bool,
                rent_min: int | None = None) -> int:
    notified = 0

    for page in range(1, 4):
        page_path = "" if page == 1 else f"page{page}/"
        url = f"{base_url}/list/{page_path}"

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
            prop_type = m.group(1)
            prop_id = m.group(2)
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

            # スケルトン物件スキップ
            if "スケルトン" in text:
                seen[key] = today_str
                print(f"    スケルトンNG: スキップ")
                continue

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

            detail_url = f"https://www.athome.co.jp/{prop_type}/{prop_id}/"

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
#  スクレイピング（HOME'S）
# ══════════════════════════════════════════════════════════════

HOMES_BASE   = "https://www.homes.co.jp/chintai/tempo"
HOMES_BID_RE = re.compile(r"/chintai/b-(\d+)/")
# HOME'Sのプルダウンが受け付ける値のみ指定可（それ以外はポストフィルタに任せる）
HOMES_WALK_OPTIONS = (1, 5, 7, 10, 15, 20)

def build_homes_url(homes_path: str, walk_limit: int, page: int) -> str:
    """アットホームと違いHOME'SはURLパラメータで絞り込めるので、条件を渡して取得件数を減らす。"""
    q = [
        f"cond%5Bhousearea%5D={AREA_MIN_SQM}",
        f"cond%5Bhouseareah%5D={AREA_MAX_SQM}",
        f"cond%5Bmonthmoneyroomh%5D={RENT_MAX_MAN}",
    ]
    if walk_limit in HOMES_WALK_OPTIONS:
        q.append(f"cond%5Bwalkminutesh%5D={walk_limit}")
    if page > 1:
        q.append(f"page={page}")
    return f"{HOMES_BASE}/{homes_path}/list/?" + "&".join(q)

def parse_homes_walk(text: str, station: str | None) -> int | None:
    """対象駅の徒歩分数だけを取る。

    HOME'Sは1物件に複数駅を併記し、対象駅が「3.6km」表記で他駅が「徒歩2分」のことがある。
    単純に最初の「徒歩N分」を拾うと別の駅の分数で通過してしまうため、駅名で限定する。
    """
    if station is None:
        mins = [int(m) for m in re.findall(r"徒歩\s*(\d+)\s*分", text)]
        return min(mins) if mins else None
    # 「近鉄奈良駅」を「奈良駅」で誤マッチしないよう、駅名の直前が区切り文字であることを要求する
    m = re.search(rf"(?:^|[\s|｜/])({re.escape(station)})駅\s*徒歩\s*(\d+)\s*分", text)
    return int(m.group(2)) if m else None

def _homes_cell_index(table, keyword: str) -> int | None:
    head = table.select_one("tr")
    if not head:
        return None
    for i, c in enumerate(head.select("th,td")):
        if keyword in c.get_text(strip=True):
            return i
    return None

def parse_homes_page(html: str, station: str | None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    props = []
    for b in soup.select("div.mod-mergeBuilding--rent"):
        link = b.select_one("a[href*='/chintai/b-']")
        if not link:
            continue
        m = HOMES_BID_RE.search(link["href"])
        if not m:
            continue
        bid = m.group(1)

        nm_span = b.select_one("span.bukkenName")
        name = nm_span.get_text(strip=True) if nm_span else ""

        btext = b.get_text(" ", strip=True)
        walk = parse_homes_walk(btext, station)

        # 建物ブロックには「所在地等のスペック表」と「部屋一覧表」がある。賃料列を持つ後者を使う
        table = None
        for t in b.select("table"):
            head = t.select_one("tr")
            if head and "賃料" in head.get_text():
                table = t
                break
        if table is None:
            continue

        # 敷金欄に「430万円」等が入るため、列位置を特定して賃料・面積を取る
        i_rent = _homes_cell_index(table, "賃料")
        i_area = _homes_cell_index(table, "面積")
        i_room = _homes_cell_index(table, "部屋")

        rows = [tr for tr in table.select("tr") if tr.select("td") and "万円" in tr.get_text()]
        for tr in rows:
            tds = tr.select("td")

            def cell(i):
                return tds[i].get_text(" ", strip=True) if i is not None and i < len(tds) else ""

            rent_man = None
            mm = re.search(r"([\d.]+)\s*万円", cell(i_rent))
            if mm:
                rent_man = float(mm.group(1))

            area_sqm = None
            mm = re.search(r"([\d.]+)\s*m[²2]", cell(i_area))
            if mm is None:
                mm = re.search(r"面積/坪数\s*([\d.]+)\s*m[²2]", btext)
            if mm:
                area_sqm = float(mm.group(1))

            room = cell(i_room)
            if room in ("", "-", "‐", "―", "－"):  # 部屋番号なしの物件は「-」表記
                room = ""
            props.append({
                "key":      f"homes_{bid}" if len(rows) == 1 else f"homes_{bid}_{room}",
                "url":      f"https://www.homes.co.jp/chintai/b-{bid}/",
                "name":     f"{name} {room}".strip() if room else name,
                "rent_man": rent_man,
                "area_sqm": area_sqm,
                "walk_min": walk,
                "text":     btext,
            })
    return props

def scrape_homes_area(area_name: str, homes_path: str, station: str | None,
                      walk_limit: int, today_str: str, seen: dict,
                      is_first_run: bool, rent_min: int | None = None) -> tuple[int, bool]:
    """戻り値: (通知件数, 1ページ目の取得に成功したか)"""
    notified = 0
    fetched_ok = False

    for page in range(1, 4):
        html = fetch_scraperapi(build_homes_url(homes_path, walk_limit, page))
        if html is None:
            break
        if page == 1:
            fetched_ok = True

        props = parse_homes_page(html, station)
        if not props:
            print(f"  HOME'S 物件なし（ページ{page}）")
            break

        # 次ページのリンクが無ければ最終ページ。無駄な404リクエストを避ける
        has_next = f"page={page + 1}" in html

        print(f"  HOME'S {area_name} page{page}: {len(props)}件")
        found_new = False

        for p in props:
            key = p["key"]
            if key in seen:
                continue
            found_new = True

            if is_first_run:
                seen[key] = today_str
                continue

            if "スケルトン" in p["text"]:
                seen[key] = today_str
                print(f"    スケルトンNG: スキップ")
                continue

            if p["area_sqm"] is not None:
                if p["area_sqm"] < AREA_MIN_SQM or p["area_sqm"] >= AREA_MAX_SQM:
                    seen[key] = today_str
                    print(f"    面積NG: {p['area_sqm']}㎡")
                    continue

            if p["walk_min"] is None:
                seen[key] = today_str
                print(f"    徒歩NG: {area_name}からの徒歩分数が不明のためスキップ")
                continue
            if p["walk_min"] > walk_limit:
                seen[key] = today_str
                print(f"    徒歩NG: {p['walk_min']}分（上限{walk_limit}分）")
                continue

            if p["rent_man"] is not None and p["rent_man"] > RENT_MAX_MAN:
                seen[key] = today_str
                print(f"    家賃NG（上限超過）: {p['rent_man']}万円")
                continue
            if rent_min is not None and p["rent_man"] is not None and p["rent_man"] < rent_min:
                seen[key] = today_str
                print(f"    家賃NG（下限未満）: {p['rent_man']}万円")
                continue

            if send_chatwork(format_message(area_name, p, source="HOME'S")):
                seen[key] = today_str  # 送信成功後にのみseen登録
                print(f"  ✅ HOME'S {area_name} {p['name'][:30]} → 通知送信")
                notified += 1
            else:
                print(f"  ⚠ 通知失敗（次回再試行）: HOME'S {area_name} {p['name'][:30]}")
            time.sleep(1)

        if not found_new or not has_next:
            break

        time.sleep(random.uniform(5, 10))

    return notified, fetched_ok


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

    homes_init = load_homes_init()

    total = 0
    for area_name, base_url, walk_limit, rent_min, homes_path, homes_station in areas:
        print(f"\n== {area_name} ==")
        total += scrape_area(area_name, base_url, walk_limit, today_str, seen, is_first_run,
                             rent_min)

        # HOME'Sはエリア単位で初回登録する。既存物件を一斉通知してしまうのを防ぐため
        homes_first = is_first_run or area_name not in homes_init
        if homes_first:
            print(f"  HOME'S【{area_name} 初回】既存物件を登録するのみ（通知なし）")
        n, ok = scrape_homes_area(area_name, homes_path, homes_station, walk_limit,
                                  today_str, seen, homes_first, rent_min)
        total += n
        if ok:
            homes_init.add(area_name)  # 取得できた時だけ登録済みにする（失敗時の一斉通知を防ぐ）

    print(f"\n通知合計: {total}件")
    save_seen(seen)
    save_homes_init(homes_init)
    save_batch(next_batch)
    print("完了")


if __name__ == "__main__":
    main()
