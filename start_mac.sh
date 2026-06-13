#!/bin/bash
# StockAI Funnel 三层漏斗智能选股系统 - Mac 一键启动脚本
# ============================================================

echo ""
echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║                   StockAI Funnel v4.0                             ║"
echo "║              三层漏斗智能选股系统 - Mac 一键启动                    ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误：未找到 Python3，请先安装 Python"
    exit 1
fi

# 检查虚拟环境
VENV_DIR="TraeAI-5"
if [ ! -d "$VENV_DIR" ]; then
    echo "⚠️  创建 Python 虚拟环境..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "❌ 虚拟环境创建失败"
        exit 1
    fi
fi

# 激活虚拟环境
echo "✅ 激活虚拟环境..."
source "$VENV_DIR/bin/activate"

# 安装依赖
echo "✅ 检查并安装依赖..."
pip install -q -r requirements.txt

# 设置环境变量
export TUSHARE_TOKEN="bcfb8db101b928d1dfff5685dff95f2441d8c1b4395e2ecd067116ea"

# 启动 Web 服务器
echo ""
echo "🚀 启动 StockAI Funnel 服务..."
echo "📍 UI 界面将在浏览器中打开"
echo "📍 服务端口：http://localhost:8000"
echo ""

# 在后台启动服务器
python web_server.py &
SERVER_PID=$!

# 等待服务器启动
sleep 3

# 打开浏览器
open "http://localhost:8000"

# 等待服务器结束
wait $SERVER_PID