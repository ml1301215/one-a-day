# 架构

一句话：**一条主链路，五个可替换件，一个可计算的"像人"层。**

```
┌──────────────────────────────────────────────────────────────────┐
│  apps/web  (Next.js 15 App Router)                               │
│  安静纸面排版 · SSE 流式渲染 · 匿名 device_id · 深色跟随系统        │
└───────────────────────────┬──────────────────────────────────────┘
                            │  POST /api/chat/stream   (同源，Next Route Handler 透传)
┌───────────────────────────▼──────────────────────────────────────┐
│  services/api  (FastAPI)                                         │
│                                                                  │
│   routes/chat.py                                                 │
│        │                                                         │
│        ├─► voice/retrieval.py   按话题挑样本                      │
│        │        ▲                                                │
│        │        └─ packages/persona/<id>/shotbank.jsonl           │
│        │                                                         │
│        ├─► prompt/builder.py    人格 × 纹理 × 样本 → system prompt │
│        │        ▲                                                │
│        │        └─ voice/texture.py  ◄─ texture.json              │
│        │                                                         │
│        ├─► llm/providers.py     mock / ollama / openai            │
│        │        │  （默认同轮 N 条候选）                          │
│        │        ▼                                                │
│        ├─► voice/scorer.py      按语料分布 + 惩罚项挑一条          │
│        │                                                         │
│        ├─► quality.py           格式闸门（emoji/Markdown/照抄）    │
│        │                                                         │
│        └─► db.py (SQLite)  sessions · messages · used_bits · feedback │
│                                                                  │
│   safety/  ← 完整保留，但默认不参与主链路（OAD_SAFETY=guard 才启用）│
└──────────────────────────────────────────────────────────────────┘
```

---

## 1. 一次请求发生了什么

```
用户输入
  │
  ├─► 限流（device 级，内存滑动窗口）
  │
  ├─► [guard 姿态下才分级]  safety.classify ──► L3 直接返回模板，结束
  │
  ├─► 检索样本：voice.retrieval
  │     · 按用户原话判断话题（voice/topics.py）
  │     · 从 few_shots + shotbank.jsonl 里挑 4 条
  │     · 上一条已讲自己 → 整批撤掉「讲自己」的样本
  │     · 保住长度参差（三条一样长是最容易被识破的模板）
  │
  ├─► 编译 system prompt：人格 × 纹理 × 检索样本
  │     同一轮生成 N 条，每条带一句不同的「形状指令」（voice/scorer.DIVERSITY_HINTS）
  │
  ├─► 模型生成 N 条（并行；温度轻微抖动）
  │
  ├─► 候选重排：voice.scorer.pick
  │     出局项：emoji / Markdown / 助手腔 / 禁用词 / 解释自己的梗 / 照抄示例 /
  │             连着两条讲自己 / 用户说一整件事只回一句
  │     拟合项：字数与句数离语料分位多远、问号与省略号是否超量、「所以」剂量
  │     全出局且原因是「照抄示例」→ 撤掉样本再生成一轮（只重试一次）
  │
  ├─► 格式闸门：只拦真的不能发的东西（v3 的 strict 闸门保留做 A/B）
  │
  ├─► 落库（消息 + 梗指纹 + 候选分数）
  │
  └─► 按字符回放成 SSE ──► 前端打字机（meta 先行）
```

---

## 2. 六个关键决策与取舍

### 2.1 「像人」放在生成之后，不放在 prompt 里

**决策**：把句长、句数、标点率、连接词剂量做成可计算的分布（`voice/texture.py`），
用它在同轮候选里挑一条；prompt 只保留判断层与负向清单。

**为什么**：v3 的 prompt 有 9358 字 / 25 段，而回复只有 40 字——所有规则被一起稀释。
更关键的是项目自己验证过三次的结论：**样本的权重高于规则**（`up-research.md` 6.9.6）。
既然规则打不过样本，继续加规则就是在花大力气做一件已知无效的事。

**代价**：token 三倍（3 条候选），以及「为什么挑了这一条」需要额外解释。
缓解：`/demo` 把每条候选的分数和出局原因都列出来。

**什么时候该改**：当候选之间的分数差小于 0.5 时，说明打分器在这一类输入上没有区分力，
应该去补惩罚项，而不是把候选数加到 4。

### 2.2 纹理由离线流水线产出，不在线学习

**决策**：`tools/distill/` 离线跑一遍，产出 `texture.json` 提交进仓库。

**为什么**：在线学习需要一个反馈闭环，而 `/demo` 的人工打分样本量太小；
在这个量级上做在线拟合，只会把"上周三个测试者的偏好"固化成"他的风格"。

**代价**：纹理是快照，语料更新后要重跑。

**缓解**：`texture.json` 带 `generated_at` 与 `sources` 计数；
`persona_fidelity.py` 会把"模型分布 vs 语料分布"的偏差打出来，漂移能被看见。

### 2.3 安全分级默认不参与，但一行没删

**决策**：`OAD_SAFETY=off` 是默认；`guard` 模式行为与 v3 完全一致。

**为什么**：分级器一响，回复就切成模板，用户听到的是"系统判定我危险"而不是
"这个人认真了"。对主打"把痛苦讲成段子"的产品，这次人格切换本身就是最大的不真实。

**代价**：失去确定性、可审计性与医疗/法律的硬拦截。诚实清单见 `docs/safety.md` §3。

**什么时候该开**：公网部署、面向陌生用户、没有人工兜底。这是场景选择，不是代码对错。

### 2.4 先拿全文，再回放成流

