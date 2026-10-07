# 开放问题与保守决策

## 架构 3.16 里程碑内容缺失

架构文件的五阶段详情为嵌入图占位符，纯文本只描述两道门槛。本次采用用户明确指定的五步实施顺序；门槛 1 为违规/越权/注入固定模型输出 100% 拦截，门槛 2 的业务准确率阈值待真实模型首轮基线后确定，不伪造真实模型准确率。

## 纠错规则冲突

3.4 含“空结果疑似错误重试”，3.6 则明确空结果不盲目重试。采用更保守的 3.6：仅执行语法/字段错误可纠错，守卫拦截立即结束，空结果解释提示；最多三次纠错。

## 外部服务

启动检查发现 Docker 服务可用。LLM 用固定模拟响应完成离线安全与流水线测试；真实模型凭证不写入仓库。PostgreSQL、Redis、MinIO 的实际可用性与验证结果由实施阶段补充，不能以 mock 通过替代集成测试结论。

## 实施工具链阻塞

当前安装 `@earendil-works/pi-coding-agent` 1.0.0、其依赖 `@earendil-works/pi-agent-core` 1.0.0 与 `pi-subagents` 0.74.0。扩展后台启动器强制解析 `@earendil-works/pi-agent-core/node`，但 core 的 exports 仅 `.` 和 `./package.json`，不含 `./node`。因此不是 AskData 代码失败，也不是缺少模型凭证；子代理尚未开始执行。

待处理：全局宿主/扩展版本兼容修复，并使用相同 subagent 协议及同一模型重试。全局工具链不属于 AskData 项目依赖，尚未修改。失败运行与工作区证据见 PROGRESS.md。



## D 组：上传写入出口与跨模块登记

现有 core 查询连接只支持 SqlGuard → SqlExecutor 的只读能力，无法承担上传 COPY。
按允许的“仅向后兼容追加”新增 `backend/app/core/importer.py`：服务端类型化 ImportPlan、
白名单列类型、合法标识符、单事务分批 COPY、PG advisory lock、每个 ds_<id> 的独立
只读账号；不接收用户或模型 SQL，也不改变已有守卫/执行器接口。API 使用只含查询凭证的
ImportReadGateway，写网关与写入凭证仅给 worker。ImportSettings 是 Settings 的兼容子类。

上传不能直接访问 datasource/meta_* 或授权表。UploadRegistration Protocol 定义 reserve、
owner_team、publish 三个 service 边界；当前并行 datasource/schema 服务的公开 create/
synchronize 为管理员专用，尚缺团队负责人可调用的“待导入数据源预留/发布”接口。
采用保守方案：不伪造管理员身份，不直接读写其他模块表；该适配器由对应模块所有者或
集成人补齐。确切接口、默认仅授权上传者团队、新增字段保持未授权和 SecretBox 要求在
`backend/app/modules/upload/INTEGRATION.md`。

## D 组：导入跨存储事务与文件版本

COPY、表准备和授权查询账号处于导入 PG 的单一事务；元数据库与 MinIO 不能参与这个
本地事务。源文件先预存不可变正式备份，COPY 成功后记录 registration_pending，再发布
元数据/权限；失败可调用发布恢复接口，不重复追加。进程在 COPY 提交与元数据库状态
提交之间崩溃时需人工对账，禁止盲目重新投递 running/failed 的追加任务。第一版不声称
跨数据库原子提交或完整崩溃恢复，暂存对象回收留待保留任务总装。源版本按 upload_job
预览预留递增，失败版本也保留；同一文件的多个 sheet 使用同一 source 分别提交表导入。

.xls 的 pandas 引擎需要共享锁中尚未包含的 xlrd。为遵守并行目录边界，不修改共享
pyproject/uv.lock，新增 upload/requirements.txt 固定 xlrd==2.0.2；Dockerfile 安装该补充，
本地检查用 uv run --frozen --with-requirements。文件入口采用 Pydantic base64 JSON，
避免未经协调新增 multipart 依赖；源文件仍限制 50 MiB，Nginx 草稿上限 70 MiB。

## D 组：评测夹具映射与总装

