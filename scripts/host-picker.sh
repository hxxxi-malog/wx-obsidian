#!/bin/bash
# 宿主目录选择器：把 macOS 的系统目录面板借给 Docker 里的 Web 服务用。
#
# 容器的 Web 后端跑在 Linux 里，没有桌面连接，弹不出系统面板。这个脚本在 Mac 上
# 装一个 LaunchAgent（Aqua 会话）常驻的 HTTP 服务，容器经 host.docker.internal
# 把「选项」请求转给它；目录面板仍然出现在这台 Mac 的屏幕上。
#
# 用法：
#   ./scripts/host-picker.sh install     # 安装并启动（登录后自动运行）
#   ./scripts/host-picker.sh uninstall   # 停止并删除
#   ./scripts/host-picker.sh run         # 前台运行（调试用）
#   ./scripts/host-picker.sh status      # 看运行状态与端点文件
#
# 首次真正点「原生面板」时，macOS 会问是否允许控制 System Events，选「允许」；
# 之后不再询问。没装这个服务也不影响使用：配置页的「浏览…」能直接浏览挂载目录。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.wx-obsidian.host-picker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
ENDPOINT="$HOME/.wx-obsidian/host-picker.json"
LOG_FILE="$HOME/.wx-obsidian/logs/host-picker.log"
# 服务代码复制到配置目录再运行：仓库可能位于 ~/Downloads 这类 TCC 受保护目录，
# launchd 起的进程读不到它（实测会卡在 Python 启动时的 getcwd）。
INSTALL_DIR="$HOME/.wx-obsidian/host-picker"

pick_python() {
  if [[ -n "${WX_HOST_PICKER_PYTHON:-}" ]]; then
    echo "$WX_HOST_PICKER_PYTHON"
    return 0
  fi
  local candidate
  for candidate in "$REPO_ROOT/.venv/bin/python" "/usr/bin/python3"; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  if command -v uv >/dev/null 2>&1; then
    candidate="$(uv python find '>=3.12' 2>/dev/null || true)"
    if [[ -n "$candidate" ]] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  fi
  echo "找不到 Python 3.12+：先执行 uv sync，或用 WX_HOST_PICKER_PYTHON 指定解释器" >&2
  return 1
}

install_files() {
  # 只复制运行必需的模块，它们全部只依赖标准库
  rm -rf "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR/wx_obsidian/web"
  cp "$REPO_ROOT/wx_obsidian/__init__.py" "$INSTALL_DIR/wx_obsidian/__init__.py"
  cp "$REPO_ROOT/wx_obsidian/paths.py" "$INSTALL_DIR/wx_obsidian/paths.py"
  cp "$REPO_ROOT/wx_obsidian/web/__init__.py" "$INSTALL_DIR/wx_obsidian/web/__init__.py"
  cp "$REPO_ROOT/wx_obsidian/web/fs.py" "$INSTALL_DIR/wx_obsidian/web/fs.py"
  cp "$REPO_ROOT/wx_obsidian/web/picker_bridge.py" "$INSTALL_DIR/wx_obsidian/web/picker_bridge.py"
  cp "$REPO_ROOT/wx_obsidian/web/host_picker.py" "$INSTALL_DIR/wx_obsidian/web/host_picker.py"
  chmod -R u+rwX,go-rwx "$INSTALL_DIR"
}

write_plist() {
  local python="$1"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.wx-obsidian/logs"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python</string>
    <string>-m</string>
    <string>wx_obsidian.web.host_picker</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$INSTALL_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>$LOG_FILE</string>
  <key>StandardErrorPath</key>
  <string>$LOG_FILE</string>
</dict>
</plist>
PLIST_EOF
  plutil -lint "$PLIST" >/dev/null
}

show_endpoint() {
  if [[ ! -f "$ENDPOINT" ]]; then
    echo "端点文件不存在：$ENDPOINT（服务没有运行）"
    return 1
  fi
  "$1" - "$ENDPOINT" <<'PY_EOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
print(f"  地址：{payload.get('url', '')}")
print(f"  进程：{payload.get('pid', 0)}（启动于 {payload.get('started_at', '')}）")
PY_EOF
}

case "${1:-}" in
  install)
    python="$(pick_python)"
    install_files
    if ! (cd "$INSTALL_DIR" && "$python" -c 'import wx_obsidian.web.host_picker' 2>/dev/null); then
      echo "这个解释器跑不了宿主选择器：$python" >&2
      exit 1
    fi
    write_plist "$python"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    # bootout 是异步的：等旧实例真正卸载再 bootstrap，否则 launchd 报 Input/output error
    for _ in $(seq 1 50); do
      launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
      sleep 0.2
    done
    rm -f "$ENDPOINT"
    launchctl bootstrap "$DOMAIN" "$PLIST"
    launchctl kickstart -k "$DOMAIN/$LABEL"
    for _ in $(seq 1 40); do
      [[ -f "$ENDPOINT" ]] && break
      sleep 0.25
    done
    if ! show_endpoint "$python"; then
      echo "启动失败，日志末尾：" >&2
      tail -20 "$LOG_FILE" >&2 || true
      exit 1
    fi
    echo "宿主目录选择器已安装并启动（$LABEL）。"
    echo "服务代码在 $INSTALL_DIR；仓库有改动时重新执行本命令。"
    echo "首次点「原生面板」时，macOS 会询问是否允许控制 System Events，选「允许」。"
    echo "容器的转发逻辑在 Web 镜像里：代码有改动时先 docker compose up -d --build。"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST" "$ENDPOINT"
    rm -rf "$INSTALL_DIR"
    echo "已卸载宿主目录选择器。"
    ;;
  run)
    python="$(pick_python)"
    cd "$REPO_ROOT"
    exec "$python" -m wx_obsidian.web.host_picker "${@:2}"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | sed -n '1,12p' || echo "LaunchAgent 未安装/未运行"
    python="$(pick_python)"
    show_endpoint "$python" || true
    ;;
  *)
    echo "用法：$0 {install|uninstall|run|status}" >&2
    exit 2
    ;;
esac
