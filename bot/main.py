"""毎朝の実行エントリポイント。

  python -m bot.main                 # 前日分を取得してDiscordに投稿（毎朝8時に実行）
  python -m bot.main --date 20261003 # 指定日分で投稿
  python -m bot.main --backfill      # シーズン開始日から前日までを取得するだけ（投稿しない）
  python -m bot.main --dry-run       # 投稿せずに本文と画像を output/ に書き出す
"""
import argparse
import datetime as dt
import io
import json
import os
import time
from pathlib import Path

import requests

from . import scrape
from .classify import TYPES, TYPE_NAME, card_count, classify, is_dragapult
from .chart import make_chart

ROOT = Path(__file__).resolve().parent.parent
DAYS_DIR = ROOT / "data" / "days"
CARDS_CACHE = ROOT / "data" / "cards.json"
DOCS = ROOT / "docs"
JST = dt.timezone(dt.timedelta(hours=9))
SEASON_START = os.environ.get("SEASON_START", "20260926")
BIG_DAY_VENUES = 10          # 会場数がこれ以上の日（週末・祝日）を推移の対象にする
TREND_POINTS = 6             # 投稿に載せる推移の点数（直近）
PAGE_URL = os.environ.get("PAGE_URL", "")
WEEK = "月火水木金土日"
KEYS = [k for k, _ in TYPES]


# ---------- データ取得 ----------
def load_cards_cache():
    return json.loads(CARDS_CACHE.read_text("utf-8")) if CARDS_CACHE.exists() else {}


def build_day(ymd, cache):
    decks = scrape.fetch_day(ymd)
    if decks is None:
        return None
    for d in decks:
        if not is_dragapult(d["label"]):
            continue
        code = d.get("code")
        if code and code not in cache:
            time.sleep(0.7)
            cache[code] = scrape.fetch_cards(code)
        cards = cache.get(code, {}) if code else {}
        d["type"], d["crush"] = classify(d["label"], cards)
        d["sam"] = card_count(cards, "サマヨール")
        d["ch"] = card_count(cards, "クラッシュハンマー")
    return {
        "date": ymd,
        "venues": len({d["venue"] for d in decks}),
        "decks": decks,
        "fetched_at": dt.datetime.now(JST).isoformat(timespec="minutes"),
    }


def ensure_days(until_ymd, retry_window=3):
    """シーズン開始日〜until_ymd の未取得日を取得して保存する。記事が無い日は保存しない。"""
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cards_cache()
    start = dt.datetime.strptime(SEASON_START, "%Y%m%d").date()
    end = dt.datetime.strptime(until_ymd, "%Y%m%d").date()
    day = start  # 未取得の日はシーズン開始日から毎回確認する（記事のない日は1リクエストで終わる）
    while day <= end:
        ymd = day.strftime("%Y%m%d")
        path = DAYS_DIR / f"{ymd}.json"
        if not path.exists():
            data = build_day(ymd, cache)
            if data and data["decks"]:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
                print(f"saved {ymd}: {data['venues']} venues, {len(data['decks'])} decks")
            else:
                print(f"no article yet for {ymd}")
            time.sleep(1)
        day += dt.timedelta(days=1)
    CARDS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")


# ---------- 集計 ----------
def summarize(data):
    drag = [d for d in data["decks"] if is_dragapult(d["label"])]
    c = {k: 0 for k in KEYS}
    for d in drag:
        c[d["type"]] += 1
    return {
        "ymd": data["date"],
        "venues": data["venues"],
        "total": len(data["decks"]),
        "n": len(drag),
        "c": c,
        "cy": sum(1 for d in drag if d["type"] == "yono" and d.get("crush")),
        "cn": sum(1 for d in drag if d["type"] == "noko" and d.get("crush")),
    }


WIN = {1: 3, 2: 2, 4: 1, 8: 0}
LOSS = {1: 0, 2: 1, 4: 1, 8: 1}
GROUPS = [("noko_c", "ノココッチ（クラハンあり）"), ("noko_n", "ノココッチ（クラハンなし）"),
          ("yono", "ヨノワール"), ("basha", "バシャーモ"), ("yonoko", "ヨノ＋ノコ"), ("crush", "クラハン")]
