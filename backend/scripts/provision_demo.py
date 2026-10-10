"""Provision the 星河优选 demo business on a running Compose stack, through public APIs only.

Run from backend with the stack up and an empty business state (see docs/RUNBOOK.md, 演示业务数据):

    ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD=... \
        uv run python scripts/provision_demo.py

It loads scripts/demo_data.py into schema `shop` of pg-import (via `docker exec` as the local
import admin), creates a SELECT-only `shop_reader` role, registers one read-only datasource,
creates four teams with leaders and members, applies column-level grants (masks for phone and
bank account, cost columns hidden from members), and publishes platform/team semantics and metrics.

Every account gets a random password. The passwords are written once to a 0600 file
(default deploy/.env.demo-accounts, git-ignored) and never printed.
"""

# ruff: noqa: E501  (long Chinese definition strings in the data tables)
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from demo_data import SCHEMA, TABLES, build_sql, table_rows
from provision_eval import Api, ImportAdmin, wait_task

ROOT = Path(__file__).resolve().parents[2]
ALL = "*"
VALID = "('已支付','已发货','已完成')"

TEAMS: dict[str, list[tuple[str, str]]] = {
    "经营分析部": [("lin.xiao", "leader"), ("he.yu", "member")],
    "销售运营部": [("liu.yang", "leader"), ("chen.jing", "member"), ("wang.lei", "member")],
    "财务部": [("zhao.min", "leader"), ("sun.hao", "member")],
    "商品运营部": [("zhou.qiang", "leader"), ("wu.xia", "member")],
}

# table -> (leader columns, member columns or None for "members cannot see the table", masks)
Grant = tuple[Any, Any, dict[str, str]]
GRANTS: dict[str, dict[str, Grant]] = {
    "经营分析部": {
        "customers": (ALL, ALL, {"phone": "phone"}),
        "products": (ALL, ALL, {}),
        "orders": (ALL, ALL, {}),
        "order_items": (ALL, ALL, {}),
        "payments": (ALL, None, {"bank_account": "full"}),
        "after_sales": (ALL, ALL, {}),
        "sales_targets": (ALL, ALL, {}),
    },
    "销售运营部": {
        "customers": ("-email", "-email,-phone,-birth_date", {"phone": "phone"}),
        "products": ("-cost_price,-supplier", "-cost_price,-supplier", {}),
        "orders": (ALL, ALL, {}),
        "order_items": ("-line_cost", "-line_cost", {}),
        "after_sales": (ALL, ALL, {}),
        "sales_targets": (ALL, None, {}),
    },
    "财务部": {
        "customers": (
            ["customer_id", "customer_name", "region", "member_level"],
            ["customer_id", "region", "member_level"],
            {},
        ),
        "products": (
            ["product_id", "product_name", "category_l1", "unit_price", "cost_price"],
            ["product_id", "product_name", "category_l1", "unit_price"],
            {},
        ),
        "orders": (ALL, ALL, {}),
        "order_items": (ALL, ALL, {}),
        "payments": (ALL, ALL, {"bank_account": "full"}),
        "after_sales": (ALL, ALL, {}),
        "sales_targets": (ALL, None, {}),
    },
    "商品运营部": {
        "customers": (
            ["customer_id", "region", "member_level", "register_channel"],
            ["customer_id", "region", "member_level"],
            {},
        ),
        "products": (ALL, "-cost_price,-supplier", {}),
        "orders": (ALL, ALL, {}),
        "order_items": (ALL, "-line_cost", {}),
        "after_sales": (ALL, ALL, {}),
    },
}

