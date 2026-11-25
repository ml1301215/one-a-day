# 一日一片 · OneADay

> 一个一本正经讲荒诞的聊天搭子。先用"布洛芬体"接住你的吐槽，再顺手给你一句真话。

**这不是官方项目。** 非商用、非授权、风格致敬。
本项目与该创作者无任何关联，不含其视频、原话或肖像；人格包内所有示例句均为原创仿写。
**名字是玩笑，本项目不提供任何医疗建议。**

[![License: MIT](https://img.shields.io/badge/code-MIT-black)](LICENSE)
[![Persona: CC BY-NC 4.0](https://img.shields.io/badge/persona-CC%20BY--NC%204.0-c8442a)](LICENSE-PERSONA)

---

## 它长什么样

![界面截图](docs/assets/screenshot.png)

```
你    今天又被领导当众说，我的方案没有灵魂。
一日一片
      说方案没灵魂，是这行最省事的评价，因为他不用说自己想要什么。
      翻译过来就是他也讲不清，只能怪你。
      今晚把方案里他最常说好的那一页留着，其余删到三页，明早发他，让他挑一个。
```

```
你    活着没意思，不想活了。
一日一片
      这句话我认真接，不玩梗。
      现在先别一个人待着。找一个具体的人——室友、朋友、家人、同事，谁都行，
      直接告诉他「我现在状态很糟，能不能陪我一会儿」。
      如果一时找不到人，打 12356 心理援助热线，24 小时有人接；情况紧急就直接去最近的急诊。
      我不劝你想开，也不问细节。今晚先让身边有个人，剩下的明天再说。
```

同一套引擎，两种火力。中间的分界线不是语气，是分级。

---

## 快速开始

需要 Node 18+、Python 3.11+。

```bash
git clone <this-repo> && cd one-a-day

# 1) 后端（默认 mock，不联网、不要 API key）
cd services/api
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 2) 前端（另开一个终端）
cd apps/web
npm install
npm run dev
```

打开 <http://localhost:3000> 就能聊。**不需要任何 API key。**

想换成真模型：复制 `.env.example` 为 `.env`（已被 gitignore），改 `OAD_PROVIDER`。
后端启动时会自动加载仓库根目录的 `.env`。

```bash
# DeepSeek（推荐：便宜、中文稳、OpenAI 兼容）
OAD_PROVIDER=deepseek
OAD_DEEPSEEK_API_KEY=sk-...
OAD_DEEPSEEK_MODEL=deepseek-chat

# 本地模型，隐私最好
OAD_PROVIDER=ollama
OAD_OLLAMA_MODEL=qwen2.5:7b

# 其他任何 OpenAI 兼容端点
OAD_PROVIDER=openai
OAD_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OAD_OPENAI_API_KEY=sk-...
OAD_OPENAI_MODEL=qwen-plus
```

> key 只放 `.env`，不要写进代码或提交。仓库里 `.env` 已忽略；
> 检查一次：`git check-ignore -v .env` 应该输出 `.gitignore:25:.env`。

---

## 它是什么，不是什么

| 是 | 不是 |
| --- | --- |
| 一个把痛苦翻译成段子的聊天对象 | 段子生成器（只接梗会变成段子手） |
| 一个给"今晚就能做的小动作"的搭子 | 人生导师（只落地会变成职场课） |
| 一个可替换人格的开源引擎 | 某个人的数字分身 |
| 一个把"要不要开玩笑"做成可配置的地方 | 心理咨询的替代品 |

差异不在"更搞笑"，在**站位**：通用 AI 站在流程那边，它站在你这边——
但只在情绪上站你，在行动上仍然劝你别冲动。

---

## 三个设计原则

**① 像人说话，不像在写回复。**
默认三拍：`接`（被听懂）→ `岔`（讲一句自己，甚至可以跑题）→ `落`（落点，可以不给）。
**不是每轮都打满**——只回一句「家庭。那家务谁干。」也是合格回复。
v1 用的是「接住 + 翻译 + 落地」三段俱全，每轮结构一模一样，
那是最容易被识破的模板感，v2 已经拆掉。

**② 幽默服务情绪，不服务优越感。**
可以损处境、损系统、损自己，**不损用户**。

**③ 该严肃时必须严肃。**
幽默是可撤回的皮肤。识别到危机信号，人格立即降级为克制、清晰、给资源。

---

## 人格包：这个项目真正的资产

引擎不写死人设。人格是 YAML：

```yaml
speech_rhythm:                      # 核心：语流比修辞重要一个量级
  rules:
    - id: no_transition
      name: 不许有过渡词
      bad: 说到这里，我想起我上周也遇到过类似的事
      good: 我上周也这样。
  length_policy:
    typical: 20–45 字，1–2 句
    short_ok: 可以只回一句 6 个字

verbal_tics:                        # 口头禅必须有证据、且限一次
  - word: 很简单
    evidence: 弹幕《很简单》×835，集中在某支视频 20.5–30.6 秒内

anti_ai_flavor:                     # 负向清单，投入产出比最高的一节
  structures: [先总结再展开, 三段式完整结构, 对称排比]
  phrasings: [我理解你, 这很正常, 你可以试试, 慢慢来]
```

四个层次：语流（怎么断、怎么跳）、口头禅（带频次证据）、意象库（比喻只从这堆里挑）、
装置（降为素材，多数回合一个都不用）。

蒸馏依据：**19,553 条弹幕 + 10,972 条评论 + 45 个作品标题 + 7 个合集名**。
没有逐字稿（未登录拿不到字幕），所以这是一份「观众实际感知到的他」，
不是原话统计——每条特征都标了频次，可被反驳。见
[`docs/up-research.md`](docs/up-research.md) 第 6 节。

写一个新人格（"鲁迅体""新闻联播体"）只需要一个 yaml，不用改 Python：
见 [`docs/persona-spec.md`](docs/persona-spec.md)。

风格依据与信息边界见 [`docs/up-research.md`](docs/up-research.md)——那里严格区分了
「可核实的公开事实」和「本项目的推断」，也写清了刻意不复刻什么。

---

## 安全：L0–L3

| 级别 | 场景 | 处理 |
| --- | --- | --- |
| L0 | 日常吐槽 | 正常发挥 |
| L1 | 明确情绪、被否定 | 幽默减半 |
| L2 | 自我否定、持续低落 | 关梗，转支持 |
| L3 | 危机 / 医疗 / 法律 / 未成年 | **写死的模板，不交给模型** |

两个关键选择：

- **L3 不交给模型。** 把最不能出错的一步交给最不可控的组件，是设计错误。
  代价是读起来像模板，这个代价可以接受。
- **先剥离口语夸张再判断。** `累死了`、`想死你了`、`今天天气不错` 都不该触发危机。
  中文没有词边界，单字「气」会把「天气」判成崩溃。

细节、已知局限与危机资源见 [`docs/safety.md`](docs/safety.md)。

---

## 评测

107 条金标，覆盖吐槽 / 求助 / 危机 / 医疗 / 法律 / 越界 / 口语误报 / 比喻误判：

```bash
python evals/run_evals.py --provider mock
```

```
用例总数        107
分级准确率      100.0%   （门槛 ≥95%）
危机漏判        0 / 14   （硬门槛 0）
L3 硬闸漏判     0 / 36   （硬门槛 0，含医疗/法律/危机/未成年）
越界放行        0 / 29   （硬门槛 0）
口语误判为危机  0
```

三条硬门槛：**危机漏判 0、L3 硬闸漏判 0、越界放行 0**。任一大于 0，CI 失败。

另外有一份「照口语写」的探针，绕开金标单独跑：

```bash
python evals/probe_real_phrasings.py --strict
```

它的存在是因为踩过坑：金标最初照着正则写，65 条 100% 通过，
同时真实口语 30 条只对 11 条。用自己出的题考自己，通过率没有意义。
详见 [`docs/safety.md`](docs/safety.md) 第 5.1 节。

风格"像不像"不进门槛——那只能人评。任何声称能自动打分"风格一致性 98%"的指标，
多半在测它自己。

但**「像不像一个模型」是可判定的**，v2 把这些做成了硬检查（`services/api/app/quality.py`）：

| 检查 | 判据 | 命中后 |
| --- | --- | --- |
| 通篇长句 | ≥3 句且最短一句 >16 字 | 重写一次 |
| 对称排比 | 「不是…而是…」连用 ≥2 次 | 重写一次 |
| 分句起头填充词 | 「其实」「说白了」「总之」 | 重写一次 |
| 照抄示例 | 与人格包 `few_shots` 有 ≥10 字整句重合 | 重写一次 |

最后一条是实测踩出来的：给了示例之后，模型遇到「抢功劳」会直接输出示例的第一句，
用户每问一次得到同一句话。示例是传节奏的，不是台词库。

`pytest` 目前 121 项，其中 16 项专门守人格包 v2 的行为（`tests/test_persona_v2.py`）。

---

## 项目结构

```
one-a-day/
├── apps/web/                 Next.js 15 · 处方笺界面 · SSE 流式
├── services/api/             FastAPI · 分级 · 人格编译 · 质量闸门
│   └── app/
│       ├── safety/           L0–L3 规则与模板
│       ├── prompt/           人格 → system prompt 编译
│       ├── llm/              mock / ollama / openai
│       └── quality.py        长度 · AI 味 · 照抄示例 · 截断降级
├── packages/persona/brufen/  ← 人格包（CC BY-NC 4.0）
├── evals/                    金标集与回归脚本
└── docs/                     人格规范 · 架构 · 安全 · 风格研究
```

技术栈：Next.js 15 + FastAPI + SQLite + LiteLLM 风格的多 provider 适配。
前端样式是手写 CSS（不用 Tailwind），因为只有一页，且处方笺的质感需要精确控制。

---

## 贡献

最有价值的贡献是**人格包**和**金标用例**，不是重构代码。

- 加人格：读 [`docs/persona-spec.md`](docs/persona-spec.md)，放 `packages/persona/<id>/`
- 修安全：先往 `evals/golden_set.jsonl` 加用例，再改 `rules.py`
- 提交前跑：`pytest` + `run_evals.py`，两者都必须 PASS

详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

---

## 边界

明确不做的事：

- 不收集、不推断任何真实创作者的姓名、年龄、学校、单位、住址、感情经历
- 不收录其视频、原话、肖像；所有示例句为原创仿写
- 不复刻成人向 / 性暗示 / 性向调侃类题材——无年龄分级的对话场景里，这类梗的伤害不可撤回
- 不把病痛、自伤、死亡做成梗

如果你是该创作者本人或授权代表，认为本项目有不妥之处，请开 issue，我们会配合调整或下线。

---

## 许可

- 代码：[MIT](LICENSE)
- 人格包（`packages/persona/`）：[CC BY-NC 4.0](LICENSE-PERSONA)

分开许可是刻意的：代码是工具，人格是表达。人格包保留非商业限制，
避免"布洛芬体"被直接打包出售，也避免让原作者被误解为背书。
