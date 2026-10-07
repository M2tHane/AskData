# Chat module integration

`api.py` exports `router` with prefix `/api/chat`; `main.mount_modules` discovers it.
`service.get_service` creates a separate request-scoped metadata session, kept open
through the SSE body. Conversation/message writes commit with their audits; the
query run commits before saving the assistant response, so response persistence
failure does not roll back the run. Do not wrap this service in a transaction that
rolls back failed query replies.

Inject these public objects through application state (or inject a ready service
through `request.state.chat_service` / FastAPI dependency overrides):

- `retrieval_index`: implements `pipeline.RetrievalIndex.retrieve` and
  `expand_relations`. It must filter every semantic/metric/example/enum item by
  the supplied user and visible scope, perform keyword + vector search for
  `mode="hybrid"`, optionally rerank, and expand only authorized FK endpoints.
  Chat independently filters structured table/column references and applies
  `ScopedQuery` visibility and priority. `chat.retrieval.ServiceRetrievalIndex`
  is the concrete adapter: it calls schema/semantic/metric/feedback/embedding
  services, carries IDs and versions, and optionally calls core RerankClient.
- `usage_recorder`: the core `UsageRecorder` protocol, including quota reservation.
  Missing recorder fails closed inside the pipeline and produces a failed run.
- Optional `llm_client` (core `LLMClient`) or `model_transport` (core transport),
  and `sql_executor` (core `SqlExecutor`) for configured deployments/tests.

The standalone chat factory builds `ServiceRetrievalIndex` for full authorized
schema, semantics, metrics and reviewed examples. Hybrid retrieval needs a
transaction-scoped embedding service (or an injected complete retrieval_index).
`ScopeOnlyIndex` remains a minimal test/bootstrap adapter and refuses hybrid
retrieval. The composition root must bind the concrete adapter too. Runtime uses
A/B-group **service** factories, never other modules' repositories or tables.

`Stage.run(ctx) -> ctx` has ten implementations. QueryLogWriter stores a maximum
of five masked preview rows, the stage timings, retry categories, prompt versions,
model/token counters and a snapshot without credentials or full results. The
shared query_run payload exposes conversation/message IDs and the snapshot as
optional fields; no chat query_run model or table is created. 0004 adds only
conversation/message and depends on revision 0003. Its message-to-query foreign
key includes query_run.created_at to match the monthly partition key, with both
reference fields cleared on log expiry. QueryLogService views preserve the
canonical semantic/metrics/examples version summaries written by the pipeline.

Clarification choices are persisted in the assistant message and atomically
consumed. A continuation must identify that message and select an offered choice;
it retains the original question and never clarifies again. Requests cannot
supply an arbitrary "clarification used" flag. Scope conflicts are resolved by the
chosen offered definition; otherwise priority is personal > team > platform.

Relative dates use the supplied IANA timezone and inclusive start/exclusive end.
Near-N-days includes today; last month is a complete calendar month; this quarter
is a complete calendar quarter; standalone last-year-same-period uses year-to-date
with leap-day clamping (documented in OPEN_QUESTIONS). History uses a bounded
extractive summary plus a freshly guarded previous SQL for follow-up filters.

Only core SQL_EXECUTION_ERROR (syntax/undefined-column driver categories) can
enter correction, up to three corrections after the first execution. Each
correction rechecks current permission and passes SqlGuard. Guard failures,
connection failures, timeouts, cost/limit denials and empty results do not retry.
Manual SQL does not invoke generation/correction. ChartSpec is a strict Pydantic
JSON schema with allowed types and result-column references; invalid chart JSON
falls back to table. Explanations append effective definitions/levels/versions,
checked filters and backend time ranges.

SSE uses a bounded queue and core registered events, monotonically increasing
IDs, one terminal done event, and a durable cancellation record on disconnect.
Only validated SQL/configuration and masked data are streamed. Exports recheck
current authorization and execute again through guard/executor; CSV and XLSX
prefix formula-like strings in both headers and cells, retain masking, and encode
timezone-aware datetimes as ISO strings. Exports obey the configured row limit.

Validation from backend/:

```sh
uv run ruff check app/modules/chat tests/test_chat.py tests/test_chat_repository.py migrations/versions/0004_chat.py app/core/audit.py app/core/errors.py app/core/sql/executor.py
uv run mypy app/modules/chat app/core/audit.py app/core/errors.py app/core/sql/executor.py
uv run lint-imports --config importlinter.ini
uv run pytest tests/test_chat.py tests/test_chat_repository.py tests/test_gate1.py tests/test_sql_executor.py tests/test_db_audit_tasks_retention.py tests/test_foundation.py tests/test_architecture.py
```
