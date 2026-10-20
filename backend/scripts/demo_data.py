"""Deterministic demo data for the 星河优选 (new-retail) business scenario.

Seven related tables in schema `shop`; every table has 10-20 columns and 100-150 rows. The data is
synthetic (names, phone numbers and bank accounts are made up) but internally consistent: order
amounts equal the sum of their items, payments match paid orders, regional targets are scaled to
what the orders actually sold.

`build_sql()` returns the full DDL + INSERT script; `TABLE_ROWS` tells how many rows each table got.
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from typing import Any

SCHEMA = "shop"
TODAY = date(2026, 10, 8)

REGIONS: dict[str, list[tuple[str, str]]] = {
    "华东": [
        ("上海", "上海"),
        ("江苏", "南京"),
        ("江苏", "苏州"),
        ("浙江", "杭州"),
        ("浙江", "宁波"),
    ],
    "华北": [("北京", "北京"), ("天津", "天津"), ("河北", "石家庄"), ("山西", "太原")],
    "华南": [("广东", "广州"), ("广东", "深圳"), ("福建", "厦门"), ("广西", "南宁")],
    "华中": [("湖北", "武汉"), ("湖南", "长沙"), ("河南", "郑州")],
    "西南": [("四川", "成都"), ("重庆", "重庆"), ("云南", "昆明")],
    "西北": [("陕西", "西安"), ("甘肃", "兰州")],
    "东北": [("辽宁", "沈阳"), ("黑龙江", "哈尔滨")],
}
REGION_WEIGHT = {"华东": 30, "华北": 18, "华南": 20, "华中": 10, "西南": 10, "西北": 6, "东北": 6}
REGION_OWNER = {
    "华东": "刘洋",
    "华北": "陈静",
    "华南": "王磊",
    "华中": "赵敏",
    "西南": "周强",
    "西北": "吴霞",
    "东北": "孙浩",
}
CHANNELS = ["小程序", "APP", "官网", "线下门店", "直播"]
LEVELS = ["普通", "银卡", "金卡", "钻石"]
SURNAMES = list(
    "王李张刘陈杨黄赵周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董潘袁蔡蒋余于杜叶程魏苏吕丁"
)
GIVEN_F = ["婷", "静", "敏", "雪", "丽", "芳", "娜", "琳", "颖", "洁", "欣", "怡", "佳", "宁", "萱"]
GIVEN_M = ["伟", "强", "磊", "军", "洋", "勇", "杰", "涛", "明", "超", "刚", "浩", "鹏", "斌", "宇"]

BRANDS = ["星河", "云舒", "木语", "澄光", "山野", "拾味", "极客", "悦己", "北辰", "暖屿"]
CATALOG: dict[str, dict[str, list[tuple[str, int, int]]]] = {
    "数码家电": {
        "手机": [
            ("5G 智能手机 128G", 2699, 1900),
            ("5G 智能手机 256G", 3299, 2350),
            ("折叠屏手机", 6999, 5100),
        ],
        "耳机": [("降噪蓝牙耳机", 799, 420), ("运动无线耳机", 349, 170), ("头戴式耳机", 1299, 720)],
        "电脑平板": [
            ("轻薄笔记本 14 英寸", 4999, 3700),
            ("学习平板 11 英寸", 2199, 1500),
            ("智能手表", 1299, 780),
        ],
    },
    "家居生活": {
        "床品": [("纯棉四件套", 459, 210), ("乳胶枕", 259, 110), ("羽绒被", 899, 480)],
        "厨房": [("不粘炒锅", 199, 85), ("空气炸锅", 399, 210), ("电饭煲", 599, 330)],
        "收纳灯具": [("折叠收纳箱", 59, 22), ("护眼台灯", 189, 90)],
    },
    "美妆个护": {
        "护肤": [("保湿面霜", 328, 96), ("防晒乳 SPF50", 168, 52), ("洁面泡沫", 89, 28)],
        "彩妆": [("哑光口红", 199, 58), ("气垫粉底", 259, 80)],
        "洗护": [("氨基酸洗发水", 98, 33), ("电动牙刷", 299, 130), ("身体乳", 79, 26)],
    },
    "食品饮料": {
        "零食坚果": [("每日坚果礼盒", 129, 62), ("海苔脆片", 25, 10), ("手工牛轧糖", 49, 21)],
        "饮品": [("挂耳咖啡 20 包", 79, 36), ("乌龙茶礼盒", 168, 78), ("气泡水 24 瓶", 69, 33)],
        "粮油": [("五常大米 10kg", 129, 82), ("山茶油 1L", 158, 92), ("蜂蜜 500g", 69, 34)],
    },
    "服饰鞋包": {
        "上装": [("纯棉 T 恤", 99, 38), ("连帽卫衣", 229, 95), ("轻薄羽绒服", 599, 290)],
        "鞋靴": [("缓震跑鞋", 459, 210), ("休闲板鞋", 329, 150), ("帆布鞋", 169, 70)],
        "箱包": [("通勤双肩包", 259, 110), ("帆布托特包", 129, 48)],
    },
}
SUPPLIERS = [
    "深圳启明供应链",
    "杭州云仓贸易",
    "苏州木语工厂",
    "广州澄光日化",
    "成都山野食品",
    "北京极客科技",
]
WAREHOUSE = {
    "华东": "华东仓",
    "华北": "华北仓",
    "华南": "华南仓",
    "华中": "华南仓",
    "西南": "西南仓",
    "西北": "华北仓",
    "东北": "华北仓",
}
PAY_METHODS = ["微信支付", "支付宝", "银行卡", "信用卡", "货到付款"]
PAY_CHANNELS = {
    "微信支付": "微信",
    "支付宝": "支付宝",
    "银行卡": "银联",
    "信用卡": "银联",
    "货到付款": "线下",
}
FEE_RATE = {"微信": 0.006, "支付宝": 0.006, "银联": 0.0038, "线下": 0.0}
REASONS = ["质量问题", "发错货", "不喜欢", "物流破损", "与描述不符", "尺寸不合适", "其他"]
HANDLERS = ["李婷", "张雪", "黄超", "郭宁", "何佳"]


def _pick(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _q(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(value, date):
        return "'" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


class Dataset:
    def __init__(self, seed: int = 20261008) -> None:
        rng = random.Random(seed)
        self.rng = rng
        self.customers = self._customers(rng)
        self.products = self._products(rng)
        self.orders, self.items = self._orders(rng)
        self.payments = self._payments(rng)
        self._customer_rollup()
        self.after_sales = self._after_sales(rng)
        self.targets = self._targets(rng)

    # --- customers -------------------------------------------------------------------------
    def _customers(self, rng: random.Random) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        used: set[str] = set()
        for cid in range(1, 121):
            gender = "女" if rng.random() < 0.58 else "男"
            while True:
                name = rng.choice(SURNAMES) + rng.choice(GIVEN_F if gender == "女" else GIVEN_M)
                if rng.random() < 0.4:
                    name += rng.choice(GIVEN_F if gender == "女" else GIVEN_M)
                if name not in used:
                    used.add(name)
                    break
            region = _pick(rng, REGION_WEIGHT)
            province, city = rng.choice(REGIONS[region])
            registered = date(2024, 6, 1) + timedelta(days=rng.randint(0, 850))
            level = rng.choices(LEVELS, weights=[55, 25, 14, 6])[0]
            rows.append(
                {
                    "customer_id": cid,
                    "customer_name": name,
                    "gender": gender,
                    "birth_date": date(1975, 1, 1) + timedelta(days=rng.randint(0, 365 * 28)),
                    "province": province,
                    "city": city,
                    "region": region,
                    "register_date": registered,
                    "register_channel": rng.choices(CHANNELS, weights=[35, 28, 10, 15, 12])[0],
                    "member_level": level,
                    "phone": f"1{rng.choice([3, 5, 8, 9])}{rng.randint(10**8, 10**9 - 1)}",
                    "email": f"user{cid:03d}@example.com" if rng.random() < 0.7 else None,
                    "points": rng.randint(0, 800) * (1 + LEVELS.index(level)),
                    "is_active": rng.random() < 0.9,
                    "first_order_date": None,
                    "last_order_date": None,
                    "total_orders": 0,
                    "total_amount": 0.0,
                }
            )
        return rows

    # --- products --------------------------------------------------------------------------
    def _products(self, rng: random.Random) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        pid = 0
        for level1, groups in CATALOG.items():
            for level2, items in groups.items():
                for title, price, cost in items:
                    pid += 1
                    brand = BRANDS[(pid * 3 + len(title)) % len(BRANDS)]
                    stock = rng.randint(0, 600)
                    rows.append(
                        {
                            "product_id": pid,
                            "sku": "",
                            "product_name": f"{brand}{title}",
                            "category_l1": level1,
                            "category_l2": level2,
                            "brand": brand,
                            "supplier": SUPPLIERS[pid % len(SUPPLIERS)],
                            "unit_price": float(price),
                            "cost_price": float(cost),
                            "stock_qty": stock,
                            "safety_stock": rng.choice([30, 50, 80, 100]),
                            "launch_date": date(2024, 1, 1) + timedelta(days=rng.randint(0, 900)),
                            "status": rng.choices(["在售", "下架", "预售"], weights=[84, 9, 7])[0],
                            "weight_kg": round(rng.uniform(0.1, 6.0), 2),
                            "rating": round(rng.uniform(3.6, 4.9), 1),
                            "is_imported": rng.random() < 0.15,
                        }
                    )
        originals = len(rows)
        while len(rows) < 100:
            # second colourway / pack size of an existing product to reach 100 SKUs
            base = rows[(len(rows) - originals) % originals]
            pid += 1
            rows.append(
                {
                    **base,
                    "product_id": pid,
                    "sku": f"SKU{10000 + pid * 7}",
                    "product_name": base["product_name"]
                    + rng.choice(["（限定色）", "（家庭装）", "（升级版）"]),
                    "unit_price": float(round(base["unit_price"] * rng.choice([1.0, 1.1, 1.2]))),
                    "stock_qty": rng.randint(0, 600),
                    "launch_date": date(2025, 1, 1) + timedelta(days=rng.randint(0, 600)),
                    "status": rng.choices(["在售", "下架", "预售"], weights=[84, 9, 7])[0],
                    "rating": round(rng.uniform(3.6, 4.9), 1),
                }
            )
        names: set[str] = set()
        for row in rows:
            row["sku"] = f"SKU{10000 + row['product_id'] * 7}"
            while row["product_name"] in names:
                row["product_name"] += "Plus"
            names.add(row["product_name"])
        return rows

    # --- orders and items ------------------------------------------------------------------
    def _orders(self, rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        orders: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        start = date(2026, 1, 5)
        span = (date(2026, 10, 5) - start).days
        double = set(rng.sample(range(1, 126), 25))
        for oid in range(1, 126):
            # busier later in the year, with a spike around 618 and 双11 预热
            day = int(span * rng.random() ** 0.65)
            when = start + timedelta(days=day)
            customer = self.customers[rng.randrange(len(self.customers))]
            if customer["register_date"] > when:
                when = customer["register_date"] + timedelta(days=rng.randint(0, 20))
                when = min(when, date(2026, 10, 5))
            stamp = datetime(
                when.year,
                when.month,
                when.day,
                rng.randint(8, 23),
                rng.randint(0, 59),
                rng.randint(0, 59),
            )
            age = (TODAY - when).days
            if age < 4:
                status = rng.choices(["待支付", "已支付", "已发货"], weights=[30, 40, 30])[0]
            elif age < 14:
                status = rng.choices(
                    ["待支付", "已支付", "已发货", "已完成", "已取消"], weights=[8, 12, 40, 30, 10]
                )[0]
            elif age < 30:
                status = rng.choices(["已发货", "已完成", "已取消"], weights=[12, 78, 10])[0]
            else:
                status = rng.choices(["已完成", "已取消", "已退款"], weights=[78, 10, 12])[0]
            channel = rng.choices(CHANNELS, weights=[35, 28, 10, 15, 12])[0]
            region = customer["region"]
            province, city = customer["province"], customer["city"]
            if rng.random() < 0.1:
                region = _pick(rng, REGION_WEIGHT)
                province, city = rng.choice(REGIONS[region])
            order_items: list[dict[str, Any]] = []
            for _ in range(2 if oid in double else 1):
                category = rng.choices(list(CATALOG), weights=[14, 22, 22, 21, 21])[0]
                product = rng.choice(
                    [
                        p
                        for p in self.products
                        if p["status"] != "下架" and p["category_l1"] == category
                    ]
                )
                qty = rng.choices([1, 2, 3], weights=[72, 20, 8])[0]
                rate = rng.choices([1.0, 0.95, 0.9, 0.85, 0.8], weights=[50, 18, 16, 10, 6])[0]
                amount = round(product["unit_price"] * qty * rate, 2)
                refunded = status == "已退款"
                order_items.append(
                    {
                        "order_id": oid,
                        "product_id": product["product_id"],
                        "quantity": qty,
                        "unit_price": product["unit_price"],
                        "discount_rate": rate,
                        "line_amount": amount,
                        "line_cost": round(product["cost_price"] * qty, 2),
                        "return_qty": qty if refunded else 0,
                        "ship_date": None
                        if status in ("待支付", "已支付", "已取消")
                        else when + timedelta(days=rng.randint(0, 2)),
                        "warehouse": WAREHOUSE[region],
                        "is_gift": False,
                    }
                )
            goods = round(sum(i["line_amount"] for i in order_items), 2)
            coupon = rng.choice([0, 0, 0, 10, 20, 30, 50]) if goods >= 150 else 0
            shipping = 0 if goods >= 99 else 8
            orders.append(
                {
                    "order_id": oid,
                    "customer_id": customer["customer_id"],
                    "order_date": when,
                    "created_at": stamp,
                    "order_status": status,
                    "channel": channel,
                    "region": region,
                    "province": province,
                    "city": city,
                    "item_count": sum(i["quantity"] for i in order_items),
                    "goods_amount": goods,
                    "discount_amount": float(coupon),
                    "shipping_fee": float(shipping),
                    "pay_amount": round(goods - coupon + shipping, 2),
                    "payment_method": rng.choices(PAY_METHODS, weights=[45, 33, 8, 10, 4])[0],
                    "is_first_order": False,
                    "sales_rep": REGION_OWNER[region],
                }
            )
            items.extend(order_items)
        orders.sort(key=lambda o: (o["order_date"], o["created_at"]))
        remap = {o["order_id"]: n for n, o in enumerate(orders, 1)}
        for o in orders:
            o["order_id"] = remap[o["order_id"]]
        items.sort(key=lambda i: remap[i["order_id"]])
        for n, i in enumerate(items, 1):
            i["order_id"] = remap[i["order_id"]]
            i["item_id"] = n
        # first order flag and customer rollups use valid orders only
        seen: set[int] = set()
        for o in orders:
            if o["order_status"] not in ("已取消", "待支付") and o["customer_id"] not in seen:
                seen.add(o["customer_id"])
                o["is_first_order"] = True
        return orders, items

    def _payments(self, rng: random.Random) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        accounts: dict[int, str] = {}
        for order in self.orders:
            status = order["order_status"]
            if status == "待支付":
                continue
            channel = PAY_CHANNELS[order["payment_method"]]
            failed_first = status == "已取消" or rng.random() < 0.06
            attempts = []
            if failed_first:
                attempts.append("失败")
            if status != "已取消":
                attempts.append("已退款" if status == "已退款" else "成功")
            for pay_status in attempts:
                paid_at = order["created_at"] + timedelta(minutes=rng.randint(1, 40))
                amount = order["pay_amount"]
                fee = round(amount * FEE_RATE[channel], 2) if pay_status != "失败" else 0.0
                settled = pay_status != "失败" and (TODAY - paid_at.date()).days > 3
                customer = order["customer_id"]
                accounts.setdefault(customer, f"6222{rng.randint(10**11, 10**12 - 1)}")
                rows.append(
                    {
                        "payment_id": len(rows) + 1,
                        "order_id": order["order_id"],
                        "pay_time": paid_at,
                        "pay_channel": channel,
                        "pay_status": pay_status,
                        "pay_amount": amount,
                        "fee_rate": FEE_RATE[channel],
                        "fee_amount": fee,
                        "settle_status": "不适用"
                        if pay_status == "失败"
                        else ("已结算" if settled else "待结算"),
                        "settle_date": paid_at.date() + timedelta(days=2) if settled else None,
                        "bank_account": accounts[customer] if channel == "银联" else None,
                        "transaction_no": f"TX{paid_at:%Y%m%d}{rng.randint(10**7, 10**8 - 1)}",
                        "refund_amount": amount if pay_status == "已退款" else 0.0,
                        "payer_customer_id": customer,
                    }
                )
        # a handful of abnormal settlements for finance questions
        for row in rng.sample([r for r in rows if r["settle_status"] == "已结算"], 5):
            row["settle_status"] = "结算异常"
        return rows

    def _customer_rollup(self) -> None:
        valid = [o for o in self.orders if o["order_status"] not in ("已取消", "待支付")]
        for customer in self.customers:
            mine = [o for o in valid if o["customer_id"] == customer["customer_id"]]
            if not mine:
                continue
            customer["first_order_date"] = min(o["order_date"] for o in mine)
            customer["last_order_date"] = max(o["order_date"] for o in mine)
            customer["total_orders"] = len(mine)
            customer["total_amount"] = round(sum(o["pay_amount"] for o in mine), 2)

    def _after_sales(self, rng: random.Random) -> list[dict[str, Any]]:
        item_by_id = {i["item_id"]: i for i in self.items}
        order_by_id = {o["order_id"]: o for o in self.orders}
        eligible = [
            i
            for i in self.items
            if order_by_id[i["order_id"]]["order_status"] in ("已完成", "已退款", "已发货")
        ]
        refunded = [i for i in eligible if order_by_id[i["order_id"]]["order_status"] == "已退款"]
        picks = list(refunded)
        rest = [i for i in eligible if i not in picks]
        picks += rng.sample(rest, min(len(rest), 100 - len(picks)))
        rows: list[dict[str, Any]] = []
        for item in picks[:100]:
            order = order_by_id[item["order_id"]]
            is_refund = order["order_status"] == "已退款"
            ticket_type = (
                "退款"
                if is_refund
                else rng.choices(
                    ["退货", "换货", "维修", "投诉", "咨询"], weights=[22, 18, 15, 15, 30]
                )[0]
            )
            created = order["order_date"] + timedelta(days=rng.randint(3, 20))
            created = min(created, TODAY - timedelta(days=1))
            status = rng.choices(
                ["已解决", "处理中", "待处理", "已关闭"], weights=[60, 18, 10, 12]
            )[0]
            resolved = (
                created + timedelta(days=rng.randint(1, 9))
                if status in ("已解决", "已关闭")
                else None
            )
            if resolved and resolved > TODAY:
                resolved = TODAY
            refund_amount = (
                item["line_amount"]
                if ticket_type in ("退款", "退货") and status != "已关闭"
                else 0.0
            )
            rows.append(
                {
                    "ticket_id": len(rows) + 1,
                    "order_id": item["order_id"],
                    "item_id": item["item_id"],
                    "customer_id": order["customer_id"],
                    "ticket_type": ticket_type,
                    "reason": "质量问题"
                    if is_refund and rng.random() < 0.5
                    else rng.choice(REASONS),
                    "status": status,
                    "priority": rng.choices(["高", "中", "低"], weights=[15, 50, 35])[0],
                    "channel": rng.choice(["电话", "在线客服", "小程序"]),
                    "created_date": created,
                    "resolved_date": resolved,
                    "refund_amount": refund_amount,
                    "handler": rng.choice(HANDLERS),
                    "satisfaction": rng.randint(1, 5) if status == "已解决" else None,
                }
            )
        del item_by_id
        return rows

    def _targets(self, rng: random.Random) -> list[dict[str, Any]]:
        product = {p["product_id"]: p for p in self.products}
        order = {o["order_id"]: o for o in self.orders}
        actual: dict[tuple[str, str, int], list[float]] = {}
        for item in self.items:
            o = order[item["order_id"]]
            if o["order_status"] in ("已取消", "待支付"):
                continue
            quarter = (o["order_date"].month - 1) // 3 + 1
            key = (o["region"], product[item["product_id"]]["category_l1"], quarter)
            slot = actual.setdefault(key, [0.0, 0.0])
            slot[0] += item["line_amount"]
            slot[1] += 1
        rows: list[dict[str, Any]] = []
        for region in REGIONS:
            for category in CATALOG:
                for quarter in (1, 2, 3):
                    amount, count = actual.get((region, category, quarter), [0.0, 0.0])
                    base = amount if amount else rng.choice([1500, 3000, 4500])
                    target = max(1000, round(base * rng.uniform(0.75, 1.45) / 100) * 100)
                    rows.append(
                        {
                            "target_id": len(rows) + 1,
                            "target_year": 2026,
                            "target_quarter": quarter,
                            "region": region,
                            "category_l1": category,
                            "target_amount": float(target),
                            "target_orders": max(2, round(count * rng.uniform(0.8, 1.4))),
                            "target_new_customers": rng.randint(1, 6),
                            "marketing_budget": float(
                                round(target * rng.uniform(0.04, 0.09) / 10) * 10
                            ),
                            "gross_margin_target": round(rng.uniform(0.28, 0.45), 2),
                            "owner": REGION_OWNER[region],
                            "status": "已确认" if quarter < 3 or rng.random() < 0.8 else "待确认",
                            "approved_date": date(2026, 1, 1)
                            + timedelta(days=(quarter - 1) * 90 - rng.randint(0, 20))
                            if quarter > 1
                            else date(2025, 12, 20) + timedelta(days=rng.randint(0, 8)),
                        }
                    )
        return rows


TABLES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "customers": (
        "客户档案，一行一个注册客户",
        [
            ("customer_id", "integer PRIMARY KEY", "客户编号"),
            ("customer_name", "text NOT NULL", "客户姓名"),
            ("gender", "text", "性别：男/女"),
            ("birth_date", "date", "出生日期"),
            ("province", "text", "所在省份"),
            ("city", "text", "所在城市"),
            ("region", "text", "销售大区：华东/华北/华南/华中/西南/西北/东北"),
            ("register_date", "date", "注册日期"),
            ("register_channel", "text", "注册渠道：小程序/APP/官网/线下门店/直播"),
            ("member_level", "text", "会员等级：普通/银卡/金卡/钻石"),
            ("phone", "text", "手机号（敏感）"),
            ("email", "text", "邮箱（敏感）"),
            ("points", "integer", "当前积分"),
            ("is_active", "boolean", "是否活跃客户（近一年有登录）"),
            ("first_order_date", "date", "首单日期，不含取消和待支付订单"),
            ("last_order_date", "date", "最近一单日期，不含取消和待支付订单"),
            ("total_orders", "integer", "累计有效订单数"),
            ("total_amount", "numeric(12,2)", "累计有效订单实付金额（元）"),
        ],
    ),
    "products": (
        "商品档案，一行一个 SKU",
        [
            ("product_id", "integer PRIMARY KEY", "商品编号"),
            ("sku", "text NOT NULL", "SKU 编码"),
            ("product_name", "text NOT NULL", "商品名称"),
            ("category_l1", "text", "一级类目：数码家电/家居生活/美妆个护/食品饮料/服饰鞋包"),
            ("category_l2", "text", "二级类目"),
            ("brand", "text", "品牌"),
            ("supplier", "text", "供应商"),
            ("unit_price", "numeric(10,2)", "标准售价（元）"),
            ("cost_price", "numeric(10,2)", "采购成本（元，敏感）"),
            ("stock_qty", "integer", "当前库存件数"),
            ("safety_stock", "integer", "安全库存件数"),
            ("launch_date", "date", "上架日期"),
            ("status", "text", "商品状态：在售/下架/预售"),
            ("weight_kg", "numeric(6,2)", "重量（公斤）"),
            ("rating", "numeric(2,1)", "用户评分，满分 5"),
            ("is_imported", "boolean", "是否进口商品"),
        ],
    ),
    "orders": (
        "销售订单，一行一个订单",
        [
            ("order_id", "integer PRIMARY KEY", "订单编号"),
            ("customer_id", "integer REFERENCES shop.customers(customer_id)", "下单客户"),
            ("order_date", "date", "下单日期"),
            ("created_at", "timestamp", "下单时间"),
            ("order_status", "text", "订单状态：待支付/已支付/已发货/已完成/已取消/已退款"),
            ("channel", "text", "下单渠道：小程序/APP/官网/线下门店/直播"),
            ("region", "text", "收货大区"),
            ("province", "text", "收货省份"),
            ("city", "text", "收货城市"),
            ("item_count", "integer", "商品件数"),
            ("goods_amount", "numeric(12,2)", "商品金额（已含商品折扣，元）"),
            ("discount_amount", "numeric(10,2)", "优惠券抵扣（元）"),
            ("shipping_fee", "numeric(8,2)", "运费（元）"),
            ("pay_amount", "numeric(12,2)", "实付金额 = 商品金额 - 优惠券 + 运费（元）"),
            ("payment_method", "text", "支付方式：微信支付/支付宝/银行卡/信用卡/货到付款"),
            ("is_first_order", "boolean", "是否客户首单"),
            ("sales_rep", "text", "负责该区域的销售"),
        ],
    ),
    "order_items": (
        "订单明细，一行一个订单商品行",
        [
            ("item_id", "integer PRIMARY KEY", "明细编号"),
            ("order_id", "integer REFERENCES shop.orders(order_id)", "所属订单"),
            ("product_id", "integer REFERENCES shop.products(product_id)", "商品"),
            ("quantity", "integer", "购买数量"),
            ("unit_price", "numeric(10,2)", "成交时标准单价（元）"),
            ("discount_rate", "numeric(3,2)", "商品折扣率，0.9 表示九折"),
            ("line_amount", "numeric(12,2)", "行金额 = 单价 × 数量 × 折扣率（元）"),
            ("line_cost", "numeric(12,2)", "行成本（元，敏感）"),
            ("return_qty", "integer", "退货数量"),
            ("ship_date", "date", "发货日期，未发货为空"),
            ("warehouse", "text", "发货仓：华东仓/华北仓/华南仓/西南仓"),
            ("is_gift", "boolean", "是否赠品"),
        ],
    ),
    "payments": (
        "支付流水，财务对账用；一笔订单可能有失败重试",
        [
            ("payment_id", "integer PRIMARY KEY", "支付流水号"),
            ("order_id", "integer REFERENCES shop.orders(order_id)", "对应订单"),
            ("payer_customer_id", "integer", "付款客户"),
            ("pay_time", "timestamp", "支付时间"),
            ("pay_channel", "text", "支付渠道：微信/支付宝/银联/线下"),
            ("pay_status", "text", "支付状态：成功/失败/已退款"),
            ("pay_amount", "numeric(12,2)", "支付金额（元）"),
            ("fee_rate", "numeric(6,4)", "渠道手续费率"),
            ("fee_amount", "numeric(10,2)", "手续费（元）"),
            ("settle_status", "text", "结算状态：已结算/待结算/结算异常/不适用"),
            ("settle_date", "date", "结算日期"),
            ("bank_account", "text", "银行账号（敏感）"),
            ("transaction_no", "text", "第三方交易号"),
            ("refund_amount", "numeric(12,2)", "已退款金额（元）"),
        ],
    ),
    "after_sales": (
        "售后工单，一行一张工单",
        [
            ("ticket_id", "integer PRIMARY KEY", "工单编号"),
            ("order_id", "integer REFERENCES shop.orders(order_id)", "关联订单"),
            ("item_id", "integer REFERENCES shop.order_items(item_id)", "关联订单明细"),
            ("customer_id", "integer REFERENCES shop.customers(customer_id)", "客户"),
            ("ticket_type", "text", "工单类型：退款/退货/换货/维修/投诉/咨询"),
            ("reason", "text", "原因：质量问题/发错货/不喜欢/物流破损/与描述不符/尺寸不合适/其他"),
            ("status", "text", "状态：待处理/处理中/已解决/已关闭"),
            ("priority", "text", "优先级：高/中/低"),
            ("channel", "text", "受理渠道：电话/在线客服/小程序"),
            ("created_date", "date", "创建日期"),
            ("resolved_date", "date", "解决日期"),
            ("refund_amount", "numeric(12,2)", "退款金额（元）"),
            ("handler", "text", "处理人"),
            ("satisfaction", "integer", "客户满意度 1-5，已解决工单才有"),
        ],
    ),
    "sales_targets": (
        "季度销售目标，按大区 × 一级类目",
        [
            ("target_id", "integer PRIMARY KEY", "目标编号"),
            ("target_year", "integer", "年份"),
            ("target_quarter", "integer", "季度 1-4"),
            ("region", "text", "销售大区"),
            ("category_l1", "text", "一级类目"),
            ("target_amount", "numeric(12,2)", "销售额目标（元）"),
            ("target_orders", "integer", "订单数目标"),
            ("target_new_customers", "integer", "新客数目标"),
            ("marketing_budget", "numeric(12,2)", "营销预算（元）"),
            ("gross_margin_target", "numeric(4,2)", "毛利率目标，0.35 表示 35%"),
            ("owner", "text", "区域负责人"),
            ("status", "text", "目标状态：已确认/待确认"),
            ("approved_date", "date", "批准日期"),
        ],
    ),
}


def build_sql(data: Dataset | None = None) -> str:
    data = data or Dataset()
    rows_of: dict[str, list[dict[str, Any]]] = {
        "customers": data.customers,
        "products": data.products,
        "orders": data.orders,
        "order_items": data.items,
        "payments": data.payments,
        "after_sales": data.after_sales,
        "sales_targets": data.targets,
    }
    out = [f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;", f"CREATE SCHEMA {SCHEMA};"]
    for name, (title, columns) in TABLES.items():
        body = ",\n  ".join(f"{col} {ddl}" for col, ddl, _ in columns)
        out.append(f"CREATE TABLE {SCHEMA}.{name} (\n  {body}\n);")
        out.append(f"COMMENT ON TABLE {SCHEMA}.{name} IS {_q(title)};")
        for col, _, comment in columns:
            out.append(f"COMMENT ON COLUMN {SCHEMA}.{name}.{col} IS {_q(comment)};")
    for name, (_, columns) in TABLES.items():
        names = [c for c, _, _ in columns]
        values = ",\n".join(
            "(" + ", ".join(_q(row.get(c)) for c in names) + ")" for row in rows_of[name]
        )
        out.append(f"INSERT INTO {SCHEMA}.{name} ({', '.join(names)}) VALUES\n{values};")
    return "\n".join(out) + "\n"


def table_rows() -> dict[str, int]:
    data = Dataset()
    return {
        "customers": len(data.customers),
        "products": len(data.products),
        "orders": len(data.orders),
        "order_items": len(data.items),
        "payments": len(data.payments),
        "after_sales": len(data.after_sales),
        "sales_targets": len(data.targets),
    }


if __name__ == "__main__":
    for table, count in table_rows().items():
        sys.stdout.write(f"{table}: {count} rows, {len(TABLES[table][1])} columns\n")
