#!/bin/bash

# ==============================================================================
# 三层量化决策系统 Web 服务一键后台启动脚本 (macOS)
# ==============================================================================

# 设置当前工作目录为项目根目录
cd "$(dirname "$0")/.." || exit 1
ROOT_DIR=$(pwd)
LOG_FILE="${ROOT_DIR}/logs/web_server.log"

echo "🛸 [QuantServer] 正在初始化量化决策系统 Web 服务..."

# 1. 检查 Python3 环境
if ! command -v python3 &> /dev/null; then
    echo "❌ [Error] 系统未安装 python3，启动中止。"
    exit 1
fi

# 2. 检测 8001 端口占用情况
PORT=8001
PID=$(lsof -t -i:$PORT)

if [ -n "$PID" ]; then
    echo "⚠️ [Warning] 检测到端口 $PORT 已被进程 $PID 占用。正在尝试重启该服务..."
    kill -9 "$PID"
    sleep 1
fi

# 3. 创建日志目录
mkdir -p "${ROOT_DIR}/logs"

# 4. 后台拉起 Web Server
echo "🚀 [QuantServer] 正在后台拉起 FastAPI Web Server (端口: $PORT)..."
nohup python3 web_server.py > "$LOG_FILE" 2>&1 &

# 等待 1.5 秒确认进程是否存活
sleep 1.5
NEW_PID=$(lsof -t -i:$PORT)

if [ -n "$NEW_PID" ]; then
    echo "✅ [Success] Web 服务已成功启动！"
    echo "   - 进程 PID: $NEW_PID"
    echo "   - 日志输出: $LOG_FILE"
    echo "   - 访问入口: http://localhost:$PORT/"
else
    echo "❌ [Error] 服务启动失败，请检查日志内容：$LOG_FILE"
    exit 1
fi
