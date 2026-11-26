# -*- coding: utf-8 -*-
"""语料蒸馏工具链。

用法（从仓库根目录）：
    python tools/distill/run.py --all
    python tools/distill/run.py --texture
    python tools/distill/run.py --shotbank
    python tools/distill/run.py --report
    python tools/distill/run.py --his-comments

模块：
    corpus.py       读 .work/ 五类语料 + 清洗 + 脱敏
    dictation.py    弹幕时间轴聚簇，反推「观众替他说的话」
    attention.py    1 秒桶密度峰 + 峰值处的话 → 抽象骨架
    his_comments.py 抽取他本人敲的评论回复，量回复语体画像
    texture.py      统计画像 → packages/persona/brufen/texture.json
    handwritten.py  手写兜底池（LLM 不可用时用）
    shotbank.py     示例库扩容 + 硬校验 → packages/persona/brufen/shotbank.jsonl
    report.py       把边界、方法、n、核对表写成人能读的报告
    validate.py     对 texture.json / shotbank.jsonl 做 schema 断言
"""
