"""Retrieval recall on the 75 eval questions with a reference SQL, offline.

tests/fixtures/retrieval_eval.json holds what retrieval reads for them (eval schema, each
asker's scope and definitions) plus the Embedding and Rerank answers recorded from a live run
(scripts/export_retrieval_fixture.py). The real ServiceRetrievalIndex and ContextBuilder run
on it, so a change that loses recall or bloats the context fails here, not 20 minutes into a
benchmark. Re-export when the eval schema or definitions change.
"""

import asyncio
import hashlib
import json
import statistics
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.core.datasource import Datasource
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.llm import ModelConfig, ModelRegistry, RerankClient
from app.core.permission import VisibleScope
from app.core.sql.dialect import Dialect
from app.core.sql.guard import SqlGuard
from app.modules.chat.pipeline import ContextBuilder
from app.modules.chat.retrieval import ServiceRetrievalIndex
from app.modules.embedding.service import EmbeddingService
from app.modules.feedback.schemas import ExampleView
from app.modules.metric.schemas import MetricView
from app.modules.schema.schemas import RelationRead, TableRead
from app.modules.semantic.schemas import SemanticView, TargetReference
from tests.fakes import FakeModels, FakeUsage

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "retrieval_eval.json").read_text(encoding="utf-8")
)
CASES = FIXTURE["cases"]


class Datasources:
    async def resolve(self, datasource_id, actor):
        dialect = FIXTURE["datasources"][str(datasource_id)]["dialect"]
        return Datasource(
            id=datasource_id,
            dialect=Dialect(dialect),
            host="fixture",
            port=5432,
            database="fixture",
            username="fixture",
            password=SecretStr("fixture"),
        )


class Targets:
    async def resolve_target(self, datasource_id, kind, ref_id):
        table, column = FIXTURE["targets"][f"{kind}:{ref_id}"]
        return TargetReference(table=table, column=column)


class View:
    """The services retrieval calls, answering as one asker on one datasource."""

    def __init__(self, view_key):
        view = FIXTURE["views"][view_key]
        self.scope = VisibleScope.model_validate(view["scope"])
        self.user = CurrentUser.model_validate(view["user"])
        self.raw = view
        self.permissions = None
        self.targets = Targets()
        self.datasources = Datasources()
        self.guard = SqlGuard()

    def _catalog(self, datasource_id):
        return [
            TableRead.model_validate(table)
            for table in FIXTURE["datasources"][str(datasource_id)]["tables"]
        ]

    async def catalog(self, datasource_id):
        return self._catalog(datasource_id)

    async def browse(self, datasource_id, permissions, *, actor):
        result = []
        for table in self._catalog(datasource_id):
            visible = self.scope.tables.get(table.qualified_name)
            if visible:
                columns = [c for c in table.columns if c.name in visible.columns]
                result.append(table.model_copy(update={"columns": columns}))
        return result

    async def relations(self, datasource_id, permissions, *, actor):
        tables = await self.browse(datasource_id, permissions, actor=actor)
        ids = {c.id for t in tables for c in t.columns}
        return [
            RelationRead.model_construct(from_column_id=a, to_column_id=b)
            for a, b in FIXTURE["datasources"][str(datasource_id)]["relations"]
            if a in ids and b in ids
        ]

    async def examples(self, user, datasource_id):
        return [ExampleView.model_validate(item) for item in self.raw["examples"]]


def services(view_key):
    view = View(view_key)
    semantic = SimpleNamespace(
        permissions=None,
        targets=view.targets,
        list=lambda user, ds: _async(
            [SemanticView.model_validate(t) for t in view.raw["semantics"]]
        ),
    )
    metric = SimpleNamespace(
        datasources=view.datasources,
        guard=view.guard,
        list=lambda user, ds: _async([MetricView.model_validate(m) for m in view.raw["metrics"]]),
    )
    feedback = SimpleNamespace(
        datasources=view.datasources, guard=view.guard, examples=view.examples
    )
    return view, semantic, metric, feedback


async def _async(value):
    return value


class RecordedVectors:
    """The Embedding answers of the live run; a question it never saw gets none."""

    def __init__(self):
        self.misses = 0

    async def nearest(self, datasource_id, query, keys, actor):
        found = FIXTURE["vectors"].get(f"{datasource_id}:{query}")
        if found is None:
            self.misses += 1
            return []
        allowed = set(keys)
        return [tuple(key) for key in found if tuple(key) in allowed]


class RecordedRerank:
    """The Rerank answers of the live run, keyed by query and candidate texts."""

    registry = SimpleNamespace(select=lambda team_id, kind: _async(None))

    def __init__(self):
        self.misses = 0

    async def rerank(self, query, texts, context):
        key = hashlib.sha256(json.dumps([query, texts], ensure_ascii=False).encode()).hexdigest()
        ranking = FIXTURE["reranks"].get(key)
        if ranking is None:
            # The candidates changed since the export: keep the fused order.
            self.misses += 1
            raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
        return ranking


