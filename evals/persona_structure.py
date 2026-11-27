#!/usr/bin/env python
"""结构距离：测「有没有他的引擎」，而不是「长度像不像」。

为什么需要这个脚本
------------------
`evals/persona_fidelity.py` 测形状（长度、句数、标点），它能证明「像人」，
证明不了「像他」。实测发现一个更糟的情况：形状指标全绿的同时，
结构在收敛成一条新流水线——

    复述一个词 → 对这个词发表评论 → 接一段自己的经历

而他的引擎是：

    抓一个字 → 换音（不说出来）→ 用「所以」推两步 → 落点砸自己

前者是**评价**一个词，后者是**用掉**一个词。差一个字，是两个工种。

这个脚本把这三件事变成数字：
  1. 引擎率：有多少条真的发生了字音替换 / 伪因果推进；
  2. 模板率：有多少条是同一条「复述→点评→自述」的流水线；
  3. 锚点复制率：有多少条其实是人格包 few_shots 的换词版
     （现有照抄检测门槛是 ≥12 字且 ≥70%，模型改一个名词就绕过去了）。

跑法：
    python evals/persona_structure.py                  # 读现有 fidelity_report.json
    python evals/persona_fidelity.py --provider deepseek && python evals/persona_structure.py

注意：探针只有 24 条，这些比例是方向指示，不是精确测量。
但 8% vs 75% 这种差距不是噪声。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("OAD_DB_PATH", str(ROOT / "services" / "api" / "data" / "evals.db"))

from app import quality  # noqa: E402
from app.config import settings  # noqa: E402
from app.persona.loader import get_persona  # noqa: E402

REPORT = ROOT / "evals" / "fidelity_report.json"
OUT = ROOT / "evals" / "structure_report.json"

# ---- 引擎：字音替换 / 伪因果推进 -------------------------------------------
# 只用「判据」不用词表：谐音不能被正则穷举，但「换音之后的那句话」有形状——
# 一个短句里出现两个同音/近音词，且中间没有解释性连接词。
PUN_CHAIN = re.compile(
    r"(属名|待停|下海|上岸|白说|扯蛋|南凉|新牲|卷全球|两眼一黑|谐音|一个音)"
)
# 伪因果推进：出现「所以/于是/这就」并且后面接一个自我指向的落点
SO_CAUSAL = re.compile(r"(所以|于是|这就)[^。！？\n]{0,40}")
SELF_LAND = re.compile(r"(所以|于是|这就)[^。！？\n]{0,20}(我|咱)")

# ---- 结构模板：复述 → 点评这个词 → 自述 -------------------------------------
ECHO_OPEN = re.compile(r"^[^。！？\n]{2,8}。")
COMMENT_WORD = re.compile(r"(这|那)(词|话|字)")
SELF_STORY = re.compile(
    r"(我上回|我上次|我以前|我去年|我那个|我妈也|我周末也|我也|我那次|我上个月|我抽屉|我工位|我上一份)"
)


def _norm(s: str) -> str:
    return quality._norm_for_copy(s)


def structure_stats(replies: list[str], persona) -> dict:
    n = len(replies)
    if not n:
        return {}

    anchors: list[str] = []
    for shot in persona.raw.get("few_shots") or []:
        for ln in str(shot.get("reply", "")).split("\n"):
            norm = _norm(ln)
            if len(norm) >= 8:
                anchors.append(norm)

    engine_pun = sum(1 for t in replies if PUN_CHAIN.search(t))
    engine_causal = sum(1 for t in replies if SO_CAUSAL.search(t))
    engine_land = sum(1 for t in replies if SELF_LAND.search(t))
    echo = sum(1 for t in replies if ECHO_OPEN.match(t))
    comment_word = sum(1 for t in replies if COMMENT_WORD.search(t))
    self_story = sum(1 for t in replies if quality.self_story(t))
    pipeline = sum(
        1
        for t in replies
        if ECHO_OPEN.match(t) and (COMMENT_WORD.search(t) or SELF_STORY.search(t))
    )

    copies, near = 0, 0
    for t in replies:
        nt = _norm(t)
        best = 0.0
        for a in anchors:
            best = max(best, quality.longest_common_substring(a, nt) / len(a))
        if best >= 0.8:
            copies += 1
        elif best >= 0.5:
            near += 1

    def r(x: int) -> float:
        return round(x / n, 3)

    return {
        "n": n,
        "engine": {
            "字音替换": {"count": engine_pun, "rate": r(engine_pun)},
            "伪因果推进": {"count": engine_causal, "rate": r(engine_causal)},
            "落点砸自己": {"count": engine_land, "rate": r(engine_land)},
        },
        "template": {
            "复述起手": {"count": echo, "rate": r(echo)},
            "点评这个词": {"count": comment_word, "rate": r(comment_word)},
            "讲一段自己": {"count": self_story, "rate": r(self_story)},
            "同一条流水线": {"count": pipeline, "rate": r(pipeline)},
        },
        "copy": {
            "锚点几乎照抄(>=80%)": {"count": copies, "rate": r(copies)},
            "锚点大幅改写(50-80%)": {"count": near, "rate": r(near)},
        },
        "verdict": {
            "engine_any_rate": r(max(engine_pun, engine_causal, engine_land)),
            "pipeline_rate": r(pipeline),
            "note": (
                "engine 是「有没有他的手艺」，template 是「是不是又变成一条流水线」。"
                "两者都低才是真问题；template 高 engine 低是最坏情况——形似而神不似。"
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(REPORT), help="fidelity_report.json 路径")
    args = ap.parse_args()

    path = Path(args.report)
    if not path.is_file():
        print(f"找不到 {path}，先跑 evals/persona_fidelity.py 生成回复。")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    replies = [r["reply"] for r in data.get("rows", []) if r.get("reply")]
    persona = get_persona(str(settings.persona_path))
    stats = structure_stats(replies, persona)
    if not stats:
        print("报告里没有可用回复。")
        return 1

    provider = (data.get("summary") or {}).get("provider", "?")
    OUT.write_text(
        json.dumps({"provider": provider, "persona_version": persona.version, **stats},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n一日一片 · 结构距离（provider = {provider}, {stats['n']} 条）")
    print("=" * 60)
    print("他的手艺")
    for k, v in stats["engine"].items():
        print(f"  {k:<12} {v['count']:>2}/{stats['n']}  ({v['rate']:.0%})")
    print("\n我们的流水线")
    for k, v in stats["template"].items():
        print(f"  {k:<12} {v['count']:>2}/{stats['n']}  ({v['rate']:.0%})")
    print("\n锚点复制")
    for k, v in stats["copy"].items():
        print(f"  {k:<18} {v['count']:>2}/{stats['n']}  ({v['rate']:.0%})")
    print(f"\n引擎命中率 {stats['verdict']['engine_any_rate']:.0%}"
          f"　流水线率 {stats['verdict']['pipeline_rate']:.0%}")
    print(f"明细已写入 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
