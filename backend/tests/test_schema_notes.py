from types import SimpleNamespace

from app.core.deps import CurrentUser, Role
from app.modules.chat.retrieval import NOTE_LIMIT, ServiceRetrievalIndex


def col(name, comment="", description=""):
    return SimpleNamespace(name=name, comment=comment, description=description)


def table(name, comment, *columns, description=""):
    return SimpleNamespace(
        name=name,
        qualified_name=f"shop.{name}",
        comment=comment,
        description=description,
        columns=list(columns),
    )


class Schema:
    def __init__(self, visible, catalog):
        self.visible, self.full = visible, catalog

    async def browse(self, datasource_id, permissions, *, actor):
        return self.visible

    async def catalog(self, datasource_id):
        return self.full


async def notes_for(visible, catalog, tables):
    index = ServiceRetrievalIndex(
        Schema(visible, catalog), SimpleNamespace(permissions=None), None, None
    )
    member = CurrentUser(id=2, role=Role.MEMBER, team_id=1)
    return await index.notes(member, SimpleNamespace(datasource_id=1), tables)


async def test_notes_describe_visible_objects_and_never_name_hidden_ones():
    long = "很长的说明" * 100
    visible = [
        table(
            "product",
            "商品档案；cost为采购成本",
            col("sku", "商品编码"),
            col("price", "标价", description="对外标价，含税"),
            col("status", "status 为上下架状态"),
        ),
        table("order_item", "订单明细", col("line_amt", "折后行金额"), col("memo", long)),
        table("orders", "订单，关联 employee_salary 表核算提成", col("id")),
    ]
    catalog = [
        table("product", "", col("sku"), col("price"), col("status"), col("cost")),
        table("order_item", "", col("line_amt"), col("memo")),
        table("orders", "", col("id")),
        table("employee_salary", "", col("base_salary")),
    ]
    notes = await notes_for(visible, catalog, ["shop.product", "shop.order_item", "shop.orders"])
    # The table comment names the hidden column (even glued to Chinese text): left out.
    assert "shop.product" not in notes
    # A comment naming a hidden table is left out too.
    assert "shop.orders" not in notes
    # An admin-edited description wins over the database comment.
    assert notes["shop.product.price"] == "对外标价，含税"
    assert notes["shop.order_item.line_amt"] == "折后行金额"
    assert notes["shop.order_item"] == "订单明细"
    assert len(notes["shop.order_item.memo"]) == NOTE_LIMIT
    assert "shop.product.cost" not in notes
    assert not any("cost" in note or "employee_salary" in note for note in notes.values())


async def test_names_visible_elsewhere_are_not_treated_as_hidden():
    visible = [
        table("orders", "", col("status", "status 为订单状态")),
        table("product", "", col("sku")),
    ]
    catalog = [
        table("orders", "", col("status")),
        table("product", "", col("sku"), col("status")),
    ]
    notes = await notes_for(visible, catalog, ["shop.orders"])
    assert notes == {"shop.orders.status": "status 为订单状态"}