EvalAssets 消费 /eval 的 100 题和权限夹具；Gate1Runner 经 core.llm 固定假模型与真实
SqlGuard 跑全部 22 道安全题。完整评测仅依赖 ChatPipeline Protocol，无 C 组代码导入；
参考答案走 PermissionService → SqlGuard → SqlExecutor。fixture 的字符串身份和固定
schema 是离线适配契约，接入实际组织/导入数据源时须经各模块 service 建立别名映射。
执行计数、图表 schema verdict 与命中的语义/指标需由真实 chat 适配器提供，不能信任
模型自报。root main/worker 的路由、依赖、任务 handler 装配属于集成范围，D 组未修改。

Compose 暂不提供 web/宿主端口，在注释中说明无前端；nginx.conf 为后续唯一入口草稿。
配置语法与独立 PG 导入集成已验证，完整镜像构建/Compose 启动和真实模型准确率未验证。


## I3 总装接口与待对接项

总装根 `app/composition.py` 为每个请求/任务创建独立 Services，会话不跨请求共享。I2 的 upload/eval adapters 尚未出现，暂用延迟导入；约定工厂为 `build_service`（上传参数 session/storage/importer/permissions/audit/tasks/services；评测参数 session/assets_path/services/tasks/executor），反馈转换为 `build_feedback_sink(session, services)`。最终会按实际工厂签名对接。

既有 RetentionService 仅提供核心日志清理，upload/feedback service 尚无公开保留清理接口；I3 不直接访问其他模块表，也不在未实现时声称完整清理源文件。每日同步/索引定时任务仅在显式配置 ASKDATA_SCHEDULER_ADMIN_ID 且 org.identity 确认其为有效管理员后运行，未配置则不伪造身份。

## I1 审计决策与总装交接

- 已在 A 组公开 service 中补齐上传端口：`DatasourceService.reserve_upload`、`upload_owner`、`publish_upload_credentials`、`finish_upload_publication`；`SchemaService.synchronize_upload`；`GrantService.grant_upload_owner`。发布返回对象的四个属性与 upload adapter 端口一致；调用链必须共享元数据库事务。`0002` 新增 datasource 自有的 `upload_initialized` 和 JSON 发布记录，第一次提交才初始化上传者团队授权，已完成 job 重试不扩权；后续新字段/新表默认未授权。仅本团队负责人或管理员可写，成员与其他团队负责人拒绝，不伪造管理员。该更新只改 A 组与 0002，没有修改 upload 模块。
- `ServiceRetrievalIndex(schema, semantic, metric, feedback, embedding=None, rerank=None)` 已在 chat 中实现，可由 composition root 为每次请求实例化并交给 `ContextBuilder`。它只调用各模块 service，携带条目 ID/版本，合并三级口径、读取审核通过的示例，混合检索并补全授权外键；Rerank 未配置可跳过。独立 chat factory 已默认使用它；总装根若仍绑定 `ScopeOnlyIndex`，语义/指标不会进入查询上下文，必须换成此适配器。main/worker/composition 属于其他总装者边界，I1 不修改。
- schema 同步及人工描述变更、semantic/metric 保存、示例审核均写入同事务的 embedding outbox；worker 须排队/定期消费更新任务。没有已绑定的 Embedding 服务时，超过阈值的混合检索保守返回不可用，不放大可见范围。模型配置的向量维度须匹配 0003 的固定 1024 列；首个模型也不再允许任意维度。变更维度仍需单独评审迁移。
- `chat.service.build_retention_store(session)` 是可交给 `core.retention.CompositeRetentionStore` 的公开清理器，仅清理 chat 自有过期消息。消息引用 query_run 的 (id,created_at) 复合键，日志清理会 SET NULL；父消息清理也会 SET NULL，避免尚未过期的回答挡住清理。PostgreSQL 的 feedback 同期清理依赖 0003 的级联外键，转换出的评测样例由 eval 保留自己的副本。worker 必须装配 chat 清理器；I1 不改 worker。
- PRD 的“切换图表类型”在无前端范围内以公开 `ChartSpec` 允许集合实现，客户端可根据同一已脱敏结果切换渲染；当前没有持久化个人图表偏好的后端需求。P0 Excel/CSV 完整导入、100 题全量回归、真实模型准确率、入口装配与部署验证分别归 upload/eval/其他总装者，I1 的测试不能替代这些验收。
- P1 daily_query_limit 原按模型调用预留，查询流水线可能有多次调用。已由 G1 解决：core/llm 新增 `CallContext.query_id`（contextvar 归一）与 `QueryQuotaRecorder.authorize_query`，usage 新增 `usage_query`（迁移 0006，(team_id, query_id) 唯一）作幂等登记回执，重试与澄清共用同一 query_id 只计一次，token 额度保持逐调用。回归见 tests/group_b/test_query_quota_g1.py。
- 0002/0004 按本次未发布开发链修正。已有数据库若已经应用旧版同名 revision，不应直接重新运行旧 revision；后续发布者需为存量环境另建迁移，不在 I1 的 0002–0004 边界内编造线上升级记录。

