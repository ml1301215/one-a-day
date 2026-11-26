# -*- coding: utf-8 -*-
"""流水线入口。

用法（从仓库根目录跑，Windows PowerShell 也可以）：
    python tools/distill/run.py --all
    python tools/distill/run.py --texture
    python tools/distill/run.py --report
    python tools/distill/run.py --shotbank
    python tools/distill/run.py --his-comments

为什么拆子命令：
    texture 是纯分析（秒级、零网络），shotbank 会调 LLM（分钟级、要 key），
    report 只读前两者。分开之后，改校验规则时不用每次重新生成 55 条示例，
    也不会因为一次网络抖动把整条流水线判死。

依赖：标准库 + pyyaml。不新增第三方依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows PowerShell 的默认控制台编码不一定是 UTF-8，打印中文进度会直接
# UnicodeEncodeError 把流水线打断。这里只影响终端输出，不影响文件写入。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# python tools/distill/run.py 直接执行时，sys.path[0] 是 tools/distill/，
# 相对导入会失败。这里把 tools/ 加进 path，让 `from distill import ...` 成立。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill import attention, corpus, dictation, his_comments, report, texture  # noqa: E402
from distill import validate as validate_mod  # noqa: E402

PERSONA_DIR = corpus.ROOT / "packages" / "persona" / "brufen"
TEXTURE_PATH = PERSONA_DIR / "texture.json"
SHOTBANK_PATH = PERSONA_DIR / "shotbank.jsonl"
REPORT_PATH = Path(__file__).resolve().parent / "report.md"


def analyze() -> dict:
    """跑一遍语料层（不写任何文件）。子命令共享这一份内存结果。"""
    print("[1/4] 读语料 …")
    timed = corpus.load_timed_danmaku()
    comments = corpus.load_comments()
    transcripts = corpus.load_transcripts()
    videos = corpus.load_videos()
    titles, title_notes = corpus.load_titles()
    print(
        "      videos=%d danmaku=%d comments=%d transcripts=%d titles=%d"
        % (
            len(timed),
            sum(len(v["rows"]) for v in timed.values()),
            len(comments),
            len(transcripts),
            len(titles),
        )
    )
    his = his_comments.build(comments)
    print(
        "      his_comments: n=%d（他本人敲的评论回复，%d 主评论 + %d 二级）"
        % (his["section"]["n"], sum(1 for r in his["rows"] if r["layer"] == 1),
           sum(1 for r in his["rows"] if r["layer"] == 2))
    )
    print("[2/4] 弹幕清洗 + 时间轴聚簇 …")
    utterances, dstats = dictation.build_dataset()
    print(
        "      raw=%d clean=%d 多片段组=%d utterance=%d relay=%d"
        % (
            dstats["raw_rows"],
            dstats["clean_rows"],
            dstats["clusters"],
            dstats["utterances"],
            dstats["relay_clusters"],
        )
    )
    print("[3/4] 注意力峰 + 骨架抽象 …")
    att = attention.build_attention(utterances, timed)
    print(
        "      peaks=%d utterance 落在峰附近=%d skeletons=%d"
        % (
            att["stats"]["peaks"],
            att["stats"]["utterances_near_peak"],
            att["stats"]["skeletons"],
        )
    )
    print("[4/4] 统计画像 …")
    payload = texture.build_texture(
        utterances=utterances,
        dictation_stats=dstats,
        attention=att,
        titles=titles,
        transcripts=transcripts,
        timed=timed,
        comments_total=len(comments),
        title_notes=title_notes,
        his_replies=his["section"],
    )
    return {
        "timed": timed,
        "comments": comments,
        "transcripts": transcripts,
        "videos": videos,
        "titles": titles,
        "title_notes": title_notes,
        "utterances": utterances,
        "dstats": dstats,
        "attention": att,
        "texture": payload,
        "his": his,
    }


def write_texture(ctx: dict) -> dict:
    payload = ctx["texture"]
    PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    TEXTURE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("      写入 %s" % TEXTURE_PATH.relative_to(corpus.ROOT))
    errors = validate_mod.validate_texture(payload)
    if errors:
        print("      ✗ texture 自检失败：")
        for e in errors:
            print("        - %s" % e)
        raise SystemExit(1)
    print("      ✓ texture 自检通过")
    return payload


def load_or_build_texture(ctx: dict) -> dict:
    if TEXTURE_PATH.is_file():
        try:
            return json.loads(TEXTURE_PATH.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return write_texture(ctx)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一日一片 · 语料蒸馏流水线")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="texture → shotbank → report")
    group.add_argument("--texture", action="store_true", help="只生成 texture.json")
    group.add_argument("--report", action="store_true", help="只生成 report.md")
    group.add_argument("--shotbank", action="store_true", help="只生成 shotbank.jsonl")
    group.add_argument("--his-comments", action="store_true", help="只打印他本人评论的画像")
    args = parser.parse_args(argv)

    ctx = analyze()

    if args.his_comments:
        print(his_comments.summarize(ctx["his"]))
        print("register:", json.dumps(ctx["his"]["register"], ensure_ascii=False))
        return 0

    if args.texture:
        write_texture(ctx)
        return 0

    if args.shotbank:
        payload = load_or_build_texture(ctx)
        ctx["texture"] = payload
        from distill import shotbank

        result = shotbank.build_shotbank(payload, verbose=True)
        print("      shotbank %d 条，新增 %d，拒绝 %d" % (
            result["total"], result["accepted_new"], result["rejected_total"]
        ))
        return 0

    if args.report:
        payload = load_or_build_texture(ctx)
        ctx["texture"] = payload
        report.write_report(ctx, REPORT_PATH)
        print("      写入 %s" % REPORT_PATH.relative_to(corpus.ROOT))
        return 0

    # --all
    payload = write_texture(ctx)
    ctx["texture"] = payload
    from distill import shotbank

    sb = shotbank.build_shotbank(payload, verbose=True)
    print("      shotbank %d 条，新增 %d，拒绝 %d" % (
        sb["total"], sb["accepted_new"], sb["rejected_total"]
    ))
    report.write_report(ctx, REPORT_PATH)
    print("      写入 %s" % REPORT_PATH.relative_to(corpus.ROOT))
    errors = validate_mod.validate_shotbank(SHOTBANK_PATH, payload)
    if errors:
        print("      ✗ shotbank 自检失败：")
        for e in errors:
            print("        - %s" % e)
        return 1
    print("      ✓ shotbank 自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
