# 企业 ChatBI 查数平台：架构设计

### 3.1 总览与公共基础层

**系统按 api → service → repository 三层组织业务模块，底下是一个必须复用的公共基础层 `core/`；凡是涉及权限、SQL 执行、LLM 调用、加密、日志审计的逻辑，只能在 `core/` 里实现一次，业务模块只能调用，不能自己再写。** 这是防止各模块各写一套的核心约束。

业务模块：auth（登录与令牌）、org（用户与团队）、datasource（数据源与连接）、schema（表与字段元数据）、upload（Excel/CSV 导入）、permission（可见范围配置）、semantic（语义）、metric（指标）、chat（会话与查询流水线）、feedback（反馈）、querylog（查询日志）、eval（评测）、modelcfg（模型配置）、usage（用量）、audit（操作审计）。

**依赖方向：** api 只调 service；service 可调同层其他 service 和 core；repository 只被所属 service 调用；core 不依赖任何业务模块。用 import-linter 在 CI 里强制检查。

[embedded content: 分层架构 · 4 层加 6 类外部依赖]

业务模块之间不直接读对方的表，也不直接访问数据库、对象存储或模型；这些调用都经过 core 里的唯一出口。

### 3.2 必须复用的公共组件

| 组件             | 位置                  | 职责与约束                                                                                                                                                             |
| -------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 配置             | `core/config`       | 统一读取环境变量，Pydantic Settings；业务代码不直接读 `os.environ`                                                                                                                  |
| 错误体系           | `core/errors`       | `AppError(code, message, http_status)` 及子类（认证失败、无权限、被 SQL 校验拦截、数据源不可用等）；错误码集中登记，前端按码处理；业务代码只抛 AppError                                                            |
| 统一响应与分页        | `core/response`     | 所有接口返回同一信封 `{code, message, data}`；列表接口统一分页参数与返回结构                                                                                                                |
| 数据库会话与仓储基类     | `core/db`           | 异步会话、事务装饰器、`BaseRepository`（通用增删改查、软删除、审计字段 created_at、updated_at、created_by）                                                                                     |
| 密码与令牌          | `core/security`     | 密码哈希、JWT 签发与校验、刷新令牌；认证提供者抽象成接口，后续接 SSO 只加实现                                                                                                                       |
| 加密盒            | `core/crypto`       | `SecretBox.encrypt/decrypt`，用于数据源密码与模型 API Key；密钥来自环境变量；接口返回给前端时只给掩码，不给明文                                                                                         |
| 当前用户与角色依赖      | `core/deps`         | `CurrentUser`（含 role、team_id）、`require_role(...)`；所有接口通过它取身份，不自己解析令牌                                                                                              |
| **权限服务（唯一出口）** | `core/permission`   | `PermissionService.visible_scope(user, datasource_id)` 返回该用户可见的表与字段集合、脱敏规则、行级条件；schema 浏览、schema 检索、提示词构造、SQL 校验、语义与指标列表都必须通过它取范围，禁止各自查权限表                        |
| 三级可见性          | `core/scope`        | 平台、团队、个人三级通用模型：`ScopeMixin(scope, owner_team_id, owner_user_id, status)` 加 `ScopedQuery.visible_to(user)`；语义与指标共用；提升申请与审核流程（状态机 private → pending → team）也在这里实现一次 |
| LLM 统一客户端      | `core/llm`          | `LLMClient.chat/stream`、`EmbeddingClient.embed`、`RerankClient.rerank`，底层 LiteLLM；统一超时、重试、降级、token 统计、调用日志；`ModelRegistry` 按团队设置选择模型；业务代码禁止直接用任何厂商 SDK             |
| 提示词注册表         | `core/prompt`       | 提示词以模板文件加版本号管理（SQL 生成、澄清、纠错、图表配置、结果解释）；每次调用记录模板版本，反馈快照和评测都要用                                                                                                      |
| SQL 守卫         | `core/sql/guard`    | `SqlGuard.check(sql, dialect, scope)`：sqlglot 解析，单条 SELECT，禁用函数与系统表，表字段越权检查，返回规范化 SQL 与涉及的表字段；是 SQL 进入执行前的唯一关卡                                                    |
| SQL 执行器        | `core/sql/executor` | `SqlExecutor.run(datasource, sql, limits)`：只读账号、语句超时、最大行数、`EXPLAIN` 成本上限、取消、结果脱敏；业务代码禁止自己拿连接执行用户或 LLM 生成的 SQL                                                     |
| 方言适配           | `core/sql/dialect`  | MySQL 与 PG 的引号、日期函数、分页、类型映射差异集中在一处；提示词里的方言说明从这里取                                                                                                                  |
| 连接器            | `core/datasource`   | `Connector` 基类及 MySQL、PG 实现（读元数据、执行、连接测试）；导入库也走 PG 连接器                                                                                                            |
| 对象存储           | `core/storage`      | MinIO 客户端封装，统一路径规则与上传下载                                                                                                                                           |
| 后台任务           | `core/tasks`        | 统一任务封装，任务状态写入 `task_run` 表（排队、运行、成功、失败、进度），前端轮询同一个接口；文件导入、元数据同步、批量评测都复用                                                                                           |
| 流式事件           | `core/stream`       | 统一 SSE 事件结构（阶段、SQL、数据、图表、解释、错误、结束），查询流水线各步只管发事件                                                                                                                   |
| 脱敏             | `core/masking`      | 掩码规则（手机号、身份证、自定义）；执行器与导出共用                                                                                                                                        |
| 审计与查询日志写入      | `core/audit`        | `audit.record(actor, action, target, diff)` 与 `querylog.write(...)`；写操作 service 必须调用，由装饰器统一完成，避免遗漏                                                                |
| 日志与追踪          | `core/logging`      | 结构化日志、请求 ID 贯穿；禁止直接 `print` 或自建 logger                                                                                                                            |
| 保留策略           | `core/retention`    | 定时任务清理超期的日志结果摘要与导入历史                                                                                                                                              |

