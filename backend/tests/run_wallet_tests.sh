#!/usr/bin/env bash
# 钱包 / 充值 —— 一键回归
#
#   1. 建一个临时库，跑完整 alembic 迁移链（含 wallet schema）
#   2. 跑 77 项 SQL 功能用例
#   3. 跑 19 项真实并发用例（8 线程）
#   4. 可选：跑 34 项端到端用例（需要已经启动的 uvicorn + 种子数据）
#
# 用法:
#   ./run_wallet_tests.sh                              # 只跑 SQL/并发（用本机 PG 客户端）
#   ./run_wallet_tests.sh /tmp postgres                # 指定 socket 目录 / 用户
#   E2E_URL=http://127.0.0.1:8000 ./run_wallet_tests.sh   # 额外跑端到端
#
# 依赖：psql/createdb/dropdb、python3 + psycopg2（并发用例）、
#       alembic + 后端依赖（迁移）；都可用 PYTHON 指定解释器。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/.." && pwd)"
DB="${WALLET_TEST_DB:-teacafe_wallet_test}"
PYTHON="${PYTHON:-python3}"

if [ $# -ge 1 ]; then export PGHOST="$1"; fi
if [ $# -ge 2 ]; then export PGUSER="$2"; fi

# 注意：变量后面紧跟中文标点必须写 ${VAR}——macOS 自带 bash 3.2 会把全角字符
# 的字节当成变量名的一部分。
for bin in psql createdb dropdb; do
  command -v "$bin" >/dev/null 2>&1 || { echo "缺少 ${bin}，请先装 PostgreSQL 客户端"; exit 127; }
done

echo "== 0/4 环境 =="
psql --version
echo "PGHOST=${PGHOST:-<默认>}  PGUSER=${PGUSER:-<默认>}  DB=${DB}  PYTHON=${PYTHON}"

echo
echo "== 1/4 建库 + 迁移 =="
dropdb --if-exists "$DB" 2>/dev/null || true
createdb "$DB"
# alembic.ini 里的 script_location 是相对路径，必须在 backend/ 下执行。
# 迁移日志平时不刷屏，失败时原样打出来——否则出错只剩一个退出码，没法查。
LOG="$(mktemp)"
if ! ( cd "$BACKEND" && DATABASE_URL="postgresql+asyncpg://${PGUSER:-postgres}@/${DB}${PGHOST:+?host=${PGHOST}}" \
        "$PYTHON" -m alembic upgrade head ) >"$LOG" 2>&1; then
  cat "$LOG"; rm -f "$LOG"; echo "迁移失败"; exit 1
fi
rm -f "$LOG"
echo "OK: alembic upgrade head"

echo
echo "== 2/7 钱包功能用例（97 项）=="
psql -d "$DB" -v ON_ERROR_STOP=1 -f "$HERE/wallet_tests.sql" 2>&1 \
  | grep -E "用例总数|ALL TESTS PASSED|TEST FAILED|ERROR"

echo
echo "== 3/7 钱包并发用例（19 项）=="
"$PYTHON" "$HERE/wallet_concurrency_test.py" "${PGHOST:-}" "${PGUSER:-postgres}" "$DB" \
  | grep -E "并发用例|ALL CONCURRENCY TESTS PASSED|FAIL"

echo
echo "== 4/7 每日报表用例（18 项：统计口径 + 投递幂等 + 调度到点判断）=="
DATABASE_URL="postgresql+asyncpg://${PGUSER:-postgres}@/${DB}${PGHOST:+?host=${PGHOST}}" \
  "$PYTHON" "$HERE/daily_report_test.py" | tail -3

echo
echo "== 5/7 菜品售卖时间/规格 纯逻辑用例（39 项，不需要服务）=="
"$PYTHON" "$HERE/product_options_test.py" | tail -3

if [ -n "${E2E_URL:-}" ]; then
  echo
  echo "== 6/7 订单/钱包端到端用例（70 项，${E2E_URL}）=="
  "$PYTHON" "$HERE/wallet_api_e2e.py" "$E2E_URL" | tail -5
  echo
  echo "== 7/7 安全用例（68 项，${E2E_URL}）=="
  "$PYTHON" "$HERE/wallet_security_test.py" "$E2E_URL" | tail -5
  echo
  echo "== 7/7 菜品端到端用例（27 项，${E2E_URL}）=="
  "$PYTHON" "$HERE/menu_options_e2e.py" "$E2E_URL" | tail -5
else
  echo
  echo "== 6/7 端到端 / 7/7 安全用例 =="
  echo "跳过（未设置 E2E_URL）。要跑：先 alembic upgrade head + 灌种子 + 起服务，然后"
  echo "  E2E_URL=http://127.0.0.1:8000 ./run_wallet_tests.sh"
fi

echo
echo "全部通过。临时库 ${DB} 保留着，可 psql -d ${DB} 复查（清理：dropdb ${DB}）"
