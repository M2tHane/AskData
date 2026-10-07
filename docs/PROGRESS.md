# 实施计划与阶段进度

本次范围：backend/、eval/、deploy/；不创建 frontend/。遵循 AGENTS.md、PRD、TECH_STACK、ARCHITECTURE。

## 实施拓扑与独占边界

本任务为多边界实施。公共接口先由单一写入者建立；独立组件在隔离 Git worktree 实施，生成工具绑定的报告与补丁交接；主工作区只由集成阶段写入。所有子代理使用 magpie/codex/gpt-6.1-sol，high thinking（工具使用模型后缀 :high）。

| 阶段/组件     | 独占文件或接口                                  | 隔离与交接             | 下一门槛                           |
| --------- | ---------------------------------------- | ----------------- | ------------------------------ |
| 公共层与脚手架   | backend 配置、core（SQL 守卫除外）、提示词、基础测试、检查脚本  | 主工作区单一写入者，完成后本地提交 | ruff、mypy、import-linter、pytest |
| SQL 安全    | core/sql/guard、executor 安全修正、安全测试        | 独立 worktree，补丁交接  | AST 越权、只读、规范化 SQL、资源限制         |
| 评测题库      | eval/testdb.sql、fixtures.yaml、100 题与样例文件 | 独立 worktree，补丁交接  | 题数与分类、自检                       |
| 门槛 1 集成   | 以上补丁集成、安全评测适配                            | 主工作区单一写入者         | 违规/越权/注入固定模拟输出全部拦截；四项检查通过      |
| 身份与配置模块   | auth/org/permission/modelcfg             | 独立 worktree，补丁交接  | 身份、团队子集授权、密钥掩码、审计              |
| 数据源模块     | datasource/schema/upload                 | 独立 worktree，补丁交接  | 元数据默认拒绝、导入事务、读写账号隔离            |
| 语义与指标模块   | semantic/metric                          | 独立 worktree，补丁交接  | 三级私密性、提升审核、指标守卫试跑              |
| 运维与反馈模块   | feedback/querylog/eval/usage/audit       | 独立 worktree，补丁交接  | 日志范围、摘要保留、反馈转换、评测任务            |
| 业务集成与迁移   | 模块装配、Alembic、共用接口适配                      | 主工作区单一写入者         | 迁移、OpenAPI、四项检查                |
| 查询流水线     | chat 模块、十步流水线、SSE、导出                     | 主工作区单一写入者         | 澄清一轮、执行纠错最多三次、拦截零执行、失败日志       |
| 评测运行器     | eval 运行器与结果比较                            | 独立 worktree，补丁交接  | 结果比较、参考答案自检、统计与对比              |
| 部署        | deploy、Dockerfile、后台 worker 装配           | 独立 worktree，补丁交接  | Compose、服务健康、唯一 Nginx 入口、SSE   |
| 最终集成与独立审查 | 全部补丁集成与已证实问题修复                           | 主工作区单一写入者，审查只读    | 四项检查、安全门槛、可用外部服务验证             |

## 当前状态

- 已读项目要求与三份必读文档。
- 初始目录仅含文档，无代码与 Git 历史；为隔离并行实施建立本地 Git 仓库，不配置远端、不发布。
- uv、Python 与 Docker 可用，Docker 服务版本 29.6.1；外部模型使用固定模拟输出，服务测试优先 Docker。
- 后续阶段在此记录实际完成项、未完成项、检查证据与运行命令。未执行的检查不得标记为通过。

## 当前阻塞：子代理宿主版本兼容

- 已编写并通过静态验证的协调脚本：`/tmp/askdata-implementation.js`。所有子任务显式指定 `magpie/codex/gpt-6.1-sol:high`。
- 工作流 `84ee41f6-464c-4665-85e3-b7b312d9807a` 状态 `failed`；`foundation` 在启动时失败，子任务 ID `63813bb6-3b76-47fe-acbb-77ff79af335f`，没有子会话可恢复。
- 精确原因：`pi-subagents` 0.74.0 要求宿主导出 `@earendil-works/pi-agent-core/node`，当前 Pi 与 pi-agent-core 1.0.0 的 package exports 没有 `./node`。后台启动预检直接拒绝。
- 启动失败后已确认主工作区干净，分支 `main`，当时 HEAD `33ddc1e`；仅主工作区，无隔离 worktree，没有任何子代理实施的部分代码。
- 已完成：文档阅读、实施边界、保守决策、基础 `.gitignore`、本地 Git 初始化、uv/Python/Docker 可用性检查、协调脚本及静态验证。
- 未完成：backend/、eval/、deploy/ 全部实施；ruff、mypy、import-linter、pytest、安全门槛与服务集成均未运行。
- 恢复条件：修复全局 Pi/subagents 的导出兼容后，经同一 subagent 协议重试原协调脚本。没有改用外部 CLI 或前台执行。

## 阶段 1：后端脚手架与公共层（本次直接实施，未使用 subagent）