前端同样有公共层：

| 组件                                      | 职责                                   |
| --------------------------------------- | ------------------------------------ |
| `api/client`                            | 由后端 OpenAPI 自动生成类型与请求函数，统一错误码处理与令牌刷新 |
| `useSSE`                                | 统一的流式查询 hook，解析 `core/stream` 定义的事件  |
| `PermissionGate`                        | 按角色控制菜单与按钮显示                         |
| `ScopeBadge`、`ScopeSelect`              | 平台、团队、个人级别的标签与选择，语义与指标页面共用           |
| `SqlViewer`、`DataTable`、`ChartRenderer` | SQL 展示、结果表格、图表渲染，会话页、日志页、反馈页、评测页共用   |
| `TaskProgress`                          | 后台任务状态展示，上传、同步、评测共用                  |

### 3.3 强制规则

1. 任何校验与执行 SQL 的代码路径都必须经过 `SqlGuard`，再经过 `SqlExecutor`；包括会话查询、指标试跑、评测运行、手动改 SQL 重跑。
2. 任何可见范围的判断都必须调用 `PermissionService`，前端的显示控制不算权限。
3. 任何模型调用都必须经过 `core/llm`。
4. 所有写接口必须产生审计记录。
5. 新增的错误码、提示词、事件类型必须先在 `core` 中登记。
6. 这些规则用 import-linter 与架构测试在 CI 里检查，例如禁止业务模块直接 import 数据库驱动或 LiteLLM。

### 3.4 查询流水线

**一次提问由固定的十个步骤组成，每个步骤是一个实现统一接口 `Stage.run(ctx) -> ctx` 的函数，共享一个 `QueryContext` 状态对象；步骤只做自己的事，通过 `core/stream` 发事件，由 `core/audit` 统一记录耗时与结果。** 这样每一步都可单测，也能在评测里单独打分。

[embedded content: 查询流水线 · 10 步，3 个分支]

守卫不通过直接拒绝且不重试；只有执行报错才带着错误信息回到生成步骤，最多三次。

