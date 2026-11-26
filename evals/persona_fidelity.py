#!/usr/bin/env python
"""语料距离：生成的回复，离真实分布有多远。

这个脚本回答一个 v2/v3 一直回避的问题：「像不像」能不能自动测。

v3 的立场是「不能，只能人评」，所以每次改版都靠人读 8 条探针然后说
「还是有点 AI 味」。那句话无法反驳，也无法回归——它把风格的一致性
完全押在评判者的耐心上。

v4 换了个问法。**「像不像他」测不了，但「是不是一个人说话的物理形状」测得了**，
因为那有参照物：`texture.json` 是从 19553 条弹幕 + 10972 条评论里量出来的分布。
于是可以问一个可判定的问题：

    同一批问题，模型写出来的句子，长度/句数/标点/第一人称/连接词的分布，
    和真实语料差多少？

这不是「风格一致性 98%」那种自欺指标——它不测好不好笑、像不像那个人。
它只测一件事：**模型的输出是不是落在人类说话的形状里**。
这个指标可以被反驳（换一批探针数就变），但它至少是可复现的。

跑法：
    python evals/persona_fidelity.py                    # 默认 mock，不花钱
    python evals/persona_fidelity.py --provider deepseek  # 看真模型
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("OAD_DB_PATH", str(ROOT / "services" / "api" / "data" / "fidelity.db"))

from app import quality  # noqa: E402
from app.config import REPO_ROOT, settings  # noqa: E402
from app.llm.providers import get_provider  # noqa: E402
from app.persona.loader import get_persona  # noqa: E402
from app.prompt import builder  # noqa: E402
from app.voice import scorer  # noqa: E402
from app.voice.texture import design_bands, get_texture  # noqa: E402

REPORT = REPO_ROOT / "evals" / "fidelity_report.json"

# 探针刻意不复用金标集：金标是为了测分级，这里是测形状。
# 覆盖语料里的三条主线（打工 / 家庭 / 身份）加上几种长度极端的输入。
PROBES: list[str] = [
    "今天又被领导当众说，我的方案没有灵魂。",
    "同事把我做的方案改成他的名字发出去了。",
    "我想辞职，但不敢。",
    "我妈又催婚了，说再不结婚就没人要了。",
    "我最近什么都不想干，躺着也不想动。",
    "老板说我们是一个大家庭。",
    "甲方又改需求了，第四版回到第一版。",
    "过年回家，亲戚问我一个月挣多少。",
    "面试官问我五年规划，我脑子一片空白。",
    "工资不涨，房租涨了三百。",
    "我在公司群里说错话，被截图了。",
    "今天地铁上有人踩我脚，还不道歉。",
    "我爸住院了，我请不出假。",
    "加班费拖了三个月，财务说走流程。",
    "我把爱好做成了工作，现在不想碰了。",
    "同学都买房了，就我还在合租。",
    "领导说年轻人要多吃苦。",
    "我提的想法没人接，领导复述一遍大家都说好。",
    "今天没什么事，就是有点空。",
    "你会不会觉得自己其实挺失败的。",
    "我好像不太会跟人相处。",
    "我妈说我变了，我说我没变。",
    "凌晨三点还在改PPT。",
    "我想请一天假去看海，但不敢开口。",
]


def _features(text: str) -> dict:
    """一条回复的可测量特征。全部是客观计数，不含任何风格判断。"""
    t = (text or "").strip()
    chars = len([c for c in t if c.strip()])
    sents = quality.count_sentences(t)
    pieces = [p.strip() for p in quality.SENT_SPLIT.split(t) if p.strip()]
    return {
        "chars": chars,
        "sentences": sents,
        "piece_lens": [len(p) for p in pieces] or [0],
        "question": 1 if ("？" in t or "?" in t) else 0,
        "exclaim": t.count("！") + t.count("!"),
        "period": 1 if t.rstrip().endswith("。") else 0,
        "ellipsis": 1 if ("……" in t or "..." in t) else 0,
        "first_person": sum(t.count(w) for w in ("我", "咱")),
        "so": t.count("所以"),
        "but": t.count("但是") + t.count("不过"),
        "then": t.count("然后") + t.count("接着"),
        "opener": (pieces[0][:4] if pieces else ""),
    }


def _ratio(num: int, den: int) -> float:
    return round(num / den, 3) if den else 0.0


async def run(provider_name: str | None = None) -> int:
    persona = get_persona(str(settings.persona_path))
    texture = get_texture(settings.texture_path)
    provider = get_provider(provider_name)
    n_cand = max(1, settings.candidates) if provider.name != "mock" else 1

    rows = []
    for probe in PROBES:
        kwargs = dict(
            persona=persona,
            humor_scale=1.0,
            level="L0",
            recent_bits=[],
            last_self_story=False,
            user_text=probe,
            texture=texture,
        )
        systems = [
            builder.build_system_prompt(**{**kwargs, "candidate_index": i})
            for i in range(n_cand)
        ]
        drafts = []
        for s in systems:
            try:
                drafts.append(
                    quality.normalize(
                        await provider.complete(
                            system=s,
                            messages=[{"role": "user", "content": probe}],
                            temperature=settings.temperature,
                            top_p=settings.top_p,
                            presence_penalty=settings.presence_penalty,
                        )
                    )
                )
            except Exception as exc:  # noqa: BLE001
                rows.append({"probe": probe, "reply": "", "error": type(exc).__name__})
        if not drafts:
            continue
        picked, _ = scorer.pick(
            drafts, persona=persona, texture=texture, user_text=probe
        )
        rows.append({"probe": probe, "reply": picked, "candidates": len(drafts)})

    ok_rows = [r for r in rows if r.get("reply")]
    if not ok_rows:
        print("没有可用回复（模型调用全失败），无法计算。")
        return 1

    feats = [_features(r["reply"]) for r in ok_rows]
    n = len(feats)

    def med(key: str) -> float:
        return float(statistics.median([f[key] for f in feats]))

    # ---- 与语料分布的对照 ----
    # band 的来源要打出来：语料代理不可靠的指标会回落到人格包的设计标尺，
    # 那时这个指标测的是「是否符合设计规格」，不是「是否像真实语料」。
    design = design_bands(persona.raw)
    band_c, src_c = texture.effective("reply_chars", design)
    band_s, src_s = texture.effective("sentences", design)
    band_cl = texture.band("clause_chars")
    pun = texture.punctuation
    conj = texture.conjunctions

    def within_band(values: list[float], band) -> float:
        if not band:
            return 0.0
        inside = [v for v in values if band.p10 <= v <= band.p90]
        return round(len(inside) / len(values), 3)

    chars = [f["chars"] for f in feats]
    sents = [f["sentences"] for f in feats]
    pieces = [ln for f in feats for ln in f["piece_lens"] if ln]

    # 相对偏差：中位数离开语料中位数多少（用 p90-p10 归一化，量纲无关）
    def deviation(model_med: float, corpus_med: float, spread: float) -> float:
        if not spread:
            return 0.0
        return round((model_med - corpus_med) / spread, 3)

    metrics = {
        "chars": {
            "band_source": src_c,
            "model_median": med("chars"),
            "corpus_median": band_c.p50 if band_c else None,
            "in_band_ratio": within_band(chars, band_c),
            "relative_deviation": deviation(
                med("chars"),
                band_c.p50 if band_c else 0,
                (band_c.p90 - band_c.p10) if band_c else 0,
            ),
        },
        "sentences": {
            "band_source": src_s,
            "model_median": med("sentences"),
            "corpus_median": band_s.p50 if band_s else None,
            "in_band_ratio": within_band(sents, band_s),
            "relative_deviation": deviation(
                med("sentences"),
                band_s.p50 if band_s else 0,
                (band_s.p90 - band_s.p10) if band_s else 0,
            ),
        },
        "clause_chars": {
            "model_median": float(statistics.median(pieces)) if pieces else 0.0,
            "corpus_median": band_cl.p50 if band_cl else None,
            "band_source": texture.band_source("clause_chars"),
        },
        "question_rate": {
            "model": _ratio(sum(f["question"] for f in feats), n),
            "corpus": pun.get("question_ratio"),
        },
        "exclaim_per_reply": {
            "model": round(sum(f["exclaim"] for f in feats) / n, 3),
            "corpus": pun.get("excl_ratio"),
        },
        "period_ending_rate": {
            "model": _ratio(sum(f["period"] for f in feats), n),
            "corpus": pun.get("period_ending_ratio"),
        },
        "first_person_per_reply": {
            "model": round(sum(f["first_person"] for f in feats) / n, 2),
        },
        "conjunction_per_reply": {
            "所以": round(sum(f["so"] for f in feats) / n, 3),
            "但是/不过": round(sum(f["but"] for f in feats) / n, 3),
            "然后/接着": round(sum(f["then"] for f in feats) / n, 3),
            "语料参考": {k: round(v, 3) for k, v in conj.items()},
        },
    }

    # ---- 模板感：这一批回复是不是同一个形状 ----
    openers = [f["opener"] for f in feats if f["opener"]]
    opener_counts: dict[str, int] = {}
    for o in openers:
        opener_counts[o] = opener_counts.get(o, 0) + 1
    shape_counts: dict[str, int] = {}
    for f in feats:
        key = f"{f['sentences']}句/{int(f['chars'] // 20) * 20}字"
        shape_counts[key] = shape_counts.get(key, 0) + 1

    repetition = {
        "distinct_openers": len(set(openers)),
        "openers_total": len(openers),
        "max_same_opener": max(opener_counts.values()) if opener_counts else 0,
        "max_same_shape_share": round(max(shape_counts.values()) / n, 3) if shape_counts else 0.0,
        "chars_stdev": round(statistics.pstdev(chars), 2) if n > 1 else 0.0,
    }

    # 综合距离：三项相对偏差的绝对值平均。越小越像人。
    dev = [
        abs(metrics["chars"]["relative_deviation"]),
        abs(metrics["sentences"]["relative_deviation"]),
    ]
    distance = round(sum(dev) / len(dev), 3)

    summary = {
        "provider": provider.name,
        "persona_version": persona.version,
        "texture_available": texture.available,
        "probes": n,
        "corpus_distance": distance,
        "repetition": repetition,
        "metrics": metrics,
        "note": (
            "corpus_distance 只衡量形状（长度与句数）与真实分布的相对偏差，"
            "0 表示中位数对齐。它不测好不好笑、像不像那个人——那两个只能人评。"
        ),
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---- 输出 ----
    print(f"\n一日一片 · 语料距离（provider = {provider.name}, 探针 {n} 条）")
    print("=" * 62)
    print(f"语料纹理        {'已加载' if texture.available else '缺失（用回落标尺，结论仅供参考）'}")
    print(f"长度中位        {metrics['chars']['model_median']:.0f} 字"
          f"  标尺 {metrics['chars']['corpus_median']}（{metrics['chars']['band_source']}）"
          f"  落在 p10–p90 的比例 {metrics['chars']['in_band_ratio']:.0%}")
    print(f"句数中位        {metrics['sentences']['model_median']:.0f} 句"
          f"  标尺 {metrics['sentences']['corpus_median']}（{metrics['sentences']['band_source']}）"
          f"  落在 p10–p90 的比例 {metrics['sentences']['in_band_ratio']:.0%}")
    print(f"分句中位        {metrics['clause_chars']['model_median']:.0f} 字"
          f"  语料 {metrics['clause_chars']['corpus_median']}"
          f"（{metrics['clause_chars']['band_source']}）")
    print(f"问号率          {metrics['question_rate']['model']:.0%}"
          f"  语料 {metrics['question_rate']['corpus']}")
    print(f"句号收尾率      {metrics['period_ending_rate']['model']:.0%}"
          f"  语料 {metrics['period_ending_rate']['corpus']}")
    print(f"模板感          起始式去重 {repetition['distinct_openers']}/{repetition['openers_total']}"
          f"｜最多同一开头 ×{repetition['max_same_opener']}"
          f"｜最多同一形状占比 {repetition['max_same_shape_share']:.0%}")
    print(f"\n语料距离（越小越好）  {distance}")
    print(f"\n明细已写入 {REPORT.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock", help="mock（默认）/ deepseek / openai / ollama")
    args = ap.parse_args()
    return asyncio.run(run(args.provider))


if __name__ == "__main__":
    raise SystemExit(main())
