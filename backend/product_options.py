"""菜品规格 / 附加选择的校验与定价。

**价格永远由服务端算**：前端只传 `group_id` + `option_id`，服务端回库把
`price_delta_minor` 取出来求和。客户端传来的任何价格字段都不看——
否则改一个请求体就能 0 元下单。

规则：
  * SPEC（单选）：最多选 1 个；`required=true` 时**必须**选 1 个。
  * ADDON（多选）：`required=true` 时至少 1 个；`max_select` 限制上限（NULL = 不限）。
  * 选项必须属于该菜品、组必须 active、选项必须 active —— 否则拒绝。
  * 已下架（active=false）的组/选项不能通过旧链接继续点单。
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import Product, ProductOptionGroup


class OptionError(Exception):
    """规格/附加选择不合法。message 会直接返回给前端展示。"""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _pick_name(name: Any, language: str = "en") -> str:
    if not isinstance(name, dict):
        return str(name or "")
    for key in (language, "en", "zh-CN", "zh_CN", "km"):
        if name.get(key):
            return str(name[key])
    return next((str(v) for v in name.values() if v), "")


async def load_groups(db: AsyncSession, product_ids: Iterable[int]) -> dict[int, list[ProductOptionGroup]]:
    """一次把多个菜品的可用分组读出来，避免下单时 N 次查询。"""
    ids = list({int(pid) for pid in product_ids})
    if not ids:
        return {}
    result = await db.execute(
        select(ProductOptionGroup)
        .filter(ProductOptionGroup.product_id.in_(ids))
        .options(selectinload(ProductOptionGroup.options))
        .order_by(ProductOptionGroup.sort_order, ProductOptionGroup.id)
    )
    groups: dict[int, list[ProductOptionGroup]] = {pid: [] for pid in ids}
    for group in result.scalars().all():
        groups.setdefault(group.product_id, []).append(group)
    return groups


def active_groups(groups: Iterable[ProductOptionGroup]) -> list[ProductOptionGroup]:
    """只保留 active 的组和 active 的选项。"""
    out = []
    for group in groups:
        if not group.active:
            continue
        options = [o for o in group.options if o.active]
        if not options:
            continue          # 组里一个可选项都没有 = 不展示也不校验（否则会卡死下单）
        out.append(group)
    return out


def serialize_groups(groups: Iterable[ProductOptionGroup], language: str = "en",
                     include_inactive: bool = False) -> list[dict]:
    """给前端用的结构。带上 min/max，前端据此渲染单选/多选。

    后台编辑器需要看到已停用的组和选项，顾客菜单仍只接收可用项。
    """
    payload = []
    source_groups = list(groups) if include_inactive else active_groups(groups)
    for group in source_groups:
        is_multi = bool(group.multi_select) or group.kind == "ADDON"
        source_options = list(group.options) if include_inactive else [option for option in group.options if option.active]
        payload.append({
            "id": group.id,
            "name": group.name,
            "name_text": _pick_name(group.name, language),
            "kind": group.kind,
            "required": bool(group.required),
            "multi_select": is_multi,
            "min_select": 1 if group.required else 0,
            "max_select": (group.max_select if group.max_select else (None if is_multi else 1)),
            "active": bool(group.active),
            "options": [{
                "id": option.id,
                "name": option.name,
                "name_text": _pick_name(option.name, language),
                "price_delta_minor": int(option.price_delta_minor or 0),
                "is_default": bool(option.is_default),
                "active": bool(option.active),
            } for option in source_options],
        })
    return payload


def resolve_selections(groups: Iterable[ProductOptionGroup], selections: list[dict] | None,
                       language: str = "en") -> tuple[int, list[dict]]:
    """校验选择并算出加价与快照。返回 (加价合计, 快照列表)。

    `selections` 形如 `[{"group_id": 1, "option_id": 3}, ...]`。
    """
    groups = active_groups(groups)
    by_id = {group.id: group for group in groups}

    chosen: dict[int, list] = {}
    seen_pairs: set[tuple[int, int]] = set()
    for raw in selections or []:
        # 既支持 dict（SQL/JSON 调用），也支持带属性的对象——API 层传进来的
        # 是 pydantic 模型，不是 dict。之前只认 dict，导致所有选择都被判成
        # BAD_PAYLOAD（状态码对、原因错），是端到端测试抓出来的。
        if isinstance(raw, dict):
            group_id, option_id = raw.get("group_id"), raw.get("option_id")
        else:
            group_id, option_id = getattr(raw, "group_id", None), getattr(raw, "option_id", None)
        try:
            group_id = int(group_id)
            option_id = int(option_id)
        except (TypeError, ValueError):
            raise OptionError("OPTION_BAD_PAYLOAD", "Invalid option selection")
        if (group_id, option_id) in seen_pairs:
            raise OptionError("OPTION_DUPLICATE", "Duplicate option selection")
        seen_pairs.add((group_id, option_id))

        group = by_id.get(group_id)
        if group is None:
            # 分组不存在 / 不属于该菜品 / 已停用 —— 都不允许
            raise OptionError("OPTION_UNKNOWN_GROUP",
                              "This option group is not available for this product")
        option = next((o for o in group.options if o.id == option_id and o.active), None)
        if option is None:
            raise OptionError("OPTION_UNKNOWN",
                              f"Option is not available in group {_pick_name(group.name, language)}")
        chosen.setdefault(group_id, []).append((group, option))

    # 逐个分组校验数量
    for group in groups:
        picked = chosen.get(group.id, [])
        is_multi = bool(group.multi_select) or group.kind == "ADDON"
        label = _pick_name(group.name, language)
        if len(picked) > 1 and not is_multi:
            raise OptionError("OPTION_TOO_MANY", f"Only one choice allowed for {label}")
        if group.required and not picked:
            raise OptionError("OPTION_REQUIRED_MISSING", f"Please choose {label}")
        if is_multi and group.max_select and len(picked) > group.max_select:
            raise OptionError("OPTION_TOO_MANY",
                              f"Choose at most {group.max_select} from {label}")

    delta = 0
    snapshot: list[dict] = []
    for group in groups:
        for _group, option in chosen.get(group.id, []):
            price_delta = int(option.price_delta_minor or 0)
            delta += price_delta
            snapshot.append({
                "group_id": group.id,
                "group_name": group.name,
                "group_kind": group.kind,
                "option_id": option.id,
                "option_name": option.name,
                "price_delta_minor": price_delta,
            })
    return delta, snapshot


def default_selections(groups: Iterable[ProductOptionGroup]) -> list[dict]:
    """按 `is_default` 生成一份默认选择（用于「一键加入」和测试）。"""
    selections = []
    for group in active_groups(groups):
        defaults = [o for o in group.options if o.active and o.is_default]
        if not defaults and group.required and group.kind == "SPEC":
            defaults = [o for o in group.options if o.active][:1]
        for option in defaults[: 1 if group.kind == "SPEC" else (group.max_select or len(defaults))]:
            selections.append({"group_id": group.id, "option_id": option.id})
    return selections