| #  | 步骤      | 说明                                                                                                                  |
| -- | ------- | ------------------------------------------------------------------------------------------------------------------- |
| 1  | 加载上下文   | 取会话最近若干轮问答，过长则摘要；识别是否为追问（继承上一轮的表、过滤条件）                                                                              |
| 2  | 解析可见范围  | 调 `PermissionService.visible_scope`，后续所有步骤只在这个范围内工作                                                                 |
| 3  | 构建检索上下文 | 表数不超过阈值（默认 30）时全量注入；超过则混合检索（`pg_trgm` 或全文检索加 pgvector），可选 Rerank，再按外键补全关联表；同步检索语义、指标、枚举映射、示例 SQL，三级合并（个人 > 团队 > 平台） |
| 4  | 澄清判断    | 候选字段得分接近、指标口径冲突或缺少必要条件时，返回带选项的追问，本步结束；同一问题最多追问一轮                                                                    |
| 5  | 生成 SQL  | 使用带版本号的提示词，写明目标方言、可见表结构、命中的语义与指标、示例 SQL                                                                             |
| 6  | SQL 守卫  | `SqlGuard.check`；通过后得到规范化 SQL 及涉及的表字段                                                                               |
| 7  | 执行      | `SqlExecutor.run`，只读账号，限时限行，结果按规则脱敏                                                                                 |
| 8  | 纠错重试    | 仅对执行报错和空结果疑似错误重试，把错误信息回传模型，最多 3 次；守卫拦截的越权和危险语句不重试，直接拒绝                                                              |
| 9  | 图表与解释   | 模型根据列类型和行数输出图表配置 JSON，后端按 schema 校验；另生成解释：用了哪个指标定义、过滤条件、时间范围、生效的是哪一级语义                                              |
| 10 | 记录      | 写查询日志（问题、SQL、是否拦截及原因、耗时、行数、模型、token、提示词版本）；失败也要写                                                                    |

`QueryContext` 至少包含：用户与团队、数据源、可见范围、会话历史、检索结果（表、语义、指标、示例）、模型与提示词版本、生成的 SQL、守卫结果、执行结果摘要、重试记录、图表配置、各步骤耗时、token 统计。反馈快照直接序列化这个对象。

### 3.5 SQL 安全：多层防线

**安全不依赖单一手段：数据库账号只读是第一道，AST 校验与越权比对是第二道，资源限制与脱敏是第三道；模型生成的文本永远不直接执行，执行的是守卫输出的规范化 SQL。**

| 层     | 措施                                                                                                                                                         |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 账号    | 数据源接入账号只授予 SELECT，且只覆盖允许的库或 schema；Excel/CSV 导入库的查询账号与写入账号分开                                                                                               |
| 语句类型  | 仅允许单条 SELECT（含 WITH … SELECT）；拒绝 INSERT、UPDATE、DELETE、DROP、ALTER、TRUNCATE、CREATE、GRANT、CALL、SET、COPY、多语句、`SELECT … FOR UPDATE`、`INTO OUTFILE/DUMPFILE`     |
| 函数与对象 | 禁用 `pg_sleep`、`pg_read_file`、`lo_*`、`dblink`、`sleep`、`benchmark`、`load_file` 等函数；禁止访问 `information_schema`、`pg_catalog`、`mysql`、`performance_schema`、`sys` |
| 越权比对  | 解析出所有涉及的 (表, 字段)，展开 `SELECT *`，穿透子查询、CTE、别名与 JOIN；任何一项不在可见范围内就拒绝；无法确定来源的字段也拒绝                                                                              |
| 资源限制  | 语句超时（默认 30 秒）、最大返回行数（默认 10000，超出截断并提示）、`EXPLAIN` 预估成本上限、单用户并发与频率限制                                                                                         |
| 脱敏与行级 | 命中脱敏字段在执行器出口处统一掩码；行级条件由守卫注入，不靠模型自觉                                                                                                                         |
| 提示词注入 | 表字段注释、Excel 单元格、用户上传内容一律作为数据放入分隔标记内；模型输出只提取 SQL 与配置 JSON；不可见的表结构不进入提示词                                                                                     |
| 信息不泄漏 | 被拦截的回复只说明原因类别，不透露不可见表或字段的名称                                                                                                                                |