MAIN_GROUPS = ["noko_c", "noko_n", "yono", "basha"]


def group_of(d):
    if d["type"] == "noko":
        return "noko_c" if d.get("crush") else "noko_n"
    return d["type"]


def season_stats(until_ymd):
    """シーズン開始〜until_ymd の、トナメ勝率・TOP8→TOP4・サマヨール/クラハン枚数。"""
    g = {k: {"n8": 0, "t4": 0, "w": 0, "l": 0} for k, _ in GROUPS}
    sam = {1: 0, 2: 0}
    ch = {3: 0, 4: 0}
    for p in sorted(DAYS_DIR.glob("*.json")):
        if not (SEASON_START <= p.stem <= until_ymd):
            continue
        for d in json.loads(p.read_text("utf-8"))["decks"]:
            if not is_dragapult(d["label"]) or "type" not in d:
                continue
            if d["type"] == "yono" and d.get("sam") in sam:
                sam[d["sam"]] += 1
            if d["type"] == "noko" and d.get("ch") in ch:
                ch[d["ch"]] += 1
            k = group_of(d)
            if k in g and d["place"] <= 8:
                g[k]["n8"] += 1
                g[k]["t4"] += d["place"] <= 4
                g[k]["w"] += WIN[d["place"]]
                g[k]["l"] += LOSS[d["place"]]
    return {"groups": g, "sam": sam, "ch": ch}


def all_summaries():
    out = []
    for p in sorted(DAYS_DIR.glob("*.json")):
        if p.stem >= SEASON_START:
            out.append(summarize(json.loads(p.read_text("utf-8"))))
    return out


def label(ymd):
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    return f"{d.month}/{d.day}", WEEK[d.weekday()]


def pct(a, b):
    return a / b * 100 if b else 0.0


# ---------- 投稿文 ----------
def breakdown_line(s):
    parts = [f"{TYPE_NAME[k].replace('型', '')} {pct(s['c'][k], s['n']):.1f}%" for k in KEYS if s["c"][k]]
    return "｜".join(parts)


def trend_block(sums):
    big = [s for s in sums if s["venues"] >= BIG_DAY_VENUES][-TREND_POINTS:]
    if len(big) < 2:
        return ""
    lines = ["**■ 週末・祝日の推移（ドラパルト内の割合）**", " → ".join(label(s["ymd"])[0] for s in big)]
    for k in ["yono", "noko", "basha"]:
        vals = [pct(s["c"][k], s["n"]) for s in big]
        diff_prev = vals[-1] - vals[-2]
        diff_first = vals[-1] - vals[0]
        arrow = "↑" if diff_prev > 0.05 else "↓" if diff_prev < -0.05 else "→"
        lines.append(
            f"{TYPE_NAME[k].replace('型', '')} " + " → ".join(f"{v:.1f}%" for v in vals)
            + f"（前回比 {diff_prev:+.1f}pt {arrow}／初回比 {diff_first:+.1f}pt）"
        )
    return "\n".join(lines)


