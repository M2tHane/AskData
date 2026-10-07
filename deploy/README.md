# Backend deployment

完整运行、初始管理员、评测映射、定时任务与冒烟命令见 [RUNBOOK](../docs/RUNBOOK.md)。本阶段只提供后端，不创建前端。

```bash
cd backend
uv run --frozen python scripts/generate_env.py ../deploy/.env
cd ..
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
curl --fail http://127.0.0.1:8080/healthz
```

Compose 包含 nginx 唯一入口、api/worker、一次性 Alembic migrate、pg-meta/pgvector、pg-import、Redis、MinIO 和 bucket 初始化。API/worker 等待迁移及基础设施就绪。只有 nginx 默认发布到回环地址 8080；SSE 禁用缓冲。`docker-compose.local.yml` 是显式开发覆盖文件，将基础设施端口发布到回环地址，不用于公网部署。

Python 依赖使用冻结 uv.lock；基础设施沿用固定镜像 digest。部署密钥只从环境注入；填好的 `.env` 不进入镜像或代码库。导入 writer URL 只注入 worker，API 使用 ImportReadGateway；每个上传源有独立只读 role。`start-import.sh` 将宿主挂载的初始化脚本复制到容器文件系统，由官方 PostgreSQL 入口 source，避免 Docker Desktop 的挂载执行限制；健康检查同时确认 TCP 和 import_writer 已就绪。初始化仅适用于空卷，已有卷的账号轮换不靠重新运行首启脚本。

I3 已完成应用组装与 I2 适配器对接。独立 Compose 项目上实际启动四个基础设施、迁移到 head、启动 API/arq 后，HTTP 主流程、危险 SQL 拦截、SSE、撤权拒绝和 CSV 导入/默认授权/员工查数均通过。完整外部服务 pytest 为 479 passed、零跳过。完整后端镜像 build 和 nginx 容器运行未验证；本次 nginx 镜像拉取超过 60 秒而中止。反馈转评测、过期上传历史/对象清理的公开业务接口仍待补齐，详见 OPEN_QUESTIONS。
