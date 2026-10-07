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
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build api
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --wait --wait-timeout 90
```

本阶段启动 API、arq worker、pg-meta/pgvector、独立 pg-import、Redis 和 MinIO 六个运行服务；无前端，`web`/nginx 服务按要求保持注释，不拉取或启动 nginx。默认不发布任何宿主端口。`migrate` 容器先运行 `alembic upgrade head`，API/worker 等待迁移成功，MinIO bucket 由 `minio-init` 创建；两个初始化容器成功后显示 `Exited (0)`，属于预期状态。API 从未注入导入写入 URL，写入凭证仅注入 worker。API 用 `/healthz` 检查，worker 用 arq 自带的 `--check` 检查；模型调用所需出站网络保留。Python/uv 基础镜像沿用原版本并锁定实际 manifest digest，生产镜像只复制应用、迁移、运行脚本和评测资产，不整体复制 backend 目录。

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T api \
  python -c "import urllib.request; import sys; sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode()+'\\n')"
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T worker \
  arq --check app.worker.WorkerSettings
docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm --no-deps migrate \
  alembic current
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps --all
```

`/healthz` 与 `/api/health` 返回统一信封 `{code:"OK",message:"Success",data:{status:"ok"}}`，表示 API 进程就绪；基础设施连通性由 Compose 各自的健康检查和实际冒烟验证。OpenAPI 位于 API 的 `/openapi.json`，直接本地启动时文档位于 `/docs`。注释中的未来 nginx 入口只代理 `/api/` 和 `/healthz`，SSE 禁用缓冲，允许 70 MiB 的 base64 上传请求；它尚未作为常驻服务启动。

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

上传总装使用 I2 的 `build_upload_registration` 工厂和其他组提供的 datasource/schema/permission 公共上传方法。CSV 预览→确认→arq 导入→MinIO 备份→元数据发布→所属团队默认授权→员工提问已通过真实冒烟；不会伪造管理员或绕过模块边界写表。待发布数据源在 upload 的 PermissionService 中返回空查询范围，只允许合法上传者继续导入生命周期，激活后回到普通授权快照。Docker Desktop 挂载脚本的执行行为通过 `start-import.sh` 复制到容器文件系统再 source 解决；pg-import 健康检查同时验证 TCP 就绪和 import_writer 已创建。反馈转评测与上传历史清理的公开接口已由并行业务组追加，当前 Composition 中的反馈 sink 和业务保留清理器仍需总装者接线；G3 不修改 composition/main/worker。已绑定的核心保留任务清理审计/查询日志/任务记录，关联 FK 依迁移处理；业务接口及后续验收以对应组记录为准，详见 OPEN_QUESTIONS.md。

## 容器内完整冒烟（G3 已验证）

需要全新的专用项目与元数据库；测试会创建首个管理员，不应在已有业务部署执行。环境文件只提供运行时密钥，不复制进镜像。固定模型由额外的 `smoke-model` 测试容器提供，`smoke` 一次性容器运行 `deploy/smoke_container.py`，原样调用现有 `backend/scripts/smoke.py` 的 `exercise()` HTTP 断言。适配器只把回环 API/模型地址和数据源连接地址换成 Compose 服务名；API、worker、模型各在独立容器中。没有 Docker socket、宿主网络或模拟业务 service 注入。

```bash
cd backend
uv run --frozen python scripts/generate_env.py /tmp/askdata-container-smoke.env
cd ..
docker compose --env-file /tmp/askdata-container-smoke.env -p askdata-container-smoke \
  -f deploy/docker-compose.yml build api
docker compose --env-file /tmp/askdata-container-smoke.env -p askdata-container-smoke \
  -f deploy/docker-compose.yml up -d --wait --wait-timeout 90
docker compose --env-file /tmp/askdata-container-smoke.env -p askdata-container-smoke \
  -f deploy/docker-compose.yml -f deploy/docker-compose.smoke.yml --profile smoke \
  up -d --wait --wait-timeout 45 smoke-model
docker compose --env-file /tmp/askdata-container-smoke.env -p askdata-container-smoke \
  -f deploy/docker-compose.yml -f deploy/docker-compose.smoke.yml --profile smoke \
  run --rm --no-deps smoke
```

成功输出 `smoke_passed` 和 `container_smoke_passed`，检查登录→创建只读源→真实 arq 元数据同步→新元数据默认未授权→团队/员工授权→真实 LiteLLM 固定模型提问结果→危险 SQL 拦截→SSE done→撤权拒绝，同时检查 100 题加载及 CSV 预览/后台 COPY/MinIO 备份/发布/所属团队默认授权/员工查数。上传 writer 仍只给 worker；仅冒烟容器额外得到导入 admin 密码，用于建立、删除自身的静态测试 fixture。API 和 worker 不获得该 admin 密码。fixture SQL 不接受用户或模型输出。

