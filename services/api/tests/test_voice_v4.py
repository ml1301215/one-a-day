"""v4 的守门测试：这一版把「像人」从 prompt 挪进了数据和选样，每一条都要能被测出来。

v4 只有四件事，对应四个测试组：
1. 语料纹理能加载、能回落、能被压成一段数字（voice/texture.py）；
2. 样本按话题检索，而不是每轮给同样三条（voice/retrieval.py）；
3. 同轮多条候选按分布挑一条，且能拦住 v3 踩过的坑（voice/scorer.py）；
4. prompt 不再是 9 千字的规则手册，且安全分级真的不在主链路上。

第 4 条尤其要有数字：prompt 长度是这套系统最容易被悄悄改回去的指标——
每次「再加一条规则」，单看都有道理，合起来就是模型只记得最近那条。
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.persona.loader import get_persona
from app.prompt import builder
from app.voice import retrieval, scorer
from app.voice.texture import Texture, design_bands, get_texture


@pytest.fixture(scope="module")
def persona():
    return get_persona(str(settings.persona_path))


@pytest.fixture(scope="module")
def texture():
    return get_texture(settings.texture_path)


# ---------------------------------------------------------------- 纹理


def test_texture_falls_back_without_file(tmp_path):
    """语料文件没生成时不能炸，也不能静默返回空——要给一套手写标尺兜底。"""
    t = get_texture(str(tmp_path / "nope.json"))
    assert t.available is False
    assert t.band("reply_chars") is not None
    assert t.digest(), "回落时也必须能编译出一段数据画像，否则 prompt 会缺一整节"


def test_texture_band_fit_is_asymmetric():
    """低于 p10 的惩罚要比高于 p90 轻：短是他正常的，长不是。"""
    t = Texture()
    band = t.band("reply_chars")
    assert band is not None
    assert band.fit(band.p50) > 0.9
    assert band.fit(band.p10 - 2) > -1.0
    assert band.fit(band.p90 * 3) < -0.5


def test_texture_digest_carries_numbers_not_adjectives(texture):
    """数据画像里必须出现具体数字。写成「句子要短」等于没写。"""
    digest = texture.digest()
    assert digest
    assert any(ch.isdigit() for ch in digest)


def test_texture_loaded_when_corpus_present(texture):
    """如果 tools/distill 已经跑过，纹理必须是真语料，而不是回落值。"""
    if not (settings.persona_path.parent / "texture.json").is_file():
        pytest.skip("texture.json 尚未生成（先跑 tools/distill/run.py）")
    assert texture.available is True
    sources = texture.sources
    assert sources.get("danmaku_total", 0) > 0


def test_artifact_band_falls_back_to_design(persona, texture):
    """方法伪影必须被识别出来，不能当标尺用。

    实测：dictation 聚簇出来的句数 band 是 p90=2，因为聚簇规则排除了
    以句末标点结尾的片段——utterance 天然几乎不分句。它不是他的句数分布。
    拿它打分会把「三句话」判成超长，而三句正是这个人的常态。
    """
    design = design_bands(persona.raw)
    assert design, "人格包必须提供 voice.reply_band 作为回落标尺"
    band, src = texture.effective("sentences", design)
    corpus_band = texture.band("sentences")
    if corpus_band and corpus_band.p90 < 3:
        assert src == "design", "p90<3 的句数 band 是伪影，必须回落"
        assert band.p90 == design["sentences"].p90


def test_band_source_is_reported(persona, texture):
    """标尺来源要能被打印出来，否则「为什么这条被压短了」只能猜。"""
    design = design_bands(persona.raw)
    for name in ("reply_chars", "sentences"):
        _, src = texture.effective(name, design)
        assert src in ("corpus", "design", "corpus-fallback")


def test_near_copy_catches_one_char_edit(persona):
    """模型抄示例时会改一两个字。精确子串匹配会放过它，那比完全照抄更难发现。"""
    from app import quality

    example = "（我妈说过的是「你再挑，就剩别人挑剩下的」。）"
    edited = "我妈说的是「你再挑，就剩别人挑剩下的」。"
    assert quality.near_copy(edited, [example], min_len=12, ratio=0.7), "改一个字就躲过抄袭检测"
    # 但正常表达不能被误判
    assert not quality.near_copy("明天把这事写成三行发他，让他自己挑。", [example], 12, 0.7)


# ---------------------------------------------------------------- 检索


def test_retrieval_varies_by_topic(persona):
    """同一个问题永远拿到同一批样本，是 v3 模板感的主要来源之一。"""
    a = retrieval.retrieve(persona, "同事把我做的方案改成他的名字发出去了", k=4)
    b = retrieval.retrieve(persona, "我妈又催我相亲，说再不结婚就没人要了", k=4)
    assert a and b
    assert {s.id for s in a} != {s.id for s in b}, "两个完全不同的话题拿到了同一批样本"


def test_retrieval_avoids_self_story_when_asked(persona):
    """上一条刚讲过自己，这一轮整批撤掉「讲自己」的样本。

    v3 验证过：规则写「这条别讲自己」压不住示例，撤样本可以。
    """
    shots = retrieval.retrieve(persona, "今天开会被老板当众说方案没想清楚", k=4, avoid_self=True)
    assert shots
    assert not any(s.has_self for s in shots)


def test_retrieval_keeps_length_spread(persona):
    """三条一样长是最容易被识破的模板。检索必须保住参差。"""
    shots = retrieval.retrieve(persona, "我想辞职，但不敢", k=4)
    if len(shots) < 2:
        pytest.skip("样本不足")
    lens = [s.chars for s in shots]
    assert max(lens) - min(lens) >= 8, f"样本长度太齐：{lens}"


def test_empty_user_text_returns_persona_shots(persona):
    """没有用户输入时（评测/预热）不检索，回落到人格包自带的 few_shots。"""
    shots = builder._pick_shots(
        persona, user_text="", last_self_story=False, recent_bits=None
    )
    assert shots
    assert all(s.origin == "persona" for s in shots)


# ---------------------------------------------------------------- 打分与选样


def test_scorer_prefers_band_fitting_candidate(persona, texture):
    """同轮两条候选，长度落在真实分布里的那条应该赢。"""
    short = "行。那咱俩一起排。"
    long = (
        "你今天遇到的这件事我一点都不意外，因为这类公司的运作方式本来就是这样的，"
        "他们把所有人的付出都当成理所当然，并且从不觉得需要道谢，所以你现在这种感受"
        "其实是非常正常而且完全可以理解的反应，你需要做的是先照顾好自己的情绪，"
        "然后再去考虑下一步应该怎么处理这件事，千万不要因为别人的评价就否定自己。"
    )
    picked, scored = scorer.pick(
        [long, short],
        persona=persona,
        texture=texture,
        # 用户这句要短于 10 字，否则「用户讲了一整件事」的过短判定会把短候选判死，
        # 测到的就不是长度拟合，而是另一条规则。
        user_text="又被领导说了",
    )
    assert picked == short
    assert scored[0].ok, scored[0].summary()
    assert scored[0].total > scored[-1].total


def test_scorer_kills_explained_pun(persona, texture):
    """解释自己的梗是 v3 第一轮实测里 6 条中 5 条的同一个毛病，出现即出局。"""
    s = scorer.score(
        "署名。属名。这两个字一个音，念起来像属谁的。",
        persona=persona,
        texture=texture,
        user_text="同事抢我功劳",
    )
    assert not s.ok
    assert any("解释" in f for f in s.fatal)


def test_scorer_kills_self_story_after_self_story(persona, texture):
    s = scorer.score(
        "我上回也被人抢过一次功劳，回工位把原稿另存了一份。",
        persona=persona,
        texture=texture,
        last_self=True,
        user_text="同事把我做的方案改成他的名字发出去",
    )
    assert not s.ok
    assert any("讲自己" in f for f in s.fatal)


def test_scorer_kills_too_short_answer_to_long_message(persona, texture):
    """用户讲了一整件事只回一句，是敷衍，不是「短是他的风格」。"""
    s = scorer.score(
        "行。", persona=persona, texture=texture, user_text="今天开会被老板当众说方案没想清楚，回来路上一直想"
    )
    assert not s.ok


def test_scorer_rejects_format_noise(persona, texture):
    for bad in ("**重点**在这。", "今天辛苦了🙂", "1. 先这样\n2. 再那样"):
        assert not scorer.score(bad, persona=persona, texture=texture).ok


def test_pick_never_returns_empty_when_a_draft_exists(persona, texture):
    """所有候选都出局时也要发一条，不能让用户等一次重写。"""
    picked, _ = scorer.pick(
        ["**全都不合格**", "也不行🙂"], persona=persona, texture=texture
    )
    assert picked


def test_candidate_hints_differ():
    """多候选如果拿到同一句提示，生成的三条会几乎一样，选样就白做了。"""
    hints = [scorer.hint_for(i) for i in range(4)]
    assert len(set(hints)) == 4


# ---------------------------------------------------------------- prompt


def test_prompt_is_much_shorter_than_v3(persona, texture):
    """v3 是 9358 字 / 25 段。v4 必须显著更短，否则「规则挤掉判断」会重演。

    阈值定在 7000 而不是卡着当前的 6500：纹理/样本一变，数字会浮动几十字，
    卡太死会让这条测试变成「测文本长度」而不是「防规则膨胀」。
    它真正要拦的是回到 9000+。
    """
    system = builder.build_system_prompt(persona, user_text="今天又被领导当众说了", texture=texture)
    assert len(system) < 7000, f"prompt 又长回去了：{len(system)} 字"


def test_prompt_has_no_safety_hard_rules(persona, texture):
    """v4 默认姿态下 prompt 里不该再出现「硬约束，违反即失败」那一段。"""
    system = builder.build_system_prompt(persona, user_text="今天又被领导当众说了", texture=texture)
    assert "硬约束，违反即失败" not in system
    assert "L3" not in system
    assert "本轮等级" not in system


def test_prompt_carries_mind_model(persona, texture):
    """v4 的心智模型必须在 prompt 里：先注意什么、把错归给谁、什么时候不说话。"""
    system = builder.build_system_prompt(persona, user_text="今天又被领导当众说了", texture=texture)
    assert "先看见物件" in system or "物件" in system
    assert "不主动教人怎么办" in system or "给建议" in system
    assert "不推动对话" in system


def test_prompt_injects_texture_data(persona, texture):
    system = builder.build_system_prompt(persona, user_text="今天又被领导当众说了", texture=texture)
    assert "数据画像" in system


def test_prompt_uses_retrieved_shots_not_fixed_three(persona, texture):
    a = builder.build_system_prompt(persona, user_text="同事把我做的方案改成他的名字", texture=texture)
    b = builder.build_system_prompt(persona, user_text="我妈又催我相亲", texture=texture)
    assert "语感参照" in a and "语感参照" in b
    assert a != b


def test_guard_posture_only_adds_restraint_block(persona, texture):
    """默认姿态（humor_scale=1）不该出现「本轮的克制」；guard 姿态才出现。"""
    normal = builder.build_system_prompt(persona, user_text="我是不是很没用", texture=texture)
    guard = builder.build_system_prompt(
        persona, humor_scale=0.0, level="L2", user_text="我是不是很没用", texture=texture
    )
    assert "本轮的克制" not in normal
    assert "本轮的克制" in guard
    assert "换掉那把尺子" in guard


def test_guard_posture_gives_no_shots(persona, texture):
    """L2 给样本会被逐字照抄，用户每问一次得到同一段话。"""
    guard = builder.build_system_prompt(
        persona, humor_scale=0.0, level="L2", user_text="我是不是很没用", texture=texture
    )
    assert "语感参照" not in guard


# ---------------------------------------------------------------- 自洽性
# 下面这组测的不是行为，是人格包**自己有没有跟自己打架**。
# v3.1 里就藏着一个：anti_ai_flavor.phrasings 明令禁止「这词以前写作」
# 和「同一个音」，而几条例子里恰恰用了它们（还用来解释谐音）。
# 示例的权重高于规则，所以那几条负向清单一进模型就被示例推翻了。


def test_no_few_shot_explains_its_own_pun(persona):
    from app.voice.scorer import _EXPLAIN_PUN_RE

    for shot in persona.raw.get("few_shots") or []:
        reply = str(shot.get("reply") or "")
        m = _EXPLAIN_PUN_RE.search(reply)
        assert not m, f"示例自己解释梗（命中「{m.group(0)}」）：{reply[:30]}"


def test_few_shots_do_not_use_banned_phrasings(persona):
    banned = [
        str(p).strip()
        for p in ((persona.raw.get("anti_ai_flavor") or {}).get("phrasings") or [])
        if len(str(p).strip()) >= 4
    ]
    for shot in persona.raw.get("few_shots") or []:
        reply = str(shot.get("reply") or "")
        hit = [b for b in banned if b in reply]
        assert not hit, f"示例用了自己禁掉的句子 {hit}：{reply[:30]}"


def test_few_shot_lengths_are_uneven(persona):
    """示例长度必须参差。三条一样长，模型会学成同一个形状。"""
    lens = [len("".join(str(s["reply"]).split())) for s in persona.raw.get("few_shots") or []]
    assert max(lens) - min(lens) >= 40, f"样本长度太齐：{sorted(lens)}"