## I1 对照清单（PRD P0 与架构 3.2/3.3/3.4/3.7/3.10/3.11/3.12）

| 条目 | 审计结果与证据 |
| --- | --- |
| 会话历史/多轮/流式 | ChatRepository 用户独占会话；十阶段流水线、SSE 阶段/SQL/数据/图表/解释/错误/结束；历史提供持久化澄清选项与已消费状态；失败/断连均记日志，tests/test_chat* |
| SQL/结果/解释/自动绘图/导出 | 所有执行仅 GuardedSql → SqlExecutor；图表 JSON 限制字段和类型，错误回退；解释附层级/版本/过滤/后端日期；CSV/XLSX 新权限复核、脱敏和公式前缀防护 |
| 澄清/最多三次纠错 | 原问题原选项一次消费；仅 core 标记的执行语法/字段错误纠错，每次重新取权限并过守卫；空结果不重试；固定 22 安全题零连接/执行 |
| MySQL/PG 接入、连接测试、同步/刷新 | DatasourceInspector 唯一管理出口；写权限警告入审计；TaskManager 后台同步；人工描述不被覆盖；新元数据无授权；A 组真实 PG 测试 |
| 表字段浏览与描述 | browse/relations 先 PermissionService 再过滤字段/外键；描述变更审计并写索引 outbox；普通成员无描述修改权限 |
| 团队授权/员工子集 | GrantService 仅管理员替换上限、负责人仅本队收窄；表授权先于字段、继承掩码；撤销/元数据变化失效缓存，A 组缓存回滚与子集测试 |
| 别名/枚举/三级/提升 | core.scope 唯一三级可见性和状态机；个人对负责人/管理员不可见，负责人仅审核本队待提升；语义版本变更前后审计、异步索引 outbox |
| 指标保存前试跑 | 参数转 SQL 字面量，SqlGuard 规范化后再守卫 bounded LIMIT 100 SELECT、只读 SqlExecutor 试跑；失败不保存；保存/提升/删除审计，B 组 metric/promotion 测试 |
| 点赞/点踩、快照、处理和转换 | 点踩必须合法原因，仅本人回答可反馈；管理员/本队负责人处理；快照保留模型、提示词、脱敏摘要及有效语义/指标版本；示例未审核不进入检索；评测转换依赖 eval 公开 sink |
| 查询日志范围/拦截/耗时/模型/token | 角色行过滤、时间/用户/数据源/状态/拦截码/反馈筛选；最多 5 行脱敏摘要；修复 chat 与 B 组快照键不一致；被拦截 SQL（含纠错前的旧 capability）不再落库或返回 |
| 三类模型/密钥/团队选择/测试 | 管理员维护、负责人仅本团队选择已启用模型，平台默认回退；SecretBox 密文落库、API 掩码；连接测试只经 core.llm；固定向量维度检查 |
| 公共层、分层、审计、安全规则 | 未发现负责模块直接 import 驱动/SDK/LiteLLM/MinIO/环境或跨模块仓储模型；import-linter 六契约及架构 AST 测试通过。Embedding search POST 新增 core 审计，quota GET 不再隐式插配置；所有 JSON API OpenAPI 为 Envelope |
| 本次边界外 P0 | upload 的文件解析/COPY/MinIO、eval 的 100 题与全量回归、部署/入口绑定由对应模块和其他总装者负责；只补 A 组所需公开上传端口与检索/清理交接，不据此声明整个平台上线 |