拦截原因统一登记为错误码并写入日志，同时作为评测里“违规查询”类题目的期望结果：`SQL_NOT_SELECT`、`SQL_MULTI_STATEMENT`、`SQL_FORBIDDEN_FUNC`、`SQL_SYSTEM_TABLE`、`SQL_OUT_OF_SCOPE`、`SQL_PARSE_FAILED`、`SQL_COST_EXCEEDED`。

### 3.6 澄清与纠错

- **澄清触发：** 多个候选字段或指标得分接近；同一术语在不同级别语义下含义冲突；缺少无法默认的条件（如“最近”没有范围）。澄清以按钮选项呈现，用户选择后继续同一次流水线。
- **时间表达：** “上个月”“近 7 天”等由后端按用户时区计算为明确日期区间后放入提示词，不让模型自己推算。
- **纠错边界：** 只重试语法与字段名错误；空结果不盲目重试，而是在解释里提示“结果为空，可能是条件过严或口径不符”。


### 3.7 权限模型

**可见范围的最小粒度是 (数据源, 表, 字段)，由管理员授权给团队，再由团队负责人在这个范围内决定员工可见的部分；任何新同步出来的表和字段默认不授权。** 不变量：员工可见 ⊆ 团队已授权；字段授权要求所属表已授权。

| 角色 | `visible_scope` 的计算 |
| --- | --- |
| 管理员 | 该数据源的全部表与字段 |
| 团队负责人 | 管理员授权给本团队的表与字段 |
| 员工 | 本团队已授权，且负责人标记为“员工可见”的表与字段 |

- 字段授权可附带脱敏规则（掩码方式），脱敏规则由管理员设置，负责人只能在可见与不可见之间选择，不能取消管理员设置的脱敏。
- 表结构同步后，被删除的字段对应授权标记为失效，新增字段保持未授权。
- 行级权限（P1）：团队授权上可挂一个行过滤条件模板，由 `SqlGuard` 注入。
- `PermissionService` 的结果按 (用户, 数据源) 缓存在 Redis，授权变更时主动失效。

### 3.8 元数据库表设计

**通用约定：** 主键用 bigint 自增，所有表带 `created_at`、`updated_at`、`created_by`；配置类表用 `deleted_at` 软删除；日志类表按月分区并带 `expires_at` 供保留任务清理；所有跨表引用建外键。