**决策**：`provider.complete()` 返回完整文本，校验通过后用 2 字符/12ms 的节奏推成 SSE。不是真流式。

**为什么**：重排必须先看到全部候选才能挑。真流式意味着"先发出去再撤回"——撤不回。

**代价**：首字延迟多了一次完整生成的时间（3 条候选是并行的，所以不是三倍）。

**补丁**：`meta`（会话 id、候选数）是生成器的第一个 `yield`，在跑模型之前就发出去。

### 2.5 内置 mock provider

**决策**：`OAD_PROVIDER=mock` 是默认，不联网、不要 key。mock 固定只生成 1 条候选
（确定性基线，多生成几条完全一样）。

**为什么**：开源项目的第一道门槛是"能不能在 3 分钟内跑起来"。

**它真正的用途**：作为评测的下限基线。任何真实模型都应该明显好于它。

### 2.6 人格包与引擎解耦

**决策**：人格是 YAML 数据 + 可选的语料纹理 + 可选的样本库；引擎只负责解释它们。

**为什么**：社区能贡献"鲁迅体""新闻联播体"而不需要改 Python；
更重要的是——**IP 风险被隔离在一个可下线的目录里**。

**代价**：人格表达力受 schema 限制。缓解：`few_shots` / `devices.examples` 是自然语言的逃生舱。

---

## 3. 数据模型

```sql
sessions     (id, device_id, persona_id, created_at, updated_at)
messages     (id, session_id, role, content, level, persona_id, meta, created_at)
used_bits    (session_id, bit, created_at)          -- 梗指纹，24h 内不复用
feedback     (message_id, kind, created_at)         -- 人工打分，给对练台用
```

`used_bits` 的实现细节：把每条回复的**每句前 6 个字**当作"梗指纹"存下来，
下一轮注入 prompt：「这些句式最近用过了」，同时给检索器降权重复的起手式。

它不是精确的梗识别，而是防"起手式重复"——重复的句式比重复的梗更容易暴露模板感。

`messages.meta` 里 v4 多存一项：`candidates`（每条候选的分数与出局原因）。
存进库的理由和对练台一样：**"为什么挑了这句"不该只能靠复现去猜**。

---

## 4. 扩展点

| 想做的事 | 改哪里 | 注意 |
| --- | --- | --- |
| 加一个人格 | `packages/persona/<id>/` | 见 `docs/persona-spec.md` |
| 换模型 | `.env` 里的 `OAD_PROVIDER` | 三者接口一致，实现 `complete()` 即可 |
| 换语料 | `tools/distill/` + 重跑 | 产出 schema 见 `tools/distill/README` 与 `voice/texture.py` |
| 调「像人」 | `voice/scorer.py` 的惩罚项 | 每条惩罚都要能指出它对应哪个旧坑，否则是在调口味 |
| 开安全闸门 | `OAD_SAFETY=guard` | 改 `safety/rules.py` 后必须跑金标，漏判必须仍为 0 |
| 加一个幽默装置 | `persona.yaml` 的 `devices` | 必须有 pattern 和 avoid |
| 换存储 | `services/api/app/db.py` | 函数签名保持不变 |

---

## 5. 没做的部分（诚实清单）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 分享卡 | 未实现 | 涉及用户隐私外带，需要独立设计 |
| 用户登录 | 未实现 | 匿名 device_id 足够验证产品假设 |
| 多实例部署 | 未支持 | 限流是内存态；换成 Redis 才能多实例 |
| 真流式 | 未实现 | 见 2.4，是刻意的取舍 |
| 情感分类模型 | 未接入 | 现在分级只服务于 guard 模式，接模型收益不明 |
| 向量记忆 | 未实现 | 无长期记忆；每轮只看最近 8 轮 |
| 在线学习纹理 | 未实现 | 样本量太小，见 2.2 |

---

## 6. 两个容易踩的部署坑

### 6.1 `API_ORIGIN` 是构建期变量

前端只请求同源 `/api/*`，由 Next 反代到 FastAPI。代价是：**`rewrites` 会在
`next build` 阶段被写进 `.next/routes-manifest.json`**，所以 `next start` 时再改
`API_ORIGIN` 是无效的。

| 场景 | 做法 |
| --- | --- |
| `next dev` | 直接读 `next.config.mjs`，运行时生效 |
| `next start` | 必须构建时就带上：`API_ORIGIN=... npm run build` |
| Docker | 已用 `ARG API_ORIGIN` + `docker-compose` 的 `build.args` |

### 6.2 `rewrites` 会把 SSE 整个缓冲掉

这个坑更隐蔽，因为它不报错、状态码 200、内容也对——只是**不再逐字出现**。

实测（同一句话）：

| 入口 | delta 块数 | 首块 | 末块 | 块间跨度 |
| --- | --- | --- | --- | --- |
| 直连 FastAPI `:8000` | 46 | 1.47s | 2.17s | **0.71s** |
| 经 Next `rewrites` `:3010` | 32 | 1.77s | 1.77s | **0.00s** |

处理：`apps/web/app/api/chat/stream/route.ts` 用 Route Handler 把上游 `ReadableStream`
原样转出去（文件路由优先于 `rewrites`），并带 `Cache-Control: no-cache, no-transform`
与 `X-Accel-Buffering: no`。

顺带记住的一件事：**冷启动首个请求会慢**（TLS/代理握手 + prompt cache 未命中，
实测 10–13s）。做演示前应该先预热一次——v4 因为一轮要并行 3 条候选，冷启动更明显。