本次写入边界为 `backend/` 与本文件的追加记录；未创建前端，未修改 eval/deploy 或其他文档。实施期间共享工作区出现后续阶段业务模块、eval 资产及对 core/main 的接口扩展，均保留。下文只把公共层、脚手架和对应测试归为阶段 1 的成果。

### 已完成

- uv 项目、Python 3.12、`pyproject.toml` 和完整 `uv.lock`；按稳定版解析并锁定直接及传递依赖。FastAPI、Pydantic v2、SQLAlchemy 2 async、Alembic、Redis/arq、MinIO、sqlglot、LiteLLM 等保持指定技术栈。
- Ruff（含禁止 print）、严格 mypy、pytest/pytest-asyncio、import-linter 配置，以及强制顺序执行的 `backend/scripts/check.sh`。6 条导入契约随新增业务包自动生效，架构测试补充相对导入、低层连接器、业务异常、动态调用等约束；合成违规业务包验证契约确实会失败。
- `app/main.py`：FastAPI 工厂、Pydantic 信封、统一错误处理、OpenAPI、健康检查、请求 ID 和安全日志追踪；`app/worker.py`：arq 分发、保留任务注册及定时入口。
- 架构 3.2 全部 core 公共组件；各外部依赖有协议和构造器注入，`tests/fakes.py` 提供离线假实现。
- SQL 守卫：单条只读 AST，完整元数据展开星号，穿透 JOIN/子查询/相关子查询/CTE/别名/集合运算，对每个引用做字段授权检查；未知来源、递归 CTE、未知 UDF/自定义类型保守拒绝；系统对象与危险函数拒绝；结构化行策略包装所有物理表引用，保留外连接语义；血缘驱动脱敏，敏感字段的计算结果全遮罩。
- 执行器只接受守卫签发的、绑定数据源/方言的规范化 SQL；修改或伪造签发结果在建连接之前拒绝。EXPLAIN 与游标读取共用只读事务；成本、超时、截断、取消、脱敏、单用户并发/频率限制均有实现。MySQL 无成本估计时拒绝，未缓冲游标不排空剩余大结果。
- 3.5 全部拦截码已集中登记：`SQL_NOT_SELECT`、`SQL_MULTI_STATEMENT`、`SQL_FORBIDDEN_FUNC`、`SQL_SYSTEM_TABLE`、`SQL_OUT_OF_SCOPE`、`SQL_PARSE_FAILED`、`SQL_COST_EXCEEDED`。提示词 ID/版本和 SSE 类型也已集中登记。
- Alembic `0001_core` 创建 audit_log/query_run/task_run 公共运维表；审计与业务事务共享会话，任务状态独立短事务以便轮询，保留服务可组合后续业务清理器。

### 公共 API 清单（后续阶段复用）

