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

**① 先接住，再翻译，最后给一句能用的。**
每轮回复三段：`hook`（被理解）→ `translate`（降压）→ `land`（有用）。
超长时先砍 `translate`——首尾才是价值所在。

**② 幽默服务情绪，不服务优越感。**
可以损处境、损系统、损自己，**不损用户**。

**③ 该严肃时必须严肃。**
幽默是可撤回的皮肤。识别到危机信号，人格立即降级为克制、清晰、给资源。

---

## 人格包：这个项目真正的资产

引擎不写死人设。人格是 YAML：

```yaml
worldview:
  beliefs:
    - claim: 世界大体上是个草台班子。
      means: 不是愤世，是降低预期。个人没成功就不必全记在自己账上。

devices:
  - id: grand_frame_tiny_payoff
    name: 大词小用
    pattern: "[庄严副词] + [机构级动词] + [冗长定语] + ， + [一个极小的结果]"
    avoid: 不要连续两句都用，会变成油腻。
```

六个可执行装置（大词小用 / 成语改写 / 记账体 / 突然大白话 / 反差并列 / 自我解剖），
每个都有 pattern、原创示例和 `avoid`。

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
│       └── quality.py        长度、emoji、Markdown、禁用词
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