def build_message(target, sums):
    md, wd = label(target["ymd"])
    season_n = sum(s["n"] for s in sums)
    season_t = sum(s["total"] for s in sums)
    big = target["venues"] >= BIG_DAY_VENUES
    head = (f"**【シティリーグ】ドラパルト型別シェア {md}（{wd}）**" if big
            else f"**【参考】ドラパルト型別シェア {md}（{wd}）・{target['venues']}会場**")
    body = [
        head,
        f"ドラパルト {target['n']} / {target['total']}デッキ（シェア {pct(target['n'], target['total']):.1f}%）・{target['venues']}会場",
        breakdown_line(target) if target["n"] else "ドラパルトの入賞なし",
        "",
    ]
    tb = trend_block(sums)
    if tb:
        body += [tb, ""]
    st = season_stats(target["ymd"])
    lines = []
    for k, name in GROUPS:
        x = st["groups"][k]
        if k in MAIN_GROUPS and x["w"] + x["l"]:
            lines.append(f"{name} {pct(x['w'], x['w'] + x['l']):.1f}%")
    if lines:
        body += ["**■ トナメ勝率（TOP8からの勝ち抜き戦・シーズン累計／基準50%）**", "｜".join(lines), ""]
    sm, ch = st["sam"], st["ch"]
    if sum(sm.values()) and sum(ch.values()):
        body += ["**■ 構築（シーズン累計）**",
                 f"ヨノワール型のサマヨール：1枚 {pct(sm[1], sum(sm.values())):.1f}%／2枚 {pct(sm[2], sum(sm.values())):.1f}%",
                 f"ノココッチ型のクラハン：4枚 {pct(ch[4], sum(ch.values())):.1f}%／3枚 {pct(ch[3], sum(ch.values())):.1f}%",
                 ""]
    first_md = label(sums[0]["ymd"])[0]
    body.append(f"**■ シーズン累計（{first_md}〜{md}）**：ドラパルト {season_n} / {season_t}デッキ（{pct(season_n, season_t):.1f}%）")
    if PAGE_URL:
        body.append(f"推移ページ：{PAGE_URL}")
    body.append("")
    body.append("※集計元：ポケカ飯のTOP16入賞デッキ（オープン）。型は公式デッキコードの採用カードで判定")
    body.append("※ヨノワール型＝ポケカ飯の「ボム」表記" + ("" if big else "／会場が少ない日は参考値で、推移には含めません"))
    return "\n".join(body)[:1990]


# ---------- 推移ページ ----------
def build_site(sums):
    days = []
    for s in sums:
        md, wd = label(s["ymd"])
        days.append({"d": md, "w": wd, "v": s["venues"], "t": s["total"],
                     "c": [s["c"][k] for k in KEYS], "cy": s["cy"], "cn": s["cn"]})
    st = season_stats(sums[-1]["ymd"])
    stats = {"groups": [{"name": name, "main": k in MAIN_GROUPS, **st["groups"][k]} for k, name in GROUPS],
             "sam": st["sam"], "ch": st["ch"]}
    tpl = (Path(__file__).parent / "page_template.html").read_text("utf-8")
    html = tpl.replace("__DAYS_JSON__", json.dumps(days, ensure_ascii=False)).replace(
        "__STATS_JSON__", json.dumps(stats, ensure_ascii=False)).replace(
        "__UPDATED__", dt.datetime.now(JST).strftime("%-m/%-d %H:%M"))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, "utf-8")


# ---------- Discord ----------
def post_discord(content, png):
    url = os.environ["DISCORD_WEBHOOK_URL"]
    r = requests.post(
        url,
        data={"payload_json": json.dumps({"content": content, "allowed_mentions": {"parse": []}})},
        files={"files[0]": ("dragapult_share.png", png, "image/png")},
        timeout=30,
    )
    r.raise_for_status()


