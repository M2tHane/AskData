from types import SimpleNamespace

from app.modules.chat.schemas import ContextItem, RetrievalBundle
from app.modules.eval.adapters import RealChatAdapter


def _item(kind: str, ref_id: int) -> ContextItem:
    return ContextItem(
        kind=kind, ref_id=ref_id, key=f"{kind}:{ref_id}", name="n", definition="d", tables={}
    )


def test_value_mapping_terms_count_as_semantic_items():
    adapter = object.__new__(RealChatAdapter)
    adapter.assets = SimpleNamespace(
        bindings=SimpleNamespace(
            semantic_aliases={"semantic:41": "sem_41", "semantic:46": "sem_46"},
            metric_aliases={"metric:23": "met_23"},
        )
    )
    ctx = SimpleNamespace(
        retrieval=RetrievalBundle(
            items=[_item("enum", 46), _item("semantic", 41), _item("metric", 23)]
        )
    )
    assert adapter._aliases(ctx, "semantic") == ["sem_46", "sem_41"]
    assert adapter._aliases(ctx, "metric") == ["met_23"]