| 位置 | 公共 API / 约定 |
| --- | --- |
| `core/config` | `Settings`、`get_settings()`；环境变量统一 `ASKDATA_` 前缀，生产启动校验 JWT/加密密钥，不提供默认生产密钥 |
| `core/errors` | `ErrorCode`、`AppError(code, message=None, http_status=400)`、AuthenticationError/PermissionDenied/SqlBlocked/DatasourceUnavailable；拦截消息只包含类别 |
| `core/response` | `Envelope[T]`、`PageParams(page,page_size).offset`、`Page[T]`；接口 `{code,message,data}` |
| `core/db` | `Base`、`AuditFields`、`SoftDelete`、公开 `AsyncSession` 类型；`Database.session()/transaction()/close()`；`BaseRepository(session, Model).get/list/create/update/soft_delete`；`@transactional` 共享外层事务，仓储只 flush |
| `core/security` | `hash_password/verify_password`；`TokenService.issue_access/verify_access`；`RefreshTokens.issue/rotate/revoke`；`AuthenticationProvider`、`RefreshStore`（consume 必须原子撤销，拒绝回放） |
| `core/crypto` | `SecretBox(SecretStr(key)).encrypt/decrypt/mask`；明文以 SecretStr 承载，只输出掩码 |
| `core/deps` | `Role`、`CurrentUser(id,role,team_id,active)`、`current_user`、`require_role(...)`、`IdentityResolver`；认证 service 通过 FastAPI override 接入 |
| `core/permission` | `PermissionService(provider, cache=None).visible_scope(user,datasource_id)/invalidate(...)`；`PermissionProvider.snapshot(...) -> list[TableGrant]`；`VisibleScope`、`TableScope`、`RowFilter/RowOperator`；成员字段取团队、员工开关、存活元数据交集，保留管理员脱敏 |
| `core/permission` 缓存 | `ScopeCache.revision/get/put/invalidate`、`RedisScopeCache`；授权/元数据提交后失效；快照仅写原 generation，返回前复核，避免撤销竞态把旧权限写入新缓存 |
| `core/scope` | `ScopeLevel`、`ScopeMixin`、`ScopedQuery.visible_to/merge`、`PromotionService.request/review`；个人内容对其他角色也不可见；个人 > 团队 > 平台；审核限本团队负责人 |
| `core/llm` | `ModelRegistry.select/get`、`ModelConfig`、`ModelProvider`；`LLMClient.chat/stream`、`EmbeddingClient.embed`、`RerankClient.rerank`；`ModelTransport`/LiteLLMTransport；调用携带 `CallContext(user_id,team_id,prompt_version)`；`UsageRecorder.authorize/record` 注入配额与用量写入 |
| `core/prompt` | `PromptId`、`PromptRegistry.render(...)->RenderedPrompt`（template/version/content）；5 类 v1 文件在 `app/prompts/`；不可信数据 JSON 转义后放入分隔区 |
| `core/sql/guard` | `SqlGuard.check(sql,dialect,scope)->GuardedSql`；normalized_sql/tables/columns/output_masks/datasource_id/dialect；`authentic()` 检查完整性；签发仅在当前进程有效，worker 必须重新取权限并过守卫 |
| `core/sql/executor` | `SqlExecutor(registry,limiter=None).run(datasource,GuardedSql,QueryLimits)->QueryResult`；limits 需真实 user_id，默认 30 秒/10000 行/成本 100000；返回 columns/rows/row_count/truncated/duration_ms；多进程生产注入 RedisQueryLimiter |
| `core/sql/dialect` | `Dialect.from_name`、`DialectAdapter.quote/paginate/date_trunc/type_name/prompt_hint`；MySQL/PG 方言差异集中处理 |
| `core/datasource` | `Datasource`、元数据/关系/连接测试 Pydantic DTO；`Connector`、PostgresConnector/MysqlConnector、ConnectorRegistry；后续阶段新增的 `DatasourceInspector.test/metadata` 为管理访问出口，无原始 SQL API；业务禁止使用低层 read_session |
| `core/storage` | `ObjectStore` 协议、`MinioStore.put/get/delete`；`object_key/validate_key`；StoredObject 含 size/sha256；路径验证，下载总是释放连接 |
| `core/tasks` | `TaskRecord`、`TaskStatus`、`TaskManager.enqueue/run`、`TaskContext.progress`；TaskStore/TaskQueue，DatabaseTaskStore/ArqTaskQueue；重复投递不重跑，失败信息只存错误码 |
| `core/stream` | `EventType`（stage/sql/data/chart/explanation/error/done）、`StreamEvent.encode`、`sse_response`；JSON 转义，关闭缓存和代理缓冲 |
| `core/masking` | `MaskKind/MaskRule`、`mask_value/mask_rows`、`safe_export_cell`；手机号/身份证/自定义/全遮罩；导出公式前缀防护 |
| `core/audit` | `AuditService.record`、`@audited(action,target_type)`（actor 关键字，与业务同事务）；AuditStore/DatabaseAuditStore；`QueryLogWriter.write`、QueryLogStore/DatabaseQueryLogStore；只存最多 5 行脱敏预览、清洗审计 diff/context details |
| `core/logging` | `LogEvent`、`SafeLogger`、唯一 `logger`、`configure_logging`、`RequestContextMiddleware`；请求 ID 与 OpenTelemetry span，摘要字段白名单，丢弃密钥/令牌/SQL/完整结果/异常文本 |
| `core/retention` | `RetentionService.run`、RetentionStore、DatabaseRetentionStore、CompositeRetentionStore；按 expires_at 清理，业务模块提供其自己的上传历史/关联反馈清理器，保留已转评测样例 |

`VisibleScope.all_columns` 是守卫内部所需的完整元数据，不能原样返回给 schema 浏览接口；接口只输出 `columns` 授权子集。守卫输出不能作为跨进程持久执行票据。业务服务必须通过 PermissionService 获得范围，不能自行构造授权。

### 已验证的证据与后续验证

- 固定模型输出门槛：`tests/fixtures/gate1.json` 违规 10、越权 8、注入 4，22/22 精确错误码匹配、消息无不可见对象名称、连接/EXPLAIN/execute 均为零。不是对真实模型业务准确率的声明。
- 初轮离线 pytest：282 passed；添加真实适配器与游标回归后的完整测试：293 passed。此后仍在补充边界、worker、LiteLLM 适配与错误追踪测试，最终数量/四项检查在本节后追加。
- 真实服务验证：`uv run pytest tests/test_external_services.py --external-services -q`：4 passed。独立临时 PostgreSQL 16、MySQL 8、Redis 7、MinIO 容器；验证只读事务、账号写权限检测、字段/主键元数据、PG 行级过滤、MySQL 成本/截断、Redis 缓存失效/分布式限制、MinIO 上传下载删除；容器 finally 清理，未复用现有服务/卷。
- 后续阶段写入业务代码之前，core 严格 mypy 为 28 个文件零错误；6 条 import-linter 契约全通过。最近整仓 mypy/测试会包含并行写入的后续业务模块，部分模块仍在修改；不能把该状态写成整仓全绿。
- 运行/检查入口：`cd backend && bash scripts/check.sh`；真实服务全量检查：`bash scripts/check.sh --external-services`；启动、环境变量、注入说明见 `backend/README.md`。
- 尚未纳入阶段 1：完整业务模块/查询流水线、完整评测集、部署与仓库根 CI workflow；写入边界不允许创建 `.github/workflows/`，已提供 backend 内强制检查脚本供后续 CI 接入。真实 LLM 凭证/准确率、生产配额存储、业务日志月分区/外键及业务保留策略由后续阶段接入；不以假实现通过冒充这些验证。