I1 最后整仓格式检查（代码检查时间点）：`ruff check .` 全通过，`ruff format --check .` 仅报 `app/core/datasource.py:389` 文件尾多余空行。该公共层文件在并行修改中且不属 I1 边界，I1 未改；负责模块及测试/迁移的 119 文件格式检查全通过。对应公共层写入者须修复后重跑整仓格式检查。



## I2：上传登记 service 缺口与保守适配

已实现 `modules/upload/adapters.py` 的 `build_upload_registration`，只编排公开
service，不访问其他模块 repository/models，不构造管理员身份。当前 datasource/schema/
permission service 缺少以下方法，因此工厂明确返回 TASK_FAILED/503，而不是冒充完成
真实登记。需要对应模块所有者/I3 补齐：

- DatasourceService：`reserve_upload(name, team_id, *, actor) -> int`、
  `upload_owner(datasource_id) -> int`、
  `publish_upload_credentials(job_id, datasource, owner_team_id, *, actor)`、
  `finish_upload_publication(job_id, datasource_id, *, actor)`。
- SchemaService：`synchronize_upload(datasource_id, table, *, actor) -> SyncResult`。
- GrantService：`grant_upload_owner(datasource_id, team_id, *, actor)`。

现有 OrgService.ensure_team、GrantService.expire_metadata 可直接复用。预留必须创建
pending/upload，不能提前测试不存在的 reader；owner 读取必须拒绝非 upload 类型；发布
必须校验 ds_<id>、使用 SecretBox 加密只读凭证，与 upload 共用事务并锁定 datasource；
持久 job receipt 的 completed/initialize_owner_grants 标记须在首次完整提交前保持可重试。
只初始化 owner team 授权，后续发布新增字段默认不授权；保留人工说明、其他 sheet、掩码，
失效删除字段授权，提交后再失效缓存。员工可见开关采取保守默认关闭，负责人可再明确配置。
详细端口及返回结构在 upload/INTEGRATION.md；对应所有者可返回自己模块的同形 DTO，
无需反向 import upload/adapters。每一步写入都必须以原 actor 审计且不得单独 commit。

另一个现有缺口：UploadService.confirm/publish 的 _job_access 会问 PermissionService，
而 GrantService.snapshot 当前立即调用 active-only 的 DatasourceService.connection。
预留 pending source 会在确认/登记之前触发 DatasourceUnavailable。权限 provider/数据源
service 需要支持 pending 上传的空可见范围，由 upload 继续按真实 job 所有者判定访问。
I2 不跨界修改这些 service，也不绕过权限调用。

## I2：真实流水线回放的验证边界

已接入真实 chat QueryPipeline，经 core LLMClient 固定输出、PermissionService、SqlGuard、
SqlExecutor、QueryLogWriter，支持显式身份 ID/schema 映射、固定时钟、历史、参考结果、
每题独立执行计数及评分。I3 需经公开 service 提前创建 fixture 身份/授权/连接，并在每次
worker dispatch 重新解析绑定，生产运行传入实际 GrantService.permissions；不传权限服务
只供隔离 fixture 回放。没有直接向其他模块表插入评测种子。

100 题实际 PostgreSQL 18 回放及 CLI 入口已验证。固定 SQL/table 输出配合 ScopeOnlyIndex
的有意基线为 76/100，门槛 1 为 22/22；另外 24 项为 10 semantic（无实际 retrieval 命中）、
8 chart（固定 table 不在允许图表类型中）、6 clarify（现有默认候选与题集候选不匹配）。
这些失败被真实 scorer 报告，不能称为题集全部通过或真实模型准确率；正确的语义检索和
澄清候选由 I1/chat service 及 I3 依赖工厂接入，I2 不读取期望答案补造上下文。
chat.service 现有运行时 QueryPipeline/QueryContext/PipelineDependencies 等隐式 reexports
可用，但严格 mypy 不视为显式导出；adapter 仅在此 service 导入处有一个 attr-defined
ignore，未修改 chat 或绕过架构检查。复杂日期 SQL 无可靠结构化时间范围时返回 None，
当前仅报告实际执行 SQL 中明确的单表、无歧义、左闭右开日期区间。

