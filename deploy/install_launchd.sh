#!/bin/zsh
# 在 macOS 上把监控装成登录即启动的后台服务（LaunchAgent）。
# 用法：在项目根目录执行  ./deploy/install_launchd.sh [服务名]
# 服务名默认 com.jipiao.monitor，必须和 config.yaml 里 service.launchd_label 一致。
set -e

LABEL="${1:-com.jipiao.monitor}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -x "$PY" ] || { echo "找不到 $PY，先按 README 建虚拟环境并安装依赖"; exit 1; }
[ -f "$ROOT/config.yaml" ] || { echo "找不到 config.yaml，先 cp config.example.yaml config.yaml 并修改"; exit 1; }
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

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
        <string>$ROOT/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <!-- 崩溃才拉起；日期全部过期时程序会自行停用并以 0 退出，此时不重启 -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin</string>
        <key>PYTHONIOENCODING</key>
        <string>utf-8</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/launchd.err.log</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST"

UID_=$(id -u)
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl enable "gui/$UID_/$LABEL"
launchctl bootstrap "gui/$UID_" "$PLIST"
echo "已安装并启动：$LABEL"
echo "看日志：tail -f \"$ROOT/logs/launchd.err.log\""