SEMANTICS: list[tuple[str, str, list[str], str]] = [
    (
        "orders",
        "pay_amount",
        ["销售额", "成交额", "GMV", "实付金额"],
        "订单实付金额（元）。口径上的销售额只统计状态为已支付、已发货、已完成的订单，不含待支付、已取消、已退款。",
    ),
    (
        "orders",
        "order_status",
        ["订单状态", "单据状态"],
        "待支付、已支付、已发货、已完成、已取消、已退款。有效订单指已支付、已发货、已完成。",
    ),
    (
        "orders",
        "channel",
        ["渠道", "下单渠道", "销售渠道"],
        "订单来自的渠道：小程序、APP、官网、线下门店、直播。",
    ),
    ("orders", "order_date", ["下单日期", "成交日期", "订单日期"], "订单创建的日期，按自然日。"),
    (
        "orders",
        "region",
        ["大区", "区域", "收货大区"],
        "收货所在大区：华东、华北、华南、华中、西南、西北、东北。",
    ),
    ("orders", "is_first_order", ["首单", "新客订单"], "客户的第一笔有效订单。"),
    (
        "orders",
        "discount_amount",
        ["优惠券", "优惠金额", "券抵扣"],
        "优惠券抵扣金额，不含商品折扣。",
    ),
    (
        "customers",
        "member_level",
        ["会员等级", "会员级别", "等级"],
        "普通、银卡、金卡、钻石，从低到高。",
    ),
    ("customers", "register_channel", ["注册渠道", "拉新渠道"], "客户首次注册的渠道。"),
    ("customers", "is_active", ["活跃客户", "是否活跃"], "近一年有登录的客户为活跃。"),
    (
        "customers",
        "total_amount",
        ["累计消费", "客户贡献", "消费总额"],
        "客户累计的有效订单实付金额（元）。",
    ),
    (
        "products",
        "category_l1",
        ["品类", "一级类目", "大类"],
        "数码家电、家居生活、美妆个护、食品饮料、服饰鞋包。",
    ),
    (
        "products",
        "stock_qty",
        ["库存", "库存件数"],
        "当前库存件数；低于安全库存的在售商品需要补货。",
    ),
    ("products", "status", ["商品状态", "上下架"], "在售、下架、预售。"),
    (
        "order_items",
        "line_amount",
        ["商品销售额", "明细金额"],
        "订单明细行金额（元），已含商品折扣，不含优惠券和运费。",
    ),
    ("order_items", "discount_rate", ["折扣", "折扣率"], "0.9 表示九折，1 表示原价。"),
    (
        "payments",
        "pay_status",
        ["支付状态", "支付结果"],
        "成功、失败、已退款。失败的是支付重试，不算收入。",
    ),
    (
        "payments",
        "settle_status",
        ["结算状态", "对账状态"],
        "已结算、待结算、结算异常、不适用（支付失败）。",
    ),
    ("payments", "fee_amount", ["手续费", "支付手续费"], "支付渠道收取的手续费（元）。"),
    (
        "after_sales",
        "ticket_type",
        ["售后类型", "工单类型"],
        "退款、退货、换货、维修、投诉、咨询。咨询不涉及退换。",
    ),
    (
        "after_sales",
        "satisfaction",
        ["满意度", "客户满意度"],
        "1 到 5 分，只有已解决的工单有评分。",
    ),
    (
        "sales_targets",
        "target_amount",
        ["销售目标", "目标额"],
        "按大区、一级类目、季度设定的销售额目标（元）。",
    ),
]