### 阶段 1 最终检查结果

执行 `cd backend && bash scripts/check.sh --external-services`，整条命令成功退出：

- `uv sync --frozen`：成功，Python 3.12.13 与锁文件依赖一致。
- `ruff check .`、`ruff format --check .`：全部通过，173 个 Python 文件格式已校验。
- `mypy`：136 个应用文件，零错误。
- `lint-imports --config importlinter.ini`：分析 191 个文件、816 条依赖；6 条契约通过，0 broken。
- `pytest --external-services`：**393 passed**，无失败/跳过。其中阶段 1 自有测试 **311 passed**；其余为并行后续阶段加入的测试，不将这些模块的实现算为本阶段成果。
- SQL 守卫自身测试 198 例；固定模型安全门槛 22/22 精确拦截（违规 10、越权 8、注入 4），且零连接/EXPLAIN/execute。执行器另验证成本、截断、规范化执行、来源绑定、签发防篡改、脱敏、超时、取消和限流。
- 真实外部适配器 4 例全部通过；全量执行还包含后续阶段新增的 PostgreSQL 导入事务隔离测试。临时容器使用独立随机名称并在 finally 清理。第三方有 2 个非失败警告（Starlette httpx 弃用提示、MySQL EXPLAIN 提示），没有过滤或伪装为失败检查已通过。

检查前修正了一个公共认证依赖问题：无令牌请求必须先返回 AUTH_FAILED/401，再允许初始化数据库身份解析器，避免依赖解析提前返回 503。为保持整仓架构检查通过，另将并行阶段反馈 schema 中的 ValueError 改为已登记 AppError/422，并做了后续迁移 SQL 字符串格式、外部测试子进程异步调度和 Ruff 格式整理等小范围兼容修正；没有扩展实施这些业务模块。

阶段 1 公共层、SQL 安全门槛、脚手架和接口交接已完成。最终检查结论对应上述执行时的共享工作区；后续阶段继续修改代码后，仍须重新执行相同强制检查脚本。


## D 组：upload、eval 与部署交付

范围：仅 modules/upload、modules/eval、D 组测试/fixture、0005_upload_eval.py、deploy、
backend/Dockerfile（含专属 dockerignore）；core 仅新增兼容的 importer.py，原因及边界已
追加 OPEN_QUESTIONS。未使用 subagent、未创建前端、未修改其他组目录或 main/worker。

已实现：
- upload：Pydantic 预览/确认/状态/历史/登记恢复接口、角色与所有者检查、审计；CSV/XLS/
  XLSX 文件头与大小校验、前 100 行、编码/分隔符/sheet/无表头配置、日期与前导零提示、
  合法去重列名、原列名描述、类型转换；MinIO 临时/正式路径与 SHA-256、源版本保留。
- 导入：后台 TaskManager handler，ds_<id> schema，服务端固定类型的分批 COPY 单事务，
  覆盖与严格结构追加，失败整体回滚，查询/写入账号分离；COPY 后登记 pending 状态与
  单独发布恢复，避免登记失败后重复追加。跨模块登记/默认 owner_team 授权通过 Protocol。
- eval：eval_case/eval_run/eval_result，案例维护/加载审计、不可变运行快照、后台运行、
  无完整结果落日志（仅 digest）、结果列名/列序无关与行序/重复行/1e-6 容差规则、安全
  零执行/零重试/无泄漏、澄清候选、图表/语义/指标/时间判定、指标汇总、两次运行对比；
  ChatPipeline 为 Protocol，用假实现测试，不 import C 组；参考执行走真实 core 通道。
- 安全门槛：实际读取 eval/cases.jsonl 和 fixtures.yaml，真实 SqlGuard + core.llm 固定
  假模型输出，违规 10/越权 8/注入 4 共 22/22 通过；部分题运行不能授权发布。
- 迁移：只新增 0005，down_revision=0004；完整链 0001_core→0002→0003→0004→0005。
- 部署：api/worker/pg-meta(pgvector)/pg-import/redis/minio 加 MinIO bucket 初始化一次性
  服务，无 web 的原因写入 Compose 注释；内部端口、查询账号分离、SSE Nginx 草稿、
  环境变量示例、uv frozen 后端镜像、固定 xlrd 补充依赖与构建上下文排除 secrets/venv。

验证（实际运行）：
- D 组专项：34 passed，含 PostgreSQL 18 Docker 集成，容器测试后删除；证明第二批 COPY
  失败恢复旧表、追加结构拒绝、读账号跨 schema/INSERT/CREATE 拒绝、真实守卫执行器
  查询；XLS 前导零、题库全部 7 份上传配置的实际解析/转换通过。