## I2：范围外整仓检查结果（共享工作区快照）

专项检查全绿（75 passed，含 I2 的 24 项测试和两个临时 PG 容器）；整仓扩展检查在本次
执行时为 512 passed、7 failed、8 skipped。失败均位于并行新增的
`tests/group_b/test_i1_audit.py::test_protected_routes_authenticate_before_unconfigured_services`：
/semantic、/metric、/promotion、/feedback、/querylog、/usage、/audit 返回 404，测试期望
401；这些路由仍需 main/I3 总装。I2 未改 main.py、worker.py 或其他模块来消除这些失败。
整仓 ruff check 通过、mypy 140 文件通过、import-linter 6 kept/0 broken；整仓 format check
当时另发现 core/datasource.py 末尾空行需整理，属于范围外。该结果不等于整仓全绿；后续
并行修改应由总装者再跑整仓检查。


### I3 最终对接与实际冒烟发现

前述临时 build_service 工厂约定已被实际 I2 API 取代：upload 使用 build_upload_registration；eval 使用 build_fixture_assets/build_eval_adapter/build_reference_runner。A 组稍后提供了 reserve_upload/upload_owner/publish_upload_credentials/finish_upload_publication、synchronize_upload、grant_upload_owner，现已完成真实 CSV 发布与默认 owner-team 授权冒烟。没有修改这些业务模块。评测完整运行必须显式配置真实团队/用户/数据源别名映射，不能套用离线 fixture 的整数身份与授权；RuntimeAssets 使用独立会话中的 live PermissionService 查询，不与 eval 自管事务混用。

本范围修复：
- 多个 router 仅有 /upload、/eval、/metric 等前缀，直接 mount 无法经 nginx /api 入口访问；main 统一按 router 原前缀补 /api，避免 /api/api。新增回归断言。
- 待发布上传 source.status=pending 经普通 GrantService.snapshot 会抛 DATASOURCE_UNAVAILABLE，使 confirm/run_import 无法进入。总装为 upload 注入核心 PermissionService + PendingUploadGrants，先问普通 grant service，只在该错误且公开 upload_owner 确认类型为 upload 时返回空 TableGrant 列表。没有授予待发布对象查询范围；发布后使用普通快照及原有失效流程。
- Docker Desktop bind mount 上的初始化脚本虽然为 0644，postgres entrypoint 仍尝试 exec，报 bad interpreter: Permission denied；重启后数据库可健康但 import_writer 不存在。deploy/start-import.sh 复制脚本到容器文件系统并 chmod 0644 后运行官方入口，pg-import 健康检查同时确认 TCP 就绪与写入 role 已创建。全新卷重复验证成功。
- scripts/login.py 的 Path.open(opener=...) 不受 pathlib 支持，已改为线程中使用内置 open 与 0600 权限。

剩余外范围接口已于 G1+G2 补缺完成：eval 公开 `create_case_from_feedback`（FeedbackCaseSink/EvalService，负责人限本队、审计、同事务回滚）；upload 与 feedback 各自公开 `build_retention_store` 保留清理器，可组合进 core 保留服务。整仓 ruff/format/mypy/import-linter/pytest（含外部服务 527 passed）全绿，证据与命令见 PROGRESS.md 的 G1+G2 补缺小节。I3 原有保留：整仓并行写入期间的 Ruff 格式错误由各所属组处理；本次已全部清零。

最终整仓 ruff check 已通过；ruff format --check 仍报告 app/core/datasource.py:389 的末尾空行，属于 core 所有者范围，I3 范围格式检查通过。Nginx 1.28-alpine 的容器配置验证因镜像拉取超过 60 秒中止，未声明 nginx 容器或完整应用镜像 build 已验证；Compose config 与 shell 语法、基础设施和实际 API/worker HTTP 链路已验证。
