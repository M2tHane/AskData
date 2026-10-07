# 企业 ChatBI 查数平台：技术栈

**后端 Python + FastAPI，前端 React + TypeScript，元数据与向量都放 PostgreSQL，Excel/CSV 导入一个独立的 PostgreSQL 库，这样所有数据源最终都是 SQL 库，SQL 生成、校验、权限走同一套逻辑。** 版本号在项目启动时锁定到当时的稳定版，并写入锁文件。

| 层                  | 选型                                                         | 选择理由                                                | 备选             |
| ------------------ | ---------------------------------------------------------- | --------------------------------------------------- | -------------- |
| 后端框架               | FastAPI、SQLAlchemy 2（async）、Alembic、Pydantic v2            | 异步，SSE 流式输出方便，LLM 与数据生态都在 Python                    | Django         |
| 元数据库               | PostgreSQL + pgvector                                      | 一套库存业务数据和向量，权限过滤可直接写成 SQL 条件                        | 独立向量库（规模大后再考虑） |
| 缓存与任务              | Redis + arq                                                | 文件解析、元数据同步、批量评测都走后台任务；Redis 同时做限流和会话临时状态            | Celery         |
| 对象存储               | MinIO                                                      | 保存 Excel/CSV 源文件备份                                  | 无              |
| 表格解析               | pandas、openpyxl（只读值，不执行公式）、charset-normalizer              | 够用；文件很大时换 polars 或 calamine                         | polars         |
| SQL 解析校验           | sqlglot                                                    | AST 层面限定单条 SELECT，提取表和字段做权限比对，支持 MySQL 与 PG 方言      | pglast（仅 PG）   |
| 数据源驱动              | asyncpg、aiomysql（或 asyncmy）                                | 异步查询，便于超时与取消                                        | psycopg3       |
| LLM 接入             | LiteLLM                                                    | 统一对接 OpenAI 兼容接口、Claude、通义、DeepSeek、本地模型；自定义模型靠配置完成 | 自写适配层          |
| Embedding 与 Rerank | 外部平台的 bge-m3 与 bge-reranker，通过统一的模型客户端调用                   | 中文表字段检索效果好；Rerank 可选                                | 其他厂商模型         |
| 流水线编排              | 自写薄流水线（普通函数加状态对象）                                          | 流程固定，没必要引入重型框架；便于单测和打点                              | LangGraph      |
| 认证授权               | JWT（短期 access + refresh）、自写 RBAC                           | 角色只有三种，权限按逐级收窄建模，自写更直观；认证接口预留 SSO                   | Casbin         |
| 前端                 | React 18、Vite、TypeScript、Ant Design、TanStack Query、Zustand | 管理后台类界面成熟                                           | Vue 3          |
| 图表                 | ECharts                                                    | LLM 只输出图表配置，前端校验后渲染                                 | 无              |
| SQL 展示与编辑          | CodeMirror 6                                               | 轻量，支持 SQL 高亮和只读模式                                   | Monaco         |
| 可观测                | OpenTelemetry、结构化日志；可选 Langfuse                            | 追踪每次 LLM 调用的提示词、延迟、token                            | 无              |
| 测试                 | pytest、pytest-asyncio、Playwright（关键路径）                     | 评测集另有独立运行器                                          | 无              |
| 部署                 | Docker Compose                                             | 后端、worker、前端、PG（元数据库）、PG（导入库）、Redis、MinIO           | K8s（规模上来后）     |

### 关键选型说明

1. **为什么导入库用 PostgreSQL，而不是文档数据库：** 查询语言是 SQL，LLM 的 SQL 能力才用得上；评测库本来就是 PG，方言一致；每个上传数据源一个独立 schema，查询账号只授予该 schema 的 SELECT，权限隔离简单。DuckDB 更轻，但并发与隔离不如 PG，第一版不用。
2. **为什么不用重型 Agent 框架：** 查数流程是固定的（检索、生成、校验、执行、纠错、出图），写成显式的步骤函数更好测、更好限制，也方便每一步记录日志。需要多步探索的归因类 agent 以后再评估。
3. **为什么 LiteLLM：** 只用它做模型调用的统一入口，业务代码不直接依赖任何厂商 SDK，换模型只改配置。
4. **为什么 pgvector 而不是独立向量库：** 数据量是表、字段、指标、示例 SQL 级别，万级到十万级向量，PG 足够；好处是权限过滤和向量检索在同一条 SQL 里完成。

