# AskData 后端运行手册

仅包含 API、后台任务、数据库与对象存储，不创建或提供前端。执行目录为仓库根目录，标为 `backend/` 的命令需先进入该目录。Python 3.12、uv、Docker Compose 是前置依赖；Python 依赖使用 `backend/uv.lock`。

## Compose 启动

生成真实密钥到权限为 0600 的文件；该命令拒绝覆盖已有文件。备份此文件，尤其是加密主密钥，后续启动复用原值。

```bash
cd backend
uv sync --frozen
uv run python scripts/generate_env.py ../deploy/.env
cd ..
docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

服务包括 nginx `web`、API、arq worker、pg-meta/pgvector、独立 pg-import、Redis 和 MinIO。`migrate` 服务先运行 `alembic upgrade head`；API/worker 等待迁移成功，MinIO bucket 由 `minio-init` 创建。API 从未注入导入写入 URL，写入凭证仅注入 worker。生产默认只有 nginx 的 `127.0.0.1:8080` 入口；可通过环境文件的 `HTTP_BIND`、`HTTP_PORT` 调整绑定，公网 HTTPS 在外部 TLS 入口配置。nginx 禁用 SSE 缓冲，允许 70 MiB 的 base64 上传请求。

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/api/health
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

`/healthz` 与 `/api/health` 返回统一信封 `{code:"OK",message:"OK",data:{status:"ok"}}`，表示 API 进程就绪；基础设施连通性由 Compose 各自的健康检查和实际冒烟验证。OpenAPI 位于 API 的 `/openapi.json`，直接本地启动时文档位于 `/docs`；nginx 只代理 `/api/` 和 `/healthz`。

## 初始管理员

密码必须至少 12 字符，只从环境读取，不放进命令参数。下面的 `read` 隐藏输入，密码不会写入 shell 历史。只允许创建首个管理员；重复执行返回 `CONFLICT`，不会重置密码或新增管理员。命令输出安全审计事件与 `user_id`，不输出密码或令牌。

```bash
read -rs -p 'Initial admin password: ' ASKDATA_BOOTSTRAP_PASSWORD
export ASKDATA_BOOTSTRAP_PASSWORD
export ASKDATA_BOOTSTRAP_USERNAME=admin
docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm --no-deps \
  -e ASKDATA_BOOTSTRAP_PASSWORD -e ASKDATA_BOOTSTRAP_USERNAME \
  api python scripts/create_admin.py
unset ASKDATA_BOOTSTRAP_PASSWORD
```

登录接口为 `POST /api/auth/login`，Pydantic 请求体含 `username`、`password`，返回 access/refresh token。access token 使用 `Authorization: Bearer …`。`POST /api/auth/refresh` 轮换刷新令牌；`POST /api/auth/logout` 撤销令牌。本地 `scripts/login.py --output /private/tokens.json` 将登录令牌加密写入一个新建的 0600 文件，凭证使用 `ASKDATA_LOGIN_USERNAME`、`ASKDATA_LOGIN_PASSWORD`，加密使用原部署的 `ASKDATA_ENCRYPTION_KEY`；禁止把令牌打印到日志。

## 本地运行

显式加上开发 override 才会将基础设施端口发布到回环地址。默认端口：pg-meta 54321、pg-import 54322、Redis 56379、MinIO 59000；通过环境文件的 META_PORT/IMPORT_PORT/REDIS_PORT/MINIO_PORT 可调整。不可将此 override 用于公网主机。

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml \
  -f deploy/docker-compose.local.yml up -d --wait pg-meta pg-import redis minio
```

本地 API/worker 需要注入以下 `ASKDATA_*` 环境变量，密码与密钥使用同一部署环境文件的值，连接地址改为上述回环地址：

| 环境变量 | 内容 |
| --- | --- |
| DATABASE_URL | `postgresql+asyncpg://askdata:<密码>@127.0.0.1:54321/askdata` |
| REDIS_URL | `redis://:<密码>@127.0.0.1:56379/0` |
| JWT_SECRET、ENCRYPTION_KEY | 原部署密钥 |
| MINIO_ENDPOINT、MINIO_ACCESS_KEY、MINIO_SECRET_KEY | `127.0.0.1:59000` 与原部署对象存储凭证 |
| MINIO_SECURE | 本地为 `false` |
| IMPORT_READER_HOST、IMPORT_READER_PORT、IMPORT_READER_DATABASE | `127.0.0.1`、`54322`、`askdata_import` |
| IMPORT_READER_PASSWORD | 导入查询账号密码 |
| IMPORT_WRITER_URL | 只给 worker：`postgresql://import_writer:<密码>@127.0.0.1:54322/askdata_import` |

表中名称均加 `ASKDATA_` 前缀。设置 `ASKDATA_ENVIRONMENT=production` 后，入口强制验证 JWT 与加密密钥。密钥不得提交代码库，数据库迁移只使用 Alembic。

```bash
cd backend
bash scripts/migrate.sh
uv run python ../deploy/bootstrap_minio.py
bash scripts/start_api.sh
# 在另一个终端、注入 worker 环境后：
bash scripts/start_worker.sh
```

API 默认 `127.0.0.1:8000`，可通过 `ASKDATA_API_HOST`、`ASKDATA_API_PORT` 调整。等价生产命令为 `uvicorn app.main:app --host 0.0.0.0 --port 8000` 和 `arq app.worker.WorkerSettings`。

## 后台任务与定时运行

API 和 worker 共用 `Composition` 的任务注册表。`dispatch_task` 根据持久化 task_run 分发，不直接执行队列提交的任意函数或 SQL。

