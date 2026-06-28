#!/bin/bash
# B站动态监控 —— macOS launchd 开机自启配置脚本
#
# 用法（在 Mac mini 上，项目目录内执行）:
#   bash scripts/setup_launchd.sh           # 安装并启动
#   bash scripts/setup_launchd.sh uninstall # 卸载自启
#   bash scripts/setup_launchd.sh status    # 查看运行状态
#
# 原理: 生成 ~/Library/LaunchAgents/com.cyrus.bili-monitor.plist
#       开机/登录自动启动 monitor.py，崩溃自动重启(KeepAlive)

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.cyrus.bili-monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$PROJECT_DIR/logs"
PY="$(command -v python3 || echo /usr/bin/python3)"

mkdir -p "$LOG_DIR"

cmd="${1:-install}"

gen_plist() {
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$PROJECT_DIR/monitor.py</string>
    <string>--interval</string>
    <string>120</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/monitor.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/monitor.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF
  echo "已生成 plist: $PLIST"
}

check_env() {
  if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "⚠ 未找到 .env，请先配置（参考 .env.example）："
    echo "  BILI_COOKIE=...  BILI_UIDS=...  BARK_KEY=...  DEEPSEEK_API_KEY=..."
    echo "  将 .env 从开发机拷贝过来，或新建后填写。"
    return 1
  fi
  echo "✓ .env 已配置"
}

check_deps() {
  echo "检查 python3..."
  "$PY" --version
  echo "安装依赖..."
  "$PY" -m pip install --user -q -r "$PROJECT_DIR/requirements.txt" 2>&1 | tail -2 || true
  echo "✓ 依赖已安装"
}

case "$cmd" in
  install)
    echo "=== 安装 B站监控 launchd 自启 ==="
    echo "项目目录: $PROJECT_DIR"
    echo "Python:   $PY"
    check_env || exit 1
    check_deps
    # 先卸载旧的(如有)
    launchctl unload "$PLIST" 2>/dev/null || true
    gen_plist
    launchctl load "$PLIST"
    echo "✓ 已加载并启动"
    echo
    sleep 2
    "$0" status
    ;;
  uninstall)
    echo "=== 卸载 ==="
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✓ 已卸载自启（项目文件保留）"
    ;;
  status)
    echo "=== 运行状态 ==="
    if launchctl list "$LABEL" >/dev/null 2>&1; then
      pid=$(launchctl list "$LABEL" | awk '/"PID"/{gsub(/[";,]/,"",$3);print $3}')
      echo "✓ 监控已在运行 (PID: ${pid:-未知})"
    else
      echo "✗ 监控未运行"
    fi
    echo
    echo "=== 最近日志 (logs/monitor.err 末10行) ==="
    tail -10 "$LOG_DIR/monitor.err" 2>/dev/null || echo "(无日志)"
    echo
    echo "管理命令:"
    echo "  重新加载: launchctl unload $PLIST && launchctl load $PLIST"
    echo "  停止:     launchctl unload $PLIST"
    echo "  查看日志: tail -f $LOG_DIR/monitor.err"
    ;;
  *)
    echo "用法: $0 {install|uninstall|status}"
    exit 1
    ;;
esac