| 分组 | 表 | 关键字段 | 说明 |
| --- | --- | --- | --- |
| 身份 | `team` | name、status | 团队 |
| 身份 | `user` | username、password\_hash、role（admin、leader、member）、team\_id、status | 一人一团队，管理员的 team\_id 可空 |
| 身份 | `refresh_token` | user\_id、token\_hash、expires\_at、revoked | 刷新令牌 |
| 数据源 | `datasource` | name、type（mysql、postgresql、upload）、host、port、database、username、secret\_enc、owner\_team\_id、dialect、status | 连接密码用 `SecretBox` 加密 |
| 数据源 | `meta_table` | datasource\_id、schema\_name、name、comment、description、status、synced\_at | 表元数据，description 为人工补充 |
| 数据源 | `meta_column` | table\_id、name、data\_type、comment、description、is\_pk、ordinal、status | 字段元数据 |
| 数据源 | `meta_relation` | from\_column\_id、to\_column\_id、source（fk、manual） | 外键与人工补充的关联，检索时补全关联表用 |
| 导入 | `upload_job` | datasource\_id、file\_key、filename、sha256、size、parse\_config（sheet、表头行、编码、分隔符、字段类型）、mode（overwrite、append）、version、status | 源文件在 MinIO，保留历史版本 |
| 授权 | `team_table_grant` | team\_id、table\_id、member\_visible | 管理员授权加负责人开关 |
| 授权 | `team_column_grant` | team\_id、column\_id、member\_visible、mask\_rule、row\_filter | 字段级授权、脱敏与行过滤 |
| 语义 | `semantic_term` | scope、owner\_team\_id、owner\_user\_id、status、datasource\_id、target\_type（table、column、enum）、target\_id、aliases、description、enum\_mapping、version | 别名、同义词、枚举值映射 |
| 指标 | `metric` | scope、owner\_team\_id、owner\_user\_id、status、datasource\_id、name、aliases、description、sql\_template、params、last\_validated\_at、version | 常见计算的 SQL 模板 |
| 语义与指标 | `promotion_request` | kind（semantic、metric）、ref\_id、requested\_by、reviewed\_by、status（pending、approved、rejected）、comment | 个人提升为团队级的申请，两者共用 |
| 对话 | `conversation` | user\_id、team\_id、datasource\_id、title、model\_id | 绑定数据源，创建后不可改 |
| 对话 | `message` | conversation\_id、role、content、query\_run\_id | 消息 |
| 日志 | `query_run` | conversation\_id、message\_id、user\_id、team\_id、datasource\_id、question、generated\_sql、normalized\_sql、status（success、blocked、failed、clarify）、blocked\_code、error、duration\_ms、stage\_timings、row\_count、result\_preview、chart\_spec、model\_id、prompt\_versions、tokens\_in、tokens\_out、retry\_count、context\_snapshot、expires\_at | 查询日志，同时作为反馈快照的来源；只存结果摘要 |
| 反馈 | `feedback` | query\_run\_id、user\_id、team\_id、rating、reason\_code、comment、status（open、handled）、handled\_by、handled\_at、converted\_ref | 反馈，可转评测样例或示例 SQL |
| 示例 | `example_sql` | datasource\_id、scope、team\_id、question、sql、source（feedback、manual）、approved | few-shot 示例库 |
| 检索 | `embedding_item` | kind（table、column、semantic、metric、example）、ref\_id、text、embedding（vector）、model\_id | 向量与关键词索引；记录模型 ID，换模型后支持重建 |
| 模型 | `model_config` | type（llm、embedding、rerank）、provider、base\_url、model\_name、api\_key\_enc、params、enabled | 仅管理员维护 |
| 模型 | `team_model_pref` | team\_id、type、model\_id | 团队选用哪个 |
| 评测 | `eval_case` | category、difficulty、question、expected\_sql、expected\_result、expect\_blocked、expected\_code、source | 评测题 |
| 评测 | `eval_run` | model\_id、prompt\_versions、config、started\_at、finished\_at、summary | 一次回归运行 |
| 评测 | `eval_result` | run\_id、case\_id、passed、actual\_sql、actual\_result\_digest、blocked\_code、detail | 单题结果 |
| 运维 | `task_run` | type、status、progress、payload、error | 后台任务状态，统一复用 |
| 运维 | `audit_log` | actor\_id、action、target\_type、target\_id、diff、ip | 操作审计，与查询日志分开 |
| 运维 | `usage_daily` | date、team\_id、user\_id、model\_id、calls、tokens\_in、tokens\_out | 用量统计 |
| 运维 | `team_quota` | team\_id、monthly\_token\_limit、daily\_query\_limit | 额度 |

**关于向量维度：** pgvector 的向量列维度固定，由部署时选定的 Embedding 模型决定（例如 bge-m3 为 1024 维）。更换成不同维度的模型需要一次迁移和全量重建索引，所以 `embedding_item` 记录 `model_id`，并提供“重建索引”后台任务。

**关于索引：** `query_run` 建 (team\_id, created\_at)、(user\_id, created\_at)、(status, created\_at)；`semantic_term`、`metric` 建 (datasource\_id, scope, owner\_team\_id)；`embedding_item` 建 HNSW 向量索引并带 (datasource\_id, kind) 过滤。

### 3.9 数据源与 Excel/CSV 导入

**所有数据源最终都表现为“一个只读连接加一套元数据”：MySQL、PostgreSQL 直接接入，Excel/CSV 先导入独立的 PostgreSQL 库，再作为 `upload` 类型数据源登记，之后与其他数据源走完全相同的授权、语义和查询流程。**

**接入与同步**

- 接入时先做连接测试，并检查账号是否具备写权限；检测到写权限给出明确警告并写入审计，推荐换成只读账号。
- 元数据同步是后台任务（`task_run`）：读取表、字段、注释、外键，与 `meta_*` 对比更新。新增的表和字段默认未授权；消失的字段使对应授权失效；人工补充的 description 不被覆盖。支持手动刷新和每日定时同步，同步后触发向量索引更新。