PLATFORM_METRICS: list[tuple[str, list[str], str, str]] = [
    (
        "销售额",
        ["GMV", "成交额"],
        "有效订单（已支付、已发货、已完成）的实付金额合计，单位元。",
        f"SELECT SUM(pay_amount) AS sales_amount FROM {SCHEMA}.orders WHERE order_status IN {VALID}",
    ),
    (
        "订单数",
        ["有效订单数"],
        "有效订单（已支付、已发货、已完成）的数量。",
        f"SELECT COUNT(*) AS order_count FROM {SCHEMA}.orders WHERE order_status IN {VALID}",
    ),
    (
        "客单价",
        ["平均订单金额"],
        "有效订单实付金额合计 ÷ 有效订单数，单位元。",
        f"SELECT SUM(pay_amount) / COUNT(*) AS avg_order_value FROM {SCHEMA}.orders WHERE order_status IN {VALID}",
    ),
    (
        "毛利率",
        ["毛利"],
        "(商品行金额合计 - 商品行成本合计) ÷ 商品行金额合计，只算有效订单。",
        f"SELECT 1 - SUM(i.line_cost) / SUM(i.line_amount) AS gross_margin FROM {SCHEMA}.order_items i "
        f"JOIN {SCHEMA}.orders o ON o.order_id = i.order_id WHERE o.order_status IN {VALID}",
    ),
    (
        "复购率",
        ["回购率"],
        "有效订单数不少于 2 的客户数 ÷ 有过有效订单的客户数。",
        f"SELECT COUNT(*) FILTER (WHERE c >= 2)::numeric / COUNT(*) AS repurchase_rate FROM "
        f"(SELECT customer_id, COUNT(*) AS c FROM {SCHEMA}.orders WHERE order_status IN {VALID} GROUP BY customer_id) t",
    ),
    (
        "售后率",
        ["售后占比"],
        "发生过退款、退货、换货、维修或投诉的订单数 ÷ 全部订单数，咨询不计。",
        f"SELECT COUNT(DISTINCT order_id)::numeric / (SELECT COUNT(*) FROM {SCHEMA}.orders) AS aftersale_rate "
        f"FROM {SCHEMA}.after_sales WHERE ticket_type <> '咨询'",
    ),
]
TEAM_METRICS: dict[str, list[tuple[str, list[str], str, str]]] = {
    "财务部": [
        (
            "支付退款率",
            ["退款率"],
            "已退款金额合计 ÷ 成功和已退款支付的金额合计。",
            f"SELECT SUM(refund_amount) / SUM(pay_amount) AS refund_rate FROM {SCHEMA}.payments "
            "WHERE pay_status IN ('成功','已退款')",
        ),
        (
            "手续费率",
            ["渠道费率"],
            "手续费合计 ÷ 成功和已退款支付的金额合计。",
            f"SELECT SUM(fee_amount) / SUM(pay_amount) AS fee_rate FROM {SCHEMA}.payments "
            "WHERE pay_status IN ('成功','已退款')",
        ),
    ],
    "经营分析部": [
        (
            "目标达成率",
            ["达成率"],
            "已确认季度目标的销售额达成：有效订单商品行金额 ÷ 已确认目标金额（按全年累计）。",
            f"SELECT (SELECT SUM(i.line_amount) FROM {SCHEMA}.order_items i JOIN {SCHEMA}.orders o ON o.order_id = i.order_id "
            f"WHERE o.order_status IN {VALID}) / (SELECT SUM(target_amount) FROM {SCHEMA}.sales_targets WHERE status = '已确认') "
            "AS achievement_rate",
        ),
    ],
    "商品运营部": [
        (
            "缺货风险商品数",
            ["低库存商品数"],
            "在售商品中库存低于安全库存的 SKU 数。",
            f"SELECT COUNT(*) AS low_stock_skus FROM {SCHEMA}.products WHERE status = '在售' AND stock_qty < safety_stock",
        ),
    ],
}


def resolve(spec: Any, all_columns: list[str]) -> list[str]:
    if spec == ALL:
        return list(all_columns)
    if isinstance(spec, list):
        return list(spec)
    banned = {part.strip().lstrip("-") for part in str(spec).split(",")}
    return [c for c in all_columns if c not in banned]


def load_data(container: str, reader_password: str) -> None:
    pg = ImportAdmin(container)
    pg.run(build_sql())
    pg.run(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'shop_reader') THEN "
        "CREATE ROLE shop_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END $$;\n"
        f"ALTER ROLE shop_reader PASSWORD '{reader_password}';\n"
        "GRANT CONNECT ON DATABASE askdata_import TO shop_reader;\n"
        f'GRANT USAGE ON SCHEMA "{SCHEMA}" TO shop_reader;\n'
        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{SCHEMA}" TO shop_reader;\n'
    )


def provision_org(admin: Api) -> tuple[dict[str, int], dict[str, Api], dict[str, str]]:
    teams = {name: admin.call("POST", "/api/org/teams", {"name": name})["id"] for name in TEAMS}
    sessions: dict[str, Api] = {}
    passwords: dict[str, str] = {}
    for team, people in TEAMS.items():
        for username, role in people:
            password = secrets.token_urlsafe(18)
            admin.call(
                "POST",
                "/api/org/users",
                {"username": username, "password": password, "role": role, "team_id": teams[team]},
            )
            sessions[username] = admin.login(username, password)
            passwords[username] = password
    return teams, sessions, passwords