- 本工作区全量 backend（带 xlrd 补充）：442 passed，6 skipped（其他外部测试及本组
  外部测试已在专项单独执行）；1 条既有 Starlette TestClient 弃用 warning。
- 本组 ruff、strict mypy 17 文件通过；import-linter 6 contracts kept / 0 broken；
  架构规则/OpenAPI/现有门槛测试在前一轮 65 项专项中通过。
- Docker Compose config --quiet、sh -n deploy/init-import.sh 通过。

复现命令（backend/）：
```bash
uv run --frozen ruff check app/core/importer.py app/modules/upload app/modules/eval \
  migrations/versions/0005_upload_eval.py tests/test_upload_d.py tests/test_eval_d.py \
  tests/test_upload_d_external.py
uv run --frozen mypy --config-file pyproject.toml app/core/importer.py app/modules/upload app/modules/eval
uv run --frozen lint-imports --config importlinter.ini
uv run --frozen --with-requirements app/modules/upload/requirements.txt pytest -q \
  tests/test_upload_d.py tests/test_eval_d.py tests/test_upload_d_external.py --external-services
uv run --frozen --with-requirements app/modules/upload/requirements.txt pytest -q
```
根目录配置检查：
```bash
docker compose --env-file deploy/.env.example -f deploy/docker-compose.yml config --quiet
sh -n deploy/init-import.sh
```

待总装：UploadRegistration 的 datasource/schema/permission service 适配器、真实 chat
评测适配器及 fixture 身份/schema 映射、main/worker 路由/handler 绑定；具体构造与权限
边界写在两个模块的 INTEGRATION.md。未验证完整镜像构建/Compose 启动，未报告真实模型
准确率；未实现导入跨库提交崩溃自动恢复、遗留临时对象清理。部署说明见 deploy/README.md。

## A 组：身份、组织、数据源、结构、权限与模型（I1 审计补记）

原有 A 组实现已存在，本节补齐缺失的阶段记录，不把原实现归为 I1 新增成果。

- 已核对 auth/org/datasource/schema/permission/modelcfg 的 Pydantic 接口、service/repository 分层、登录/刷新/撤销、用户团队角色、连接测试、后台元数据同步、结构浏览/描述、团队上限及员工子集授权、三类模型与团队偏好。密钥经 SecretBox，API 掩码，写操作经 core.audit；不直接导入驱动/SDK。
- I1 修复：受保护路由先认证再初始化服务；schema 同步/描述更新写入 embedding outbox；首个 Embedding 配置也必须匹配已迁移的 1024 维；测试模型向量调整为正确维度。
- I1 补齐 datasource/schema/permission 的上传公开 service 端口。预留数据源不可查询，发布凭证加密、持有数据源锁、持久化幂等状态；负责人仅本团队，首次团队授权一次，后续新字段/新表不自动授权；按表同步保留其他 sheet 和人工描述。实际上传 adapter、COPY/MinIO 属于其他组，未修改。
- Alembic 0002 增加 datasource 自有发布状态字段；A 组测试用已升级到 0003 的数据库验证索引 outbox，0002 的独立迁移测试保留。新上传端口测试验证禁止成员/跨队写入、仅 owner 团队授权、凭证密文、发布重试、新字段默认拒绝、多 sheet 保留与整个元数据事务回滚。

## B 组：语义、指标、提升、反馈、日志、用量、审计与索引（I1 审计补记）

- 已核对 semantic/metric/promotion/feedback/querylog/usage/audit/embedding。语义别名/枚举、三级私密性与版本、提升审核、指标 LIMIT 100 守卫试跑、反馈原因/快照/处理/示例审核/评测 sink、角色日志范围和筛选、quota 与用量回调、审计浏览及索引任务均有测试。个人内容不因负责人/管理员角色而可见。
- I1 修复 OpenAPI 测试：用明确的 20 个路径及每条 HTTP 方法清单验证覆盖，所有 29 个 JSON 操作为 Envelope；原先用路径数量 >=23 无法表达接口契约。原始四处 Ruff 长行问题已修复。
- I1 修复：Embedding 先通过 source service + PermissionService 求允许的条目引用，再把过滤集合放入数据库向量/关键词排名条件，召回后仍复核；关键词使用 pg_trgm similarity 与余弦距离联合排序；POST search 经 core.audit。GET quota 不再隐式插配置。
- 0003 分区/索引、真实 pgvector 排名的权限预过滤、feedback 的复合键级联删除均经独立 PostgreSQL pgvector 容器测试。转换为评测样例的持久化依赖 eval 的公开 sink；主入口及 worker 装配由对应总装者负责。

## C 组：查询流水线、SSE 与导出（I1 审计补记）

