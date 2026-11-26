# -*- coding: utf-8 -*-
"""一日一片 · 架构图的生成器。

为什么用代码画图而不是拖图形界面：
  1. 数字会变（19553 条弹幕、9358 字 prompt……），图跟着数据走才不会过期；
  2. 坐标和字号是显式写的，改一行就能重排，比在 PPT 里挪框可靠；
  3. 生成的是 SVG，字体、线宽、细网格在任何缩放级别都干净。

产物：docs/assets/architecture/*.svg
再用无头浏览器渲成 2x PNG（命令见文件末尾 README）。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 设计系统：纸、墨、朱红、青
# ---------------------------------------------------------------------------
W, H = 1600, 1000
M = 72  # 版心边距

PAPER = "#F6F1E6"
PAPER2 = "#EFE7D5"
CARD = "#FBF7EC"
INK = "#171614"
INK2 = "#4A443B"
INK3 = "#8A8172"
RULE = "#D7CDB8"
RED = "#C0392B"
TEAL = "#175F5A"
GOLD = "#A0742A"

SERIF = "'Noto Serif SC', Georgia, serif"
SANS = "'Noto Sans SC', 'Microsoft YaHei', sans-serif"

OUT = Path(__file__).resolve().parents[2] / "docs" / "assets" / "architecture"


# ---------------------------------------------------------------------------
# 基础图元
# ---------------------------------------------------------------------------
def esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def rect(x, y, w, h, fill="none", stroke=RULE, sw=1, rx=3, dash=None, opacity=1):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d} opacity="{opacity}"/>'
    )


def line(x1, y1, x2, y2, stroke=RULE, sw=1, dash=None, opacity=1):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="{stroke}" stroke-width="{sw}"{d} opacity="{opacity}"/>'
    )


def path(d, stroke=INK3, sw=1.2, fill="none", dash=None, marker=None, opacity=1):
    da = f' stroke-dasharray="{dash}"' if dash else ""
    mk = f' marker-end="url(#{marker})"' if marker else ""
    return (
        f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"'
        f'{da}{mk} opacity="{opacity}"/>'
    )


def txt(
    x,
    y,
    s,
    size=14,
    fill=INK,
    family=SANS,
    weight=400,
    anchor="start",
    ls=0,
    opacity=1,
    italic=False,
):
    st = ' font-style="italic"' if italic else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
        f'letter-spacing="{ls}"{st} opacity="{opacity}">{esc(s)}</text>'
    )


def caps(x, y, s, fill=TEAL, size=11, anchor="start", opacity=1):
    """小型大写字母标签（西文），用来标 FIG. / A / 01 这类技术标记。"""
    return txt(x, y, s, size=size, fill=fill, weight=600, anchor=anchor,
               ls=size * 0.22, opacity=opacity)


def wrap(text: str, max_em: float, cjk: float = 1.0, latin: float = 0.55) -> list[str]:
    """按「显示宽度」折行，而不是按字符数。

    按字符数折行会把「0.84 / 0.90 / 0.96」切成「0.84 / 0.90 / 0」+「96）」，
    把「meta」切成「met」+「a」——中文里混英文的句子几乎必然踩这个坑。
    这里按 em 估宽：汉字 1.0，其余 0.55。
    """
    out: list[str] = []
    cur, width = "", 0.0
    for ch in str(text):
        w = cjk if ("\u4e00" <= ch <= "\u9fff" or ch in "「」，。：；！？（）—…、") else latin
        if width + w > max_em and cur:
            out.append(cur)
            cur, width = ch, w
        else:
            cur += ch
            width += w
    if cur:
        out.append(cur)
    return out


def lines(x, y, rows, size=12.5, fill=INK2, family=SANS, step=None, anchor="start", weight=400):
    step = step or (size + 6)
    out = []
    for i, r in enumerate(rows):
        out.append(txt(x, y + i * step, r, size=size, fill=fill, family=family,
                       anchor=anchor, weight=weight))
    return "".join(out)


def card(x, y, w, h, title, rows=(), note=None, accent=None, fill=CARD,
         stroke=RULE, title_size=13.5, rows_size=11.5, tag=None):
    """一个节点。accent 给左上角一道色条，用来标记它属于哪一层。"""
    out = [rect(x, y, w, h, fill=fill, stroke=stroke, sw=1)]
    if accent:
        out.append(rect(x, y, 2.5, h, fill=accent, stroke="none", rx=2))
    tx = x + 14
    if tag:
        out.append(caps(tx, y + 17, tag, fill=accent or INK3, size=9))
        title_y = y + 36
    else:
        title_y = y + 24
    out.append(txt(tx, title_y, title, size=title_size, fill=INK, weight=600))
    if rows:
        out.append(lines(tx, title_y + 18, rows, size=rows_size, fill=INK2))
    if note:
        out.append(txt(x + w - 12, y + h - 10, note, size=10, fill=INK3, anchor="end"))
    return "".join(out)


def ghost_card(x, y, w, h, title, rows=(), tag=None):
    out = [rect(x, y, w, h, fill="none", stroke=RULE, sw=1, dash="3 4")]
    out.append(txt(x + 14, y + 22, title, size=12.5, fill=INK3, weight=500))
    if rows:
        out.append(lines(x + 14, y + 42, rows, size=11, fill=INK3))
    if tag:
        out.append(caps(x + w - 12, y + 18, tag, fill=INK3, size=9, anchor="end"))
    return "".join(out)


def num_badge(cx, cy, n, color=INK, r=13):
    """圈号。给流程步骤用。"""
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="1.2"/>'
        + txt(cx, cy + 4.5, str(n), size=12.5, fill=color, family=SERIF,
              weight=600, anchor="middle")
    )


def arrow_h(x1, x2, y, color=INK3, label=None, dash=None, both=False):
    """水平箭头。"""
    out = [path(f"M {x1} {y} L {x2} {y}", stroke=color, sw=1.2, dash=dash,
                marker="ah-" + ("red" if color == RED else "ink" if color == INK else "muted"))]
    if both:
        out.append(path(f"M {x2} {y} L {x1} {y}", stroke=color, sw=1.2, dash=dash))
    if label:
        out.append(txt((x1 + x2) / 2, y - 8, label, size=10, fill=color, anchor="middle"))
    return "".join(out)


def arrow_v(x, y1, y2, color=INK3, label=None, dash=None):
    out = [path(f"M {x} {y1} L {x} {y2}", stroke=color, sw=1.2, dash=dash,
                marker="ah-" + ("red" if color == RED else "ink" if color == INK else "muted"))]
    if label:
        out.append(txt(x + 8, (y1 + y2) / 2 + 4, label, size=10, fill=color))
    return "".join(out)


def elbow(x1, y1, x2, y2, color=INK3, dash=None, label=None, mid_y=None):
    """正交折线：先横后竖再横。"""
    my = mid_y if mid_y is not None else (y1 + y2) / 2
    d = f"M {x1} {y1} L {x1} {my} L {x2} {my} L {x2} {y2}"
    out = [path(d, stroke=color, sw=1.2, dash=dash,
                marker="ah-" + ("red" if color == RED else "muted"))]
    if label:
        out.append(txt((x1 + x2) / 2, my - 7, label, size=9.5, fill=color, anchor="middle"))
    return "".join(out)


def curve(x1, y1, x2, y2, color=INK3, dash=None, label=None, bend=0.5):
    """二次贝塞尔，用于跨层的「产物被读取」这类长连接。"""
    cx = x1 + (x2 - x1) * bend
    cy = y1 + (y2 - y1) * 0.5
    d = f"M {x1} {y1} Q {cx} {cy} {x2} {y2}"
    out = [path(d, stroke=color, sw=1.1, dash=dash, marker="ah-muted")]
    if label:
        out.append(txt((x1 + x2) / 2 + 10, (y1 + y2) / 2, label, size=9.5, fill=color, italic=True))
    return "".join(out)


def dot(x, y, r=3, fill=INK3):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"/>'


def defs() -> str:
    markers = []
    for name, color in (("ink", INK), ("muted", INK3), ("red", RED), ("teal", TEAL),
                        ("gold", GOLD)):
        markers.append(
            f'<marker id="ah-{name}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>'
        )
    return (
        "<defs>"
        + "".join(markers)
        + '<filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.9" '
        'numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/>'
        "</filter>"
        + "</defs>"
    )


def plate(body: str, fig_no: str, title: str, subtitle: str, caption: str) -> str:
    """统一版式：角标 + 标题 + 分隔线 + 内容 + 底部说明。"""
    out = [rect(0, 0, W, H, fill=PAPER, stroke="none", rx=0)]
    # 纸张颗粒
    out.append(rect(0, 0, W, H, fill=INK, stroke="none", rx=0, opacity=0.028))
    out.append(
        f'<rect x="0" y="0" width="{W}" height="{H}" filter="url(#grain)" '
        f'opacity="0.05" style="mix-blend-mode:multiply"/>'
    )
    # 四角十字标记（技术图纸感）
    for cx, cy in ((34, 34), (W - 34, 34), (34, H - 34), (W - 34, H - 34)):
        out.append(line(cx - 7, cy, cx + 7, cy, stroke=INK3, sw=0.8, opacity=0.5))
        out.append(line(cx, cy - 7, cx, cy + 7, stroke=INK3, sw=0.8, opacity=0.5))

    out.append(caps(M, 62, f"FIG. {fig_no}", fill=TEAL))
    out.append(
        txt(W - M, 62, "一日一片 / OneADay — v4 架构", size=10.5, fill=INK3,
            anchor="end", ls=1.6)
    )
    out.append(txt(M, 128, title, size=44, fill=INK, family=SERIF, weight=600))
    out.append(txt(M, 168, subtitle, size=15, fill=INK2))
    out.append(line(M, 200, W - M, 200, stroke=INK, sw=1.4, opacity=0.85))

    out.append(body)

    out.append(line(M, H - 74, W - M, H - 74, stroke=RULE, sw=1))
    out.append(txt(M, H - 46, caption, size=11.5, fill=INK3))
    out.append(caps(W - M, H - 46, "ONE A DAY · ARCHITECTURE", fill=INK3, size=9, anchor="end"))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">{defs()}' + "".join(out) + "</svg>"
    )