**Excel/CSV 上传流程**

1. **上传：** 文件先存入 MinIO 临时路径。限制：`.xlsx`、`.xls`、`.csv`，默认 50MB 以内；拒绝 `.xlsm`；除后缀外还校验文件头。
2. **解析预览：** 返回前 100 行。用户可调整：sheet（多 sheet 可分别导入为多张表）、表头所在行或无表头、CSV 的编码与分隔符、每列名称与类型、表名、字段描述。类型推断容易出错（日期读成文本、`00123` 读成数字），预览页对可疑列给出提示。
3. **确认导入：** 创建后台任务，在独立导入库中为该数据源建一个专属 schema（如 `ds_<id>`），表与字段名清洗为合法标识符，原列名保存到字段描述里。用 `COPY` 分批写入，整个导入在一个事务内，失败则整体回滚。
4. **落盘与登记：** 成功后源文件移到 MinIO 正式路径并记录 sha256 与版本；登记 `datasource`、`meta_table`、`meta_column`，默认只对上传者所在团队授权，开放给其他团队需管理员操作。
5. **更新数据：** 支持覆盖与追加；追加时校验列结构一致，不一致则拒绝并提示差异；每次导入保留历史版本。

**处理细节：** 用只读值模式读取 Excel，不执行公式与宏；合并单元格和多级表头第一版只提示“请先整理”；写入账号与查询账号分开，查询账号只有该 schema 的 SELECT；导出结果时，以 `=`、`+`、`-`、`@` 开头的单元格加前缀防止表格注入。

### 3.10 语义与指标管理

**语义管理给表、字段、枚举值起别名和补充说明，指标管理保存常见计算的 SQL 模板；两者都只是给模型的参考，不绕过 `SqlGuard`，并共用同一套三级可见性（`core/scope`）。**

| 级别 | 谁可创建与修改 | 谁可见 |
| --- | --- | --- |
| 平台级 | 管理员 | 所有人 |
| 团队级 | 本团队负责人（管理员可查看） | 本团队全体成员 |
| 个人级 | 员工本人 | 仅本人，负责人与管理员均不可见 |

- **优先级：** 同一目标出现冲突时，个人 > 团队 > 平台；提问时命中哪一级，在结果解释里写明。
- **语义内容：** 目标（表、字段或枚举值）、别名与同义词、说明；枚举映射示例：“华东”对应 `region IN ('上海','江苏','浙江')`，状态码 `1` 对应“已支付”。
- **指标内容：** 名称、别名、说明、SQL 模板（可带参数，如起止日期）、适用数据源。保存前必须试跑：经 `SqlGuard` 校验后用只读账号执行带 `LIMIT` 的版本，通过才允许保存。
- **提升流程：** 员工对个人条目发起申请，状态 private → pending；负责人审核，通过后该条目变为团队级，驳回需填写原因，员工可见审核结果。
- **版本追溯：** 每次修改版本号加一，修改前的内容记入审计；`query_run.context_snapshot` 记录当次命中的语义与指标及其版本，旧会话可追溯。
- **索引：** 保存后异步更新 `embedding_item`；条目内容一律作为数据进入提示词，不当作指令。

### 3.11 反馈与查询日志

- **反馈入口：** 每条回答下方有点赞和点踩。点踩要求选择原因（口径错误、表选错、字段选错、结果为空、结果不对、图表不合适、其他）并可补充说明。提交时自动关联该次 `query_run`，快照里包含问题、SQL、结果摘要、模型与提示词版本、命中的语义与指标。
- **可见与处理：** 团队负责人看本团队，管理员看全平台。处理动作：标记已处理；补充语义或指标；转为评测样例；转为示例 SQL（需审核通过才进入 few-shot 检索）。
- **点赞的使用：** 点赞的问答作为示例 SQL 的候选，同样需审核。
- **查询日志：** 管理员看全平台，负责人看本团队，员工只看自己的会话历史。列表支持按时间、用户、数据源、状态、拦截码、是否有反馈筛选；被拦截的请求也在其中。
- **数据保留：** 日志只存 SQL 与结果摘要（行数加少量脱敏后的预览行），默认保留 90 天，由 `core/retention` 定时清理；反馈快照随所属日志同期清理，已转为评测样例的内容除外。

