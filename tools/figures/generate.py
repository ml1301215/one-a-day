# -*- coding: utf-8 -*-
"""生成 docs/assets/architecture/ 下的 SVG 图。

用法：
    python tools/figures/generate.py                    # 只写 SVG（源文件，入库）
    node tools/figures/render.cjs --format jpeg --quality 92   # SVG → 2x JPG（README 用）

为什么 PNG 换成 JPG：纸张颗粒是 feTurbulence 噪点，PNG 几乎压不动
（3200×2000 单张 2.6–3 MB）；JPEG q92 对这套「大字 + 平色块」的图没有可见损失，
体积降到 1/5。SVG 才是可无限缩放的源文件，JPG 只是给 README 显示用的。

图里的数字全部来自仓库里的真实产物（texture.json / report.md / 测试计数），
改数据就重跑，别手改 SVG。
"""

from __future__ import annotations

import json
from pathlib import Path

from base import (  # type: ignore
    CARD,
    GOLD,
    H,
    INK,
    INK2,
    INK3,
    M,
    PAPER,
    PAPER2,
    RED,
    RULE,
    SANS,
    SERIF,
    TEAL,
    W,
    arrow_h,
    arrow_v,
    caps,
    card,
    curve,
    dot,
    ghost_card,
    line,
    lines,
    num_badge,
    path,
    plate,
    rect,
    txt,
    wrap,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "architecture"

# 真实数字，尽量从产物里读，读不到才用兜底
def _stats() -> dict:
    texture = ROOT / "packages" / "persona" / "brufen" / "texture.json"
    d = {}
    if texture.is_file():
        t = json.loads(texture.read_text(encoding="utf-8"))
        s = t.get("sources") or {}
        d["danmaku"] = s.get("danmaku_total", 19553)
        d["comments"] = s.get("comments", 10972)
        d["videos"] = s.get("videos", 20)
        d["clusters"] = s.get("dictation_clusters", 189)
        d["utter"] = s.get("dictation_utterances", 115)
        d["his"] = (t.get("his_replies") or {}).get("n", 224)
    bank = ROOT / "packages" / "persona" / "brufen" / "shotbank.jsonl"
    if bank.is_file():
        d["shots"] = sum(1 for ln in bank.read_text(encoding="utf-8").splitlines() if ln.strip())
    return d


D = _stats()


# ===========================================================================
# FIG. 01 — 两段时间：离线蒸馏 / 在线生成
# ===========================================================================
def fig01() -> str:
    b = []

    # ---------------- A. 离线 ----------------
    b.append(caps(M, 250, "A · 离线蒸馏", fill=TEAL))
    b.append(txt(M + 132, 250, "一次性，可复跑；产物入库", size=11, fill=INK3))
    b.append(
        txt(W - M, 250, "python tools/distill/run.py --all", size=10.5, fill=INK3,
            anchor="end", ls=0.6)
    )

    y = 282
    h = 96
    b.append(card(72, y, 246, h, "原始语料",
                  [f"{D.get('danmaku', 0):,} 条带时间轴弹幕",
                   f"{D.get('comments', 0):,} 条评论 · {D.get('videos', 20)} 个标题",
                   "3 份英文机翻字幕（只量结构）"],
                  accent=TEAL, tag=".work /"))
    b.append(arrow_h(330, 372, y + h / 2, color=INK3))
    b.append(card(372, y, 150, h, "清洗",
                  ["去 @ / 链接 / 纯情绪", "8,090 条进聚簇"],
                  accent=TEAL))
    b.append(arrow_h(534, 564, y + h / 2, color=INK3))
    b.append(card(564, y, 176, h, "时间轴聚簇",
                  [f"{D.get('clusters')} 簇 → {D.get('utter')} 条转写", "低置信：多为观众接梗"],
                  accent=TEAL))
    b.append(arrow_h(752, 782, y + h / 2, color=INK3))
    b.append(card(782, y, 150, h, "统计画像",
                  ["分位数 · 标点率", "连接词剂量 · 人称"],
                  accent=TEAL))

    # 产物：深色卡，作为整张图的视觉锚点
    ax, aw = 972, 214
    b.append(arrow_h(944, ax - 4, y + h / 2, color=INK, label="产物"))
    arts = [
        ("texture.json", "统计画像 · 每条带 n"),
        ("shotbank.jsonl", f"{D.get('shots', 55)} 条原创样本"),
        ("report.md", "29 条结论的对账表"),
    ]
    for i, (name, sub) in enumerate(arts):
        yy = y + i * 50
        b.append(rect(ax, yy, aw, 42, fill=INK, stroke=INK, rx=3))
        b.append(txt(ax + 14, yy + 19, name, size=13, fill=PAPER, weight=600))
        b.append(txt(ax + 14, yy + 33, sub, size=9.5, fill="#BFB49E"))

    # 他本人评论这条支线：整张图最值钱的一条数据
    b.append(
        path(f"M 195 {y + h} L 195 {y + h + 22}", stroke=GOLD, sw=1.1, dash="3 3",
             marker="ah-gold")
    )
    b.append(card(72, y + h + 26, 246, 52, f"他本人 {D.get('his', 224)} 条评论",
                  ["旧文档断言「0 次」——错的"], accent=GOLD, tag="HIS_REPLIES",
                  title_size=13, rows_size=11))

    # 右下留白处放对账结果，避免空
    bx, by = 1216, y
    b.append(txt(bx, by + 4, "旧文档 29 条结论，复算结果", size=11.5, fill=INK2, weight=500))
    ledger = [("一致", 12, TEAL), ("不一致", 13, RED), ("方法伪影", 3, GOLD), ("无法判定", 1, INK3)]
    total = sum(n for _, n, _ in ledger)
    barx, barw = bx, 312
    cursor = barx
    for name, n, col in ledger:
        wseg = barw * n / total - 2
        b.append(rect(cursor, by + 16, wseg, 10, fill=col, stroke="none", rx=1))
        cursor += wseg + 2
    ly = by + 48
    for i, (name, n, col) in enumerate(ledger):
        colx = barx + (i % 2) * 160
        rowy = ly + (i // 2) * 24
        b.append(dot(colx + 3, rowy - 4, r=3, fill=col))
        b.append(txt(colx + 13, rowy, name, size=11, fill=INK2))
        b.append(txt(colx + 140, rowy, str(n), size=12, fill=INK, family=SERIF,
                     weight=600, anchor="end"))

    # ---------------- 分界 ----------------
    b.append(line(M, 560, W - M, 560, stroke=RULE, sw=1, dash="4 5"))
    b.append(rect(W / 2 - 118, 548, 236, 24, fill=PAPER, stroke="none", rx=2))
    b.append(caps(W / 2, 565, "PRODUCT  ⇒  RUNTIME", fill=INK3, size=9.5, anchor="middle"))
    b.append(txt(W / 2 + 128, 565, "产物被运行时读取，跨两个时间尺度", size=10.5, fill=INK3))

    # ---------------- B. 在线 ----------------
    by2 = 604
    b.append(caps(M, by2, "B · 在线生成", fill=RED))
    b.append(txt(M + 108, by2, "一次请求 ≈ 0.8–1.4s（3 条候选并行，不是 3 倍延迟）",
                 size=11, fill=INK3))

    ny = 646
    nh = 104
    steps = [
        (72, 138, "apps/web", ["Next.js 15", "SSE 打字机"], RED),
        (238, 148, "routes/chat", ["限流 · 会话", "落库 · meta 先行"], RED),
        (414, 158, "voice/retrieval", ["按话题取 4 条", "样本，不是固定三条"], TEAL),
        (600, 150, "prompt/builder", ["人格 × 纹理", "× 检索样本"], TEAL),
        (778, 150, "llm ×3", ["同轮 3 条候选", "温度微抖"], TEAL),
        (956, 150, "voice/scorer", ["按分布 + 惩罚项", "挑 1 条"], TEAL),
        (1134, 150, "quality", ["只拦格式", "emoji/列表/照抄"], TEAL),
        (1312, 216, "SSE 回放", ["逐字 2 字 / 12ms", "→ 浏览器打字机"], RED),
    ]
    for i, (x, w, name, rows, col) in enumerate(steps):
        b.append(num_badge(x + 8, ny - 22, i + 1, color=col, r=11))
        b.append(card(x, ny, w, nh, name, rows, accent=col))

    for i in range(len(steps) - 1):
        x1 = steps[i][0] + steps[i][1]
        x2 = steps[i + 1][0]
        b.append(arrow_h(x1 + 3, x2 - 3, ny + nh / 2, color=INK3))

    # 跨时间尺度的两条读取路径：走两条不同的正交走廊，不交叉、标签不打架
    b.append(
        path(
            f"M 1079 324 L 1079 496 L 681 496 L 681 {ny - 4}",
            stroke=TEAL,
            sw=1.1,
            dash="4 4",
            marker="ah-teal",
        )
    )
    b.append(
        txt(690, 490, "纹理 → 进 prompt，也进打分器", size=9.5, fill=TEAL)
    )
    b.append(
        path(
            f"M 1186 374 L 1186 532 L 494 532 L 494 {ny - 4}",
            stroke=TEAL,
            sw=1.1,
            dash="4 4",
            marker="ah-teal",
        )
    )
    b.append(
        txt(503, 526, "样本 → 只进检索", size=9.5, fill=TEAL)
    )

    # 中段留白处放一句判断：这张图想说的话
    qx = 372
    b.append(rect(qx, 404, 2, 66, fill=RED, stroke="none", rx=1))
    b.append(
        txt(qx + 16, 434, "「规则能约束的边界，比真实说话窄。」", size=21, fill=INK,
            family=SERIF, weight=500)
    )
    b.append(
        lines(qx + 16, 460, [
            "v3：25 段规则 / 9,358 字 prompt，每条回复长成同一个形状。",
            "v4：6,515 字 + 3 条候选 + 一份从语料量出来的分布。",
        ], size=11.5, fill=INK3, step=18)
    )

    # 开关
    sy = 800
    b.append(line(M, sy - 14, W - M, sy - 14, stroke=RULE, sw=1, opacity=0.8))
    b.append(caps(M, sy + 4, "开关", fill=INK3, size=9.5))
    pills = [
        ("OAD_SAFETY", "off", "guard 一键回滚", RED),
        ("OAD_CANDIDATES", "3", "1 = 关掉重排", TEAL),
        ("OAD_STYLE_GATE", "format", "strict = v2 行为", INK3),
        ("OAD_PROVIDER", "mock", "deepseek / ollama", INK3),
    ]
    px = M + 54
    for name, val, note, col in pills:
        wpx = 236
        b.append(rect(px, sy - 14, wpx, 48, fill=CARD, stroke=RULE, rx=24))
        b.append(dot(px + 16, sy + 10, r=3, fill=col))
        b.append(txt(px + 27, sy + 14, name, size=9.5, fill=INK3, ls=0.8))
        b.append(txt(px + 27, sy + 30, val, size=13, fill=INK, weight=600, family=SANS))
        b.append(txt(px + wpx - 14, sy + 22, note, size=9.5, fill=INK3, anchor="end"))
        px += wpx + 16

    return plate(
        "".join(b),
        "01",
        "一条请求，两段时间",
        "语料在离线被量成产物；请求在线上被检索、生成、重排。蓝色是「被量出来的」，朱红是「一次请求」。",
        "读法：上排一年跑几次，下排一秒跑一次。中间那道虚线是全世界唯一被两边共享的东西——texture.json。",
    )


# ===========================================================================
# FIG. 02 — 一轮请求的七拍，以及那条重试通道
# ===========================================================================
def fig02() -> str:
    b = []
    steps = [
        ("限流", "device 级滑动窗口；429 就直接回，不进模型", RED),
        ("检索样本", "voice/retrieval：按用户原话判话题，取 4 条；上一条已自述 → 整批撤掉讲自己的样本", TEAL),
        ("编译 prompt", "人格 × 纹理 × 样本；同一轮 N 条候选各带一句不同的「形状指令」", TEAL),
        ("并行生成", "provider ×3，温度轻微抖动（0.84 / 0.90 / 0.96）", TEAL),
        ("打分重排", "voice/scorer：形状拟合 + 惩罚项，挑 1 条；全出局时按「错误最轻」退回", TEAL),
        ("格式闸门", "只拦 emoji / Markdown / 分点列表 / 空回复 / 助手腔 / 照抄", TEAL),
        ("落库回放", "记消息与梗指纹 → 按 2 字 / 12ms 回放成 SSE；meta 在生成之前就先发", RED),
    ]
    y0, gap = 272, 80
    b.append(line(96, y0 - 10, 96, y0 + gap * (len(steps) - 1) + 10, stroke=RULE, sw=1))
    for i, (title, detail, col) in enumerate(steps):
        y = y0 + i * gap
        b.append(num_badge(96, y, i + 1, color=col, r=13))
        b.append(txt(124, y + 5, title, size=15.5, fill=INK, weight=600))
        b.append(lines(124, y + 25, wrap(detail, 42), size=11, fill=INK3, step=15))
        if i < len(steps) - 1:
            b.append(line(124, y + gap - 22, 632, y + gap - 22, stroke=RULE, sw=1, opacity=0.55))

    # 左下：代价写清楚——3 候选不是免费的
    b.append(line(M, 812, 632, 812, stroke=RULE, sw=1))
    b.append(caps(M, 840, "COST", fill=RED))
    b.append(txt(M + 58, 840, "每轮 3 次模型调用；触发重试时 6 次。这就是 OAD_CANDIDATES 默认 3、不是 5 的原因。",
                 size=11, fill=INK3))

    # 右上：候选重排
    px, pw = 744, 784
    b.append(caps(px, 268, "RERANK", fill=TEAL))
    b.append(txt(px + 86, 268, "同轮 3 条候选，只发 1 条", size=13, fill=INK2))
    b.append(rect(px, 288, pw, 186, fill=CARD, stroke=RULE, rx=3))
    rows = [
        ("★ 挑中", 4.25, True),
        ("候选", 2.37, False),
        ("候选", None, False),
    ]
    for i, (tag, sc, picked) in enumerate(rows):
        ry = 316 + i * 52
        if picked:
            b.append(rect(px + 1, ry - 24, pw - 2, 44, fill=INK, stroke="none", rx=2))
        fg = PAPER if picked else INK
        fg2 = "#BFB49E" if picked else INK3
        b.append(txt(px + 18, ry - 2, tag, size=10, fill=fg2, ls=1.2))
        b.append(txt(px + 18, ry + 14, f"第 {i + 1} 条", size=13, fill=fg, weight=600))
        bx = px + 150
        bw = 470
        if sc is None:
            b.append(txt(bx, ry + 6, "出局：照抄示例（改一两个字也抓得到）", size=11, fill=RED if not picked else "#F0A79E"))
        else:
            b.append(rect(bx, ry - 2, bw * sc / 5.0, 12, fill=("none" if picked else RULE),
                          stroke=(fg if picked else "none"), sw=1, rx=1))
            if picked:
                b.append(rect(bx + 1.5, ry - 0.5, bw * sc / 5.0 - 3, 9, fill=PAPER, stroke="none", rx=1))
            b.append(txt(px + pw - 18, ry + 6, f"{sc:+.2f}", size=17, fill=fg,
                         family=SERIF, weight=600, anchor="end"))
    b.append(txt(px + 18, 462, "分数 = 离真实分布的拟合 + 惩罚项；绝对值没有意义，只作同轮比较。",
                 size=10.5, fill=INK3))

    # 右中：出局项
    by = 512
    b.append(caps(px, by, "REJECT", fill=RED))
    b.append(txt(px + 76, by, "命中即弃，不参与边际比较", size=13, fill=INK2))
    rejects = [
        "解释自己的梗（「一个音」「念起来像」）",
        "连着两条讲自己（单条不算，抢戏才算）",
        "照抄示例：最长公共子串 ≥ 12 字且覆盖 ≥ 70%",
        "用户说一整件事，只回一句（< 12 字）",
        "emoji / Markdown / 分点列表 / 助手腔",
    ]
    for i, r in enumerate(rejects):
        ry = by + 30 + i * 30
        b.append(txt(px + 4, ry, "×", size=15, fill=RED, weight=700))
        b.append(txt(px + 24, ry, r, size=12, fill=INK2))

    # 右下：重试通道（这张图的重点）
    ty = 700
    b.append(rect(px, ty, pw, 176, fill="none", stroke=RED, sw=1.2, dash="5 4", rx=3))
    b.append(caps(px + 18, ty + 24, "RETRY", fill=RED))
    b.append(txt(px + 78, ty + 24, "三条候选全在抄样本 —— 问题不在候选，在样本", size=13, fill=INK))
    boxes = [
        (px + 18, "3 条候选", "全判「照抄示例」"),
        (px + 262, "要不要再写一条规则？", "不。规则打不过样本"),
        (px + 526, "撤掉样本，再要一轮", "同一轮里只重试一次"),
    ]
    for i, (bx, t1, t2) in enumerate(boxes):
        b.append(rect(bx, ty + 44, 218, 56, fill=CARD, stroke=RULE, rx=2))
        b.append(txt(bx + 12, ty + 66, t1, size=12, fill=INK, weight=600))
        b.append(txt(bx + 12, ty + 84, t2, size=10, fill=INK3))
        if i < 2:
            b.append(arrow_h(bx + 220, bx + 258, ty + 72, color=RED, dash="4 3"))
    b.append(txt(px + 18, ty + 126, "实测触发场景：辞职、催婚、大家庭——越对题的样本，越容易被当成台词背下来。",
                 size=10.5, fill=INK3))
    b.append(txt(px + 18, ty + 148, "这条通道只对「抄」生效，不对「写得不好」生效——后者交给下一轮对话，不交给重写。",
                 size=10.5, fill=INK3))

    return plate(
        "".join(b),
        "02",
        "一轮请求的七拍",
        "真正让行为变的是第五拍和那条重试通道——prompt 改十次，不如在这里换一条。",
        "朱红的两拍是「一定要做」的边界动作（限流、回放）；青色的五拍是「可以更好」的部分。中间那条虚线框是实测逼出来的补丁。",
    )


# ===========================================================================
# FIG. 03 — 三层机制
# ===========================================================================
def fig03() -> str:
    b = []

    # 顶部：四组 v3 → v4
    stats = [
        ("PROMPT", "9,358 → 6,515", "字（−30%）", TEAL),
        ("SAMPLES", "3 → 55", "条，按话题检索", TEAL),
        ("CANDIDATES", "1 → 3", "同轮生成后重排", RED),
        ("LENGTH", "手写 → 双源", "语料 + 设计标尺", GOLD),
    ]
    for i, (label, value, note, col) in enumerate(stats):
        x = 72 + i * 366
        b.append(rect(x, 236, 2, 70, fill=col, stroke="none", rx=1))
        b.append(caps(x + 16, 252, label, fill=INK3, size=9))
        b.append(txt(x + 16, 286, value, size=30, fill=INK, family=SERIF, weight=600))
        b.append(txt(x + 16, 306, note, size=11, fill=INK3))
    b.append(line(M, 336, W - M, 336, stroke=RULE, sw=1))

    cols = [
        (
            "01",
            "TEXTURE",
            "纹理",
            "把「他说话多长、多碎」量成数字",
            [
                "从 19,553 条弹幕 + 10,972 条评论里算分位数、",
                "标点率、连接词剂量、第一人称密度。",
                "编译期压成 prompt 里的一小段【数据画像】，",
                "同时给打分器当尺子。",
            ],
            "替代的是：更好的形容词。",
            TEAL,
        ),
        (
            "02",
            "RETRIEVAL",
            "样本",
            "让示例对题，而不是每轮同样三条",
            [
                "55 条原创样本按「话题 × 形态」打标签。",
                "按用户原话判话题，取 4 条；",
                "上一条已讲自己 → 把「讲自己」的样本整批撤掉。",
                "三条一样长是最容易被识破的模板，检索要保住参差。",
            ],
            "替代的是：固定给同样的三条。",
            TEAL,
        ),
        (
            "03",
            "RERANK",
            "选样",
            "生成之后挑，而不是生成之前求",
            [
                "同轮 3 条候选，按「离真实分布多远 + 有没有踩旧坑」挑 1 条。",
                "每个惩罚项都对应一次真实踩过的坑，注释里写了出处。",
                "全出局时按「错误最轻」退回，不重写——",
                "重写会把「有点糙但像人」改成「通顺但不像人」。",
            ],
            "替代的是：再写一条规则。",
            RED,
        ),
    ]
    for i, (no, en, cn, sub, body, replace, col) in enumerate(cols):
        x = 72 + i * 480
        w = 440
        # 大号底纹数字
        b.append(txt(x + 250, 470, no, size=150, fill=col, family=SERIF, weight=700, opacity=0.07))
        b.append(caps(x, 384, en, fill=col, size=9.5))
        b.append(txt(x, 424, cn, size=32, fill=INK, family=SERIF, weight=600))
        b.append(line(x, 444, x + 46, 444, stroke=col, sw=2))
        b.append(txt(x, 476, sub, size=13, fill=INK2))
        b.append(lines(x, 512, body, size=11.8, fill=INK3, step=21))
        b.append(line(x, 618, x + w - 42, 618, stroke=RULE, sw=1, dash="3 4"))
        b.append(txt(x, 646, replace, size=11.5, fill=col, weight=500))
        b.append(txt(x, 668, "（不是变好，是换了个位置使劲）", size=10, fill=INK3))

    # 底部：三层各自的降级路径（都能单独关掉）
    b.append(caps(M, 700, "DEGRADE", fill=INK3))
    b.append(txt(M + 92, 700, "三层都能单独关掉，退了会发生什么", size=12, fill=INK2))
    degr = [
        ("texture.json 缺失", "回落到 persona.voice.reply_band（手写标尺），分数里标注 design"),
        ("shotbank.jsonl 缺失", "只用 few_shots 13 条，检索退化成按长度挑"),
        ("OAD_CANDIDATES = 1", "退回单条生成，重排失效；mock 默认就是 1"),
    ]
    for i, (k, v) in enumerate(degr):
        x = 72 + i * 480
        b.append(rect(x, 712, 440, 62, fill="none", stroke=RULE, sw=1, dash="3 4", rx=2))
        b.append(txt(x + 14, 734, k, size=12, fill=INK2, weight=500))
        b.append(lines(x + 14, 754, wrap(v, 40), size=10, fill=INK3, step=14))

    # 底部：分工
    b.append(line(M, 792, W - M, 792, stroke=INK, sw=1.2))
    b.append(txt(M, 832, "一句话：判断层留在 prompt，纹理进数字，样本进检索，风格进选样。",
                 size=19, fill=INK, family=SERIF, weight=500))
    b.append(
        txt(M, 860, "这四件事以前全挤在 9,358 字的 prompt 里，互相稀释；现在分开之后，每一层都能单独被反驳、单独被回滚。",
            size=12, fill=INK3)
    )

    return plate(
        "".join(b),
        "03",
        "把「像人」拆成三层能单独回滚的东西",
        "同一个目标换了个位置使劲：从「写得更细」改成「量得更准、挑得更狠」。",
        "读法：三层从上往下是「量 → 取 → 挑」。任何一层都能单独关掉，所以改版时能定位到具体是哪一层出了问题，而不是笼统的「不够像」。",
    )


# ===========================================================================
# FIG. 04 — 语料与边界
# ===========================================================================
def fig04() -> str:
    b = []

    # 轨道一：弹幕
    b.append(caps(M, 252, "TRACK A · 弹幕", fill=TEAL))
    b.append(txt(M + 132, 252, "观众边听边打，是二手材料", size=11, fill=INK3))
    ax = [
        ("19,553", "条带时间轴弹幕", "20 个投稿"),
        ("8,090", "条进入聚簇", "去掉 9,896 条纯情绪"),
        ("189", "个多片段时间簇", "时间差 ≤ 2.5s + 语义桥"),
        ("115", "条转写 utterance", "抽检 10 条只有 2–3 条像人话"),
    ]
    for i, (num, label, note) in enumerate(ax):
        x = 72 + i * 226
        b.append(rect(x, 272, 206, 104, fill=CARD, stroke=RULE, rx=3))
        b.append(txt(x + 16, 320, num, size=30, fill=INK, family=SERIF, weight=600))
        b.append(txt(x + 16, 342, label, size=11, fill=INK2))
        b.append(txt(x + 16, 360, note, size=9.5, fill=INK3))
        if i < 3:
            b.append(arrow_h(x + 208, x + 224, 324, color=INK3))

    # 轨道二：评论 + 一手材料
    b.append(caps(M, 432, "TRACK B · 评论", fill=GOLD))
    b.append(txt(M + 132, 432, "10,972 条里，有 223 条是他自己敲的", size=11, fill=INK3))
    b.append(rect(72, 452, 206, 104, fill=CARD, stroke=RULE, rx=3))
    b.append(txt(88, 500, "10,972", size=30, fill=INK, family=SERIF, weight=600))
    b.append(txt(88, 522, "条评论（主 + 二级）", size=11, fill=INK2))
    b.append(txt(88, 540, "已全量翻过一遍，不是抽样", size=9.5, fill=INK3))
    b.append(arrow_h(280, 300, 504, color=GOLD))
    b.append(rect(302, 450, 360, 112, fill=INK, stroke=INK, rx=3))
    b.append(txt(318, 496, "223", size=34, fill=PAPER, family=SERIF, weight=600))
    b.append(txt(386, 496, "条他本人敲的中文", size=12.5, fill=PAPER, weight=600))
    b.append(txt(318, 520, "本机唯一的一手材料：不经「观众转写」损耗", size=10, fill="#BFB49E"))
    b.append(txt(318, 540, f"中位 9 字 · 91% 不带句末标点 → 只量语体，不量长度", size=10, fill="#BFB49E"))

    # 被推翻的那句旧结论：划掉它，是这张图最想说的话
    fx = 700
    b.append(caps(fx, 476, "FALSIFIED", fill=RED))
    b.append(txt(fx, 510, "「10972 条评论里，他本人出现 0 次」", size=16, fill=INK3,
                 family=SERIF))
    b.append(line(fx - 2, 505, fx + 322, 505, stroke=RED, sw=1.6))
    b.append(txt(fx, 536, "这句话曾被人格包当成行为依据（「他不回评论，所以不追问后续」）。", size=10.5, fill=INK3))
    b.append(txt(fx, 554, "复算后：223 条。结论的方向没错，但理由一直是错的。", size=10.5, fill=INK3))

    # 右侧：产物
    b.append(caps(1008, 252, "ARTIFACTS", fill=INK3))
    arts = [
        ("texture.json", "统计画像 · 每个数字带 n 与置信"),
        ("shotbank.jsonl", "55 条原创样本 · 话题 × 形态"),
        ("report.md", "29 条结论对账 · 含「最可能错在哪」"),
    ]
    for i, (name, note) in enumerate(arts):
        yy = 272 + i * 62
        b.append(rect(1008, yy, 520, 52, fill=CARD, stroke=INK, sw=1, rx=3))
        b.append(txt(1026, yy + 22, name, size=14, fill=INK, weight=600))
        b.append(txt(1026, yy + 40, note, size=10, fill=INK3))
    b.append(
        txt(1008, 480, "产物入库，缓存不入库；改数据就重跑，别手改。", size=10, fill=INK3)
    )

    # 分隔
    b.append(line(M, 588, W - M, 588, stroke=INK, sw=1.2))

    # 拿到 / 没拿到
    b.append(txt(M, 626, "拿到了什么", size=13, fill=TEAL, weight=600))
    got = [
        "19,553 条带时间轴弹幕（11 支被抽样到 1200 条）",
        "10,972 条评论（含 223 条他本人的）",
        "20 个投稿标题 · 7 个合集名",
        "3 份英文机器翻译字幕（只量结构，不回译）",
    ]
    b.append(lines(M, 652, ["· " + g for g in got], size=11.5, fill=INK2, step=20))

    b.append(txt(M + 520, 626, "没拿到什么（以及为什么）", size=13, fill=RED, weight=600))
    lost = [
        ("中文逐字稿", "20 个投稿里 17 个没有字幕，3 个只有 ai-en"),
        ("音轨 / 自建 ASR", "下载被运行环境拦下（HTTP 567），未绕过"),
        ("简介 · 动态 · 专栏", "简介全空，动态只有自动转发"),
        ("45 个标题的完整清单", "本机只能解析到 20 个"),
    ]
    for i, (k, v) in enumerate(lost):
        yy = 652 + i * 20
        b.append(txt(M + 520, yy, "×", size=12, fill=RED))
        b.append(txt(M + 538, yy, k, size=11.5, fill=INK2, weight=500))
        b.append(txt(M + 538 + 132, yy, v, size=11, fill=INK3))

    # 对账条
    b.append(txt(M, 768, "旧文档 29 条结论，逐条复算", size=12, fill=INK2, weight=500))
    ledger = [("一致", 12, TEAL), ("不一致", 13, RED), ("方法伪影", 3, GOLD), ("无法判定", 1, INK3)]
    total = sum(n for _, n, _ in ledger)
    cursor = M
    for name, n, col in ledger:
        wseg = 1120 * n / total - 3
        b.append(rect(cursor, 780, wseg, 12, fill=col, stroke="none", rx=1))
        cursor += wseg + 3
    lx = M
    for name, n, col in ledger:
        b.append(dot(lx + 3, 818, r=3, fill=col))
        b.append(txt(lx + 13, 822, name, size=11, fill=INK2))
        b.append(txt(lx + 13, 840, str(n), size=14, fill=INK, family=SERIF, weight=600))
        lx += 180

    return plate(
        "".join(b),
        "04",
        "语料、边界，和一次诚实对账",
        "没有中文逐字稿，所以这份人格是「观众实际感知到的他」；每个数字都标了 n，可以被推翻。",
        "最值钱的不是那 19,553 条弹幕，而是 223 条他自己敲的字——以及那份写清「这套方法最可能错在哪」的报告。",
    )


# ===========================================================================
# FIG. 05 — 安全分级退役
# ===========================================================================
def fig05() -> str:
    b = []

    def panel(x, w, title, caps_label, col):
        b.append(rect(x, 236, w, 456, fill="none", stroke=RULE, sw=1, rx=3))
        b.append(caps(x + 20, 264, caps_label, fill=col, size=9.5))
        b.append(txt(x + 20, 300, title, size=22, fill=INK, family=SERIF, weight=600))

    def box(x, y, w, h, t1, t2=None, dark=False):
        b.append(rect(x, y, w, h, fill=(INK if dark else CARD), stroke=(INK if dark else RULE), rx=2))
        fg1 = PAPER if dark else INK
        fg2 = "#BFB49E" if dark else INK3
        b.append(txt(x + 12, y + (22 if t2 else h / 2 + 5), t1, size=12.5, fill=fg1, weight=600))
        if t2:
            b.append(txt(x + 12, y + 40, t2, size=10, fill=fg2))

    # ---- 左：v3 ----
    panel(72, 628, "识别到危险，就换一个人", "V3 · 分级 + 模板", RED)
    box(92, 340, 264, 46, "用户输入")
    b.append(arrow_v(224, 388, 410, color=INK3))
    box(92, 412, 264, 62, "safety.classify()", "正则扫四类：危机 / 医疗 / 法律 / 未成年")
    # L3 分支
    b.append(path("M 356 443 L 404 443", stroke=RED, sw=1.1, dash="4 3", marker="ah-red"))
    box(408, 412, 268, 62, "写死的模板", "不交给模型；每次都逐字相同", dark=True)
    b.append(path("M 224 474 L 224 504", stroke=INK3, sw=1.1, marker="ah-muted"))
    box(92, 508, 264, 62, "humor_scale → 0", "few_shots 整批撤掉，只剩规则")
    b.append(path("M 224 570 L 224 600", stroke=INK3, sw=1.1, marker="ah-muted"))
    box(92, 604, 264, 46, "模型生成", "通顺、正确、不像人")
    b.append(
        txt(92, 660, "问题不是模板写得差，是那次切换本身：", size=10.5, fill=INK3)
    )
    b.append(
        txt(92, 676, "用户听到的是「系统识别到我危险了」，不是「这个人认真了」。", size=10.5, fill=INK3)
    )

    # ---- 中：退役 ----
    b.append(arrow_h(704, 892, 470, color=RED, dash="5 4"))
    b.append(txt(798, 458, "退役", size=20, fill=RED, family=SERIF, weight=600, anchor="middle"))
    b.append(txt(798, 494, "OAD_SAFETY = off", size=9.5, fill=INK3, anchor="middle", ls=0.5))

    # ---- 右：v4 ----
    panel(900, 628, "同一个人，把语气拨到最认真", "V4 · 性格承接（默认）", TEAL)
    box(920, 340, 264, 46, "用户输入")
    b.append(arrow_v(1052, 388, 410, color=INK3))
    box(920, 412, 264, 62, "voice/retrieval", "seriousness 的样本被正常检索到")
    b.append(arrow_v(1052, 474, 504, color=INK3))
    box(920, 508, 264, 62, "3 条候选 → 重排", "没有特殊分支，没有模板")
    b.append(arrow_v(1052, 570, 600, color=INK3))
    box(920, 604, 264, 46, "模型生成", "长句、带号码、像一个人")
    # 右侧说明
    b.append(rect(1204, 412, 300, 158, fill="none", stroke=TEAL, sw=1, dash="4 4", rx=2))
    b.append(caps(1220, 434, "SERIOUSNESS", fill=TEAL, size=8.5))
    b.append(lines(1220, 456, [
        "收掉玩笑，不是换成播音腔",
        "句子变短、变慢，说一句真话",
        "给一件今晚就能做的事",
        "身边具体的人优先，其次 12356",
        "说完就停，不追问「现在好点了吗」",
    ], size=10, fill=INK3, step=17))
    b.append(
        txt(920, 660, "还是那一套人格，只是把温度拨到最高——", size=10.5, fill=INK3)
    )
    b.append(
        txt(920, 676, "所以他认真起来，用的是「给动作」的方式，不是「给安慰」。", size=10.5, fill=INK3)
    )

    # ---- 底部：代价清单 ----
    b.append(line(M, 712, W - M, 712, stroke=INK, sw=1.2))
    b.append(caps(M, 740, "COST", fill=RED))
    b.append(txt(M + 52, 740, "撤掉之后失去了什么（诚实清单，写在 docs/safety.md §3）", size=12, fill=INK2))
    costs = [
        ("确定性", "危机措辞变成模型生成，可能偏离、可能漏掉热线", "缓解：样本里写死 12356 与急诊"),
        ("可审计", "出事故时不能指着某一行正则说「这里漏了」", "缓解：guard 模式可整条链路打开做对照"),
        ("硬拦截", "医疗 / 法律 / 未成年人不再有机器闸门", "缓解：人格里写着不提供，但没有强制执行"),
    ]
    for i, (k, v, m) in enumerate(costs):
        x = 72 + i * 300
        b.append(rect(x, 758, 282, 88, fill="none", stroke=RULE, sw=1, rx=2))
        b.append(txt(x + 14, 782, "×", size=14, fill=RED, weight=700))
        b.append(txt(x + 32, 782, k, size=13, fill=INK, weight=600))
        b.append(lines(x + 32, 802, wrap(v, 24), size=10, fill=INK3, step=14))
        b.append(lines(x + 32, 830, wrap(m, 26), size=9.5, fill=TEAL, step=13))

    # ---- 回滚说明 ----
    b.append(rect(1008, 758, 520, 88, fill=INK, stroke=INK, rx=2))
    b.append(caps(1024, 782, "ROLLBACK", fill="#BFB49E", size=8.5))
    b.append(txt(1024, 806, "OAD_SAFETY=guard", size=17, fill=PAPER, weight=600))
    b.append(txt(1024, 830, "行为与 v3 完全一致；代码与测试一行没删。", size=10, fill="#BFB49E"))

    return plate(
        "".join(b),
        "05",
        "安全分级退役",
        "v3 在识别到危险时换一个人；v4 让同一个人把语气拨到最认真。",
        "这不是更安全，是更连贯。代价写在这里，回滚开关也写在这里——「不要安全」和「没想过代价」是两件事。",
    )


FIGS = {
    "fig01-architecture": fig01,
    "fig02-generation": fig02,
    "fig03-three-layers": fig03,
    "fig04-corpus": fig04,
    "fig05-posture": fig05,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in FIGS.items():
        p = OUT / f"{name}.svg"
        p.write_text(fn(), encoding="utf-8")
        print("wrote", p.relative_to(ROOT))


if __name__ == "__main__":
    main()
