"""按身份限流的中间件。

为什么放在中间件而不是每个 handler 里：
  * 限流必须在**读请求体、访问数据库之前**生效，否则被限的请求照样打到数据库；
  * 规则集中在一张表里，新增端点不容易漏掉。

规则按「身份 + 具体路由」计算：
  * 身份优先取会话 Cookie 里的 JWT sub（客户和员工各自分开算），
    取不到就退化成客户端 IP；
  * 客户端 IP 用的是 `request.client.host` —— Docker 里 uvicorn 带
    `--proxy-headers`，它已经根据 nginx 传来的 X-Forwarded-For 还原过真实 IP。
    这里**不自己解析 XFF**，避免请求头伪造绕过限流。

已知局限（部署前必读）：
  * 计数器在**进程内存**里，多副本部署时每个副本各算一份。要全局限流请上
    Redis 或直接在网关（nginx limit_req / Cloudflare）做；
  * 进程重启计数清零。

数据库侧另有一层与限流互补的保护：单笔上下限、未完成充值单上限（默认 3）、
单日累计上限、凭证唯一性——那些是**业务额度**，这里是**频率**。
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict, deque

from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("teacafe.ratelimit")

#: (HTTP 方法, 路径正则, 窗口内允许次数, 窗口秒数)
#: 次数给得比真实人类操作宽得多——被限流的是脚本和刷单，不是正常顾客。
RULES: tuple[tuple[str, re.Pattern[str], int, int], ...] = (
    # 建充值单：真正的防刷是业务额度（max_open_orders 默认 3、单日累计上限），
    # 限流在这里的职责是防脚本洪泛，所以给得宽松一些
    ("POST", re.compile(r"^/api/v1/wallet/recharges$"), 30, 60),
    # 下单
    ("POST", re.compile(r"^/api/v1/orders$"), 20, 60),
    # 登录：防撞库/暴力破解（按 IP）
    ("POST", re.compile(r"^/api/v1/auth/admin/login$"), 10, 300),
    # 登录前只能按 IP 限流：店里 WiFi 会让很多顾客共用一个出口 IP，
    # 所以这条给得宽（120/分），它的作用是挡脚本，不是卡正常顾客
    ("POST", re.compile(r"^/api/v1/auth/telegram$"), 120, 60),
    # 员工审核动作
    ("POST", re.compile(r"^/api/v1/admin/recharges/\d+/(approve|reject|received)$"), 60, 60),
    ("POST", re.compile(r"^/api/v1/admin/wallets/\d+/adjust$"), 20, 60),
    ("POST", re.compile(r"^/api/v1/admin/orders/\d+/(status|refund|payment-review|payment-proof)$"), 60, 60),
    ("POST", re.compile(r"^/api/v1/admin/wallet/maintenance$"), 6, 60),
)

#: 其它写操作的兜底限额（按身份）：每分钟 300 次
DEFAULT_MUTATING_LIMIT = (300, 60)

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 不参与限流的路径：
#:   * Telegram webhook 由密钥鉴权、且失败时 Telegram 会重试，限流会放大故障；
#:   * 健康检查给编排系统用。
EXEMPT_PREFIXES = ("/api/v1/telegram/webhook", "/health")

#: 计数器上限，防止被大量随机身份打爆内存
MAX_KEYS = 50_000


def _enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").lower() not in {"0", "false", "no"}


def _identity(request) -> str:
    """身份：优先会话身份，其次客户端 IP。"""
    secret = os.getenv("JWT_SECRET")
    if secret:
        for cookie in ("session_token", "admin_session_token"):
            token = request.cookies.get(cookie)
            if not token:
                continue
            try:
                payload = jwt.decode(token, secret, algorithms=["HS256"],
                                     options={"verify_exp": False})
                sub = payload.get("sub")
                if sub:
                    return f"{cookie}:{sub}"
            except Exception:  # noqa: BLE001 - 令牌坏掉就退化成 IP 限流，不放行
                pass
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _rule_for(method: str, path: str) -> tuple[str | None, int, int]:
    """返回 (分桶键, 限制次数, 窗口秒数)。

    分桶键用**规则本身**而不是具体路径：`/admin/recharges/1/approve` 与
    `/admin/recharges/2/approve` 必须共享同一个桶，否则换个 order_id 就绕过了。
    """
    if method in MUTATING_METHODS:
        for rule_method, pattern, limit, window in RULES:
            if rule_method == method and pattern.match(path):
                return f"{method}:{pattern.pattern}", limit, window
        return f"{method}:default", *DEFAULT_MUTATING_LIMIT
    return None, 0, 0  # 读接口不限流


class RateLimitMiddleware(BaseHTTPMiddleware):
    """滑动窗口计数。超限返回 429 + Retry-After。"""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _check(self, key: str, limit: int, window: int) -> float:
        """返回 0 表示放行，否则返回需要等待的秒数。"""
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return max(bucket[0] + window - now, 1.0)
        bucket.append(now)
        # 顺手清理空桶，避免长期运行后字典越来越大
        if len(self._hits) > MAX_KEYS:
            for stale in [k for k, v in self._hits.items() if not v]:
                self._hits.pop(stale, None)
            if len(self._hits) > MAX_KEYS:      # 仍然过大就整体重置（宁可放宽也不能吃光内存）
                log.warning("限流计数器超过 %s 个键，整体重置", MAX_KEYS)
                self._hits.clear()
        return 0.0

    async def dispatch(self, request, call_next):
        path = request.url.path
        if not _enabled() or path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        bucket, limit, window = _rule_for(request.method, path)
        if bucket and limit:
            retry_after = self._check(f"{_identity(request)}|{bucket}", limit, window)
            if retry_after:
                log.warning("限流命中 rule=%s path=%s identity=%s", bucket, path, _identity(request))
                return JSONResponse(
                    {"detail": "请求过于频繁，请稍后再试"},
                    status_code=429,
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )
        return await call_next(request)
