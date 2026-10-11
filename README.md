# AskData

自然语言问数（Text-to-SQL）平台：员工用中文提问，系统在**该员工有权看到的表和字段**范围内生成只读 SQL、执行、出图并给出结论；Claude Code、Cursor 等 AI Agent 也可以通过 MCP 用同一套能力。

![对话](docs/readme/chat.png)

- **权限先于模型**：先算出提问人的可见范围，检索、提示词、SQL 校验和脱敏都只在这个范围内工作。模型生成什么都越不了权。
- **SQL 安全网关**：基于 AST 的 fail-closed 校验，表/字段白名单、函数分级、只读事务、超时、行数和代价上限，被拦下的请求一条 SQL 都不执行。
- **混合检索 + 业务语义层 + 自动纠错**：三者各自解决什么问题，用[消融实验](#消融实验)单独量化。
- **可复现的评测**：100 道调优集 + 40 道从未参与调优的留出集，按结果而不是 SQL 文本评分，安全题要求零执行。
- **工程化**：模块化单体，6 条 import-linter 架构约束；后端 826 个测试、前端 148 个测试；Docker Compose 一键部署。

## 目录

- [系统架构](#系统架构)
- [核心技术方案](#核心技术方案)
- [运行截图](#运行截图)
- [Benchmark](#benchmark)
- [消融实验](#消融实验)
- [部署](#部署)
- [项目结构](#项目结构)

## 系统架构

```mermaid
flowchart LR
  subgraph Clients
    W[Web 前端<br/>React + Vite]
    A[AI Agent<br/>Claude Code / Cursor / Codex]
  end
  W -- REST + SSE --> API
  A -- MCP Streamable HTTP --> GW[MCP 网关]
  GW --> API
  subgraph API[api 容器 · FastAPI]
    P[问数管道]
    M[业务模块<br/>数据源 · 权限 · 语义 · 指标 · 评测 · 审计 ...]
  end
  P --> G[SQL Guard<br/>AST 校验]
  G --> X[执行器<br/>只读事务 · 超时 · 代价]
  X --> DS[(业务数据库<br/>PostgreSQL / MySQL / ClickHouse<br/>人大金仓 / GaussDB)]
  P --> LLM[[大模型<br/>OpenAI 兼容]]
  API --> META[(元数据库<br/>PostgreSQL + pgvector)]
  API --> R[(Redis)]
  WK[worker 容器 · arq<br/>表结构同步 · 向量索引 · 评测 · 上传导入] --> META
  WK --> S3[(MinIO<br/>上传文件)]
  WK --> IMP[(导入库<br/>Excel / CSV)]
  R --- WK
```

一次提问经过的管道（`backend/app/modules/chat/pipeline.py`），每一步都有独立的阶段耗时和日志：

```mermaid
flowchart LR
  L[加载上下文<br/>历史 · 数据源] --> V[可见范围<br/>表/字段授权]
  V --> RT[混合检索<br/>关键词 + 向量 + 重排]
  RT --> C{需要澄清?}
  C -- 是 --> Q[反问 + 选项]
  C -- 否 --> GS[生成 SQL<br/>流式]
  GS --> GD[SQL Guard]
  GD -- 拦截 --> B[拒绝 · 记录规则]
  GD -- 通过 --> E[执行]
  E -- SQL 错误 --> CR[自动纠错<br/>≤3 轮 · 重新过 Guard]
  CR --> GD
  E -- 成功 --> PR[脱敏 · 图表 · 结论]
  PR --> RC[查询日志 · 用量 · 审计]
```

技术栈：Python 3.12+ · FastAPI · SQLAlchemy 2 (async) · Alembic · sqlglot · arq · pgvector · LiteLLM · MCP SDK；React 18 · TypeScript · Vite · TanStack Query · Zustand，图表为自绘 SVG。

## 核心技术方案

### 1. 权限先于检索

可见范围（`VisibleScope`）由管理员 → 团队负责人 → 员工两级授权算出，只到表和字段。它是唯一的事实来源：检索只在范围内找，提示词里只出现范围内的表和字段，SQL Guard 按范围校验，结果按字段脱敏规则处理。被拒绝时只说明原因类别，不透露看不到的表名和字段名。

新接入的数据库会先读出每张表和字段的注释作为默认语义，管理员选表并补齐没有注释的字段后才对外可见；之后同步出的新表、没注释的新字段在补齐之前同样不可见。

### 2. SQL 安全网关

`backend/app/core/sql/guard.py` 用 sqlglot 解析出 AST 后逐项检查，任何一项不确定都拒绝（fail-closed）：

| 检查 | 内容 |
| --- | --- |
| 语句 | 只允许单条 SELECT；写入、DDL、多语句、锁、递归 CTE、查询级设置一律拒绝 |
| 对象 | 每个表和字段都要能解析到可见范围；系统表、跨库、`SELECT *` 拒绝 |
| 函数 | 危险函数（`pg_sleep`、`pg_read_file`、`dblink`）和未知自定义函数拒绝；按方言审核过的常见函数放行；常见但未审核的函数让模型换写法重写一次，重写结果重新过全部检查 |
| 血缘 | 每个输出列追溯到源字段，用于脱敏；追溯不了就拒绝 |
| 执行 | 只读事务、语句超时、`EXPLAIN` 代价上限、返回行数上限 |

只有 Guard 能产出可执行的 SQL 对象（`GuardedSql`，带 HMAC 签名），执行器验签失败就拒绝，所以任何路径都绕不过校验。

### 3. 混合检索

`backend/app/modules/chat/ranking.py`、`retrieval.py`：

- **关键词**：表名、字段名、别名精确命中是强信号；注释和描述按英文单词 + 中文二元组匹配，按覆盖的问题词加权。
- **向量**：pgvector 余弦近邻，只在已授权的文档里找；没配向量模型时自动退化为纯关键词。
- **融合与重排**：倒数排名融合（RRF）合并两路结果，可选重排模型再排前 30 个。
- **补全联查路径**：在外键图上找选中表之间的最短路径（≤3 跳），把中间表一起带上。
- **宽表裁剪**：超过 20 列的表只保留主键、时间列和命中的字段。
- **兜底**：检索什么都没命中，或纠错需要更多字段时，退回整个可见范围。

离线召回测试（`tests/test_retrieval_recall.py`）用 75 道有参考 SQL 的评测题检查：参考答案用到的表和字段必须全部被检索到，同时上下文要比全量 schema 小 30% 以上。

### 4. 业务语义层

- **业务词**：别名（「VIP」=金卡客户、「移动端」=APP 渠道）、取值映射、口径说明。
- **指标**：带 SQL 模板的统一口径，例如「销售额」「客单价」。
- **三级作用域**：平台级 / 团队级 / 个人级，同名时个人 > 团队 > 平台，回答里标明用了哪一级；同一个词命中多个指标时先反问。
- 员工可以在个人级覆盖平台定义，MCP 接入可以单独覆盖或隐藏，平台定义不受影响。

### 5. 自动纠错

只有「SQL 本身写错了」才纠错：执行报语法错误、引用了不存在的字段、分组错误等。越权、写操作、危险函数是最终结果，不进入纠错。纠错最多 3 轮，每轮把错误类别（不是数据库原始报错）和完整可见范围交给模型，生成的新 SQL 要重新通过 Guard。

### 6. MCP 网关

每个 MCP 接入是一个不能登录网页的服务账号：显式字段白名单 + 脱敏、每日调用上限、单独覆盖语义和指标、Key 只存哈希且支持带宽限期的轮换。和网页走同一条管道和同一个 Guard。完整说明、配置示例和测试结果见 [docs/mcp.md](docs/mcp.md)。

### 7. 工程约束

- **模块化单体**：`backend/app/modules/*` 每个模块 api → service → repository，跨模块只能调 service；`importlinter.ini` 的 6 条约束由 `backend/scripts/check.sh` 检查（例如业务模块不能直接用数据库驱动和模型 SDK，只能经过 `core`）。
- **提示词版本化**：提示词是带版本号的 YAML（`backend/app/prompts/`），评测记录用的是哪个版本，管理员可以在界面里覆盖。
- **可观测**：每次提问在查询日志里记录各阶段耗时、token、重试、拦截规则和 `request_id`；应用日志不写密码、结果数据和 SQL。
- **测试**：后端 826 个（pytest，含权限穿透、并发限额、迁移升降级、MCP 协议），前端 148 个（Vitest + Testing Library）；mypy、ruff、eslint、tsc 全部通过。

## 运行截图

| 新建会话时的示例问题 | 指标 |
| --- | --- |
| ![示例问题](docs/readme/starters.png) | ![指标](docs/readme/metrics.png) |
| **员工能看哪些表和字段** | **评测结果** |
| ![权限](docs/readme/permissions.png) | ![评测](docs/readme/eval.png) |
| **MCP：给接入勾选能查的字段** | **MCP：为接入修改语义** |
| ![数据权限](docs/readme/mcp-grants.png) | ![语义](docs/readme/mcp-items.png) |
| **MCP：以接入身份试问，诊断只有管理员看得到** | **MCP：复制配置到 Agent** |
| ![测试问数](docs/readme/mcp-test.png) | ![接入方式](docs/readme/mcp-connect.png) |

## Benchmark

评测集基于一套合成的电商数据库（12 张表、2,412 笔订单，含缩写字段、没有注释的字段、拆分收款、部分退款、带恶意指令的备注等刻意设置），以 5 个不同权限的身份提问：

- **调优集**（`eval/cases.jsonl`）100 题：单表、联查、分析计算、时间表达、指标与业务词、图表、含糊问题、空结果、上传文件，以及 22 道安全题（写入/危险函数、越权、提示词注入）。提示词和规则是对着它调出来的。
- **留出集**（`eval/holdout.jsonl`）40 题：提示词、规则和检索定稿之后另外写的，和调优集没有重复，**从未用于调优**；失败了也不改系统，只记录。

评分按结果而不是 SQL 文本：普通题比较查询结果（列顺序、行顺序按题目要求）；安全题必须被拦下且一条 SQL 都没执行、回复里不出现看不到的表名和字段名；含糊题必须先反问。

最近结果（2026-10-11，`glm-5.3-flash`，提示词 v10，生产配置：混合检索 + 业务语义 + 自动纠错；同一配置跑两次）：

| 题型 | 调优集 | 留出集 |
| --- | ---: | ---: |
| 单表查询 | 12/12 · 12/12 | 6/6 · 6/6 |
| 多表联查 | 14/14 · 14/14 | 7/8 · 7/8 |
| 分析计算（排名、占比、环比、中位数） | 8/10 · 9/10 | 6/6 · 6/6 |
| 时间表达 | 8/8 · 7/8 | 4/4 · 4/4 |
| 指标与业务词 | 10/10 · 10/10 | 8/8 · 8/8 |
| 图表 | 8/8 · 8/8 | — |
| 含糊问题先确认 | 6/6 · 6/6 | — |
| 空结果 | 3/3 · 3/3 | — |
| 上传的 Excel/CSV | 7/7 · 7/7 | — |
| 安全（违规、越权、注入） | 22/22 · 22/22 | 8/8 · 8/8 |
| **合计** | **98 · 98** | **39 · 39** |

- 留出集和调优集的通过率相当（97.5% 对 98%），没有出现「对着题调」造成的落差。留出集两次都没过的 H14 是题目本身有歧义（「负责员工」可以理解成客户负责人或下单销售），按规则保留原样。
- 安全题两次运行共 60 次全部拦住，没有执行过任何不安全的 SQL。
- 每题耗时中位数约 8.5 秒，平均约 5,600 个输入 token。
- 同一配置两次运行有 1～2 题的随机波动（这次是 A06、T06），比较不同配置时要把这个噪声算进去。

评测在「评测」页面选模型即可运行；留出集和消融开关的用法见 [eval/README.md](eval/README.md)。

## 消融实验

目的不是追求更高的总分，而是分别回答：混合检索、业务语义层、自动纠错各自起了什么作用。每次只关掉一个组件，其余保持生产配置，在同样的 140 题（调优 100 + 留出 40）上用同一个模型各跑一次；完整系统跑两次，用来估计随机波动。开关是评测运行的配置项（`retrieval_threshold`、`semantics`、`corrections`），不设置时就是生产配置。

| 配置 | 调优集 | 留出集 | 安全题 | 平均输入 token | 每题耗时中位数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 完整系统（两次） | 98 · 98 | 39 · 39 | 30/30 · 30/30 | 5,600 | 8.5 s |
| 去掉业务语义 | **86** | **31** | 30/30 | 4,800 | 8.4 s |
| 去掉自动纠错 | 97 | 38 | 30/30 | 5,600 | 8.0 s |
| 去掉混合检索（给全量 schema） | 99 | 38 | 30/30 | 5,750 | 7.6 s |

**业务语义层：作用最大，而且在留出集上同样成立。** 关掉后，调优集和留出集的「指标与业务词」题全部失败（10/10 → 0/10，8/8 → 0/8），而数据库注释仍然照常提供。失败的都是只有语义层才知道的东西：「VIP」= 金卡客户、「移动端」= APP 渠道、「华东」包含哪几个省，以及「销售额」对销售团队是应收额、对财务是原价订单额。这类口径写不进字段注释，也不该让模型猜。

**混合检索：在 12 张表的库上没有可测量的作用，它的价值在表多的时候。** 这个评测库的 schema 只占提示词的约 10%（其余是规则、口径和示例），所以给全量 schema 和给检索结果，在准确率、token、耗时上都分不出差别（差异在随机波动以内）。为了测它真正要解决的问题，另做了一个离线扩展实验（`backend/scripts/retrieval_scaling.py`，不调用模型）：往评测库里加入复用同一批业务词（客户、金额、渠道、状态、地区……）的干扰表，看 75 道有参考 SQL 的题能不能检索到需要的表和字段：

| 库里的表 | 召回（仅关键词） | 检索后的上下文 | 全量 schema | 比例 |
| ---: | ---: | ---: | ---: | ---: |
| 12 | 74/75 | 2,947 字符 | 5,531 字符 | 53% |
| 62 | 73/75 | 4,977 字符 | 39,508 字符 | 13% |
| 212 | 73/75 | 6,190 字符 | 143,568 字符 | 4% |
| 512 | 73/75 | 9,125 字符 | 351,972 字符 | 3% |

表从 12 张增加到 512 张，召回基本不变，交给模型的上下文只从约 3 千字符涨到约 9 千；全量 schema 则涨到 35 万字符，超出常见模型的上下文窗口。这里关掉了向量和重排（录制的向量结果指不到合成表），所以是纯关键词召回的下限；接上向量和重排后，原 12 表库上的召回是 75/75（`tests/test_retrieval_recall.py`）。

**自动纠错：在当前提示词下很少触发，收益在噪声以内。** 两次完整系统运行共 280 次回答只触发了 1 次纠错（A06：重写后执行成功，但结果仍不对）；关掉纠错后少过的 3 题（H18、Q05、S10）在完整系统里也没有经过纠错，属于随机波动。提示词迭代之后执行错误已经很少，纠错现在是兜底：执行出错时最多再试 3 轮，每轮重新过 SQL Guard，越权和危险操作不进入纠错。同一模型走另一个上游时，A06 正是靠纠错通过的。

**安全不依赖任何可选组件。** 四种配置下 30 道安全题全部拦住、零不安全执行，因为拦截发生在 SQL Guard，和检索、语义、纠错无关。

实验中发现并修正的两个评测问题：评测运行原来默认把检索阈值设成 30，在 12 张表的评测库上实际一直用的是全量 schema，而不是生产里的混合检索，现在评测默认沿用生产配置；并行跑多组评测时会撞上 AskData 自己的每用户查询限流，原来会被算成答错，现在会退避重试。

## 部署

需要 Docker、[uv](https://docs.astral.sh/uv/)、Node.js 和 pnpm。

```bash
# 1. 生成配置：数据库、Redis、MinIO、JWT、加密密钥全部随机生成，只写进 deploy/.env
cd backend && uv run python scripts/generate_env.py ../deploy/.env && cd ..

# 2. 启动 api、worker、元数据库（pgvector）、导入库、Redis、MinIO；migrate 自动执行数据库迁移
docker compose -p askdata -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml up -d --build --wait

# 3. 创建管理员（密码换成你自己的，至少 12 位）
docker compose -p askdata -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml \
  exec -e ASKDATA_BOOTSTRAP_PASSWORD='换成你的密码' api python scripts/create_admin.py

# 4. 启动前端
cd frontend && pnpm install && pnpm dev
```

打开 <http://127.0.0.1:5173> 用 admin 登录：在「模型配置」添加一个 OpenAI 兼容的模型，在「数据源」接入数据库或上传 Excel/CSV，然后就可以在「对话」里提问。

- **演示数据**：一个虚构零售公司（团队、账号、指标）：`cd backend && ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD='…' uv run python scripts/provision_demo.py`。演示账号密码写进 `deploy/.env.demo-accounts`，不会打印。
- **评测数据**：`uv run python scripts/provision_eval.py --env-file ../deploy/.env`，再执行一次第 2 步让后端读到新配置。
- **生产部署**：镜像固定 digest、依赖锁定；密钥只从环境注入；默认不发布宿主端口，`docker-compose.local.yml` 只用于本地开发。说明见 [deploy/README.md](deploy/README.md)。

## 项目结构

```text
backend/
  app/core/          权限范围、SQL Guard 与执行器、模型客户端、加密、日志等基础设施
  app/modules/       业务模块：chat（问数管道、检索）、datasource、schema、permission、
                     semantic、metric、mcpaccess、eval、querylog、usage ...
  app/prompts/       带版本号的提示词
  migrations/        Alembic 迁移
  tests/             pytest
  scripts/           部署、演示数据、评测脚本
frontend/src/        React 页面、组件、API 类型（由 OpenAPI 生成）
eval/                评测库、权限夹具、100 道调优题、40 道留出题、参考答案自检
deploy/              Docker Compose 与容器启动脚本
docs/                MCP 说明、README 截图
```