- 已核对十阶段流水线、用户独占会话、追问及当前权限复核、后端日期、澄清一次、只读守卫执行、最多三次纠错、自动图表/解释、SSE/断连记录、CSV/XLSX 导出、手改 SQL 重跑。固定模拟安全题 22/22 拦截，零连接/执行，纠错后守卫拒绝也立即停止。
- I1 新增 `chat.retrieval.ServiceRetrievalIndex`，通过 A/B service 取得当前授权 schema、语义/枚举、指标、审核示例，携带 ID/版本及授权引用，支持混合检索、可选 core RerankClient 和授权 FK 一跳补全。独立 chat factory 已默认使用具体适配器；其他 composition root 的绑定交接写入 OPEN_QUESTIONS。
- I1 修复持久化：MessageView 返回澄清问题/选项/消费状态；QueryContext 写入与 B 组日志视图一致的 semantic/metrics/examples/time_range 版本摘要；拦截日志不保留拒绝 SQL 或纠错前的旧守卫 capability。
- 0004 消息外键改为 query_run (id,created_at)，在真实分区 PG 上升级/降级成功，日志删除后消息关联同时清空；父消息删除也 SET NULL。`chat.service.build_retention_store(session)` 可组合进 core 保留服务，仅清理本模块过期消息；已测试保留未到期的子消息。
- ChartSpec 可切换图表类型与下载是后端契约，未创建前端。真实模型准确率和完整部署验收不在本节测试结论内。

## I1：本轮审计最终验证与边界

已完整读取 /tmp/i1.md、AGENTS.md、docs 顶层全部文档及原始合并稿；自行实现，未使用 subagent。写入仅限任务列明的 A/B/C 模块（chat README 含接口交接）、backend/tests、迁移 0002/0004 与本次文档追加；没有改 upload、eval、core、main.py、worker.py、composition.py、deploy 或前端。并行总装者正在更新共享工作区，以下结果对应检查时的实际版本。

- `ruff check .`：整仓通过。I1 范围 `ruff format --check`：119 文件全部通过。整仓格式检查发现并行修改的 core/datasource.py 尾部空行，属 I1 边界外，已追加 OPEN_QUESTIONS；未越界格式化它。
- `mypy`：整仓 140 个应用文件，零错误。
- `lint-imports --config importlinter.ini`：198 文件、935 依赖，6 kept / 0 broken。
- I1/A/B/C + 架构/固定模型门槛，带真实外部服务：**167 passed**，无跳过；包含 A 组真实 PG、0002→0004 分区迁移/pgvector 排名/降级和外键清理。临时容器均测试后清理。
- 整仓离线回归（带现有 xlrd 补充依赖）：**465 passed, 7 skipped**。七个跳过项为外部 Docker 测试，其中 I1/A/B 自有外部项已在上一条命令单独实际执行。既有 Starlette TestClient 弃用 warning 不影响通过。
- 没有伪造真实 LLM 成功率。与提示词/检索有关的回归采用固定输出的真实 core.llm + SqlGuard/SqlExecutor 路径；完整 eval 回归仍由 eval 组验收。逐条 P0/架构审计矩阵、额度定义与总装待接接口见 OPEN_QUESTIONS 的 I1 小节。

复现（backend/）：

```bash
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen lint-imports --config importlinter.ini
uv run --frozen pytest tests/a_group tests/group_b tests/test_chat.py \
  tests/test_chat_repository.py tests/test_architecture.py tests/test_gate1.py \
  --external-services -q
uv run --frozen --with-requirements app/modules/upload/requirements.txt pytest -q
```

I1 格式范围检查使用上述 15 个模块、tests/a_group、tests/group_b、tests/test_chat.py、tests/test_chat_repository.py 与 migrations/versions/0002–0004；整仓格式检查仍须对应公共层写入者修复后由最终总装重跑。

最后补充验证：消息 query_run_id 的 ORM 类型显式为 BIGINT，与 Alembic 列一致；PG 外键/持久化测试使用 `2**31 + 17` 查询 ID，和 chat 仓储/保留测试合计 **5 passed**。之后整仓 ruff lint、mypy（140 文件）、六条 import-linter 契约及 I1 范围格式检查（119 文件）再次全部通过。



## I2：上传/真实 chat 评测适配器总装

