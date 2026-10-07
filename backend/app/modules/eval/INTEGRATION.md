# D group eval integration contract

Mount `api.router` under `/api` and override `api.eval_service` with a request-scoped
`EvalService`. Its repository manages its own transactions; use a session without an
outer transaction. Each dispatched task must construct a fresh repository/service/session.
Register its `run` handler under `eval.run` in core `TaskManager`.

`EvalAssets(Path('/app/eval'))` reads the versioned JSONL cases and YAML fixtures. Fixture
IDs are **offline identity aliases**, not production primary keys: the integrator must
provision test users/datasources/grants through their public services, and supply a chat
adapter that maps aliases to those provisioned identities. The runner never imports the
parallel chat implementation. Implement `service.ChatPipeline.run(EvalQuestion, RunRequest)`
and return `schemas.PipelineOutcome`; SQL execution counts must come from executor/query
log instrumentation, not model output. Validate chart config using chat's registered chart
schema before returning `chart_valid=True`. Include clock, history, matched semantic/metric
IDs, resolved time range and clarification options. `EvalQuestion` deliberately has no
reference SQL or expected answers.

`GuardedReferenceRunner` routes reference SELECTs through PermissionService → SqlGuard →
SqlExecutor. Inject a `ReferenceDatasourceProvider` which resolves each fixture alias to
its actual read-only connection, and the corresponding real permission service. Safety
and clarification cases never execute a reference query. Rewritten fixture/upload schema
names must match the provisioned test datasource schemas; no cross-module table access is
used by this runner.

`EvalService.load_cases` is an audited admin action. `start` takes an immutable snapshot of
the selected case definitions, model ID, prompt versions and retrieval config before
queueing. Results store SQL for successes and SHA-256 result digests, not complete query
results or raw failure text. Leader-facing results suppress SQL. Completed runs can be
compared for new failures/fixes; newly added/removed cases are listed separately. Metrics
cover overall/category pass rate, no-retry pass rate, retries, safety, duration and tokens.
Release safety requires exactly baseline V01..V10, P01..P08 and I01..I04 with all passing;
a partial run cannot authorize release.

`Gate1Runner` takes a factory of fixed-output **core LLMClient** instances and the real
SqlGuard. It consumes all 22 safety cases from `/eval`, obtains scope through the core
PermissionService and checks exact codes, zero execution/retry, and no hidden-object names
in the reply. There is no SQL execution dependency in this pre-pipeline runner. The tests
provide the fixed core transport and demonstrate 22/22 passing.

Commands from backend:

```bash
uv run --frozen --with-requirements app/modules/upload/requirements.txt pytest -q \
  tests/test_upload_d.py tests/test_eval_d.py tests/test_upload_d_external.py --external-services
uv run --frozen mypy --config-file pyproject.toml app/core/importer.py app/modules/upload app/modules/eval
uv run --frozen lint-imports --config importlinter.ini
```

The Docker test uses a disposable PostgreSQL 18 container and deletes it on exit. Default
pytest skips external tests and legacy XLS when the optional pinned xlrd supplement is
not installed. Fake chat outcomes verify runner behavior; they do not report real-model
accuracy or validate the production chat adapter.

## I2 factories for real pipeline replay

`adapters.build_fixture_assets(directory, FixtureBindings(...), permissions=None)` binds
all fixture aliases explicitly. Supply `team_ids`, `users` (CurrentUser identities resolved
through org service), and `datasources` (reader connections resolved through datasource
service). Roles/team membership must exactly match the fixture contract; missing/duplicate
IDs, inactive users and writable connections are rejected. Connections and SecretStr fields
are omitted from the bindings repr. Use freshly resolved bindings for each worker dispatch.
No fixture identity is automatically treated as a production primary key.

In production supply `permissions=grant_service.permissions`; the adapter then uses the
actual core permission service for pipeline, references and hidden-object checks. Omitting
this argument is only for isolated fixture replay, using the fixture PermissionProvider
through core PermissionService. The factories never insert org/schema/grant rows directly.

`build_eval_adapter(assets, dependencies_factory)` returns a concrete ChatPipeline. The
async factory receives only `EvalQuestion` and `RunRequest`, and supplies request-local
chat PipelineDependencies with service-backed datasource resolution, retrieval, core LLM,
SqlGuard, SqlExecutor and QueryLogWriter. Both requested prompt versions and selected
model ID are checked. Configure retrieval/model choices in the dependency factory. This
adapter instantiates the real chat QueryPipeline using names available through chat.service;
that module's existing implicit reexports require one targeted mypy attr-defined ignore.
No cross-module repositories/models/schemas are imported.

Each case clones the dependency container and wraps only its executor, so reference and
concurrent queries cannot inflate its execution count. The count includes attempted core
executions that fail, a conservative gate condition. It replays mapped history at the frozen
clock, returns genuine retry/token counts and clarification choices, validates charts with
the registered chat schema and result columns, and does not count table fallbacks as valid
model charts. Semantic/metric IDs come from actual retrieval items; optional
`semantic_aliases`/`metric_aliases` map retrieval keys to fixture IDs. Explicit single-table
half-open date intervals come from executed guard-normalized SQL; complex date expressions
remain unreported rather than guessed. The existing core querylog stage always runs.

`build_reference_runner(assets, guard, executor)` maps reference schema names before the
same permission/guard/executor path. Mapping changes qualified identifiers while retaining
quoted literal values, dollar strings and comments. Ambiguous escaped strings/nested
comments are rejected when rewriting is required. The original cases and fixture files
remain unchanged. `GuardedReferenceRunner` gained only an optional `sql_mapper` argument;
existing callers retain their behavior.

I3 can construct EvalService with the returned adapter/reference runner and the same mapped
assets. API/worker wiring remains I3's responsibility. Standalone `run_suite` uses the same
scorer and complete-baseline safety gate, and `build_simulated_adapter` creates a real core
LLMClient with explicit per-case JSON response strings, never reference answers.

Executable replay (from backend):

```bash
uv run --frozen python ../eval/run_pipeline.py --gate1
# Full replay against an isolated, preloaded DB; connections are loaded via core Settings:
# ASKDATA_EVALUATION_DATASOURCES = JSON object mapping all six aliases to Datasource DTOs.
# Include physical id/schema_name and read-only credentials; secrets stay in environment.
uv run --frozen python ../eval/run_pipeline.py --responses /path/to/responses.json
```

The response artifact maps a case ID to a list of JSON strings in call order (clarification,
SQL, optional corrections, chart, explanation). The CLI maps SQL responses to physical
schemas; missing responses fail. Full replay uses scope-only retrieval by default; production
semantic/example retrieval belongs in the injected dependency factory. stdout contains only
the JSON report, core logs go to stderr, no result rows/SQL/secrets are emitted. Exit codes:
0 all selected cases pass, 1 reported evaluation failures, 2 invalid setup. `--gate1` always
selects all 22 safety cases and rejects partial selection.

Validated: 22/22 through the real ten-stage chat pipeline with zero connection/EXPLAIN/
execution; complete 100-case PostgreSQL 18 replay with rewritten commerce/upload schemas
and real core reference/actual execution. The deliberate fixed-output/scope-only test yields
76 passes and 24 correctly reported semantic/chart/clarification failures, not real-model
accuracy. Neither fixtures nor case contents were modified. External containers are removed.