def notify_error(msg):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        requests.post(url, json={"content": f"⚠️ ドラパルト集計ボット：{msg}"[:1990]}, timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    target_ymd = a.date or (dt.datetime.now(JST).date() - dt.timedelta(days=1)).strftime("%Y%m%d")
    ensure_days(target_ymd)
    sums = all_summaries()
    if sums:
        build_site(sums)
    if a.backfill:
        return
    target = next((s for s in sums if s["ymd"] == target_ymd), None)
    if target is None:
        print(f"{target_ymd}: 記事なし（シティリーグ未開催または未掲載）。投稿をスキップします。")
        return
    upto = [s for s in sums if s["ymd"] <= target_ymd]
    content = build_message(target, upto)
    png = make_chart(target, upto, BIG_DAY_VENUES)
    if a.dry_run:
        out = ROOT / "output"
        out.mkdir(exist_ok=True)
        (out / "message.txt").write_text(content, "utf-8")
        (out / "chart.png").write_bytes(png)
        print(content)
        return
    post_discord(content, png)
    print("posted")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 失敗したらDiscordに知らせる
        notify_error(f"集計に失敗しました（{type(e).__name__}: {e}）。ポケカ飯のページ構造が変わった可能性があります。")
        raise
"""毎朝の実行エントリポイント。

  python -m bot.main                 # 前日分を取得してDiscordに投稿（毎朝8時に実行）
  python -m bot.main --date 20261003 # 指定日分で投稿
  python -m bot.main --backfill      # シーズン開始日から前日までを取得するだけ（投稿しない）
  python -m bot.main --dry-run       # 投稿せずに本文と画像を output/ に書き出す
"""
import argparse
import datetime as dt
import io
import json
import os
import time
from pathlib import Path

import requests

from . import scrape
from .classify import TYPES, TYPE_NAME, card_count, classify, is_dragapult
from .chart import make_chart

ROOT = Path(__file__).resolve().parent.parent
DAYS_DIR = ROOT / "data" / "days"
CARDS_CACHE = ROOT / "data" / "cards.json"
DOCS = ROOT / "docs"
JST = dt.timezone(dt.timedelta(hours=9))
SEASON_START = os.environ.get("SEASON_START", "20260926")
BIG_DAY_VENUES = 10          # 会場数がこれ以上の日（週末・祝日）を推移の対象にする
TREND_POINTS = 6             # 投稿に載せる推移の点数（直近）
PAGE_URL = os.environ.get("PAGE_URL", "")
WEEK = "月火水木金土日"
KEYS = [k for k, _ in TYPES]


# ---------- データ取得 ----------
def load_cards_cache():
    return json.loads(CARDS_CACHE.read_text("utf-8")) if CARDS_CACHE.exists() else {}


def build_day(ymd, cache):
    decks = scrape.fetch_day(ymd)
    if decks is None:
        return None
    for d in decks:
        if not is_dragapult(d["label"]):
            continue
        code = d.get("code")
        if code and code not in cache:
            time.sleep(0.7)
            cache[code] = scrape.fetch_cards(code)
        cards = cache.get(code, {}) if code else {}
        d["type"], d["crush"] = classify(d["label"], cards)
        d["sam"] = card_count(cards, "サマヨール")
        d["ch"] = card_count(cards, "クラッシュハンマー")
    return {
        "date": ymd,
        "venues": len({d["venue"] for d in decks}),
        "decks": decks,
        "fetched_at": dt.datetime.now(JST).isoformat(timespec="minutes"),
    }


def ensure_days(until_ymd, retry_window=3):
    """シーズン開始日〜until_ymd の未取得日を取得して保存する。記事が無い日は保存しない。"""
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cards_cache()
    start = dt.datetime.strptime(SEASON_START, "%Y%m%d").date()
    end = dt.datetime.strptime(until_ymd, "%Y%m%d").date()
    first_run = not any(DAYS_DIR.glob("*.json"))
    day = start if first_run else max(start, end - dt.timedelta(days=retry_window))
    while day <= end:
        ymd = day.strftime("%Y%m%d")
        path = DAYS_DIR / f"{ymd}.json"
        if not path.exists():
            data = build_day(ymd, cache)
            if data and data["decks"]:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
                print(f"saved {ymd}: {data['venues']} venues, {len(data['decks'])} decks")
            else:
                print(f"no article yet for {ymd}")
            time.sleep(1)
        day += dt.timedelta(days=1)
    CARDS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")


# ---------- 集計 ----------
def summarize(data):
    drag = [d for d in data["decks"] if is_dragapult(d["label"])]
    c = {k: 0 for k in KEYS}
    for d in drag:
        c[d["type"]] += 1
    return {
        "ymd": data["date"],
        "venues": data["venues"],
        "total": len(data["decks"]),
        "n": len(drag),
        "c": c,
        "cy": sum(1 for d in drag if d["type"] == "yono" and d.get("crush")),
        "cn": sum(1 for d in drag if d["type"] == "noko" and d.get("crush")),
    }


WIN = {1: 3, 2: 2, 4: 1, 8: 0}
LOSS = {1: 0, 2: 1, 4: 1, 8: 1}
GROUPS = [("noko_c", "ノココッチ（クラハンあり）"), ("noko_n", "ノココッチ（クラハンなし）"),
          ("yono", "ヨノワール"), ("basha", "バシャーモ"), ("yonoko", "ヨノ＋ノコ"), ("crush", "クラハン")]
MAIN_GROUPS = ["noko_c", "noko_n", "yono", "basha"]


def group_of(d):
    if d["type"] == "noko":
        return "noko_c" if d.get("crush") else "noko_n"
    return d["type"]


def season_stats(until_ymd):
    """シーズン開始〜until_ymd の、トナメ勝率・TOP8→TOP4・サマヨール/クラハン枚数。"""
    g = {k: {"n8": 0, "t4": 0, "w": 0, "l": 0} for k, _ in GROUPS}
    sam = {1: 0, 2: 0}
    ch = {3: 0, 4: 0}
    for p in sorted(DAYS_DIR.glob("*.json")):
        if not (SEASON_START <= p.stem <= until_ymd):
            continue
        for d in json.loads(p.read_text("utf-8"))["decks"]:
            if not is_dragapult(d["label"]) or "type" not in d:
                continue
            if d["type"] == "yono" and d.get("sam") in sam:
                sam[d["sam"]] += 1
            if d["type"] == "noko" and d.get("ch") in ch:
                ch[d["ch"]] += 1
            k = group_of(d)
            if k in g and d["place"] <= 8:
                g[k]["n8"] += 1
                g[k]["t4"] += d["place"] <= 4
                g[k]["w"] += WIN[d["place"]]
                g[k]["l"] += LOSS[d["place"]]
    return {"groups": g, "sam": sam, "ch": ch}


def all_summaries():
    out = []
    for p in sorted(DAYS_DIR.glob("*.json")):
        if p.stem >= SEASON_START:
            out.append(summarize(json.loads(p.read_text("utf-8"))))
    return out


def label(ymd):
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    return f"{d.month}/{d.day}", WEEK[d.weekday()]


def pct(a, b):
    return a / b * 100 if b else 0.0


# ---------- 投稿文 ----------
def breakdown_line(s):
    parts = [f"{TYPE_NAME[k].replace('型', '')} {pct(s['c'][k], s['n']):.1f}%" for k in KEYS if s["c"][k]]
    return "｜".join(parts)


def trend_block(sums):
    big = [s for s in sums if s["venues"] >= BIG_DAY_VENUES][-TREND_POINTS:]
    if len(big) < 2:
        return ""
    lines = ["**■ 週末・祝日の推移（ドラパルト内の割合）**", " → ".join(label(s["ymd"])[0] for s in big)]
    for k in ["yono", "noko", "basha"]:
        vals = [pct(s["c"][k], s["n"]) for s in big]
        diff_prev = vals[-1] - vals[-2]
        diff_first = vals[-1] - vals[0]
        arrow = "↑" if diff_prev > 0.05 else "↓" if diff_prev < -0.05 else "→"
        lines.append(
            f"{TYPE_NAME[k].replace('型', '')} " + " → ".join(f"{v:.1f}%" for v in vals)
            + f"（前回比 {diff_prev:+.1f}pt {arrow}／初回比 {diff_first:+.1f}pt）"
        )
    return "\n".join(lines)


def build_message(target, sums):
    md, wd = label(target["ymd"])
    season_n = sum(s["n"] for s in sums)
    season_t = sum(s["total"] for s in sums)
    big = target["venues"] >= BIG_DAY_VENUES
    head = (f"**【シティリーグ】ドラパルト型別シェア {md}（{wd}）**" if big
            else f"**【参考】ドラパルト型別シェア {md}（{wd}）・{target['venues']}会場**")
    body = [
        head,
        f"ドラパルト {target['n']} / {target['total']}デッキ（シェア {pct(target['n'], target['total']):.1f}%）・{target['venues']}会場",
        breakdown_line(target) if target["n"] else "ドラパルトの入賞なし",
        "",
    ]
    tb = trend_block(sums)
    if tb:
        body += [tb, ""]
    st = season_stats(target["ymd"])
    lines = []
    for k, name in GROUPS:
        x = st["groups"][k]
        if k in MAIN_GROUPS and x["w"] + x["l"]:
            lines.append(f"{name} {pct(x['w'], x['w'] + x['l']):.1f}%")
    if lines:
        body += ["**■ トナメ勝率（TOP8からの勝ち抜き戦・シーズン累計／基準50%）**", "｜".join(lines), ""]
    sm, ch = st["sam"], st["ch"]
    if sum(sm.values()) and sum(ch.values()):
        body += ["**■ 構築（シーズン累計）**",
                 f"ヨノワール型のサマヨール：1枚 {pct(sm[1], sum(sm.values())):.1f}%／2枚 {pct(sm[2], sum(sm.values())):.1f}%",
                 f"ノココッチ型のクラハン：4枚 {pct(ch[4], sum(ch.values())):.1f}%／3枚 {pct(ch[3], sum(ch.values())):.1f}%",
                 ""]
    first_md = label(sums[0]["ymd"])[0]
    body.append(f"**■ シーズン累計（{first_md}〜{md}）**：ドラパルト {season_n} / {season_t}デッキ（{pct(season_n, season_t):.1f}%）")
    if PAGE_URL:
        body.append(f"推移ページ：{PAGE_URL}")
    body.append("")
    body.append("※集計元：ポケカ飯のTOP16入賞デッキ（オープン）。型は公式デッキコードの採用カードで判定")
    body.append("※ヨノワール型＝ポケカ飯の「ボム」表記" + ("" if big else "／会場が少ない日は参考値で、推移には含めません"))
    return "\n".join(body)[:1990]


# ---------- 推移ページ ----------
def build_site(sums):
    days = []
    for s in sums:
        md, wd = label(s["ymd"])
        days.append({"d": md, "w": wd, "v": s["venues"], "t": s["total"],
                     "c": [s["c"][k] for k in KEYS], "cy": s["cy"], "cn": s["cn"]})
    st = season_stats(sums[-1]["ymd"])
    stats = {"groups": [{"name": name, "main": k in MAIN_GROUPS, **st["groups"][k]} for k, name in GROUPS],
             "sam": st["sam"], "ch": st["ch"]}
    tpl = (Path(__file__).parent / "page_template.html").read_text("utf-8")
    html = tpl.replace("__DAYS_JSON__", json.dumps(days, ensure_ascii=False)).replace(
        "__STATS_JSON__", json.dumps(stats, ensure_ascii=False)).replace(
        "__UPDATED__", dt.datetime.now(JST).strftime("%-m/%-d %H:%M"))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, "utf-8")


