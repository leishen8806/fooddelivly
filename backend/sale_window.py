"""菜品售卖时间判断（纯函数，方便单测）。

规则：
  * 没有配置时间段 -> 整天可售（对存量菜品零影响）；
  * `start < end` -> 当天区间，左闭右开 `[start, end)`；
  * `start > end` -> **跨午夜**（如 20:00–02:00）；
  * `start == end` -> 数据库已用 CHECK 拒绝（否则「0 长度」和「24 小时」会有歧义）。

时间一律按**店铺时区**的墙上时间比较，所以这里只接受 `datetime.time`。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Window:
    start: time
    end: time


def _as_window(raw) -> Window:
    """支持 ORM 行、dict（门店覆盖表存的 JSON）、Window 与 (start, end) 元组。"""
    if isinstance(raw, Window):
        return raw
    if isinstance(raw, dict):
        start = time.fromisoformat(str(raw.get("start", "")).strip())
        end = time.fromisoformat(str(raw.get("end", "")).strip())
        return Window(start.replace(second=0, microsecond=0), end.replace(second=0, microsecond=0))
    if isinstance(raw, tuple):
        return Window(raw[0], raw[1])
    return Window(raw.start_time, raw.end_time)


def is_on_sale(windows, now: time) -> bool:
    """当前是否在售卖时间内。windows 为空 -> 全天可售。"""
    items = [_as_window(w) for w in windows]
    if not items:
        return True
    for w in items:
        if w.start < w.end:
            if w.start <= now < w.end:
                return True
        else:  # 跨午夜：20:00-02:00 -> [20:00, 24:00) ∪ [00:00, 02:00)
            if now >= w.start or now < w.end:
                return True
    return False


def next_open_at(windows, now: datetime) -> datetime | None:
    """下一次开始售卖的时刻；当前可售则返回 now。没有配置时返回 None（= 永远可售）。"""
    items = [_as_window(w) for w in windows]
    if not items:
        return None
    if is_on_sale(items, now.time()):
        return now
    best: datetime | None = None
    for w in items:
        candidate = now.replace(hour=w.start.hour, minute=w.start.minute,
                                second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        if best is None or candidate < best:
            best = candidate
    return best


def describe(windows) -> list[dict]:
    """给前端/接口用的可序列化形式。"""
    items = sorted((_as_window(w) for w in windows), key=lambda w: w.start)
    return [{"start": w.start.strftime("%H:%M"), "end": w.end.strftime("%H:%M"),
             "overnight": w.start > w.end} for w in items]


def windows_from_json(raw) -> list[Window]:
    """把 `[{"start": "08:00", "end": "20:00"}]` 解析成窗口列表。

    解析失败的那一条直接忽略——宁可「不限制」也不要因为一条脏配置
    把整个店的下单全锁死。
    """
    out: list[Window] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            start = time.fromisoformat(str(item.get("start", "")).strip())
            end = time.fromisoformat(str(item.get("end", "")).strip())
        except ValueError:
            continue
        out.append(Window(start.replace(second=0, microsecond=0),
                          end.replace(second=0, microsecond=0)))
    return out

