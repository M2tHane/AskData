"""How retrieval holds up as the schema grows, offline and without a model.

Runs the real ServiceRetrievalIndex and ContextBuilder over the 75 eval questions with a
reference SQL (tests/fixtures/retrieval_eval.json), after adding N distractor tables to the
commerce datasource and to every asker's visible scope. Distractors reuse the questions' own
vocabulary (客户, 金额, 渠道, 状态, 地区 ...) across 15 business domains, so keywords alone
cannot tell them apart by topic. Vectors and reranking are off: their recorded answers cannot
point at synthetic tables, so this is keyword-only recall, a lower bound.

    uv run python scripts/retrieval_scaling.py [--sizes 0,50,200,500]

Reports, per size: recall (every table and column the reference SQL uses was retrieved), the
average number of tables and characters of schema context given to the model, and the same
for the full schema.
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
from typing import Any

from tests import test_retrieval_recall as harness

# The test harness is untyped; this script only reads its rows.
measure: Any = harness.measure

DATASOURCE = "3"
SCHEMA = "eval_commerce"
DOMAINS = [
    ("purchase", "采购单"),
    ("inventory", "库存流水"),
    ("shipment", "物流运单"),
    ("campaign", "营销活动"),
    ("coupon", "优惠券发放"),
    ("ticket", "售后工单"),
    ("voucher", "财务凭证"),
    ("attendance", "考勤记录"),
    ("store", "门店日报"),
    ("warehouse", "仓库盘点"),
    ("supplier", "供应商结算"),
    ("points", "会员积分"),
    ("invoice", "发票"),
    ("contract", "合同"),
    ("lead", "销售线索"),
]
COLUMNS = [
    ("customer_id", "integer", "客户ID"),
    ("amount", "numeric(12,2)", "金额（元）"),
    ("status", "smallint", "状态：0 待处理，1 已完成，2 已取消"),
    ("created_at", "timestamp", "创建时间"),
    ("region_id", "integer", "所属地区"),
    ("channel", "varchar(3)", "渠道：WEB/APP/B2B"),
    ("order_no", "varchar(16)", "单号"),
    ("qty", "integer", "数量"),
    ("price", "numeric(12,2)", "单价"),
    ("employee_id", "integer", "负责员工"),
    ("paid_at", "timestamp", "支付时间"),
    ("discount_amt", "numeric(12,2)", "优惠金额"),
    ("product_id", "integer", "商品ID"),
    ("remark", "text", "备注"),
    ("tier", "smallint", "等级"),
    ("name", "text", "名称"),
]


def distractors(count: int, seed: int = 20251011) -> tuple[list[dict[str, Any]], list[list[int]]]:
    rng = random.Random(seed)
    catalog = harness.FIXTURE["datasources"][DATASOURCE]
    ids = {t["name"]: {c["name"]: c["id"] for c in t["columns"]} for t in catalog["tables"]}
    tables: list[dict[str, Any]] = []
    relations: list[list[int]] = []
    next_id = 1_000_000
    for index in range(count):
        domain, label = DOMAINS[index % len(DOMAINS)]
        table_id, next_id = next_id, next_id + 1
        picked = [("id", "integer", "主键")] + rng.sample(COLUMNS, rng.randint(6, 12))
        columns = []
        for ordinal, (name, data_type, comment) in enumerate(picked, 1):
            columns.append(
                {
                    "comment": comment,
                    "data_type": data_type,
                    "description": "",
                    "id": next_id,
                    "is_pk": name == "id",
                    "name": name,
                    "ordinal": ordinal,
                    "status": "active",
                    "table_id": table_id,
                }
            )
            target = {"customer_id": ("customer", "id"), "product_id": ("product", "id")}.get(name)
            if target:
                relations.append([next_id, ids[target[0]][target[1]]])
            next_id += 1
        tables.append(
            {
                "columns": columns,
                "comment": f"{label}（第 {index // len(DOMAINS) + 1} 条业务线）",
                "datasource_id": int(DATASOURCE),
                "description": "",
                "id": table_id,
                "name": f"{domain}_{index:03d}",
                "schema_name": SCHEMA,
                "status": "active",
                "synced_at": "2026-10-11T00:00:00Z",
            }
        )
    return tables, relations


def install(tables: list[dict[str, Any]], relations: list[list[int]]) -> None:
    catalog = harness.FIXTURE["datasources"][DATASOURCE]
    catalog["tables"] = [t for t in catalog["tables"] if t["id"] < 1_000_000] + tables
    catalog["relations"] = [r for r in catalog["relations"] if r[0] < 1_000_000] + relations
    for key, view in harness.FIXTURE["views"].items():
        if not key.endswith(f":{DATASOURCE}"):
            continue
        scope = view["scope"]["tables"]
        for name in [n for n in scope if n.split(".", 1)[1][:-4] in {d for d, _ in DOMAINS}]:
            del scope[name]
        for table in tables:
            columns = {c["name"]: c["data_type"] for c in table["columns"]}
            scope[f"{SCHEMA}.{table['name']}"] = {
                "all_columns": columns,
                "columns": list(columns),
                "masks": {},
            }


async def main(sizes: list[int]) -> None:
    out = sys.stdout.write
    out("tables | recall | hybrid tables | hybrid chars | full chars | hybrid/full\n")
    for size in sizes:
        install(*distractors(size))
        hybrid = await measure()
        full = await measure(full=True)
        cases = [r for r in hybrid if r["case"]]
        recalled = sum(not r["missing"] for r in cases)
        h_chars = statistics.mean(r["chars"] for r in hybrid)
        f_chars = statistics.mean(r["chars"] for r in full)
        out(
            f"{12 + size:>6} | {recalled}/{len(cases)} | "
            f"{statistics.mean(r['tables'] for r in hybrid):.1f} | {h_chars:,.0f} | "
            f"{f_chars:,.0f} | {h_chars / f_chars:.0%}\n"
        )
        misses = [(r["case"], r["missing"]) for r in hybrid if r["missing"]]
        if misses:
            out(f"       missed: {json.dumps(misses, ensure_ascii=False)[:300]}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="0,50,200,500")
    args = parser.parse_args()
    asyncio.run(main([int(s) for s in args.sizes.split(",")]))