### 3.12 模型配置

- 分 LLM、Embedding、Rerank 三类，仅管理员维护：提供商、地址、模型名、API Key（加密存储，页面只显示掩码）、参数、启用状态，并提供“测试连接”。
- 团队负责人只能在已启用的模型中为本团队选择使用哪个；未选择时用平台默认。
- Rerank 是可选环节，未配置则直接使用混合检索结果。
- Embedding 模型更换：若向量维度不同，需先迁移向量列，再执行“重建索引”任务；重建期间旧索引继续服务。
- P1：LLM 备用模型自动降级；不同步骤指定不同模型（例如澄清与检索用小模型，SQL 生成用强模型）。
- 所有调用经 `core/llm`，用量按团队、用户、模型累计到 `usage_daily`，超过 `team_quota` 时拒绝并提示。

### 3.13 评测方案

**评测以“执行结果是否一致”为准，不比较 SQL 文本；安全类题目（违规、越权、注入）必须 100% 通过才允许发版。** 评测集是平台的一等公民：换模型、改提示词、改语义、改检索配置后都要一键回归，并与上一次结果对比。

**测试库：** 一个电商加销售的示例 PostgreSQL 库，文件 `eval/testdb.sql`（建表、注释、固定随机种子生成的数据），约 12 张表，例如 region、customer、product、category、orders、order\_item、payment、refund、employee、department、sales\_target，另含一张敏感表 employee\_salary。刻意加入真实企业库的特点：状态码枚举、缩写命名、部分字段缺注释、同名但口径不同的指标。另有一份 `eval/fixtures.yaml`，定义两个测试团队、三种角色用户及各自的授权范围，供越权题使用；Excel/CSV 题使用 `eval/files/` 下的样例文件，经上传流程导入。

**题集：** 共 100 题，文件 `eval/cases.jsonl`，类别分布如下。

| 类别 | 题数 | 期望判定 |
| --- | --- | --- |
| 单表查询（过滤、聚合、排序、TopN） | 12 | 结果集一致 |
| 多表联查（两表、三表及以上） | 14 | 结果集一致 |
| 分组、窗口、占比、同比环比 | 10 | 结果集一致 |
| 时间表达（上个月、近 7 天、本季度、去年同期） | 8 | 结果集一致，日期区间正确 |
| 语义与指标命中（别名、指标模板、枚举映射） | 10 | 结果集一致，解释里标明命中的语义或指标 |
| 图表绘制 | 8 | 数据一致，图表类型在允许集合内，配置通过 schema 校验 |
| 歧义与澄清 | 6 | 状态为 clarify，选项包含合理候选 |
| 违规查询（写操作、多语句、危险函数、系统表） | 10 | 状态为 blocked，拦截码匹配，未执行任何 SQL |
| 越权访问（不可见表或字段、`SELECT *`、子查询或别名绕过） | 8 | 状态为 blocked，拦截码为 `SQL_OUT_OF_SCOPE`，回复不泄漏对象名称 |
| 提示词注入（问题或数据内容里夹带指令） | 4 | 不执行注入指令，按 blocked 或正常回答判定 |
| 空结果与异常结果 | 3 | 返回空结果，解释里有提示 |
| Excel/CSV 数据源 | 7 | 结果集一致 |

其中约 10 题为多轮追问（继承上一轮条件）；每题标注难度（易、中、难）。

**题目字段：** `id`、`category`、`difficulty`、`datasource`、`as_user`（以哪个测试用户提问）、`history`（多轮题的前序问答）、`question`、`expected_sql`（参考答案，仅用于生成期望结果）、`result_mode`（无序集合、有序、单值）、`expect_status`、`expect_code`、`expected_chart_types`、`source`（manual、feedback）、`notes`。

**打分规则：**

- 结果集比较：列名无关、列顺序无关，行序仅在题目要求排序时比较，浮点数容差 1e-6。
- 安全类：状态与拦截码都对，且日志显示没有真正执行 SQL，才算通过。
- 澄清类：必须返回追问而不是直接给答案。
- 参考答案自检：CI 里先用 `expected_sql` 在测试库跑一遍，保证参考答案本身可执行、结果非意外为空。

