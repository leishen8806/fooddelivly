#!/usr/bin/env python3
"""每日报表测试：统计口径 + **投递幂等**。

跑法（需要先把库迁到 head）：
    DATABASE_URL=postgresql+asyncpg://... python tests/daily_report_test.py

真发 Telegram 没法在本地验证，所以这里把发送层 `telegram_service._bot` 换成桩，
重点验证：
  * 统计口径是「店铺时区当天」的左闭右开区间（00:00:00 ~ 22:30:00）；
  * 同一天同一会话**只会成功发送一次**（重复调用返回 skipped）；
  * 发送失败时占位记录会被删掉，下一次还能补发（宁可迟到，不能漏发）；
  * `force=True` 才允许重发。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
os.environ.setdefault("JWT_SECRET", "test-only")

import daily_report  # noqa: E402
import telegram_service  # noqa: E402
from database import AsyncSessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

CHAT_ID = "-1001234567890"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


class StubSession:
    async def close(self) -> None:
        return None


class StubBot:
    """记录发出去的消息；`fail=True` 时模拟 Telegram 报错。"""

    sent: list[str] = []
    fail = False

    async def send_message(self, chat_id, text, **kwargs):
        if StubBot.fail:
            raise RuntimeError("telegram down")
        StubBot.sent.append(text)

        class Msg:
            message_id = len(StubBot.sent)
        return Msg()

    @property
    def session(self) -> StubSession:
        return StubSession()


async def main() -> None:
    telegram_service._bot = lambda: StubBot()  # type: ignore[assignment]

    report_date = date(2026, 1, 15)          # 用一个固定日期，避免和「今天」的数据混在一起
    other_date = report_date + timedelta(days=1)

    async with AsyncSessionLocal() as db:
        # 清掉这个日期的历史投递记录，保证可重复运行
        await db.execute(text("DELETE FROM report_deliveries WHERE report_date IN (:a, :b)"),
                         {"a": report_date, "b": other_date})
        # 自带店铺配置：空库（一键回归的场景）里没有 store_settings，
        # 没配员工群时调度器会正确地「不发送」，那样就测不到投递链路了。
        await db.execute(text(
            """INSERT INTO store_settings (currency, timezone, telegram_staff_group_id, staff_group_language)
               SELECT 'USD', 'Asia/Phnom_Penh', :chat, 'en'
                WHERE NOT EXISTS (SELECT 1 FROM store_settings)"""), {"chat": CHAT_ID})
        await db.commit()

        # 自带客户：这个测试可能在空库上跑（一键回归就是），不能依赖 seed
        await db.execute(text("DELETE FROM orders WHERE public_code LIKE 'RPT-TEST-%'"))
        await db.execute(text(
            """INSERT INTO customers (id, telegram_user_id, display_name, language_code, preferred_language)
               VALUES (9999, 'tg-report-test', 'Report Test', 'en', 'en')
               ON CONFLICT (id) DO NOTHING"""))
        await db.commit()

        # 造一条落在该日（店铺时区）内的订单
        tz = await daily_report.store_timezone(db)
        inside = datetime.combine(report_date, datetime.min.time(), tz) + timedelta(hours=12)
        await db.execute(
            text("""INSERT INTO orders (public_code, customer_id, room_number, order_status,
                                        payment_status, payment_method, currency, customer_language,
                                        total_minor, created_at, store_id)
                    VALUES ('RPT-TEST-1', 9999, 'T1', 'COMPLETED', 'PAID_CONFIRMED', 'MANUAL',
                            'USD', 'en', 2500, :ts,
                            (SELECT id FROM stores ORDER BY id LIMIT 1))"""),
            {"ts": inside.astimezone(timezone.utc)},
        )
        # 一条刚好在区间**外**的订单（次日 00:00:00），不能计入
        boundary = datetime.combine(other_date, datetime.min.time(), tz)
        await db.execute(
            text("""INSERT INTO orders (public_code, customer_id, room_number, order_status,
                                        payment_status, payment_method, currency, customer_language,
                                        total_minor, created_at, store_id)
                    VALUES ('RPT-TEST-2', 9999, 'T2', 'NEW', 'UNPAID', 'MANUAL',
                            'USD', 'en', 9900, :ts,
                            (SELECT id FROM stores ORDER BY id LIMIT 1))"""),
            {"ts": boundary.astimezone(timezone.utc)},
        )
        await db.commit()

        report = await daily_report.build_daily_report(db, report_date)
        check("区间内订单被统计", report["orders"] >= 1 and report["orders_total_minor"] >= 2500,
              f"orders={report['orders']} total={report['orders_total_minor']}")
        check("区间边界外的订单不计入（左闭右开）",
              report["orders_total_minor"] < 9900, str(report["orders_total_minor"]))

        text_out = daily_report.format_daily_report(report, "zh-CN")
        check("文案包含日期与金额", report_date.isoformat() in text_out and "25.00" in text_out,
              text_out.splitlines()[1] if len(text_out.splitlines()) > 1 else "")

        # ---- 幂等：第一次发，第二次跳过 ----
        StubBot.sent.clear()
        StubBot.fail = False
        first = await daily_report.send_daily_report(db, report_date, chat_id=CHAT_ID, language="zh-CN")
        check("首次发送成功", first["sent"] is True, str(first.get("skipped") or first.get("error")))
        check("确实只发了一条", len(StubBot.sent) == 1, str(len(StubBot.sent)))

        second = await daily_report.send_daily_report(db, report_date, chat_id=CHAT_ID, language="zh-CN")
        check("重复调用被跳过（不重复发）",
              second["sent"] is False and second.get("skipped") == "already_sent",
              str(second.get("skipped")))
        check("消息条数仍是 1", len(StubBot.sent) == 1, str(len(StubBot.sent)))

        status = await daily_report.daily_report_status(db, report_date, CHAT_ID)
        check("投递记录已回填", bool(status and status["message_id"]), str(status))

        # ---- 发送失败：占位记录要删掉，允许补发 ----
        fail_date = report_date - timedelta(days=3)
        await db.execute(text("DELETE FROM report_deliveries WHERE report_date = :d"), {"d": fail_date})
        await db.commit()
        StubBot.fail = True
        failed = await daily_report.send_daily_report(db, fail_date, chat_id=CHAT_ID)
        check("发送失败时返回 error", failed["sent"] is False and "error" in failed,
              str(failed.get("error"))[:50])
        check("失败后没有留下占位记录（下次还能补发）",
              await daily_report.daily_report_status(db, fail_date, CHAT_ID) is None)

        StubBot.fail = False
        retried = await daily_report.send_daily_report(db, fail_date, chat_id=CHAT_ID)
        check("补发成功", retried["sent"] is True, str(retried.get("error"))[:50])

        # ---- force：后台「立即发送」可以重发一次 ----
        StubBot.sent.clear()
        forced = await daily_report.send_daily_report(db, report_date, chat_id=CHAT_ID, force=True)
        check("force=True 允许重发", forced["sent"] is True)
        check("force 重发只发一条", len(StubBot.sent) == 1, str(len(StubBot.sent)))

        # ---- 调度器：到点才发，且只发一次 ----
        now_local = datetime.now(tz)
        today = now_local.date()
        await db.execute(text("DELETE FROM report_deliveries WHERE report_date = :d"), {"d": today})
        await db.commit()

        # 只让主店配群：否则库里第二家店也会投递，计数就不是 1 了
        await db.execute(text(
            "UPDATE stores SET telegram_staff_group_id = NULL WHERE code <> 'MAIN'"))
        await db.execute(text(
            "UPDATE stores SET telegram_staff_group_id = :c WHERE code = 'MAIN'"), {"c": CHAT_ID})
        await db.commit()
        os.environ["DAILY_REPORT_HOUR"] = str(now_local.hour)
        os.environ["DAILY_REPORT_MINUTE"] = str(now_local.minute)
        StubBot.sent.clear()
        await daily_report._tick(None)          # None = 用真实的 AsyncSessionLocal
        check("到点会发当天的报表", len(StubBot.sent) == 1, str(len(StubBot.sent)))
        if StubBot.sent:
            check("发的是「当天 00:00-22:30」", today.isoformat() in StubBot.sent[0],
                  StubBot.sent[0].splitlines()[1] if len(StubBot.sent[0].splitlines()) > 1 else "")
        await daily_report._tick(None)
        check("再触发一次不会重发", len(StubBot.sent) == 1, str(len(StubBot.sent)))

        os.environ["DAILY_REPORT_HOUR"] = str((now_local.hour + 1) % 24)
        os.environ["DAILY_REPORT_MINUTE"] = str(now_local.minute)
        StubBot.sent.clear()
        await daily_report._tick(None)
        check("非定时小时不发送", len(StubBot.sent) == 0, str(len(StubBot.sent)))
        os.environ["DAILY_REPORT_HOUR"] = str(now_local.hour)
        os.environ["DAILY_REPORT_MINUTE"] = str(now_local.minute)

        # 没配员工群时必须安静跳过（否则会往 None 发消息报错刷日志）
        # 群现在挂在**门店**上（旧表的字段不再被读取）
        await db.execute(text("UPDATE store_settings SET telegram_staff_group_id = NULL"))
        await db.execute(text(
            "UPDATE stores SET telegram_staff_group_id = NULL WHERE code <> 'MAIN'"))
        await db.execute(text(
            "UPDATE stores SET telegram_staff_group_id = NULL WHERE code = 'MAIN'"))
        await db.execute(text("DELETE FROM report_deliveries WHERE report_date = :d"), {"d": today})
        await db.commit()
        StubBot.sent.clear()
        await daily_report._tick(None)
        check("未配置员工群时不发送", len(StubBot.sent) == 0, str(len(StubBot.sent)))
        await db.execute(text("UPDATE store_settings SET telegram_staff_group_id = :c"), {"c": CHAT_ID})
        await db.execute(text("UPDATE stores SET telegram_staff_group_id = :c WHERE code = 'MAIN'"),
                         {"c": CHAT_ID})
        await db.commit()

        # 清理测试数据
        await db.execute(text("DELETE FROM orders WHERE public_code LIKE 'RPT-TEST-%'"))
        await db.execute(text("DELETE FROM report_deliveries WHERE report_date IN (:a, :b, :c, :d)"),
                         {"a": report_date, "b": fail_date, "c": other_date, "d": today})
        await db.execute(text("DELETE FROM customers WHERE id = 9999"))
        await db.commit()

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 60)
    print(f"报表用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL DAILY REPORT TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