本次已完整读取 /tmp/i2.md、AGENTS.md、docs/*.md 和两个 INTEGRATION.md，直接实施，
没有使用 subagent；本组只修改 modules/upload、modules/eval、eval/ 及本节/开放问题，
未写前端，未改 main.py、worker.py、其他业务模块、core 或共享锁文件。本节仅追加。

已完成：
- `upload/adapters.py`：导出 build_upload_registration，保留原 actor 与 owner-team
  边界；编排 pending 预留、只读 ds_<id> 连接验证、单表元数据发布、失效删除字段授权、
  首次 owner-team 授权、scope 缓存失效及持久 job receipt。新适配器实例仍复用 service
  receipt，已有数据源新字段不自动授权。具体 service 端口缺失时明确 TASK_FAILED/503，
  不调用管理员专用方法绕过角色，不直接访问其他模块表。
- `eval/adapters.py`：导出 build_fixture_assets、build_eval_adapter、
  build_reference_runner、build_simulated_adapter、build_fixed_client 和 run_suite；
  显式身份/数据源/schema 映射，验证角色、团队、活跃状态和唯一 ID；生产可注入实际
  core PermissionService。真实 chat QueryPipeline 每题独立执行器计数，继承固定
  clock/history，检查真实模型 ID 与提示词版本，返回重试、token、图表 schema verdict、
  实际语义/指标命中、澄清候选和明确执行 SQL 日期区间。复杂日期表达式不猜测。
- `GuardedReferenceRunner` 仅追加兼容的可选 sql_mapper：参考 SQL 同样映射 schema，
  再经 PermissionService → SqlGuard → SqlExecutor。原始题目不进入依赖工厂，只有
  EvalQuestion 输入上下文；schema 改写保留字符串/注释，无法安全改写时拒绝。
- `eval/run_pipeline.py`：真实 chat 模拟输出运行入口。--gate1 自动选择完整 22 题，
  不连接数据库；--responses 对独立模拟响应 artifact 跑完整/指定题集，连接通过 core
  Settings 的 ASKDATA_EVALUATION_DATASOURCES 环境变量读取。stdout 仅 JSON 汇总，
  core 日志到 stderr；错误不会输出原始环境配置/密钥/traceback。
- 新增 `eval/tests/test_i2_adapters.py` 与 `test_i2_external.py`，共 24 项测试；更新
  两个 INTEGRATION.md 和 eval/README.md 的工厂、I3 接线方式及复现命令。本组未改
  cases.jsonl、fixtures.yaml、testdb.sql、build_assets.py 或任何题目/样例内容。

验证结果：
- 负责范围 ruff check 与 format --check 通过，23 文件格式符合；strict mypy 的应用/
  CLI 19 文件与新增测试 2 文件通过；import-linter 6 kept/0 broken，架构测试通过。
- 专项 **75 passed**，无跳过，含已有 upload/eval、架构检查及新增 24 项测试；两个
  临时 PostgreSQL 18 容器分别验证 COPY/账号隔离与完整真实查询回放，finally 删除。
- 门槛 1：真实 core LLMClient 固定输出 → chat QueryPipeline → PermissionService →
  SqlGuard，违规 10、越权 8、注入 4，**22/22** 精确拦截、零连接/EXPLAIN/执行/重试，
  无不可见对象泄漏。CLI 入口同样报告 safety_gate_passed=true。
- 完整 100 题和 CLI full replay 在真实只读 PG 连接上运行；参考与模型 SQL 均经过
  真实 core 执行器，commerce/upload schema 改为真实不同名称，100 条查询日志均写入
  脱敏摘要。固定 SQL/table 输出 + ScopeOnlyIndex 的有意基线为 **76/100**：54 个普通
  查询/时间/上传/空结果题与22个安全题通过，10语义、8图表、6澄清正确报告失败。
  这证明接线/评分及剩余能力边界，不是实际 LLM 准确率，也不声称题集100%通过。
- 独立 eval 资产检查通过（100 题，22安全题，7导入任务）；原有资产 pytest **34 passed**，
  新增两个 backend 依赖测试模块在独立资产环境按设计跳过。资产自身 mypy 3 文件、
  eval/ 全目录 ruff 通过。
- 扩展整仓检查快照：ruff check 通过、mypy 140 应用文件通过、6导入契约通过；pytest
  **512 passed / 7 failed / 8 skipped**，失败属于 test_i1_audit 的未绑定路由/401 预期，
  format 检查当时另发现 core/datasource.py 末尾空行。均记录 OPEN_QUESTIONS，未越界
  修复，不能将此快照称为整仓全绿；后续并行修改仍需 I3 重新执行整仓检查。

复现（backend/）：
```bash
uv run --frozen ruff check app/modules/upload app/modules/eval ../eval/run_pipeline.py \
  ../eval/tests/test_i2_adapters.py ../eval/tests/test_i2_external.py
uv run --frozen ruff format --check app/modules/upload app/modules/eval ../eval/run_pipeline.py \
  ../eval/tests/test_i2_adapters.py ../eval/tests/test_i2_external.py
uv run --frozen mypy --config-file pyproject.toml app/modules/upload app/modules/eval \
  ../eval/run_pipeline.py
MYPYPATH=../eval uv run --frozen mypy --config-file pyproject.toml --follow-imports=silent \
  ../eval/tests/test_i2_adapters.py ../eval/tests/test_i2_external.py
uv run --frozen lint-imports --config importlinter.ini
uv run --frozen --with-requirements app/modules/upload/requirements.txt pytest \
  -c pyproject.toml -q tests/test_upload_d.py tests/test_eval_d.py tests/test_upload_d_external.py \
  tests/test_architecture.py ../eval/tests/test_i2_adapters.py \
  ../eval/tests/test_i2_external.py --external-services
uv run --frozen python ../eval/run_pipeline.py --gate1
```

未完成/外部依赖：真实上传登记仍等待 DatasourceService 的 reserve_upload/upload_owner/
publish_upload_credentials/finish_upload_publication、SchemaService.synchronize_upload、
GrantService.grant_upload_owner，以及 pending source 空权限快照支持；I3 还需绑定路由/
后台任务和生产 service-backed retrieval/fixture identities。端口、事务/审计/持久回执、
成员开关及默认仅 owner-team 授权的约定已写入 upload/INTEGRATION.md 和 OPEN_QUESTIONS。
本组未伪造这些 service 的实际可用性，也未通过读取期望答案补造语义/澄清候选。


## I3 应用组装与运行验证（直接实施，未使用 subagent）

仅写 main.py、worker.py、新 composition.py、scripts/、deploy/、RUNBOOK，以及本节/OPEN_QUESTIONS 记录；未写前端、未修改其他组业务/core/迁移文件。保留共享工作区已有内容。

已完成：
- Composition 组装 Settings/Database/SecretBox、核心连接器/执行器/Redis 限制、MinIO、PermissionService、用量/模型客户端及各业务服务。一个请求/job 一个服务图和数据库会话；普通写 API 共用事务，chat/upload/eval 使用各自提交生命周期。SSE 会话保持到响应完成。
- 所有 modules/*/api.py 路由自动挂载，已含 /api 的不重复加前缀；健康端点 /healthz 与兼容 /api/health。7 项 I3 新增回归覆盖并发会话/用量隔离、eval 加载、导入读写隔离、待发布上传空范围、未配置认证、路由与任务注册。
- worker 注册 schema_sync、upload.import、eval.run、embedding.update/rebuild、retention，定时核心保留/每日同步/每 15 分钟索引更新；同步有 Embedding 模型时触发重建。定时使用明确指定且重新验证的管理员，upload/eval worker 重新解析发起者身份。
- 使用 I2 build_upload_registration 与 A 组新提供的公开上传方法；eval 使用 build_fixture_assets/build_eval_adapter/build_reference_runner，真实身份/数据源映射、live PermissionService、固定 run 模型、独立每题会话和真实执行计数。未提供映射时实际题目 fail closed。接入 I1 ServiceRetrievalIndex 和可选 Rerank。
- migrate/start_api/start_worker 脚本、初始管理员安全日志、0600 私有环境生成器、加密登录文件 helper 修复、真实 HTTP scripts/smoke.py。RUNBOOK 含 Compose/本地命令、管理员、env、定时、评测映射和当前限制。
- Compose 一次性迁移依赖、nginx 唯一入口/SSE、开发回环端口 override；导入首启脚本复制 wrapper 与角色就绪健康检查，修复 Docker Desktop bad interpreter 导致写账号缺失却数据库健康的问题。

