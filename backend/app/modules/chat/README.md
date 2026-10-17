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

Every question gets retrieved context (`ContextBuilder(threshold=0)`):
`ServiceRetrievalIndex` scores visible tables, columns, terms, metrics and reviewed
examples by keyword (`ranking.keyword_scores`: IDF-weighted Chinese bigrams and
ASCII words, plus a literal-name bonus), adds pgvector neighbours when an Embedding
model is configured (`EmbeddingService.nearest`), fuses both with reciprocal rank
fusion, optionally reranks the top 30, then keeps up to six tables (wide tables
only their keys, times and matched columns), the terms of those tables, three
metrics and three examples. `expand_relations` adds only join-path tables. No hit
means full context. Two fallbacks give the model the whole schema: an
"unanswerable" reply is asked once more, and every correction round.
`scripts/retrieval_recall.py` measures recall on the eval questions without a
model. `ScopeOnlyIndex` remains a minimal test/bootstrap adapter and refuses hybrid
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
with leap-day clamping. History uses a bounded
extractive summary plus a freshly guarded previous SQL for follow-up filters.

Only core SQL_EXECUTION_ERROR (syntax/undefined-column driver categories) can
enter correction, up to three corrections after the first execution. Each
correction rechecks current permission and passes SqlGuard. Guard failures,
connection failures, timeouts, cost/limit denials and empty results do not retry.
Manual SQL does not invoke generation/correction. ChartSpec is a strict Pydantic
JSON schema with allowed types and result-column references; invalid chart JSON
falls back to a shape heuristic. A long-format result (x, one text column with 2-5 values,
one measure) gets `series` set to that column and the front end pivots it into one series
per value. Explanations append effective definitions/levels/versions,
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

## 历史回放与追问上下文

- 回答成功时，把当时展示的结果（已脱敏、最多 `PREVIEW_ROWS` 行）连同 SQL 用到的表和字段存进 `message_result`。删除消息时一并删除，删除会话时立即删除。
- `GET /conversations/{id}/messages/{message_id}/replay` 不再查询数据源：先按当前权限重新守卫 SQL，通过后返回保存的结果，并按现在的规则再脱敏一次，同时带上 `queried_at`。这个功能上线前的旧回答没有保存结果：查询一次并保存，`reread=true`。
- 权限复核也在消息列表里做：回答用到的字段只要有一个不可见，就不返回正文、思考过程、图表和 SQL，只返回 `revoked_fields`（如 `orders.pay_amount`）。有字段现在要脱敏而当时没有时（`masks_changed`），正文和思考过程不返回，因为里面可能有明文。
- 导出：保存的结果就是全部数据时，直接用它导出，和页面一致；否则重新查询，界面会提示结果可能不一致。
- 追问最多带前 5 轮（`HISTORY_ROUNDS`）：用户问题、回答首行，以及每轮经当前权限复核仍通过守卫的 SQL；是否沿用前文由模型判断，当前问题本身不会进入 `history`。
- 提示词覆盖：`PipelineDependencies.prompt_source` 在每次模型调用前读取管理员发布的版本；没有则用内置模板。
