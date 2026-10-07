# AskData（企业 ChatBI 查数平台）

业务人员用自然语言查数、出图。核心保证：越权不可能、SQL 只读、口径可控、质量可评测。

## 必读文档（实施前先读，冲突以文档为准）
- `docs/PRD.md` — 需求、角色权限矩阵、功能清单、不做的事
- `docs/TECH_STACK.md` — 技术选型（不得擅自替换）
- `docs/ARCHITECTURE.md` — 公共基础层 `core/`、查询流水线、SQL 安全、权限、表设计、导入、评测、目录结构、开发规范、里程碑
- 原始合并稿：`docs/source/`

## 当前范围
- **只做后端、评测、部署**（`backend/`、`eval/`、`deploy/`）。**暂不写前端**（`frontend/` 不创建），但接口须用 Pydantic 定义并能生成 OpenAPI。
- 仓库结构按 `docs/ARCHITECTURE.md` 3.14。
- 版本号在启动时锁定到当时稳定版并写入锁文件（建议 uv + `uv.lock`）。

## 硬性规则（违反即视为 bug）
1. 分层 api → service → repository；`core/` 不依赖任何业务模块；模块间不直接读对方的表，只调对方 service。
2. 校验/执行 SQL 只能经 `core/sql/guard` 的 `SqlGuard` 再到 `core/sql/executor` 的 `SqlExecutor`，包括会话、指标试跑、评测、手改 SQL。
3. 可见范围只能问 `core/permission` 的 `PermissionService`；新同步的表/字段默认不授权；员工可见 ⊆ 团队授权。
4. 模型调用只能经 `core/llm`（LiteLLM）；业务代码禁止 import 厂商 SDK、数据库驱动、LiteLLM、MinIO 客户端。
5. 所有写接口必须产生审计记录（`core/audit`）。
6. 错误码、提示词、SSE 事件类型先在 `core` 登记再使用；业务代码只抛 `AppError`；响应统一信封 `{code, message, data}`。
7. 密钥只走环境变量，用 `SecretBox` 加密落库，接口只返回掩码；日志不得含密钥、令牌、完整结果数据；禁止 `print`。
8. 被拦截回复只说明原因类别，不泄漏不可见对象名称；模型生成文本永不直接执行，执行守卫输出的规范化 SQL。
9. 数据库迁移只用 Alembic。
10. 以上规则用 import-linter（`backend/importlinter.ini`）与架构测试在 CI 强制。

## 工作方式
- 按 `docs/ARCHITECTURE.md` 3.16 里程碑顺序推进；**门槛 1**：用固定模拟模型输出跑守卫，“违规/越权/注入”三类评测题必须 100% 拦住，才能做查询主流程。
- 先 `core/` 后业务模块；先定接口（Pydantic）再写实现。
- 每个功能配 pytest；提交前跑 ruff、类型检查（mypy/pyright）、import-linter、pytest。
- 不确定的需求不要臆测：在 `docs/OPEN_QUESTIONS.md` 记录并采用文档中最保守的方案。
- 完成一阶段后在 `docs/PROGRESS.md` 记录已完成项、未完成项、运行/测试命令。
