"""语料纹理：把 `texture.json` 读成一组可计算的标尺。

这个文件是**唯一**允许把「像人」变成数字的地方。它只做三件事：

1. 读工具链（`tools/distill/`）从真实语料量出来的分布；
2. 缺失时回落到人格包里的手写标尺（不报错，只是精度降低）；
3. 提供 `fit()`：一条回复离这个分布有多远，返回 [-1, 1]。

为什么要把「远不远」做成一个数：v3 的每次改版都靠人读 8 条探针然后说
「还是有点 AI 味」。那句话无法反驳，也无法回归。有了 fit 之后，
至少「变长/变短/变整齐」这三种最常见的漂移能在 CI 里被拦住。

注意这里刻意**不计算**「好不好笑」「像不像他」——那两个只能人评。
fit 只回答一个问题：这条回复的物理形状，是不是一个人嘴里能出来的形状。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

# 手写回落值。来源是 v3.1 人格包 voice.sentence_max/char_max 与
# length_policy.typical（20–70 字 / 2–4 句），不是新编的数字。
# 它存在的意义只有一条：语料文件还没生成时，打分器不至于失灵。
_FALLBACK = {
    "bands": {
        "reply_chars": {"p10": 6, "p25": 14, "p50": 28, "p75": 52, "p90": 78},
        "sentences": {"p10": 1, "p50": 2, "p90": 4},
        "clause_chars": {"p10": 3, "p50": 8, "p90": 18},
    },
    "punctuation": {
        "period_ending_ratio": 0.55,
        "question_ratio": 0.08,
        "excl_ratio": 0.03,
        "ellipsis_ratio": 0.02,
    },
    "conjunctions": {"所以": 0.25, "但是": 0.30, "然后": 0.55, "其实": 0.20, "就是": 0.45},
    "self": {"first_person_ratio": 0.45, "first_person_per_100": 7.0},
    "openers": [],
    "skeletons": [],
    "tics": [],
    "limits": ["texture.json 不存在，使用人格包里的手写标尺"],
}


@dataclass
class Band:
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float

    def fit(self, value: float) -> float:
        """值落在分布里的程度：1 = 正中位数，0 = 在 p10/p90 边上，负 = 超出。

        用两侧不对称的尺度：短是这个人正常的（可以只回一句 6 个字），
        所以低于 p10 的惩罚比高于 p90 轻。v3 踩过的坑正好相反——
        抑制「讲自己」时回复缩到 11–13 字，用户讲了一整件事只回一句，
        而当时的闸门只拦长、不拦短。
        """
        v = float(value)
        if v <= 0:
            return -1.0
        if v < self.p10:
            span = max(self.p10, 1.0)
            return max(-1.0, -0.35 * (self.p10 - v) / span)
        if v > self.p90:
            span = max(self.p90 - self.p50, 1.0)
            return max(-1.0, -1.0 * (v - self.p90) / span)
        if v <= self.p50:
            span = max(self.p50 - self.p10, 1.0)
            return round((v - self.p10) / span, 3)
        span = max(self.p90 - self.p50, 1.0)
        return round(1.0 - 0.35 * (v - self.p50) / span, 3)


# ---------------------------------------------------------------------------
# 每条 band 单独过合理性检查
# ---------------------------------------------------------------------------
# 语料代理不是万能的，每一条的可靠度不一样，所以检查必须逐条做，而不是整体信任。
#
# 实测踩到的：dictation 聚簇出来的 `sentences` band 是 p10=1 / p50=1 / p90=2。
# 原因是聚簇规则本身排除了以句末标点结尾的片段，utterance 天然几乎不分句——
# 那不是他的句数分布，是**方法伪影**。拿它当标尺会把「三句话」判成超长，
# 而三句正是这个人的常态。
#
# 判据刻意写得宽松（只要不离谱就信语料），因为收紧会退回到「凭手感定数字」，
# 那正是这套东西想摆脱的东西。
_TRUST_RULES = {
    "reply_chars": lambda b: b.p50 >= 8 and b.p90 >= 30,
    "sentences": lambda b: b.p90 >= 3,
    "clause_chars": lambda b: b.p90 >= 10,
}


def _band_from(d: Any) -> Band | None:
    """把 texture.json 里的一组分位数读成 Band。字段缺失/类型不对就返回 None。"""
    if not isinstance(d, dict):
        return None
    try:
        return Band(
            p10=float(d.get("p10", 0)),
            p25=float(d.get("p25", 0)),
            p50=float(d.get("p50", 0)),
            p75=float(d.get("p75", 0)),
            p90=float(d.get("p90", 0)),
        )
    except (TypeError, ValueError):
        return None


def design_bands(raw: dict) -> dict[str, Band]:
    """人格包里手写的设计标尺，用于语料代理不可靠时的回落。

    它编码的是 v2/v3 的设计决定（典型 20–70 字、2–4 句、上限 6 句），
    不是从语料量的——所以它落在**规格**那一侧，而不是**观察**那一侧。
    两者在打分时不能混为一谈，所以调用方要能知道当前用的是哪一个。
    """
    rb = ((raw.get("voice") or {}).get("reply_band")) or {}
    out: dict[str, Band] = {}
    for name in ("chars", "sentences"):
        b = _band_from(rb.get(name))
        if b is not None:
            out["reply_chars" if name == "chars" else "sentences"] = b
    return out



@dataclass
class Texture:
    raw: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(_FALLBACK)))
    path: Path | None = None

    # ---- 基础属性 ----
    @property
    def available(self) -> bool:
        """true 表示这是真语料量出来的，不是回落值。"""
        return self.path is not None

    @property
    def bands(self) -> dict[str, Band]:
        out = {}
        for name, d in (self.raw.get("bands") or {}).items():
            try:
                out[name] = Band(
                    p10=float(d.get("p10", 0)),
                    p25=float(d.get("p25", 0)),
                    p50=float(d.get("p50", 0)),
                    p75=float(d.get("p75", 0)),
                    p90=float(d.get("p90", 0)),
                )
            except (TypeError, ValueError):
                continue
        return out

    @property
    def his_replies(self) -> dict[str, Any]:
        """他本人敲的评论（一手材料）。

        v4 的意外收获：`tools/distill` 复算时发现评论区里有 224 条是他本人的
        （docs 曾断言 0 次）。它是一手中文，不经「观众转写」这一层损耗，
        而且和聊天场景同构——「一句话回一个人」。
        所以只要样本够，回复长度/句数就优先用它，而不是用弹幕聚簇那个低置信代理。
        """
        d = self.raw.get("his_replies")
        return dict(d) if isinstance(d, dict) else {}

    def band(self, name: str) -> Band | None:
        return self.bands.get(name)

    def band_with_source(self, name: str) -> tuple[Band | None, str]:
        """取语料侧 band 并说明来源：`his_replies` / `corpus` / 不可信。

        注意它**不决定最终用哪个 band**——那件事在 `effective()` 里做，
        因为「语料测的是什么单位」和「回复该多长」是两个问题（见下）。
        """
        if name in ("reply_chars", "sentences"):
            his = self.his_replies
            key = "chars" if name == "reply_chars" else "sentences"
            if int(his.get("n") or 0) >= 100:
                b = _band_from(his.get(key))
                if b and b.p50 > 0:
                    return b, "his_replies"
        b = self.band(name)
        if b is None:
            return None, "none"
        rule = _TRUST_RULES.get(name)
        return b, ("corpus" if (rule is None or rule(b)) else "method-artifact")

    def band_source(self, name: str) -> str:
        return self.effective(name)[1]

    def effective(
        self, name: str, design: dict[str, Band] | None = None
    ) -> tuple[Band | None, str]:
        """返回实际该用的 band 与它的来源。

        长度与句数**一律优先用人格包的设计标尺**，这一条是实测逼出来的，
        不是偏好：

        - `his_replies.chars` 中位 9 字。那是评论区的一问一答（「谢谢」「确实」
          「哈哈哈哈」占了一大半），**和聊天回复不是同一个语体**；
        - dictation 聚簇的 12–39 字是「观众打出来的半句话」，也不是一个回合。

        两个代理都在量比「回复」更小的单位。如果拿它们当回复长度的标尺，
        回复会被压到 9–20 字，正好丢掉这个人最值钱的东西：跑题之后再硬着陆。

        所以：**长度的参照物只能是同语体的东西**——人格包里那几条例子里
        反复被作者调过的长度（v2/v3 用真人评审定的），加上设计标尺。
        语料的那两个数仍然有用，但只作为「他很短」这个方向性提示，
        写进 prompt，不进入打分。
        """
        if name in ("reply_chars", "sentences") and design and name in design:
            return design[name], "design"
        b, src = self.band_with_source(name)
        if src in ("his_replies", "corpus"):
            return b, src
        fallback = (design or {}).get(name)
        if fallback is not None:
            return fallback, "design"
        return b, "corpus-fallback"

    @property
    def punctuation(self) -> dict[str, float]:
        return {k: float(v) for k, v in (self.raw.get("punctuation") or {}).items()}

    @property
    def conjunctions(self) -> dict[str, float]:
        return {k: float(v) for k, v in (self.raw.get("conjunctions") or {}).items()}

    @property
    def first_person_ratio(self) -> float:
        return float((self.raw.get("self") or {}).get("first_person_ratio", 0.45))

    @property
    def openers(self) -> list[dict]:
        return list(self.raw.get("openers") or [])

    @property
    def skeletons(self) -> list[dict]:
        return list(self.raw.get("skeletons") or [])

    @property
    def limits(self) -> list[str]:
        return [str(x) for x in (self.raw.get("limits") or [])]

    @property
    def sources(self) -> dict[str, Any]:
        return dict(self.raw.get("sources") or {})

    # ---- 画给别人看的一段话 ----
    def digest(self, design: dict[str, Band] | None = None) -> str:
        """压成 prompt 里的一小段「数据画像」。

        为什么只给数字不给形容词：v3 的 dosage 一节证明了数字有效
        （「每百词 0.2–0.9 → 平均三轮一个」之后，「所以」从每条都有降到 1/8）。
        反过来，任何形容词都会被模型翻译成另一种形容词。

        注意 band 可能来自语料，也可能来自人格包的设计标尺——两种来源不能混着说，
        所以这一段会明确写「按语料量出来」还是「按设计标尺」。
        """
        chars, chars_src = self.effective("reply_chars", design)
        sents, sents_src = self.effective("sentences", design)
        if not chars or not sents:
            return ""
        conj = self.conjunctions
        his = self.his_replies
        his_n = int(his.get("n") or 0)

        if chars_src == "his_replies" and sents_src == "his_replies":
            head = f"下面的长度与句数，是从他自己敲的 {his_n} 条评论里量出来的，按它写："
        elif chars_src == "corpus" and sents_src == "corpus":
            head = "下面是这个人的真实分布，按它写，不要按「好回复」的标准写："
        else:
            # 设计标尺不是从语料量的，要把原因说清楚，否则读者会以为这是测出来的
            head = (
                "下面的长度与句数用的是设计标尺，不是语料量的："
                "他没留下逐字稿，能拿到的两批中文都比「一个回合」短"
                "（评论区一问一答中位 9 字，弹幕转写片段中位 20 字）。按它写。"
            )

        lines = [
            head,
            f"· 长度：中位 {chars.p50:.0f} 字（{chars.p10:.0f}–{chars.p90:.0f} 是真正常见区间）。"
            f"超过 {chars.p90:.0f} 字就算长，长到中位数的两倍以上才会被注意到。",
            f"· 句数：中位 {sents.p50:.0f} 句，p90 是 {sents.p90:.0f} 句。"
            "只回一句很正常，不是敷衍。",
        ]

        # 标点：优先用他自己敲字的习惯（一手），没有才用弹幕代理。
        his_pun = his.get("punctuation") or {}
        if his_pun:
            q = his_pun.get("question", 0.0) * 100
            no_end = his_pun.get("no_end_punct", 0.0) * 100
            lines.append(
                f"· 标点：他打短回复时 {no_end:.0f}% 连句末标点都不加，问号只有 {q:.0f}%。"
                "所以他不是每句都收个句号；但一段话里该断的地方仍然要断。"
            )
        else:
            pun = self.punctuation
            lines.append(
                "· 标点：不用感叹号，不堆问号，不拿省略号装深沉。"
                f"（语料里带问号的只有 {pun.get('question_ratio', 0) * 100:.0f}%）"
            )

        if conj:
            names = "、".join(f"「{k}」" for k in conj)
            lines.append(
                f"· 连接词：{names} 平均每句话分别只出现 "
                + "、".join(f"{v:.2f}" for v in conj.values())
                + " 次。因果可以推，但不是每句都推。"
            )
        if self.first_person_ratio:
            lines.append(
                f"· 第一人称：{self.first_person_ratio * 100:.0f}% 的话里有「我」。"
                "这个比例已经很高，说明讲自己是常态；正因为是常态，连着讲才是问题。"
            )
        # 语料量的「短」只作方向提示，不当长度目标：两个代理量的都比「一个回合」小。
        corpus_chars = self.band("reply_chars")
        if corpus_chars and his_n < 100 and abs(corpus_chars.p50 - chars.p50) > 4:
            lines.append(
                f"· 方向：语料里他一段话中位只有 {corpus_chars.p50:.0f} 字——宁可短一截，不要写成段落。"
            )
        if self.limits:
            lines.append("· 这套数字的局限：" + "；".join(self.limits[:2]))
        his_limits = [str(x) for x in (his.get("limits") or [])]
        if his_limits:
            lines.append("· 评论语料的局限：" + "；".join(his_limits[:1]))
        return "\n".join(lines)


def load_texture(path: Path) -> Texture:
    """读语料纹理。文件坏了不抛异常——人格包还要能用。"""
    if not path.is_file():
        return Texture()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Texture()
    if not isinstance(raw, dict):
        return Texture()
    # 缺字段时用回落值补齐，避免下游到处 `or {}`
    merged = json.loads(json.dumps(_FALLBACK))
    for k, v in raw.items():
        merged[k] = v
    return Texture(raw=merged, path=path)


@lru_cache(maxsize=8)
def get_texture(path_str: str) -> Texture:
    return load_texture(Path(path_str))
