#!/usr/bin/env python3
"""菜品售卖时间 + 规格/附加选择的纯逻辑测试（不需要数据库、不需要起服务）。

覆盖最容易出错的地方：
  * 时间窗的边界（左闭右开）、跨午夜、多段、无配置=全天；
  * next_open_at 的下一次开店时刻；
  * 规格必选/单选/多选上限、未知分组/未知选项、已停用过滤；
  * **价格只认服务端**：加价由选项 id 查出来的 delta 求和。
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from product_options import (  # noqa: E402
    OptionError,
    active_groups,
    default_selections,
    resolve_selections,
    serialize_groups,
)
from sale_window import Window, describe, is_on_sale, next_open_at  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


# --- 测试替身：只需要属性，不需要真的 ORM ------------------------------------

class FakeOption:
    def __init__(self, oid, name, delta=0, active=True, is_default=False):
        self.id, self.name = oid, name
        self.price_delta_minor, self.active, self.is_default = delta, active, is_default


class FakeGroup:
    def __init__(self, gid, name, kind="SPEC", required=False, multi=False, max_select=None,
                 active=True, options=()):
        self.id, self.name, self.kind = gid, name, kind
        self.required, self.multi_select, self.max_select = required, multi, max_select
        self.active, self.options = active, list(options)


def test_sale_window() -> None:
    print("\n[1] 售卖时间判断")
    check("没有配置 = 全天可售", is_on_sale([], time(3, 0)) is True)
    check("没有配置 = 描述为空", describe([]) == [])

    w = [Window(time(8, 0), time(20, 0))]
    check("08:00 开始（左闭）", is_on_sale(w, time(8, 0)) is True)
    check("07:59 不能下单", is_on_sale(w, time(7, 59)) is False)
    check("19:59 可以下单", is_on_sale(w, time(19, 59)) is True)
    check("20:00 结束（右开）", is_on_sale(w, time(20, 0)) is False)

    # 跨午夜：20:00 - 02:00
    night = [Window(time(20, 0), time(2, 0))]
    check("跨午夜 23:00 可售", is_on_sale(night, time(23, 0)) is True)
    check("跨午夜 01:00 可售", is_on_sale(night, time(1, 0)) is True)
    check("跨午夜 03:00 不可售", is_on_sale(night, time(3, 0)) is False)
    check("跨午夜 19:59 不可售", is_on_sale(night, time(19, 59)) is False)

    # 多段：早餐 + 晚餐
    multi = [Window(time(7, 0), time(10, 0)), Window(time(17, 0), time(21, 0))]
    check("多段：09:00 可售", is_on_sale(multi, time(9, 0)) is True)
    check("多段：13:00 不可售", is_on_sale(multi, time(13, 0)) is False)
    check("多段：18:00 可售", is_on_sale(multi, time(18, 0)) is True)
    check("描述里标出跨午夜",
          describe([Window(time(20, 0), time(2, 0))])[0]["overnight"] is True)

    now = datetime(2026, 5, 1, 6, 0)
    nxt = next_open_at(w, now)
    check("06:00 的下次开店是当天 08:00", nxt == datetime(2026, 5, 1, 8, 0), str(nxt))
    check("当前可售时 next_open_at 返回当前时刻",
          next_open_at(w, datetime(2026, 5, 1, 9, 0)) == datetime(2026, 5, 1, 9, 0))
    check("错过今天则顺延到明天",
          next_open_at(w, datetime(2026, 5, 1, 21, 0)) == datetime(2026, 5, 2, 8, 0))
    check("无配置时 next_open_at 为 None（永远可售）", next_open_at([], now) is None)

    late = next_open_at([Window(time(20, 0), time(2, 0))], datetime(2026, 5, 1, 4, 0))
    check("跨午夜窗口的下次开店是当天 20:00", late == datetime(2026, 5, 1, 20, 0), str(late))


def test_options() -> None:
    print("\n[2] 规格 / 附加选择")
    size = FakeGroup(1, {"en": "Size"}, "SPEC", required=True, options=[
        FakeOption(11, {"en": "Medium"}, 0, is_default=True),
        FakeOption(12, {"en": "Large"}, 200),
    ])
    addon = FakeGroup(2, {"en": "Toppings"}, "ADDON", multi=True, max_select=2, options=[
        FakeOption(21, {"en": "Pearl"}, 100),
        FakeOption(22, {"en": "Coconut jelly"}, 150),
        FakeOption(24, {"en": "Grass jelly"}, 50),
        FakeOption(23, {"en": "Sold out"}, 0, active=False),
    ])
    disabled = FakeGroup(3, {"en": "Old group"}, "SPEC", active=False, options=[
        FakeOption(31, {"en": "X"}, 0)])

    groups = [size, addon, disabled]
    check("停用的分组不展示", [g["id"] for g in serialize_groups(groups)] == [1, 2])
    check("停用的选项不展示",
          [o["id"] for o in serialize_groups(groups)[1]["options"]] == [21, 22, 24])
    check("单选组 max_select = 1", serialize_groups(groups)[0]["max_select"] == 1)
    check("多选组带上限", serialize_groups(groups)[1]["max_select"] == 2)

    delta, snap = resolve_selections(groups, [{"group_id": 1, "option_id": 12}])
    check("大杯加价 200", delta == 200, str(delta))
    check("快照记录了名称与加价",
          snap[0]["price_delta_minor"] == 200 and snap[0]["option_name"]["en"] == "Large")

    delta, _ = resolve_selections(groups, [{"group_id": 1, "option_id": 11},
                                           {"group_id": 2, "option_id": 21},
                                           {"group_id": 2, "option_id": 22}])
    check("组合加价 = 0 + 100 + 150", delta == 250, str(delta))

    def expect_error(name, selections, code):
        try:
            resolve_selections(groups, selections)
        except OptionError as exc:
            check(name, exc.code == code, f"{exc.code}: {exc.message}")
        else:
            check(name, False, "没有报错")

    expect_error("必选规格没选 -> OPTION_REQUIRED_MISSING", [], "OPTION_REQUIRED_MISSING")
    expect_error("单选组选了两个 -> OPTION_TOO_MANY",
                 [{"group_id": 1, "option_id": 11}, {"group_id": 1, "option_id": 12}],
                 "OPTION_TOO_MANY")
    expect_error("多选超过上限 -> OPTION_TOO_MANY",
                 [{"group_id": 1, "option_id": 11}, {"group_id": 2, "option_id": 21},
                  {"group_id": 2, "option_id": 22}, {"group_id": 2, "option_id": 24}],
                 "OPTION_TOO_MANY")
    expect_error("同一个选项重复提交 -> OPTION_DUPLICATE",
                 [{"group_id": 2, "option_id": 21}, {"group_id": 2, "option_id": 21}],
                 "OPTION_DUPLICATE")
    expect_error("伪造其他菜品的分组 -> OPTION_UNKNOWN_GROUP",
                 [{"group_id": 999, "option_id": 11}], "OPTION_UNKNOWN_GROUP")
    expect_error("伪造不存在的选项 -> OPTION_UNKNOWN",
                 [{"group_id": 1, "option_id": 99}], "OPTION_UNKNOWN")
    expect_error("选择已停用的选项 -> OPTION_UNKNOWN",
                 [{"group_id": 2, "option_id": 23}], "OPTION_UNKNOWN")
    expect_error("选择已停用的分组 -> OPTION_UNKNOWN_GROUP",
                 [{"group_id": 3, "option_id": 31}], "OPTION_UNKNOWN_GROUP")
    expect_error("请求体结构不对 -> OPTION_BAD_PAYLOAD",
                 [{"group_id": "x"}], "OPTION_BAD_PAYLOAD")

    class Obj:
        def __init__(self, group_id, option_id):
            self.group_id, self.option_id = group_id, option_id

    delta, _ = resolve_selections(groups, [Obj(1, 12), Obj(2, 21)])
    check("也接受对象形式的选择（API 层传的是 pydantic 模型）", delta == 300, str(delta))

    check("没有可选内容的空组被忽略",
          active_groups([FakeGroup(9, {"en": "Empty"}, options=[])]) == [])

    defaults = default_selections(groups)
    check("默认选择只包含启用项",
          all(s["option_id"] in (11, 21, 22) for s in defaults), str(defaults))
    delta, _ = resolve_selections(groups, defaults)
    check("按默认选择算出的加价可用", delta >= 0, str(delta))


def main() -> None:
    test_sale_window()
    test_options()
    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 60)
    print(f"菜品用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL PRODUCT TESTS PASSED")


if __name__ == "__main__":
    main()
