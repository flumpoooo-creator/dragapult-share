"""投稿用の画像（週末推移の折れ線＋前日の内訳バー）を作る。"""
import io
import datetime as dt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from .classify import TYPES

COLORS = {"yono": "#4B4F73", "noko": "#D9A21B", "basha": "#D9542B", "yonoko": "#8A7BC4",
          "crush": "#8C97A3", "plain": "#C9C3DD", "other": "#E6E2EE"}
WEEK = "月火水木金土日"

for name in ["Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "Hiragino Sans", "Yu Gothic"]:
    if any(name in f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = name
        break


def _md(ymd):
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    return f"{d.month}/{d.day}\n{WEEK[d.weekday()]}"


def make_chart(target, sums, big_venues):
    big = [s for s in sums if s["venues"] >= big_venues]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7.2), gridspec_kw={"height_ratios": [3, 1.1]}, dpi=150)
    fig.patch.set_facecolor("#F3F2F7")
    for ax in (ax1, ax2):
        ax.set_facecolor("#FFFFFF")
        for sp in ax.spines.values():
            sp.set_visible(False)

    # 推移
    xs = list(range(len(big)))
    for k, name in TYPES[:3]:
        ys = [s["c"][k] / s["n"] * 100 if s["n"] else 0 for s in big]
        ax1.plot(xs, ys, color=COLORS[k], lw=3, marker="o", ms=7)
        for x, y in zip(xs, ys):
            ax1.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(0, 8),
                         ha="center", fontsize=9, color=COLORS[k], fontweight="bold")
        if ys:
            ax1.annotate(name, (xs[-1], ys[-1]), textcoords="offset points", xytext=(12, -4),
                         fontsize=11, color=COLORS[k], fontweight="bold")
    ax1.set_xticks(xs, [_md(s["ymd"]) for s in big], fontsize=10)
    ax1.set_ylim(0, max(60, ax1.get_ylim()[1]))
    ax1.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax1.grid(axis="y", color="#DCD9E6")
    ax1.set_xlim(-0.4, max(len(big) - 1, 0) + 0.9)
    ax1.set_title("ドラパルト内の型別割合の推移（週末・祝日）", loc="left", fontsize=14, fontweight="bold", color="#211C33")

    # 対象日の内訳
    left = 0
    for k, name in TYPES:
        v = target["c"][k] / target["n"] * 100 if target["n"] else 0
        if not v:
            continue
        ax2.barh(0, v, left=left, color=COLORS[k], height=0.6)
        if v >= 7:
            ax2.text(left + v / 2, 0, f"{name.replace('型', '')}\n{v:.0f}%", ha="center", va="center",
                     fontsize=9, color="white" if k not in ("plain", "other") else "#211C33", fontweight="bold")
        left += v
    ax2.set_xlim(0, 100)
    ax2.set_yticks([])
    ax2.set_xticks([])
    md = _md(target["ymd"]).replace("\n", "（") + "）"
    share = target["n"] / target["total"] * 100 if target["total"] else 0
    ax2.set_title(f"{md}の内訳　ドラパルト {target['n']}/{target['total']}（シェア {share:.1f}%）・{target['venues']}会場",
                  loc="left", fontsize=12, fontweight="bold", color="#211C33")
    fig.tight_layout(pad=1.6)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
