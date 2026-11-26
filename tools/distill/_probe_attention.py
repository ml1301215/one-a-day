# -*- coding: utf-8 -*-
"""临时调试：看注意力峰与抽象骨架的抽样。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill.attention import build_attention  # noqa: E402
from distill.dictation import build_dataset  # noqa: E402


def main() -> None:
    utterances, dstat = build_dataset()
    result = build_attention(utterances)
    print("dictation clusters", dstat["clusters"], "relay", dstat["relay_clusters"])
    print("attention stats", result["stats"])
    print("\n=== peaks ===")
    for p in result["peaks"][:40]:
        print("%-13s t=%6.1f n=%3d rank=%d" % (p["bvid"], p["t"], p["n"], p["rank"]))
    print("\n=== skeletons ===")
    for s in result["skeletons"]:
        print("%3d %-13s %6.1f %-6s %s | %s\n        seed=%s" % (
            s["n"], s["video"], s["t"], s["mode"], s["skeleton"], s["why"], s["seed"][:60]
        ))


if __name__ == "__main__":
    main()
