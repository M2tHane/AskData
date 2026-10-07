# 实施计划与阶段进度

本次范围：backend/、eval/、deploy/；不创建 frontend/。遵循 AGENTS.md、PRD、TECH_STACK、ARCHITECTURE。

## 实施拓扑与独占边界

本任务为多边界实施。公共接口先由单一写入者建立；独立组件在隔离 Git worktree 实施，生成工具绑定的报告与补丁交接；主工作区只由集成阶段写入。所有子代理使用 magpie/codex/gpt-6.1-sol，high thinking（工具使用模型后缀 :high）。

| 阶段/组件 | 独占文件或接口 | 隔离与交接 | 下一门槛 |
| --- | --- | --- | --- |
| 公共层与脚手架 | backend 配置、core（SQL 守卫除外）、提示词、基础测试、检查脚本 | 主工作区单一写入者，完成后本地提交 | ruff、mypy、import-linter、pytest |
| SQL 安全 | core/sql/guard、executor 安全修正、安全测试 | 独立 worktree，补丁交接 | AST 越权、只读、规范化 SQL、资源限制 |
| 评测题库 | eval/testdb.sql、fixtures.yaml、100 题与样例文件 | 独立 worktree，补丁交接 | 题数与分类、自检 |
| 门槛 1 集成 | 以上补丁集成、安全评测适配 | 主工作区单一写入者 | 违规/越权/注入固定模拟输出全部拦截；四项检查通过 |
| 身份与配置模块 | auth/org/permission/modelcfg | 独立 worktree，补丁交接 | 身份、团队子集授权、密钥掩码、审计 |
| 数据源模块 | datasource/schema/upload | 独立 worktree，补丁交接 | 元数据默认拒绝、导入事务、读写账号隔离 |
| 语义与指标模块 | semantic/metric | 独立 worktree，补丁交接 | 三级私密性、提升审核、指标守卫试跑 |
| 运维与反馈模块 | feedback/querylog/eval/usage/audit | 独立 worktree，补丁交接 | 日志范围、摘要保留、反馈转换、评测任务 |
| 业务集成与迁移 | 模块装配、Alembic、共用接口适配 | 主工作区单一写入者 | 迁移、OpenAPI、四项检查 |
| 查询流水线 | chat 模块、十步流水线、SSE、导出 | 主工作区单一写入者 | 澄清一轮、执行纠错最多三次、拦截零执行、失败日志 |
| 评测运行器 | eval 运行器与结果比较 | 独立 worktree，补丁交接 | 结果比较、参考答案自检、统计与对比 |
| 部署 | deploy、Dockerfile、后台 worker 装配 | 独立 worktree，补丁交接 | Compose、服务健康、唯一 Nginx 入口、SSE |
| 最终集成与独立审查 | 全部补丁集成与已证实问题修复 | 主工作区单一写入者，审查只读 | 四项检查、安全门槛、可用外部服务验证 |

## 当前状态

- 已读项目要求与三份必读文档。
- 初始目录仅含文档，无代码与 Git 历史；为隔离并行实施建立本地 Git 仓库，不配置远端、不发布。
- uv、Python 与 Docker 可用，Docker 服务版本 29.6.1；外部模型使用固定模拟输出，服务测试优先 Docker。
- 后续阶段在此记录实际完成项、未完成项、检查证据与运行命令。未执行的检查不得标记为通过。
