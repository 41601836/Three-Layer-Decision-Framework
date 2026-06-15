#!/bin/bash

# ==============================================================================
# 三层量化决策系统 数据库每日自动备份与清理脚本 (macOS)
# ==============================================================================

# 设置当前工作目录为项目根目录
cd "$(dirname "$0")/.." || exit 1
ROOT_DIR=$(pwd)

DB_FILE="${ROOT_DIR}/db/stock_daily.db"
BACKUP_DIR="${ROOT_DIR}/db/backup"

echo "📂 [Backup] 启动物理数据库备份任务..."

# 1. 检查物理数据库是否存在
if [ ! -f "$DB_FILE" ]; then
    echo "❌ [Error] 未找到物理数据库文件: $DB_FILE，备份中止。"
    exit 1
fi

# 2. 创建备份目录
mkdir -p "$BACKUP_DIR"

# 3. 执行备份
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/stock_daily_${TIMESTAMP}.db"

echo "💾 [Backup] 正在复制数据库: $DB_FILE -> $BACKUP_FILE"
cp "$DB_FILE" "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "✅ [Success] 数据库备份成功！备份文件名: stock_daily_${TIMESTAMP}.db"
else
    echo "❌ [Error] 数据库复制失败。"
    exit 1
fi

# 4. 清理 7 天以前的旧备份 (8天前)
echo "🧹 [Cleanup] 正在检查并清理 7 天以前的历史备份..."
# 在 macOS 下，find 的 -mtime +7 能够匹配 7 天前修改的文件
find "$BACKUP_DIR" -name "stock_daily_*.db" -type f -mtime +7 -exec rm -f {} \;

echo "🏁 [Backup] 物理数据库备份任务顺利结束！"
