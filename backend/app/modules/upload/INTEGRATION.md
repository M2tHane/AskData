# D group integration contract

This module owns its five required layers, the parser, and the import lifecycle. The
composition root must mount `api.router` under `/api` and override `api.upload_service`.
Use a separate request-scoped `AsyncSession` with **no outer transaction**: the service
commits job + audit before submitting to `TaskManager`, so the worker never races an
uncommitted job. Do not reuse the same service/session for concurrent requests or tasks.

Construct `UploadService` with `UploadRepository`, `core.storage.ObjectStore`,
`core.permission.PermissionService`, `core.audit.AuditService`, `TaskManager`, and an
implementation of `service.UploadRegistration`. Register `service.run_import` under
`upload.import`, creating a fresh session/service per dispatch. The API uses
`core.importer.ImportReadGateway`; only the worker gets `PostgresImportGateway` and its
writer credentials. All API responses use the registered core error codes and envelope.

`UploadRegistration` is the boundary to datasource/schema/permission services. It must:

1. `reserve(name, team_id, actor)`: validate the team through org service, create a pending
   upload source without testing a not-yet-existing reader login, return its integer ID.
   Accept an admin with an explicitly chosen team, or a leader with only their own team.
2. `owner_team(id)`: return the source owner and reject non-upload sources. Never allow an
   existing MySQL/PG datasource to be repurposed by an import.
3. `publish(job_id, datasource, table, owner_team_id, actor)`: register **reader** credentials
   with SecretBox; synchronize meta_table/meta_column via schema service, preserving
   manual descriptions and saving original column names in comments/descriptions. Grant
   a brand-new source only to its owner team; new fields on an existing source remain
   ungranted. Invalidate removed field grants and scope caches. Make repeated calls for
   the same job_id idempotent. Never synthesize an admin actor to bypass service checks.

The parallel datasource/schema services currently expose admin-only `create`/`synchronize`
methods and no pending-source reservation method. This adapter must be added by their
owner/integrator using public service methods; upload does not access their repositories
or tables. The upload service and its tests already exercise the exact contract and
registration failure/recovery behavior.

A source may have multiple sheets/tables: preview and confirm each sheet separately with
its config and the same datasource_id. Each imported file job reserves a monotonic source
version; immutable backups live at `uploads/team_<team>/ds_<id>/v<version>/<filename>`.
Preview jobs use unique `tmp/team_<team>/ds_<id>/<uuid>/<filename>` paths.

The import data transaction encompasses schema/table preparation and every COPY batch.
Object storage and metadata are separate services: the immutable backup is staged before
COPY, successful COPY is followed by `registration_pending`, then publication. The
`/{id}/publish` endpoint recovers registration without repeating an append. A process
crash between COPY commit and the metadata status commit requires operator reconciliation;
failed/running jobs are not blindly re-enqueued. Distributed crash recovery and cleanup
of abandoned temporary/prestaged objects are not implemented in this module.

Legacy XLS uses pandas + `xlrd==2.0.2` from this module's pinned `requirements.txt`; install
that supplement after `uv sync --frozen` when testing XLS outside the Docker image. XLSX
uses openpyxl `read_only=True, data_only=True`; macros, zip expansion over 250 MiB and
merged cells are rejected. CSV encoding and delimiters are configurable. API files use a
base64 JSON body to avoid changing the shared dependency lock for multipart handling.

## I2 adapter factory and outstanding service ports

`adapters.build_upload_registration(datasources, schema, grants, org, permissions)`
returns the concrete `UploadRegistration` implementation. Supply public services sharing
one session and the upload transaction. It preserves the real actor, validates upload
ownership and `ds_<id>` reader isolation, expires removed metadata grants, grants only
initial owner-team metadata, invalidates scope, and finishes a durable job receipt.
A completed receipt skips publication on subsequent calls, including with a new adapter.
Subsequent source publications do not grant new columns. Original column comments pass
unchanged to schema service; preserving manual descriptions belongs to that service.

The current datasource/schema/grant services still lack these public methods; the factory
returns `TASK_FAILED`/503 until they are implemented. It does not use admin-only create,
update, synchronize or replace as a workaround. Required ports are defined in adapters.py:

- datasource: `reserve_upload(name, team_id, *, actor) -> int`,
  `upload_owner(datasource_id) -> int`,
  `publish_upload_credentials(job_id, datasource, owner_team_id, *, actor)`, and
  `finish_upload_publication(job_id, datasource_id, *, actor)`.
- schema: `synchronize_upload(datasource_id, table, *, actor) -> SyncResult`; upsert only
  this imported table, retain other sheets, preserve descriptions and return removed IDs.
- permission: `grant_upload_owner(datasource_id, team_id, *, actor)`; validate owner and
  source type, grant only the source owner, and preserve masks/member subset constraints.
  Existing `expire_metadata(SyncResult)` is reused. New-source member visibility should
  remain closed until explicitly configured; fixture employee grants are separate inputs.
- org: existing `ensure_team(team_id)` is reused.

The credentials method must lock the source and return a service-owned object with
`datasource_id`, `owner_team_id`, `completed`, `initialize_owner_grants` fields, equivalent
to `UploadPublication`. It must encrypt only reader credentials with SecretBox, reject
non-upload/repointed sources, and keep the initial-grant flag true until first publication
commits. The finish method persists job idempotence in the same transaction. Keep receipts
in the owning datasource service; do not import upload adapters into datasource or directly
access upload tables. All writes must audit with the original actor and must not commit
independently of the upload transaction. Owner grants and removal expiry must invalidate
cache after commit as well as within the transaction.

`GrantService.snapshot` currently calls the active-only datasource connection method.
The owner services must also support returning an empty scope for a pending upload, so
`UploadService.confirm`/`publish` can check an owned job before its reader is registered.
See `docs/OPEN_QUESTIONS.md` for this remaining integration dependency.
