"""ポケカ飯の日別シティリーグ記事から入賞デッキを取得し、公式デッキコードから採用カードを取る。"""
import re
import time
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (dragapult-share-bot; once-a-day)"}
BASE = "https://pokekameshi.com/cityleague{ymd}/"
PLACE = {"優勝": 1, "準優勝": 2, "ベスト４": 4, "ベスト８": 8, "ベスト16": 16}
PLACE_RE = re.compile(r"^(優勝|準優勝|ベスト４|ベスト８|ベスト16)：(.+)$")
DECK_RE = re.compile(r"pokemon-card\.com/deck/[^\s\"']*deckID/([^/?#\"']+)")
NAME_RE = re.compile(r"searchItemNameAlt\[(\d+)\]\s*=\s*'([^']*)'")


def _get(url):
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def parse_page(html):
    """1ページ分のHTMLから ([{venue, place, label, code}], soup) を返す。"""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".entry-content")
    if content is None:
        return [], soup
    decks, venue, cur = [], None, None
    for el in content.find_all(["h2", "h3", "strong", "b", "a"]):
        if el.name == "a":
            m = DECK_RE.search(el.get("href", ""))
            if m and venue and cur and not cur["code"]:
                cur["code"] = m.group(1)
            continue
        text = re.sub(r"\s+", "", el.get_text())
        if el.name == "h2":
            if "：" in text and not re.search(r"順位|分析|ページ", text):
                venue = text.split("：", 1)[1]
            else:
                venue = None
            cur = None
            continue
        if venue is None:
            continue
        m = PLACE_RE.match(text)
        if m and (el.name == "h3" or m.group(1) == "ベスト16"):
            cur = {"venue": venue, "place": PLACE[m.group(1)], "label": m.group(2), "code": None}
            decks.append(cur)
    return decks, soup


def fetch_day(ymd):
    """ymd='20261003'。記事がまだ無ければ None。"""
    base = BASE.format(ymd=ymd)
    html = _get(base)
    if html is None:
        return None
    decks, soup = parse_page(html)
    maxp = 1
    for a in soup.find_all("a", href=True):
        m = re.search(rf"cityleague{ymd}/(\d+)/?$", a["href"])
        if m:
            maxp = max(maxp, int(m.group(1)))
    for p in range(2, maxp + 1):
        time.sleep(1)
        h = _get(f"{base}{p}/")
        if h:
            decks += parse_page(h)[0]
    return decks


INPUT_RE = re.compile(r'(?:id|name)="deck_[a-z]+"[^>]*value="([^"]*)"')


def fetch_cards(code):
    """公式デッキページから {カード名: 枚数} を返す。"""
    html = _get(f"https://www.pokemon-card.com/deck/confirm.html/deckID/{code}/")
    if not html:
        return {}
    names = {m.group(1): m.group(2) for m in NAME_RE.finditer(html)}
    counts = {}
    for m in INPUT_RE.finditer(html):
        for part in filter(None, m.group(1).split("-")):
            fields = part.split("_")
            if len(fields) < 2:
                continue
            name = names.get(fields[0], "#" + fields[0])
            counts[name] = counts.get(name, 0) + int(fields[1] or 0)
    return counts
