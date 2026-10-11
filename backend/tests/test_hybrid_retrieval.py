"""Hybrid schema retrieval: only the tables, columns and items a question needs."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.permission import TableScope, VisibleScope
from app.modules.chat.pipeline import ContextBuilder
from app.modules.chat.ranking import Doc, fuse, grams, join_paths, keyword_scores, ranked
from app.modules.chat.retrieval import ServiceRetrievalIndex
from app.modules.semantic.schemas import SemanticView, TargetReference

USER = CurrentUser(id=7, role=Role.MEMBER, team_id=1)


# --- pure ranking -------------------------------------------------------------------------


def test_grams_are_ascii_words_and_chinese_bigrams():
    assert grams("退款金额 refund_amount") == {"退款", "款金", "金额", "refund", "amount"}


def test_rare_words_outweigh_common_ones_and_a_literal_name_wins():
    docs = [
        Doc("table", 1, names=("orders",), text="订单 主表"),
        Doc("table", 2, names=("refund",), text="订单 退款记录"),
        Doc("table", 3, names=("payment",), text="订单 支付记录"),
    ]
    scores = keyword_scores("订单的退款有多少", docs)
    assert ranked(scores)[0] == ("table", 2)
    named = keyword_scores("payment 表有几行", docs)
    assert ranked(named)[0] == ("table", 3) and named[("table", 3)] > 1


def test_rank_fusion_rewards_agreement():
    fused = fuse([[("t", 1), ("t", 2)], [("t", 2), ("t", 3)]])
    assert max(fused, key=fused.__getitem__) == ("t", 2)


def test_join_paths_add_bridges_not_neighbours():
    edges = [("order_item", "orders"), ("orders", "customer"), ("customer", "region")]
    edges.append(("orders", "payment"))
    assert join_paths({"order_item", "region"}, edges) == {"orders", "customer"}
    assert join_paths({"orders"}, edges) == set()


# --- the index ----------------------------------------------------------------------------


def _column(identifier, name, kind="bigint", comment="", is_pk=False):
    return SimpleNamespace(
        id=identifier,
        name=name,
        data_type=kind,
        comment=comment,
        description="",
        is_pk=is_pk,
    )


def _table(identifier, name, comment, columns):
    return SimpleNamespace(
        id=identifier,
        name=name,
        qualified_name=f"shop.{name}",
        comment=comment,
        description="",
        columns=columns,
    )


TABLES = [
    _table(
        1,
        "orders",
        "订单",
        [
            _column(11, "id", is_pk=True),
            _column(12, "customer_id"),
            _column(13, "status", "int", "订单状态"),
            _column(14, "amount", "numeric", "实付金额"),
            _column(15, "created_at", "timestamp", "下单时间"),
        ],
    ),
    _table(
        2,
        "customer",
        "客户",
        [_column(21, "id", is_pk=True), _column(22, "region_id"), _column(23, "name", "text")],
    ),
    _table(
        3, "region", "地区", [_column(31, "id", is_pk=True), _column(32, "name", "text", "大区")]
    ),
    _table(4, "refund", "退款记录", [_column(41, "id", is_pk=True), _column(42, "order_id")]),
    _table(
        5,
        "wide",
        "宽表",
        [_column(50, "id", is_pk=True)]
        + [_column(51 + n, f"attr_{n}", "text", f"属性{n}") for n in range(24)]
        + [_column(80, "price", "numeric", "零售单价"), _column(81, "sold_on", "date")],
    ),
]
RELATIONS = [(12, 21), (22, 31), (42, 11)]


def _scope():
    return VisibleScope(
        datasource_id=1,
        tables={
            t.qualified_name: TableScope(
                all_columns={c.name: c.data_type for c in t.columns},
                columns=frozenset(c.name for c in t.columns),
            )
            for t in TABLES
        },
    )


def _term(identifier, column_id, aliases, mapping=None):
    now = datetime.now(UTC)
    return SemanticView(
        id=identifier,
        datasource_id=1,
        scope="platform",
        target_type="enum" if mapping else "column",
        target_id=column_id,
        aliases=aliases,
        description="",
        enum_mapping=mapping or {},
        owner_team_id=None,
        owner_user_id=None,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )


class Semantic:
    permissions = None

    def __init__(self, terms):
        self.terms = terms
        columns = {c.id: (t.qualified_name, c.name) for t in TABLES for c in t.columns}
        outer = self

        class Targets:
            async def resolve_target(self, datasource_id, kind, ref_id):
                table, column = columns[ref_id]
                return TargetReference(table=table, column=column)

        outer.targets = Targets()

    async def list(self, user, datasource_id):
        return self.terms


class Schema:
    async def browse(self, datasource_id, permissions, *, actor):
        return TABLES

    async def catalog(self, datasource_id):
        return TABLES

    async def relations(self, datasource_id, permissions, *, actor):
        return [SimpleNamespace(from_column_id=a, to_column_id=b) for a, b in RELATIONS]


class Empty:
    async def list(self, user, datasource_id):
        return []

    async def examples(self, user, datasource_id):
        return []


class Vectors:
    def __init__(self, order=None, error=None):
        self.order, self.error, self.calls = order or [], error, 0

    async def nearest(self, datasource_id, query, keys, actor):
        self.calls += 1
        if self.error:
            raise self.error
        return [key for key in self.order if key in set(keys)]


def _index(terms=(), embedding=None):
    return ServiceRetrievalIndex(Schema(), Semantic(list(terms)), Empty(), Empty(), embedding)


async def _build(index, question, **kwargs):
    return await ContextBuilder(index, threshold=0).build(USER, _scope(), question, **kwargs)


async def test_question_gets_its_tables_and_the_join_path_only():
    bundle = await _build(_index(), "各大区的实付金额是多少")
    assert bundle.mode == "hybrid"
    assert set(bundle.tables) == {"shop.orders", "shop.customer", "shop.region"}


async def test_status_definitions_of_chosen_tables_come_along():
    paid = _term(1, 13, ["有效订单"], {"1": "已支付"})
    stock = _term(2, 80, ["零售价"])
    bundle = await _build(_index([paid, stock]), "上个月退款的订单实付金额")
    assert "shop.refund" in bundle.tables and "shop.orders" in bundle.tables
    names = {item.name for item in bundle.items}
    assert "有效订单" in names and "零售价" not in names


async def test_wide_tables_keep_keys_times_and_matched_columns():
    bundle = await _build(_index(), "宽表里零售单价最高的是哪个")
    columns = bundle.tables["shop.wide"]
    assert {"id", "price", "sold_on"} <= set(columns)
    assert "attr_3" not in columns


async def test_no_match_falls_back_to_the_whole_schema():
    bundle = await _build(_index(), "今天天气怎么样")
    assert bundle.mode == "full" and len(bundle.tables) == len(TABLES)


async def test_vectors_add_what_keywords_miss_and_their_absence_is_tolerated():
    vectors = Vectors(order=[("table", 4)])
    bundle = await _build(_index(embedding=vectors), "被退回的钱")
    assert vectors.calls == 1 and "shop.refund" in bundle.tables
    missing = Vectors(error=AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503))
    assert (await _build(_index(embedding=missing), "各大区的实付金额")).mode == "hybrid"


async def test_vector_errors_other_than_no_model_are_not_hidden():
    broken = Vectors(error=AppError(ErrorCode.INTERNAL_ERROR))
    with pytest.raises(AppError):
        await _build(_index(embedding=broken), "各大区的实付金额")


async def test_follow_up_keeps_earlier_tables_and_choice_uses_everything():
    bundle = await _build(
        _index(), "按月拆开", follow_up="各大区实付金额", keep_tables=frozenset({"shop.orders"})
    )
    assert {"shop.orders", "shop.region"} <= set(bundle.tables)
    chosen = await _build(_index(), "金额", choice="实付金额")
    assert chosen.mode == "full"


async def test_notes_follow_the_selected_columns():
    class Described(ServiceRetrievalIndex):
        async def notes(self, user, scope, tables):
            return {"shop.wide": "宽表", "shop.wide.attr_3": "属性3", "shop.wide.price": "单价"}

    index = Described(Schema(), Semantic([]), Empty(), Empty())
    bundle = await _build(index, "宽表里零售单价最高的是哪个")
    assert bundle.notes == {"shop.wide": "宽表", "shop.wide.price": "单价"}
