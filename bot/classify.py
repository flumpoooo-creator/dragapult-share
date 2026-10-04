"""ドラパルトの型判定。上から順に当てはめ、最初に該当した型にする。"""
import re

TYPES = [
    ("yono", "ヨノワール型"),
    ("noko", "ノココッチ型"),
    ("basha", "バシャーモ型"),
    ("yonoko", "ヨノ＋ノコ型"),
    ("crush", "クラハン型"),
    ("plain", "無印"),
    ("other", "その他"),
]
TYPE_NAME = dict(TYPES)
MAIN_RE = re.compile(r"^(ボム)?ドラパルト(/|$)")


def is_dragapult(label):
    """デッキ名の先頭がドラパルト（ポケカ飯の「ボム」＝ヨノワールを含む）。"""
    return bool(MAIN_RE.match(label))


def classify(label, cards):
    """戻り値: (型キー, クラハン入りか)"""
    if "メノコマシラ" in label:
        return "other", False
    if not cards:  # デッキコード未掲載のときはポケカ飯の表記で判定
        if label.startswith("ボム") and "ノココッチ" in label:
            return "yonoko", False
        if label.startswith("ボム"):
            return "yono", False
        if "ノココッチ" in label:
            return "noko", False
        if "バシャーモ" in label:
            return "basha", False
        if "ドデカバシ" in label:
            return "other", False
        return "plain", False
    cards = list(cards)  # dict（名前→枚数）でも list でも可
    noko = any(c.startswith("ノココッチ") for c in cards)
    yono = any("ヨノワール" in c for c in cards)
    crush = "クラッシュハンマー" in cards
    if any("ドデカバシ" in c for c in cards):
        return "other", crush
    if "バシャーモex" in cards:
        return "basha", crush
    if yono and noko:
        return "yonoko", crush
    if yono:
        return "yono", crush
    if noko:
        return "noko", crush
    if crush:
        return "crush", True
    return "plain", False


def card_count(cards, prefix):
    """cards が dict のとき、名前が prefix で始まるカードの合計枚数。"""
    if not isinstance(cards, dict):
        return 0
    return sum(v for k, v in cards.items() if k.startswith(prefix))
