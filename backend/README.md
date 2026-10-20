# AskData backend foundation

Python 3.12, FastAPI, async SQLAlchemy 2, Alembic, Redis/arq, MinIO, sqlglot and LiteLLM.
Resolved stable versions and transitive dependencies are pinned in `uv.lock`.

```bash
cd backend
uv sync --frozen
bash scripts/check.sh
# Includes disposable PostgreSQL/MySQL/Redis/MinIO containers; requires Docker.
bash scripts/check.sh --external-services
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# Inject ASKDATA_DATABASE_URL and required secrets before running migrations/worker.
uv run alembic upgrade head
uv run arq app.worker.WorkerSettings
```

Environment variables use the `ASKDATA_` prefix; see `.env.example`. Settings never supply
production JWT/encryption secrets. The encryption key is a Fernet key. `production` startup
rejects missing secrets. `/api/health` is a liveness check; `/openapi.json` exposes Pydantic schemas.

`scripts/check.sh` is the CI entrypoint: frozen install, Ruff lint/format, strict mypy,
import-linter and pytest. It exits on the first failure. Import contracts apply automatically
as business packages are added. Architecture tests also reject relative-import bypasses,
raw connectors, direct SDK/driver calls, non-AppError business exceptions and `print`.
Synthetic invalid business packages verify that the import-linter contracts actually fail.
A repository-root CI workflow is outside this stage's permitted write boundary; it should
invoke this script during deployment/CI integration.

The common query contract is:

```python
scope = await permission_service.visible_scope(current_user, datasource.id)
checked = sql_guard.check(model_sql, datasource.dialect, scope)
result = await sql_executor.run(datasource, checked, QueryLimits(user_id=current_user.id))
```

`VisibleScope` is an **internal** model. Its complete metadata is required for star expansion;
HTTP schema endpoints must only publish its granted `columns`, never `all_columns`.
The permission provider owns catalog/grant snapshots. New objects have no grants and members
receive the intersection of team grants, member visibility and live metadata. Admins receive
all live objects. Cache invalidation uses generations and retries snapshots concurrent with
revocation. Invalidate after committed metadata/authorization changes.

`GuardedSql` is sealed in-process and bound to a datasource and dialect. The executor rejects
raw strings and changed/forged capabilities before opening a connection. Workers must obtain
a fresh scope and run the guard in their own process; never persist a capability as an
execution ticket. The guard expands stars, resolves all scopes and lineage, rejects unknown
sources/functions and recursive CTEs, and injects row policies by wrapping each physical table.
Transformation of masked data gets a full mask. Ordinary aliases retain the original rule.

Execution runs `EXPLAIN` and bounded cursor reads on the same read-only transaction. Defaults
are 30 seconds, 10,000 rows and cost 100,000. MySQL estimates without a cost are refused.
The MySQL unbuffered connection is closed without draining unread rows. Cancellation closes
connections and releases concurrency leases. Inject `RedisQueryLimiter` in multiprocess
production; the default memory limiter is for development. Database login privileges must
also be restricted to SELECT; connection tests report excessive privileges.

External dependencies have protocols and constructor injection: `PermissionProvider`,
`ScopeCache`, `AuthenticationProvider`, `IdentityResolver`, `RefreshStore`, `ModelProvider`,
`ModelTransport`, `UsageRecorder`, `Connector`, `QueryLimiter`, `ObjectStore`, `TaskStore`,
`TaskQueue`, `AuditStore`, `QueryLogStore`, `RetentionStore`. Working fakes are in `tests/fakes.py`.
LLM transports never expose provider errors or log prompts/results/keys. Configure the usage
recorder to enforce quotas; retries and fallback attempts each produce a call record. Streams
are never replayed after text has been emitted.

Services share transactions with `DatabaseAuditStore` and `DatabaseQueryLogStore`; repositories
flush and do not commit. Use `@audited` on write services, with an explicit keyword `actor`.
Task status writes use their own short transactions so polling can observe progress. Register
new task handlers with `TaskManager`; workers initially register retention. Business retention
stores should be composed with core cleanup to remove upload history and linked feedback while
preserving promoted evaluation cases.

Error codes live in `core/errors.py`, prompt IDs/versions in `core/prompt.py` plus versioned
`app/prompts/` templates, and SSE event names in `core/stream.py`. Prompt data is JSON escaped
inside delimiters. SafeLogger accepts registered events and constrained summary fields;
SQL/results, secrets, tokens and arbitrary exceptions are dropped. Query log previews are
bounded to five masked rows; context details and audit diffs are redacted.

The deterministic gate fixtures are `tests/fixtures/gate1.json`: 10 prohibited queries,
8 unauthorized queries and 4 injection outputs. Each test sends a fixed fake-model response
through the guard and asserts the exact code, safe message and zero connection/EXPLAIN/execute
calls. This is an offline safety gate, not an accuracy claim about a real model.
