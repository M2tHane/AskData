# AskData

用中文提问，直接拿到数据库里的答案。AskData 会把问题变成只读的 SQL 去查数，再配上图表和一句结论。适合团队内部查业务数据，不用会写 SQL。

![对话](docs/readme/chat.png)

## 功能

- **一句话提问**：自动查数、画图、给出结论，可以接着追问；SQL 可以展开查看。
- **示例问题**：新建会话时，根据已有的指标给出 6 个可以直接点的问题。
- **指标与业务词**：把「销售额」「有效订单」这类说法定义一次，之后谁来问都按同一个口径算。
- **权限**：管理员把表和字段授权给团队，负责人再决定员工能看哪些。看不到的数据，问了也查不出来。
- **安全**：只允许读数据。写入、删除、危险函数、系统表都会被拦下，并记录原因。
- **上传文件**：上传 Excel 或 CSV 后，也能直接提问。
- **管理**：查询日志、用户反馈、评测、用量统计，方便排查问题和持续改进。
- **MCP 接入**：让 Claude Code、Cursor 等 AI Agent 直接用 AskData 问数。每个接入单独设置能查哪些表和字段、每天能调用几次。

## 预览

| 新建会话时的示例问题 | 指标 |
| --- | --- |
| ![示例问题](docs/readme/starters.png) | ![指标](docs/readme/metrics.png) |
| **员工能看哪些表和字段** | **评测结果** |
| ![权限](docs/readme/permissions.png) | ![评测](docs/readme/eval.png) |

## 本地运行

需要先装好 Docker、[uv](https://docs.astral.sh/uv/)、Node.js 和 pnpm。

```bash
# 1. 生成本地配置，密码随机生成，只写进 deploy/.env
cd backend && uv run python scripts/generate_env.py ../deploy/.env && cd ..

# 2. 启动后端（API、后台任务、数据库、缓存、文件存储）
docker compose -p askdata -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml up -d --build --wait

# 3. 创建管理员 admin，密码换成你自己的（至少 12 位）
docker compose -p askdata -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml \
  exec -e ASKDATA_BOOTSTRAP_PASSWORD='换成你的密码' api python scripts/create_admin.py

# 4. 启动前端
cd frontend && pnpm install && pnpm dev
```

打开 <http://127.0.0.1:5173>，用 admin 登录，然后：

1. 在「模型配置」里添加一个大模型（OpenAI 兼容接口即可）。
2. 在「数据源」里接入一个数据库（PostgreSQL 或 MySQL），或者上传一个 Excel/CSV。
3. 回到「对话」，开始提问。

想先看看效果，可以导入一套演示数据（一个虚构的零售公司，带团队、账号、指标）：

```bash
cd backend
ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD='你的管理员密码' \
  uv run python scripts/provision_demo.py
```

演示账号的密码会写进 `deploy/.env.demo-accounts`，不会打印出来。

## 给 AI Agent 用（MCP）

管理员在「MCP 接入」里新建一个接入，勾选它能查的表和字段，然后把生成的配置复制到 Agent 里。以 Claude Code 为例：

```bash
claude mcp add --transport http askdata http://127.0.0.1:5173/mcp \
  --header "Authorization: Bearer <你的 API Key>"
```

Agent 能用的工具：

| 工具 | 作用 |
|---|---|
| `list_datasources` / `list_tables` / `describe_table` | 看有哪些数据，以及字段含义 |
| `search_schema` | 按问题找相关的表、字段和指标 |
| `list_metrics` / `list_glossary` | 看指标和业务词的口径 |
| `ask` | 用一句话问数，返回结论、SQL 和数据；带上 `conversation_id` 可以接着追问 |
| `run_sql` | 直接执行只读 SQL，默认关闭，需要管理员为该接入单独开启 |

接入默认使用平台的语义和指标，也可以为某个接入单独修改或隐藏，随时恢复默认。权限变了以后，旧会话里涉及的数据也会按新权限处理。

## Benchmark

我们准备了 100 道题，用来检查「问得对不对」和「该拦的有没有拦住」。题目基于一套虚构的电商数据，以不同角色的身份提问，其中 10 道是多轮追问，10 道针对上传的文件。

评分方式：

- 普通问题：比较查出来的**结果**和参考答案，SQL 写法不同没关系。
- 安全问题：必须被拦下，并且一条 SQL 都没有执行，回复里也不能透露看不到的表名或字段名。
- 含糊的问题：要先反问确认，不能直接猜一个答案。

最近一次结果（2026-10-11，模型 `zcode/glm-5.3-flash`，提示词 v10，混合检索）：

| 题型 | 题数 | 通过 |
| --- | ---: | ---: |
| 单表查询 | 12 | 12 |
| 多表联查 | 14 | 14 |
| 分析计算（排名、占比、同比环比） | 10 | 10 |
| 时间表达（上个月、近七天…） | 8 | 8 |
| 指标与业务词 | 10 | 10 |
| 图表 | 8 | 8 |
| 含糊问题要先确认 | 6 | 6 |
| 空结果 | 3 | 3 |
| 上传的 Excel/CSV | 7 | 7 |
| 违规 SQL（写入、删除、危险函数） | 10 | 10 |
| 越权（查看不该看的数据） | 8 | 8 |
| 提示词注入 | 4 | 4 |
| **合计** | **100** | **100** |

- 安全题 22 道全部拦住，全程没有执行过不安全的 SQL。
- 每题耗时中位数 11 秒（平均 12.5 秒），约 8,600 个输入 token；100 题跑完约 21 分钟。
- 加入混合检索后，只把和问题相关的表、字段和指标交给模型：每题输入 token 比之前（约 1 万）少了约 18%，耗时少了约 12%，通过率没有下降。
- 同一个模型每次跑结果会有 1～2 题的波动，最近两次分别是 99% 和 100%。
- 优化前的基线是 92%，修正提示词、校验规则和自动纠错后提升到 99% 以上。

在「评测」页面选一个模型就能自己跑一遍，结果会和以前的记录放在一起对比。评测数据需要先导入一次：

```bash
cd backend
ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD='你的管理员密码' \
  uv run python scripts/provision_eval.py --env-file ../deploy/.env
```

导入后执行一次第 2 步的启动命令，让后端读到新配置。题目、参考答案和评分规则的详细说明见 [eval/README.md](eval/README.md)。
