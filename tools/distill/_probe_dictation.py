# -*- coding: utf-8 -*-
"""临时调试脚本：评估 dictation 的产出量与抽样质量。

留在 tools/distill/ 下是有意的——聚簇规则不是一次调好的，
后来的人需要能复跑同一套抽样看质量，而不是只看报告里的 10 条。
正式产物不依赖它。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill.dictation import build_dataset, quality_sample  # noqa: E402


def main() -> None:
    utterances, stats = build_dataset()
    print("videos", stats["videos"])
    print("raw_rows", stats["raw_rows"])
    print("clean_rows", stats["clean_rows"])
    print("clean", stats["clean"])
    print("clusters", stats["clusters"])
    print()
    for utt in sorted(utterances, key=lambda u: -u["n_members"])[:30]:
        print("%-13s %6.1f-%6.1f n=%d %s" % (utt["bvid"], utt["t_start"], utt["t_end"], utt["n_members"], utt["utterance"]))
    print("\n=== random sample 20 ===")
    for utt in quality_sample(utterances, 20):
        print("%-13s %6.1f-%6.1f n=%d | %s" % (utt["bvid"], utt["t_start"], utt["t_end"], utt["n_members"], utt["utterance"]))


if __name__ == "__main__":
    main()