**输出指标：** 总体与分类别通过率、一次通过率（无重试）、平均重试次数、拦截类通过率、平均耗时、token 用量。建议首版目标：安全类 100%，其余类别整体 80% 以上，具体阈值待首轮基线跑出后再调整。

**回归运行：** 评测作为后台任务运行，记录模型、提示词版本、检索配置与时间；结果页支持两次运行对比，列出新增失败与已修复的题；可单独对比“全量注入”和“启用检索”两种配置，用数据决定检索阈值。

**题集增长：** 反馈处理时可一键把问题转为评测样例，由负责人补充期望结果后进入题集，`source` 标记为 feedback。

### 3.14 部署与目录结构

**第一版用 Docker Compose 一键部署，只暴露 Nginx 一个入口，其余服务都在内部网络。**

| 服务 | 作用 | 备注 |
| --- | --- | --- |
| `web` | Nginx 加前端静态文件，反向代理 `/api` | 唯一对外端口，SSE 需关闭代理缓冲 |
| `api` | FastAPI 应用 | 无状态，可水平扩展 |
| `worker` | arq 后台任务（导入、同步、评测、索引重建、保留清理） | 与 api 同一镜像，不同启动命令 |
| `pg-meta` | 元数据库（含 pgvector） | 定期备份 |
| `pg-import` | Excel/CSV 导入库 | 两个账号：写入账号只给 worker，查询账号只读 |
| `redis` | 缓存、限流、任务队列 |  |
| `minio` | 源文件备份 | 定期备份 |

密钥（加密主密钥、JWT 密钥、数据库密码）只通过环境变量或密钥文件注入，不进入镜像与代码库；仓库里只提供 `.env.example`。

**仓库结构（单仓库）：**

```text
chatbi/
├─ backend/
│  ├─ app/
│  │  ├─ core/            # 3.2 中的公共组件，业务模块只能调用
│  │  ├─ modules/         # auth、org、datasource、schema、upload、permission、
│  │  │                   # semantic、metric、chat、feedback、querylog、eval、
│  │  │                   # modelcfg、usage、audit；各含 api、service、repository、models、schemas
│  │  ├─ prompts/         # 带版本号的提示词模板
│  │  ├─ main.py          # API 入口
│  │  └─ worker.py        # 后台任务入口
│  ├─ migrations/         # Alembic
│  ├─ tests/
│  └─ importlinter.ini    # 依赖方向与禁用导入规则
├─ frontend/
│  └─ src/ { api, components, hooks, features, pages }
├─ eval/                  # testdb.sql、fixtures.yaml、cases.jsonl、files/、运行器
├─ deploy/                # docker-compose.yml、nginx.conf、.env.example
└─ docs/
```

### 3.15 开发规范

1. **先定接口再写实现：** 接口用 Pydantic 定义，OpenAPI 生成前端类型与请求函数，前后端不手写重复的类型。
2. **CI 必过项：** ruff、类型检查、前端 eslint 与 tsc、import-linter、单元测试、评测参考答案自检；安全类评测集在 CI 里用固定的模拟模型输出跑守卫部分。
3. **改提示词、语义检索或模型配置必须跑回归评测**，把对比结果附在变更说明里。
4. **错误码、提示词、流式事件类型**先在 `core` 登记，再被使用。
5. **日志里不得出现**密钥、令牌、完整结果数据；查询日志只写摘要。
6. **数据库迁移**一律用 Alembic，不手改表结构；涉及向量维度的迁移单独评审。
7. **模块之间不直接读对方的表：** 通过对方 service 的公开方法访问。

### 3.16 里程碑

工期按 2 到 3 人全职的假设估算，团队规模不同时按比例调整。

&#91;embedded content: 里程碑 · 5 个阶段，2 道门槛\]

门槛 1 用固定的模拟模型输出跑守卫部分，验证“违规、越权、注入”三类题全部拦住再进入查询主流程；门槛 2 的阈值等首轮基线跑出后再定。
