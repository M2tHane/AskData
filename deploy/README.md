# 部署

默认不发布宿主端口；没有 Nginx 或其他反向代理服务。

```bash
cd backend
uv run --frozen python scripts/generate_env.py ../deploy/.env
cd ..
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build api
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --wait --wait-timeout 90
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T api \
  python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200"
```

六个运行服务是 api、worker、pg-meta/pgvector、pg-import、Redis、MinIO。一次性 `migrate` 自动执行 Alembic，`minio-init` 创建 bucket；成功后显示 `Exited (0)`。API/worker 等待初始化完成，并分别运行 `/healthz`/arq 健康检查。`docker-compose.local.yml` 仅供开发环境发布基础设施和 API 的回环端口。

生产镜像使用固定 manifest digest 的 Python/uv、冻结的 uv.lock 和固定的 xlrd 补充依赖，不复制整个 backend 目录。密钥只从运行环境注入。导入 writer URL 只给 worker；每个上传源有独立只读 role。`start-import.sh` 在容器文件系统中准备首启脚本，避免 Docker Desktop 挂载执行限制；pg-import 健康检查确认 TCP 和 import_writer 已就绪。初始化只适用于空卷，已有卷的账号轮换不靠首启脚本重跑。

`docker-compose.smoke.yml` 只在 smoke profile 下提供固定响应模型和一次性 runner，复用 `scripts/smoke.py` 的 HTTP 断言，仅替换部署地址；必须使用全新专用元数据库。部署验收不代替业务功能和真实模型准确率验收。
