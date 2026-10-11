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
- **MCP 接入**：让 Claude Code、Cursor 等 AI Agent 直接用 AskData 问数。每个接入单独设置能查哪些表和字段、每天能调用几次，语义和指标可以单独修改（见[下文](#给-ai-agent-用mcp)）。

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
2. 在「数据源」里接入一个数据库（PostgreSQL 或 MySQL），或者上传一个 Excel/CSV。接入数据库后会读取每张表和字段的注释，作为默认语义；勾选要导入的表，没有注释的字段补上语义（可以先用「AI 补全」起草）才能启用。没补完可以存草稿，之后接着填。以后同步出的新表和没注释的新字段，在补齐之前员工和模型都看不到。
3. 回到「对话」，开始提问。

想先看看效果，可以导入一套演示数据（一个虚构的零售公司，带团队、账号、指标）：

```bash
cd backend
ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD='你的管理员密码' \
  uv run python scripts/provision_demo.py
```

演示账号的密码会写进 `deploy/.env.demo-accounts`，不会打印出来。

## 给 AI Agent 用（MCP）

Claude Code、Cursor 这类 AI Agent 可以通过 MCP 协议用 AskData 问数。每个接入单独设置能查的表和字段、每天的调用次数，语义和指标默认沿用平台的，也可以单独改。

| 勾选能查的表和字段，可设脱敏 | 为这个接入修改口径，取消勾选「可见」即隐藏 |
| --- | --- |
| ![数据权限](docs/readme/mcp-grants.png) | ![语义](docs/readme/mcp-items.png) |
| **以接入的身份试问，诊断只有管理员看得到** | **复制配置到 Agent** |
| ![测试问数](docs/readme/mcp-test.png) | ![接入方式](docs/readme/mcp-connect.png) |

### 接入步骤

1. 管理员打开「MCP 接入」，点「新建接入」，设置每日调用上限和返回行数。创建后会显示一次完整的 API Key，请当场保存。
2. 在「数据权限」里勾选这个接入能查的表和字段，需要的话设置脱敏。
3. （可选）在「语义」「指标」里为这个接入修改口径，或取消勾选「可见」隐藏它；在「测试问数」里以它的身份先试问一次。
4. 在「接入方式」里复制配置，填进 Agent。

### 配置

服务地址是 `http://<AskData 地址>/mcp`（Streamable HTTP），用 `Authorization: Bearer <API Key>` 鉴权。新旧两代 MCP 客户端都能连。

Claude Code：

```bash
claude mcp add --transport http askdata http://127.0.0.1:5173/mcp \
  --header "Authorization: Bearer <你的 API Key>"
```

Cursor（`~/.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "askdata": {
      "url": "http://127.0.0.1:5173/mcp",
      "headers": { "Authorization": "Bearer <你的 API Key>" }
    }
  }
}
```

Codex（Key 从环境变量读取，不写进配置文件）：

```bash
export ASKDATA_MCP_KEY="<你的 API Key>"   # 写进 ~/.zshrc 等，Codex 启动时读取
codex mcp add askdata --url http://127.0.0.1:5173/mcp --bearer-token-env-var ASKDATA_MCP_KEY
```

其他支持 Streamable HTTP 的客户端：

```json
{
  "mcpServers": {
    "askdata": {
      "type": "http",
      "url": "http://127.0.0.1:5173/mcp",
      "headers": { "Authorization": "Bearer <你的 API Key>" }
    }
  }
}
```

### 工具

| 工具 | 作用 |
|---|---|
| `list_datasources` / `list_tables` / `describe_table` | 看有哪些数据，以及字段含义 |
| `search_schema` | 按问题找相关的表、字段和指标（没配向量模型时按关键词找） |
| `list_metrics` / `list_glossary` | 看指标和业务词的口径 |
| `ask` | 用一句话问数，返回结论、SQL 和数据；带上 `conversation_id` 可以接着追问 |
| `run_sql` | 直接执行只读 SQL，默认关闭，需要管理员为该接入单独开启 |

查表结构和口径的工具不计次数；`ask` 和 `run_sql` 计入每日调用上限。

### 调用示例

在 Claude Code 里直接问「各大区上个月的销售额是多少？按从高到低排」，Agent 会调用 `ask`：

```json
{ "question": "各大区上个月的销售额是多少？按从高到低排", "datasource_id": 1 }
```

AskData 返回（演示数据，SQL 有省略）：

```json
{
  "status": "success",
  "request_id": "rq_0d7428420fe77eaf",
  "conversation_id": "conv_9v1Rq9CgBRIgiaZ3GLYt1A",
  "answer": "上月销售额华东最高，为6,345.30元，其后为华南4,489.55元、华北2,836.85元，最低是华中164.05元（大区按订单收货大区）。",
  "sql": "SELECT \"o\".\"region\" AS \"region\", ROUND(SUM(\"o\".\"pay_amount\"), 2) AS \"sales_amount\" FROM \"shop\".\"orders\" AS \"o\" WHERE ...",
  "data": {
    "columns": ["region", "sales_amount"],
    "rows": [
      { "region": "华东", "sales_amount": 6345.3 },
      { "region": "华南", "sales_amount": 4489.55 },
      { "region": "华北", "sales_amount": 2836.85 },
      { "region": "东北", "sales_amount": 1701 },
      { "region": "华中", "sales_amount": 164.05 }
    ],
    "row_count": 5,
    "returned_rows": 5,
    "truncated": false
  },
  "assumption": "大区按订单收货大区",
  "metrics_used": [{ "name": "销售额", "source": "platform" }],
  "usage": { "input_tokens": 7436, "output_tokens": 371, "duration_ms": 8873 }
}
```

接着问「那华东按渠道拆开呢」，Agent 带上同一个 `conversation_id`，AskData 会沿用上一轮的口径和时间范围。

问到没授权的数据（比如「列出商品的采购成本」）时，返回的是工具错误，不会透露看不到的表名和字段名。出错也会附上这次消耗的 token：

```json
{
  "status": "error",
  "code": "SQL_OUT_OF_SCOPE",
  "message": "当前接入无权查询这些数据",
  "retryable": false,
  "request_id": "rq_61febf40724557fc",
  "usage": { "input_tokens": 13727, "output_tokens": 158, "duration_ms": 6079 }
}
```

`request_id` 可以在「查询日志」里直接搜到这次调用。

### 权限模型

- **只有管理员能管理接入。** 每个接入对应一个服务账号，不能登录网页，也不属于任何团队。
- **显式白名单。** 接入只能查勾选的字段，没勾的一律看不到；数据源同步出的新字段默认不可见。勾选的字段可以设脱敏（手机号、身份证号、全部隐藏）。
- **口径继承。** 默认沿用平台级的语义和指标，但只有用到的字段都已授权时才会出现（比如没授权成本字段，就看不到「毛利」）。为某个接入修改或隐藏口径不影响平台和其他接入，随时可以恢复默认；字段暂时撤销时，修改先失效，授权恢复后自动生效。
- **追问按当前权限处理。** `conversation_id` 只属于创建它的接入和数据源，7 天未使用就过期；别的接入拿到也用不了。权限收紧后，旧回答里涉及已撤销字段的内容不会再交给模型。
- **次数和返回大小。** 每个接入有每日调用上限。每次默认返回 100 行，最多 1000 行，总共不超过约 3 万字符，被截断时会标明原因。
- **同一套安全检查。** 和网页里提问走同一套只读 SQL 校验：越权、写入、多条语句、系统表和危险函数一律拒绝。`run_sql` 默认关闭，开启后同样要过校验，也同样脱敏。
- **Key 和审计。** API Key 只在创建或轮换时显示一次，只保存哈希；轮换时旧 Key 可以保留最多 24 小时的宽限期。停用立即生效，删除后所有 Key 失效，查询日志保留。每次调用都在「查询日志」里记下来源（网页 / MCP / MCP 测试）、接入和 `request_id`。

SQL 里的函数分三类处理：

| 类型 | 例子 | 处理 |
| --- | --- | --- |
| 危险函数，或不认识的自定义函数 | `pg_sleep`、`pg_read_file`、`dblink`、`my_udf()` | 直接拒绝 |
| 常见函数，已按数据库审核 | `LPAD`、`SPLIT_PART`、`STRPOS`（PostgreSQL）；`LPAD`、`QUARTER`（MySQL） | 允许 |
| 常见函数，但这个数据库还没审核 | `REGEXP_REPLACE`、`generate_series` | 让模型换一种写法重写一次，重写后的 SQL 要重新过全部检查 |

越权、写入、多条语句和危险函数在重写时也一律拒绝。

### 测试结果

- **自动化测试**：后端 58 个 MCP 测试，前端 9 个。覆盖：
  - 新旧两代协议，鉴权失败返回 401 / 403，非法 Host 返回 421。
  - 多个接入并发时各自的身份互不串用；在 Postgres 上 20 个并发请求、上限 5 次，恰好放行 5 次。
  - 用评测集里 22 道越权、违规、注入题问遍所有工具，输出里都不出现未授权的表、字段和口径名。
  - 追问时权限收紧、超时后继续追问、客户端中途断开、Key 轮换与宽限期、只有管理员能管理接入。
- **真实模型**：从 Benchmark 里挑了 20 道能对比的题（单轮、不含「上个月」这类相对时间，覆盖 8 种题型），以提问人相同的权限通过 MCP 问一遍，用同一套评分：
  - 通过 19/20；Benchmark 最近一次（第 28 轮）里同一批题是 20/20。
  - 唯一没过的 E01：模型那次在 SQL 里用了 `LPAD`，当时它不在允许列表里，被拦下了。函数分级后 `LPAD` 已放行（有单独的测试），重跑这题也通过了。
  - 每题耗时中位数 9.6 秒，平均约 8,900 个输入 token，和 Benchmark 相当（约 8,600）。

评测脚本：`backend/scripts/eval_mcp.py`，在 api 容器里运行。

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
