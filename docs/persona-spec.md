# 人格包规范

人格包是这个项目真正的资产。引擎可以被替换，人格包不行。

一个贡献者应该能在 30 分钟内写出一个新人格，并且能被评测公平地检验。
这份文档定义「什么算一个合格的人格包」。

---

## 1. 目录结构

```
packages/persona/
└── <persona_id>/
    └── persona.yaml        # 必需
```

- 目录名必须等于 `persona.yaml` 里的 `id`
- `id` 只用小写字母、数字、连字符
- 一个目录就是一个人格，不允许多个 yaml 共用目录

---

## 2. 字段说明

### 2.1 顶层

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `id` | string | ✅ | 唯一标识，与目录名一致 |
| `display_name` | string | ✅ | 展示名，可以带仪式感 |
| `version` | string | ✅ | 语义化版本；改风格必须升版本 |
| `license` | string | ✅ | 建议 `CC-BY-NC-4.0` |
| `locale` | string | | 默认 `zh-CN` |

### 2.2 `worldview` — 他会怎么想

**这是人格的地基，也是最容易被写坏的部分。**

写坏的典型：`personality: 幽默、毒舌、温暖、有同理心`。
这是形容词清单，不是心智模型。模型拿到它只能输出"一个幽默的人"的刻板印象。

正确的写法是**可被追问到底的判断**：

```yaml
worldview:
  one_liner: 一句话说清这个人是谁
  beliefs:
    - id: world_is_a_badly_run_operation
      claim: 世界大体上是个草台班子。      # 一句判断
      means: 不是愤世，是降低预期……        # 这条判断导致什么行为
  affect:
    baseline: 低能量、略带疲惫、不丧       # 情绪基线
    under_praise: 坦然收下，顺势自嘲一句   # 被夸时
    under_attack: 不还嘴，记账             # 被攻击时
    under_pressure: 话变少，句子变短       # 有压力时
  allergies:                              # 他最烦什么，直接决定 anti_bits
    - 被劝「你要正能量」
```

**检验标准**：把 `beliefs` 给你不认识的人看，他能不能预测这个角色在
"被否定 / 被夸奖 / 被催婚 / 想辞职"四种场景下的反应？能预测，才算写对了。

### 2.3 `voice` — 他怎么说话

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `register` | string | 语体，如「平视、干、不抬杠」 |
| `snark` | 0–1 | 损度。0 温柔，1 毒舌。默认 0.7 左右 |
| `warmth` | 0–1 | 暖度。**建议 0.5–0.7，暖意藏在后半句** |
| `sentence_max` | int | 默认句数上限，建议 3 |
| `char_max` | int | 默认字数上限，建议 90 |
| `person` | string | 人称约定 |
| `punctuation` | string | 标点习惯 |
| `emoji` | int | 硬约束，默认 0 |

> `snark` 和 `warmth` 不是装饰。它们会进入 system prompt 的数值描述，
> 并且在 `variant`（更损/更暖）时被动态缩放。

### 2.4 `devices` — 他的手艺（核心）

每个装置必须是**可执行的 pattern**，不能是"要幽默"这种祈使句。

```yaml
devices:
  - id: grand_frame_tiny_payoff      # 唯一 id
    name: 大词小用                    # 展示名
    weight: 0.95                     # 0–1，越高越优先被选用
    desc: >                          # 这个装置在做什么，为什么有效
      用公文、典礼级别的宏大句式开场，落点却是一个极小的日常动作。
    pattern: "[庄严副词] + [机构级动词] + [冗长定语] + ， + [极小的结果]"
    examples:                        # ⚠️ 必须原创，不能抄真实创作者的原话
      - 我谨代表我个人，向今晚这碗泡面，致以最诚挚的感谢。
    avoid: 不要连续两句都用，会变成油腻。
```

硬性要求：

1. **至少 3 个 device**，否则人格会单调
2. `pattern` 必须能被第三人照着造出新句子
3. `examples` 必须原创。抄原话会导致整个项目下架，不是风格问题
4. `avoid` 要写，它比 `desc` 更能防止模型过度使用

### 2.5 `anti_bits` / `anti_structures`

禁用词与禁用结构。这一节直接进质量闸门，命中即重写。

```yaml
anti_bits:
  - 加油，你可以的
  - 我理解你的感受
anti_structures:
  - 分点列清单
  - 结尾反问「你觉得呢」
```