| 任务类型 | handler | 触发方式 |
| --- | --- | --- |
| schema_sync | schema.sync_handler | `POST /api/schema/{id}/sync`、每日 02:00 UTC |
| upload.import | UploadService.run_import | 上传确认 |
| eval.run | EvalService.run | `POST /api/eval/runs` |
| embedding.update | EmbeddingService.update_task | 索引 API、每小时 05/20/35/50 分 UTC |
| embedding.rebuild | EmbeddingService.rebuild_task | 索引 API；配置 Embedding 模型后的元数据同步 |
| retention | RetentionService.run | 每日 03:00 UTC |

通过环境文件 `SCHEDULER_ADMIN_ID`（本地 `ASKDATA_SCHEDULER_ADMIN_ID`）配置真实管理员 ID。未配置时跳过定时同步/索引；已配置但用户停用、删除或不再是管理员时任务拒绝。评测任务启动时也重新解析运行发起者身份。当前没有 Embedding 模型时，同步仍可完成并支持小于等于 30 表的全量检索；混合检索需要配置 Embedding 模型，Rerank 可选。

每个 API 请求与 worker job 都有独立会话和服务；auth/org/datasource/schema/permission/modelcfg 等共享本次请求事务。chat 会话延续到 SSE 结束；chat/upload/eval 管理自己的提交，不套会回滚失败记录的外层事务。任务状态查询为 `/api/schema/tasks/{task_id}`（只读该类任务）、上传 job 状态、评测 run 状态；对应写接口均经各模块 service 产生审计。

## 评测适配与现有边界

总装使用 I2 的 `build_fixture_assets`、`build_eval_adapter`、`build_reference_runner`。运行时通过 org/datasource 的公开 service 重新解析真实身份与只读连接，权限只取核心 PermissionService；参考答案和实际流水线均经守卫、执行器。计数来自真实执行器包装器，运行固定模型 ID，每题使用独立数据库会话和用量记录器。

评测案例维护/加载可先运行。运行实际评测前，在专用评测环境经组织、数据源、授权接口建立测试资产，将 fixtures.yaml 的全部别名映射为真实 ID：环境文件配置 `EVAL_TEAM_IDS`、`EVAL_USER_IDS`、`EVAL_DATASOURCE_IDS` 为 JSON 字典，例如 `EVAL_TEAM_IDS={"sales":12,"finance":13}`。具体别名以 `eval/fixtures.yaml` 为准，不直接把离线整数 ID 当生产 ID。可选 `EVAL_SEMANTIC_ALIASES`、`EVAL_METRIC_ALIASES` 映射检索键到题库别名。直接本地启动时变量加 `ASKDATA_` 前缀。未配置完整映射时，实际评测题失败关闭，不使用离线权限替代真实授权。

上传总装使用 I2 的 `build_upload_registration` 工厂和其他组提供的 datasource/schema/permission 公共上传方法。CSV 预览→确认→arq 导入→MinIO 备份→元数据发布→所属团队默认授权→员工提问已通过真实冒烟；不会伪造管理员或绕过模块边界写表。待发布数据源在 upload 的 PermissionService 中返回空查询范围，只允许合法上传者继续导入生命周期，激活后回到普通授权快照。Docker Desktop 挂载脚本的执行行为通过 `start-import.sh` 复制到容器文件系统再 source 解决；pg-import 健康检查同时验证 TCP 就绪和 import_writer 已创建。反馈转评测仍缺对应 eval 公共 service 方法；反馈转示例 SQL 可使用已有 service。保留任务已清理核心审计/查询日志/任务记录，关联 FK 依迁移处理；过期上传对象与历史尚缺业务清理接口。以上待接点详见 OPEN_QUESTIONS.md。

## 真实冒烟与检查

冒烟需要一个全新的、专用 Compose 元数据库，因为它会创建首个管理员、团队、员工、数据源、授权、模型配置与查询日志。脚本只清理自己直接创建的导入库测试 schema/只读 role；上传生成的 ds_<id>、MinIO 文件和元数据库证据保留到删除该专用项目。请勿指向已有业务环境。凭证和端口来自 `--env-file`，模型使用脚本启动的本地 OpenAI 兼容固定响应服务，经真实 core.llm/LiteLLM 调用，不需要外部模型 Key。

```bash
cd backend
uv run python scripts/generate_env.py /tmp/askdata-smoke.env
cd ..
docker compose --env-file /tmp/askdata-smoke.env -p askdata-smoke \
  -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml \
  up -d --wait pg-meta pg-import redis minio
cd backend
uv run --frozen python scripts/smoke.py --env-file /tmp/askdata-smoke.env --project askdata-smoke
cd ..
# 仅删除本次专用冒烟项目及其数据：
docker compose --env-file /tmp/askdata-smoke.env -p askdata-smoke \
  -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml down --volumes
rm /tmp/askdata-smoke.env
```

脚本运行 `alembic upgrade head`、初始管理员命令、MinIO bucket 初始化，然后启动真实 API/worker，检查 HTTP 登录→建数据源→arq 同步→默认未授权→授权员工→固定模型提问及结果→危险 SQL 拦截→SSE→撤销授权后拒绝，并检查评测题库加载和 CSV 上传/默认授权/员工查数，成功输出 `status=smoke_passed`。子进程在 finally 中停止；不输出凭证、令牌或完整结果。首次启动失败可检查服务健康及私有环境配置；重新运行使用新建专用项目。

```bash
cd backend
bash scripts/check.sh
# XLS 解析补充依赖与真实外部适配器：
uv run --frozen --with-requirements app/modules/upload/requirements.txt \
  pytest -q tests scripts/test_composition.py --external-services
```

I3 专项为 `pytest -q scripts/test_composition.py tests/test_worker.py tests/test_foundation.py tests/test_architecture.py`；类型检查也包含 scripts/。第三方 Starlette 的 httpx 弃用提示不影响结果。