async def measure(*, vectors=None, rerank=None, full=False):
    rows = []
    for case in CASES:
        view, semantic, metric, feedback = services(case["view"])
        index = ServiceRetrievalIndex(view, semantic, metric, feedback, vectors, rerank)
        bundle = await ContextBuilder(index, threshold=0).build(
            view.user,
            view.scope,
            case["question"],
            full=full,
            follow_up=case["follow_up"],
            keep_tables=frozenset(case["keep_tables"]),
        )
        missing = {
            table: sorted(set(columns) - set(bundle.tables.get(table, {})))
            for table, columns in case["needed"].items()
            if table not in bundle.tables or set(columns) - set(bundle.tables[table])
        }
        rows.append(
            {
                "case": case["id"],
                "missing": missing,
                "mode": bundle.mode,
                "tables": len(bundle.tables),
                "chars": len(json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)),
                "bundle": bundle,
                "view": view,
            }
        )
    return rows


def recall(rows):
    return sum(not row["missing"] for row in rows)


def test_the_fixture_covers_the_eval_set():
    assert len(CASES) == 75
    assert {case["view"].split(":")[0] for case in CASES} >= {"sales_member"}


async def test_keyword_only_recall_and_context_size():
    rows = await measure()
    misses = [(row["case"], row["missing"]) for row in rows if row["missing"]]
    assert recall(rows) >= 74, misses
    chars = [row["chars"] for row in rows]
    assert statistics.mean(chars) <= 3300
    assert max(chars) <= 6500


async def test_vectors_and_rerank_recover_every_case():
    vectors, rerank = RecordedVectors(), RecordedRerank()
    rows = await measure(vectors=vectors, rerank=rerank)
    misses = [(row["case"], row["missing"]) for row in rows if row["missing"]]
    assert recall(rows) == 75, misses
    # Most answers must replay; if not, the export is stale for today's retrieval.
    assert vectors.misses <= 2 and rerank.misses <= 3
    assert statistics.mean(row["chars"] for row in rows) <= 4300


async def test_retrieved_context_is_smaller_than_the_full_schema():
    hybrid = await measure()
    full = await measure(full=True)
    assert recall(full) == 75
    assert statistics.mean(r["chars"] for r in hybrid) < 0.7 * statistics.mean(
        r["chars"] for r in full
    )


@pytest.mark.parametrize("with_models", [False, True])
async def test_nothing_outside_the_askers_scope_is_retrieved(with_models):
    models = (RecordedVectors(), RecordedRerank()) if with_models else (None, None)
    for row in await measure(vectors=models[0], rerank=models[1]):
        scope, bundle = row["view"].scope, row["bundle"]
        for table, columns in bundle.tables.items():
            assert table in scope.tables, (row["case"], table)
            assert set(columns) <= scope.tables[table].columns, (row["case"], table)
        for item in bundle.items:
            for table, columns in item.tables.items():
                assert set(columns) <= scope.tables[table].columns, (row["case"], item.name)


async def test_retrieval_is_deterministic():
    first = [(r["case"], r["bundle"].model_dump(mode="json")) for r in await measure()]
    second = [(r["case"], r["bundle"].model_dump(mode="json")) for r in await measure()]
    assert first == second


class FaultyRerank:
    """A rerank provider that answers badly in one way."""

    def __init__(self, fault):
        self.fault = fault

    async def rerank(self, model, query, documents):
        if self.fault == "error":
            raise RuntimeError("provider exploded")
        if self.fault == "hang":
            await asyncio.sleep(1)
        return {
            "out_of_range": [len(documents), 0],
            "negative": [-1, 0],
            "duplicate": [0, 0, 1],
            "empty": [],
        }.get(self.fault, list(range(len(documents))))


@pytest.mark.parametrize(
    "fault", ["out_of_range", "negative", "duplicate", "empty", "error", "hang"]
)
async def test_a_misbehaving_reranker_leaves_the_fused_order_alone(fault):
    model = ModelConfig(
        id="rank",
        kind="rerank",
        model="fake",
        api_key=SecretStr("fake"),
        retries=0,
        timeout_seconds=0.01,
    )
    client = RerankClient(ModelRegistry(FakeModels([model])), FakeUsage(), FaultyRerank(fault))
    baseline = await measure()
    faulty = await measure(rerank=client)
    assert [r["bundle"] for r in faulty] == [r["bundle"] for r in baseline]
    # The provider was asked (and failed), rather than skipped.
    calls = client.recorder.records
    assert len(calls) >= 50
    # An empty ranking is a valid (useless) answer; every other fault is a failed call.
    assert (
        all(call.success for call in calls)
        if fault == "empty"
        else not any(call.success for call in calls)
    )


async def test_nearest_returns_only_keys_it_was_given():
    rows = [
        SimpleNamespace(kind="column", ref_id=1),
        # Stale entries and documents the asker cannot see come back from the index too.
        SimpleNamespace(kind="column", ref_id=999),
        SimpleNamespace(kind="metric", ref_id=7),
        SimpleNamespace(kind="table", ref_id=2),
    ]

    class Index:
        async def nearest(self, datasource_id, model_id, vector, allowed, limit):
            return rows

    service = EmbeddingService(Index(), None, None, None, None, None)

    async def embed(text, actor):
        return [0.0], "embed"

    service._embed = embed
    found = await service.nearest(
        1, "q", [("column", 1), ("table", 2)], CurrentUser(id=1, role=Role.MEMBER, team_id=1)
    )
    assert found == [("column", 1), ("table", 2)]