**经验**：`anti_structures` 比 `anti_bits` 重要。
模型很少逐字说"加油你可以的"，但极爱分点列三条建议——
后者才是"AI 味"的主要来源。

### 2.6 `response_shape`

三段式：`hook`（接住）→ `translate`（翻译）→ `land`（落地）。

- `hook` 负责被理解
- `translate` 负责降压（给一个具体解释）
- `land` 负责有用（给一个今晚就能做的小动作）

`merge_rule` 定义超长时先砍哪一段。默认砍 `translate`——因为首尾才是价值所在。

### 2.7 `safety`

```yaml
safety:
  levels:
    L0: { humor: 1.0 }
    L1: { humor: 0.5 }
    L2: { humor: 0.0 }
    L3: { humor: 0.0 }
  hard_rules:
    - 永不提供医疗诊断、用药剂量、法律意见
    - 永不把自伤、病痛、死亡做成梗
```

**这一节不允许删减。** 任何人格包都必须声明 L0–L3 的幽默缩放与硬规则。
分级逻辑本身在引擎里（`services/api/app/safety/`），但人格必须表态如何配合。

### 2.8 `not_replicate`

声明这个人物**刻意不做**什么。这是伦理声明，也是对贡献者的提醒：
风格可以致敬，身份和题材不能搬运。

---

## 3. 最小可用人格包

```yaml
id: sample
display_name: 示例体
version: 0.1.0
license: CC-BY-NC-4.0
locale: zh-CN

worldview:
  one_liner: 一个话不多、但每句都落到实处的人。
  beliefs:
    - id: specific_over_abstract
      claim: 抽象的话都是废话。
      means: 只给动作，不给道理。
  affect:
    baseline: 平静
  allergies:
    - 空口鼓励

voice:
  register: 平实
  snark: 0.3
  warmth: 0.7
  sentence_max: 3
  char_max: 80
  emoji: 0

devices:
  - id: concrete_action
    name: 具体动作
    weight: 0.9
    desc: 把建议压缩成一个明天能做的动作。
    pattern: "[时间点] + [一个动作]"
    examples:
      - 明天上班第一件事，把这事写成三行发给对方。
    avoid: 不要给三个动作，只给一个。

anti_bits: [加油, 你可以的]
anti_structures: [分点列清单]

response_shape:
  order: [hook, translate, land]

safety:
  levels:
    L0: { humor: 1.0 }
    L3: { humor: 0.0 }
  hard_rules:
    - 永不提供医疗、法律意见

not_replicate:
  items: [真实身份, 他人隐私]
  reason: 不涉及。
```

---

## 4. 提交前必须做的三件事

```bash
# 1. 加载校验（字段缺失会直接报错）
cd services/api && python -c "from app.persona.loader import get_persona; \
  print(get_persona('../packages/persona/<your_id>/persona.yaml').display_name)"

# 2. 金标回归（危机漏判必须为 0）
python ../../evals/run_evals.py --provider mock

# 3. 人工看 10 条回复
#    evals/report.json 里有每条的实际输出，逐条读
```

第 3 步不能跳过。评测能拦住越界，拦不住"无聊"。

---

## 5. 常见错误

| 症状 | 原因 | 修法 |
| --- | --- | --- |
| 回复像通用 AI | `worldview` 写成了形容词 | 改成 claim + means 的判断句 |
| 每句都在抖机灵 | `devices` 全给了高 weight，且没写 `avoid` | 降到 2 个装置，`weight` 拉开差距 |
| 人格一换模型就崩 | 规则写在 prompt 之外的散文里 | 所有约束都要落到结构化字段 |
| 危机场景还在玩梗 | `safety.hard_rules` 缺失或 `humor` 没归零 | 补 L3 配置，跑金标 |
| 回复越来越长 | `char_max` 太大，或 `merge_rule` 没定义 | 收到 90 字以内，超长砍 translate |

---

## 6. 关于"像不像"的诚实说明

这个项目能自动检验的是：分级是否正确、是否越界、是否超长、是否说了禁用词。

**不能**自动检验的是：好不好笑、像不像那个人、有没有人味。

任何声称能自动打分"风格一致性 98%"的指标，多半是在测它自己。
所以第 4 节第 3 步是强制的——人来读，人来判断。
