# Backend deployment

完整启动、初始管理员、评测映射和冒烟命令见 [RUNBOOK](../docs/RUNBOOK.md)。无前端，`web`/nginx 服务保持 Compose 注释状态；默认不发布宿主端口。

```bash
cd backend
uv run --frozen python scripts/generate_env.py ../deploy/.env
cd ..
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build api
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --wait --wait-timeout 90
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T api \
  python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200"
```

六个运行服务是 api、worker、pg-meta/pgvector、pg-import、Redis、MinIO。一次性 `migrate` 自动执行 Alembic，`minio-init` 创建 bucket；成功后显示 `Exited (0)`。API/worker 等待初始化完成，并分别运行 `/healthz`/arq 健康检查。`docker-compose.local.yml` 仅供开发环境发布基础设施回环端口。

生产镜像使用原版本 Python/uv 的固定 manifest digest、冻结 uv.lock 以及固定 xlrd 补充依赖，不复制整个 backend 目录。密钥只从运行环境注入。导入 writer URL 只给 worker；每个上传源有独立只读 role。`start-import.sh` 在容器文件系统中准备首启脚本，避免 Docker Desktop 挂载执行限制；pg-import 健康检查确认 TCP 和 import_writer 已就绪。初始化仅适用于空卷，已有卷的账号轮换不靠首启脚本重跑。

G3 已实际完成镜像构建、六服务启动、Alembic 最新 head（本轮最终镜像为 `0006_g1`）、容器内健康检查和容器内主流程/CSV 上传/SSE/拦截/撤权冒烟。`docker-compose.smoke.yml` 只在 smoke profile 下提供固定响应模型和一次性 runner，复用 scripts/smoke.py 的原有 HTTP 断言，仅替换部署地址；不挂载 Docker socket，也不需要宿主网络。必须使用全新专用元数据库，复现与清理命令见 RUNBOOK。

Nginx 官方镜像重试拉取成功，独立 `nginx -t` 已通过；仍未作为常驻 Compose 服务启用。可用时执行：

```bash
docker pull nginx:1.28-alpine
docker run --rm --network askdata_internal \
  --mount "type=bind,source=$PWD/deploy/nginx.conf,target=/etc/nginx/conf.d/default.conf,readonly" \
  nginx:1.28-alpine nginx -t
```

网络名应按实际项目名调整；无 API 网络时，用 `--add-host api:127.0.0.1` 替代网络参数，只检查语法。部署验收不代替业务功能/真实模型准确率验收。