结束后删除本次专用项目，包括模型测试容器和上传备份。每次重跑使用新的空卷；脚本不会重置已有管理员密码或清空业务数据库。

```bash
docker compose --env-file /tmp/askdata-container-smoke.env -p askdata-container-smoke \
  -f deploy/docker-compose.yml -f deploy/docker-compose.smoke.yml --profile smoke down --volumes
rm /tmp/askdata-container-smoke.env
```

## Nginx 可用时的单独验证

`web` 仍是 Compose 注释示例。镜像可用后可独立运行语法检查，不启用常驻 web，也不需要前端资产。网络名由项目名决定；下例对应上面的专用项目，需在清理网络前执行。

```bash
docker pull nginx:1.28-alpine
docker run --rm --network askdata-container-smoke_internal \
  --mount "type=bind,source=$PWD/deploy/nginx.conf,target=/etc/nginx/conf.d/default.conf,readonly" \
  nginx:1.28-alpine nginx -t
```

若只是离线检查语法，没有运行 API 网络，可用 `--add-host api:127.0.0.1` 替代 `--network`。真实代理连通性需另启动一个临时 nginx 容器加入实际 API 网络；语法检查成功不等于外部入口已部署。镜像拉取失败可重试，或从可信镜像源获取并确认与官方镜像相同的 manifest digest；不把超时写成验证成功。G3 本轮官方源重试成功，`nginx -t` 已实际通过，但 `web` 未常驻启动。

## 宿主机冒烟与检查

以下保留 I3 的宿主机运行方式，和上述容器内完整验收分别使用独立新项目。

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


## G3 最终验收记录

本轮只修改 deploy/、backend/Dockerfile 和本手册；未改 composition.py/main.py/worker.py，未使用 subagent，未创建前端。实际验证使用独立 askdata-g3 项目及全新卷，未复用已有业务数据库。

- 完整官方镜像拉取及构建成功，最终 askdata-backend:0.1.0 manifest 为 sha256:744ceeb44a8e2fad9ad4aaf5b929f59fa2756a0e6eb394de95d88e3c29b83c71。Python 3.12.13/uv 0.12.5 版本不变，新增其官方 manifest digest 固定；uv frozen 安装及 xlrd 2.0.2 安装实际通过。镜像以 askdata/UID 10001 运行，确认未安装 pytest、未复制 backend/tests。
- api、worker、pg-meta、pg-import、redis、minio 六个运行容器全部启动且健康；migrate/minio-init 退出码为 0，API /healthz 返回 OK/Success/status=ok，arq --check 成功。初轮迁移为 0005；并行业务组新增迁移后再次构建并用新卷执行，最终 alembic current 为 0006_g1 (head)。G3 未修改迁移文件。
- 容器内复用 scripts/smoke.exercise 原断言，两轮均输出 smoke_passed/container_smoke_passed。API、worker、模型各自独立运行，验证元数据同步/授权/提问、危险 SQL 拦截、SSE、撤权拒绝、评测题库加载和 CSV 上传到员工查数。未用本地服务替代容器运行；部署适配只改测试地址。
- Nginx 官方镜像重试拉取成功，digest 为 sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236；加入实际 API 网络的 nginx -t 成功。web 保持注释，未启用常驻 nginx/前端。
- deploy/ Ruff lint/format、部署 runner/测试 strict mypy、六条 import-linter 契约、Compose config --quiet 与导入脚本 sh -n 全部通过。部署地址适配回归 6 passed；与 foundation/architecture/SQL 安全门槛合跑为 58 passed，只有既有 Starlette 第三方弃用 warning。未把这些结果写成其他并行业务范围的完整质量或真实模型准确率验收。
- 验收后删除 askdata-g3 的运行/初始化/测试容器、专用网络与四个卷，删除私有环境文件；保留可复用的已构建镜像。其他已有容器不受影响。

静态和回归复现（backend/）：
```bash
uv run --frozen ruff check ../deploy
uv run --frozen ruff format --check ../deploy
MYPYPATH=.. uv run --frozen mypy --config-file pyproject.toml --explicit-package-bases \
  ../deploy/smoke_container.py ../deploy/test_container_smoke.py
uv run --frozen lint-imports --config importlinter.ini
PYTHONPATH=.. uv run --frozen pytest -c pyproject.toml -q \
  ../deploy/test_container_smoke.py tests/test_foundation.py tests/test_architecture.py tests/test_gate1.py
```

如果并行组更新代码或锁文件，应重新构建、使用新专用元数据库再运行容器冒烟；上述 manifest 和迁移 head 对应本轮最终构建快照。