实际验证：
- 在独立 askdata-i3 项目全新卷启动 pg-meta/pg-import/redis/minio，四项健康。自动初始化产生 import_writer；未以手工账号初始化冒充首启验证。
- scripts/smoke.py 自动 alembic upgrade head、bootstrap_admin、MinIO bucket 初始化，真实 uvicorn API/arq worker、本地 OpenAI 兼容固定响应经 core.llm/LiteLLM。登录→建只读源→后台同步→默认未授权→授权员工→提问总和 30→SQL_NOT_SELECT 拦截→SSE done→撤权后拒绝均通过；eval/cases/load=100；CSV 预览/确认/后台 COPY/MinIO/发布/默认 owner-team 授权后员工提问总和 9 通过。
- I3 范围 ruff check 与 format --check：通过，8 Python 文件；整仓 ruff check：通过。整仓 format 仍有 core/datasource.py:389 的范围外空行，已记开放问题，未越界修改。
- mypy app scripts：145 source files，零错误；lint-imports --config importlinter.ini：6 kept / 0 broken。
- 专项 pytest scripts/test_composition.py tests/test_worker.py tests/test_foundation.py tests/test_architecture.py：38 passed。
- 完整外部测试 uv run --frozen --with-requirements app/modules/upload/requirements.txt pytest -q tests scripts/test_composition.py --external-services：479 passed，零失败、零跳过，20.31 秒。两项第三方非失败 warning（Starlette httpx 弃用/MySQL EXPLAIN）。
- Compose config --quiet、sh -n deploy/init-import.sh deploy/start-import.sh、bash -n 各启动/检查脚本通过。Nginx 镜像拉取超 60 秒中止，未验证 nginx 容器或完整后端镜像 build，不伪称完整 Compose 应用栈运行。
- 已清理本次 askdata-i3 的四个容器、网络、四个专用卷和 /tmp/askdata-i3.env；未改动其他现有容器。

复现（backend/）：
```bash
uv run --frozen ruff check app/main.py app/worker.py app/composition.py scripts
uv run --frozen ruff format --check app/main.py app/worker.py app/composition.py scripts
uv run --frozen mypy app scripts
uv run --frozen lint-imports --config importlinter.ini
uv run --frozen --with-requirements app/modules/upload/requirements.txt \
  pytest -q tests scripts/test_composition.py --external-services
```
真实 Compose 冒烟的建环境/启动/执行/清理命令完整列在 docs/RUNBOOK.md；必须使用全新专用元数据库。剩余外范围：eval 尚缺允许负责人反馈转评测草稿的公开 service；upload 尚缺过期历史/对象公开清理接口。对应功能保持拒绝/明确限制，不直接跨模块写表。
