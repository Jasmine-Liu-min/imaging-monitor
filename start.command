#!/usr/bin/env bash
# 一键启动（macOS 可在 Finder 里直接双击运行）：
#   1) 跑单元测试  2) 用离线样例生成演示数据  3) 起本地服务并打开看板
# 终端用户也可以： bash start.command   或   ./start.command
set -euo pipefail

# 切到脚本所在目录，保证双击运行时路径正确
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
URL="http://localhost:${PORT}/web/"

echo "============================================"
echo "  Imaging Monitor · 一键启动"
echo "============================================"

# 0. 找一个可用的 python3
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装 Python 3（https://www.python.org/downloads/）"
  read -r -p "按回车键退出…" _
  exit 1
fi
echo "✓ Python: $(python3 --version)"

# 1. 跑测试（失败也不阻塞演示，仅提示）
echo
echo "▶ 运行单元测试…"
if python3 tests/run_all.py >/tmp/imaging-monitor-tests.log 2>&1; then
  echo "✓ 测试全部通过"
else
  echo "⚠️ 测试未全通过，详见 /tmp/imaging-monitor-tests.log（演示仍会继续）"
fi

# 2. 生成离线演示数据（不联网、不调用 AI、不推送）
echo
echo "▶ 生成演示数据（离线样例）…"
rm -f data/store.json
python3 -m imaging_monitor.cli run --no-ai --dry-run --skip-push --fixture-dir tests/fixtures >/dev/null
echo "✓ 已生成 data/events.json · report.json · store.json"

# 3. 启动本地服务并打开看板
echo
echo "▶ 启动看板：${URL}"
echo "  （按 Ctrl+C 停止服务）"
# 稍等服务起来后自动打开浏览器（macOS: open / Linux: xdg-open）
( sleep 1; (command -v open >/dev/null 2>&1 && open "${URL}") || (command -v xdg-open >/dev/null 2>&1 && xdg-open "${URL}") || true ) &
exec python3 -m http.server "${PORT}"