# ---------- Discord ----------
def post_discord(content, png):
    url = os.environ["DISCORD_WEBHOOK_URL"]
    r = requests.post(
        url,
        data={"payload_json": json.dumps({"content": content, "allowed_mentions": {"parse": []}})},
        files={"files[0]": ("dragapult_share.png", png, "image/png")},
        timeout=30,
    )
    r.raise_for_status()


def notify_error(msg):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        requests.post(url, json={"content": f"⚠️ ドラパルト集計ボット：{msg}"[:1990]}, timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    target_ymd = a.date or (dt.datetime.now(JST).date() - dt.timedelta(days=1)).strftime("%Y%m%d")
    ensure_days(target_ymd)
    sums = all_summaries()
    if sums:
        build_site(sums)
    if a.backfill:
        return
    target = next((s for s in sums if s["ymd"] == target_ymd), None)
    if target is None:
        print(f"{target_ymd}: 記事なし（シティリーグ未開催または未掲載）。投稿をスキップします。")
        return
    upto = [s for s in sums if s["ymd"] <= target_ymd]
    content = build_message(target, upto)
    png = make_chart(target, upto, BIG_DAY_VENUES)
    if a.dry_run:
        out = ROOT / "output"
        out.mkdir(exist_ok=True)
        (out / "message.txt").write_text(content, "utf-8")
        (out / "chart.png").write_bytes(png)
        print(content)
        return
    post_discord(content, png)
    print("posted")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 失敗したらDiscordに知らせる
        notify_error(f"集計に失敗しました（{type(e).__name__}: {e}）。ポケカ飯のページ構造が変わった可能性があります。")
        raise