def apply_grants(admin: Api, datasource: int, teams: dict[str, int]) -> None:
    catalog = {t["name"]: t for t in admin.call("GET", f"/api/schema/{datasource}/tables")}
    for team, tables in GRANTS.items():
        writes = []
        for table_name, (leader_spec, member_spec, masks) in tables.items():
            table = catalog[table_name]
            names = [c["name"] for c in table["columns"]]
            leader_cols = resolve(leader_spec, names)
            member_cols = [] if member_spec is None else resolve(member_spec, names)
            writes.append(
                {
                    "table_id": table["id"],
                    "member_visible": bool(member_cols),
                    "columns": [
                        {
                            "column_id": column["id"],
                            "member_visible": column["name"] in member_cols,
                            "mask_rule": (
                                {"kind": masks[column["name"]]} if column["name"] in masks else None
                            ),
                        }
                        for column in table["columns"]
                        if column["name"] in leader_cols
                    ],
                }
            )
        admin.call("PUT", f"/api/permissions/{datasource}/teams/{teams[team]}", {"tables": writes})


def provision_knowledge(admin: Api, sessions: dict[str, Api], datasource: int) -> tuple[int, int]:
    catalog = {t["name"]: t for t in admin.call("GET", f"/api/schema/{datasource}/tables")}
    for table_name, column, aliases, description in SEMANTICS:
        column_id = next(c["id"] for c in catalog[table_name]["columns"] if c["name"] == column)
        admin.call(
            "POST",
            "/api/semantic",
            {
                "datasource_id": datasource,
                "scope": "platform",
                "target_type": "column",
                "target_id": column_id,
                "aliases": aliases,
                "description": description,
            },
        )
    metrics = 0
    for name, aliases, description, sql in PLATFORM_METRICS:
        admin.call(
            "POST",
            "/api/metric",
            {
                "datasource_id": datasource,
                "scope": "platform",
                "name": name,
                "aliases": aliases,
                "description": description,
                "sql_template": sql,
            },
        )
        metrics += 1
    for team, items in TEAM_METRICS.items():
        leader = TEAMS[team][0][0]
        for name, aliases, description, sql in items:
            sessions[leader].call(
                "POST",
                "/api/metric",
                {
                    "datasource_id": datasource,
                    "scope": "team",
                    "name": name,
                    "aliases": aliases,
                    "description": description,
                    "sql_template": sql,
                },
            )
            metrics += 1
    return len(SEMANTICS), metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--import-container", default="askdata-pg-import-1")
    parser.add_argument(
        "--accounts-file", type=Path, default=ROOT / "deploy" / ".env.demo-accounts"
    )
    args = parser.parse_args()
    username = os.environ.get("ASKDATA_PROVISION_USERNAME")
    password = os.environ.get("ASKDATA_PROVISION_PASSWORD")
    if not username or not password:
        raise SystemExit("ASKDATA_PROVISION_USERNAME and ASKDATA_PROVISION_PASSWORD are required")

    admin = Api(args.api).login(username, password)
    if admin.call("GET", "/api/auth/me")["role"] != "admin":
        raise SystemExit("the provisioning account must be an admin")
    if admin.call("GET", "/api/datasources?page_size=100")["items"]:
        raise SystemExit("the stack already has datasources; clear business state first")

    reader_password = secrets.token_urlsafe(24)
    load_data(args.import_container, reader_password)
    teams, sessions, passwords = provision_org(admin)
    datasource = admin.call(
        "POST",
        "/api/datasources",
        {
            "name": "星河优选-经营库",
            "type": "postgresql",
            "host": "pg-import",
            "port": 5432,
            "database": "askdata_import",
            "username": "shop_reader",
            "password": reader_password,
            "schema_name": SCHEMA,
            "tls": False,
        },
    )["id"]
    wait_task(admin, admin.call("POST", f"/api/schema/{datasource}/sync")["id"])
    apply_grants(admin, datasource, teams)
    semantics, metrics = provision_knowledge(admin, sessions, datasource)

    accounts = {
        "datasource_id": datasource,
        "teams": teams,
        "users": {
            name: {"password": passwords[name], "role": role, "team": team}
            for team, people in TEAMS.items()
            for name, role in people
        },
    }
    args.accounts_file.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    args.accounts_file.chmod(0o600)
    sys.stdout.write(
        json.dumps(
            {
                "tables": table_rows(),
                "columns": {name: len(cols) for name, (_, cols) in TABLES.items()},
                "teams": len(teams),
                "users": len(passwords),
                "semantics": semantics,
                "metrics": metrics,
                "accounts_file": str(args.accounts_file),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
