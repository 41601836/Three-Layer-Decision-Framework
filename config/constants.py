"""
全局常量配置文件
集中管理所有魔法数字、路径、接口、阈值，方便运维修改
"""

import os
from pathlib import Path

# =============================================================================
# 项目路径配置
# =============================================================================
ROOT_DIR = Path(__file__).parent.parent.absolute()
SCRIPTS_DIR = ROOT_DIR / "scripts"
STATIC_DIR = ROOT_DIR / "static"
DB_DIR = ROOT_DIR / "db"
REPORT_DIR = ROOT_DIR / "reports"
LOG_DIR = ROOT_DIR / "logs"
DATA_DIR = ROOT_DIR / "data"

# 数据库文件路径
DB_PATH = DB_DIR / "stock_daily.db"
DB_LOCK_PATH = DATA_DIR / "stock_data.db"

# 配置文件路径
PORTFOLIO_FILE = ROOT_DIR / "portfolio.json"
AI_CONFIG_FILE = ROOT_DIR / "ai_config.json"
TOKENS_FILE = ROOT_DIR / "tokens.py"
CONFIG_FILE = ROOT_DIR / "config.json"

# =============================================================================
# API 接口配置
# =============================================================================
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TARGET_MODEL = "qwen2.5:7b-instruct-q4_K_M"
OLLAMA_TIMEOUT = 2  # 秒
OLLAMA_RETRY_COUNT = 3
OLLAMA_RETRY_DELAY = 1  # 秒

# Web 服务器配置
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080

# =============================================================================
# 风控阈值配置
# =============================================================================
# 止损相关
STOP_LOSS_MAIN_MULTIPLIER = 1.02  # 趋势预警止损倍数
STOP_LOSS_STRUCT_MULTIPLIER = 0.95  # 结构止损倍数
STOP_LOSS_FINAL_MULTIPLIER = 0.97  # 最终执行止损倍数
STOP_LOSS_ATR_MULTIPLIER = 1.5  # ATR动态止损倍数
STOP_LOSS_BREAKOUT_MULTIPLIER = 0.97  # 突破确认位止损倍数

# 风控降级配置
RISK_CONTROL_MAJOR_WORDS = ["业绩暴雷", "立案调查", "财务造假", "退市风险", "重大利空", "违规处罚"]  # 重大风险关键词
RISK_CONTROL_NORMAL_WORDS = ["建议规避", "执行止损", "暂不介入", "卖出"]  # 正常风险关键词
RISK_CONTROL_MAJOR_DOWNGRADE = -25  # 重大风险降级分数
RISK_CONTROL_NORMAL_DOWNGRADE = -5  # 正常风险降级分数
RISK_CONTROL_MIN_SCORE_AFTER_DOWNGRADE = 70  # 降级后最低分数（保持A级以上）

# 仓位控制
MAX_SINGLE_STOCK_POSITION = 0.20  # 单股持仓上限 20%
MAX_TOTAL_POSITION_ATTACK = 0.80  # 进攻模式总仓上限 80%
MAX_TOTAL_POSITION_DEFENSE = 0.50  # 防守模式总仓上限 50%
MAX_SINGLE_INDUSTRY_POSITION = 0.30  # 单一行业板块仓位上限 30%

# 仓位建议
POSITION_STRONG_ATTACK = 0.15  # 进攻模式强信号仓位 15%
POSITION_MEDIUM_ATTACK = 0.08  # 进攻模式中等信号仓位 8%
POSITION_STRONG_DEFENSE = 0.05  # 防守模式强信号仓位 5%
POSITION_MEDIUM_DEFENSE = 0.02  # 防守模式中等信号仓位 2%
POSITION_NEUTRAL_STRONG = 0.08  # 中性模式强信号仓位 8%
POSITION_NEUTRAL_MEDIUM = 0.03  # 中性模式中等信号仓位 3%

# 评分阈值
MIN_RECOMMEND_SCORE = 70  # 最低推荐评分
GRADE_S_THRESHOLD = 90  # S级评分阈值
GRADE_A_THRESHOLD = 82  # A级评分阈值
GRADE_B_THRESHOLD = 65  # B级评分阈值

# =============================================================================
# 技术指标阈值
# =============================================================================
# 波动率相关
AMPLITUDE_LOW_THRESHOLD = 0.15  # 低波动阈值 15%
AMPLITUDE_MEDIUM_THRESHOLD = 0.25  # 中波动阈值 25%
AMPLITUDE_HIGH_THRESHOLD = 0.30  # 高波动阈值 30%

# 换手率相关
DEFAULT_TURNOVER_RATE = 0.0  # 默认换手率

# 均线相关
MA_DIFF_THRESHOLD = 0.03  # 均线粘合阈值 3%

# 资金流向
MAIN_MONEY_THRESHOLD = 0.0  # 主力资金阈值
HOLDER_CHANGE_THRESHOLD = 0.0  # 股东户数变化阈值

# =============================================================================
# 洗盘分析阈值
# =============================================================================
# 市值分类（亿元）
MARKET_CAP_LARGE = 500  # 大盘股阈值
MARKET_CAP_MEDIUM = 100  # 中盘股阈值

# 洗盘阶段阈值
WASHOUT_AMPLITUDE_LARGE = 0.15  # 大盘股横盘振幅上限 15%
WASHOUT_AMPLITUDE_MEDIUM = 0.20  # 中盘股横盘振幅上限 20%
WASHOUT_AMPLITUDE_SMALL = 0.25  # 小盘股横盘振幅上限 25%

WASHOUT_TURNOVER_LARGE = 0.015  # 大盘股缩量换手上限 1.5%
WASHOUT_TURNOVER_MEDIUM = 0.03  # 中盘股缩量换手上限 3%
WASHOUT_TURNOVER_SMALL = 0.05  # 小盘股缩量换手上限 5%

WASHOUT_VOLUME_MULTIPLIER_LARGE = 1.5  # 大盘股放量倍率
WASHOUT_VOLUME_MULTIPLIER_MEDIUM = 2.0  # 中盘股放量倍率
WASHOUT_VOLUME_MULTIPLIER_SMALL = 2.5  # 小盘股放量倍率

# 洗盘阶段判断
FINANCING_TREND_UP_THRESHOLD = 1.02  # 融资趋势上升阈值
FINANCING_TREND_DOWN_THRESHOLD = 0.98  # 融资趋势下降阈值
VOLUME_RATIO_DOWNTREND_THRESHOLD = 0.5  # 缩量检查阈值 50%
AMPLITUDE_5DAY_THRESHOLD = 0.025  # 连续5日振幅阈值 2.5%
POSITIVE_RETURN_MIN = 0.005  # 阳线最小涨幅 0.5%
POSITIVE_RETURN_MAX = 0.03  # 阳线最大涨幅 3%
VOLUME_INCREASE_THRESHOLD = 1.2  # 量能放大阈值 120%
VOLUME_BREAKOUT_MULTIPLIER = 1.5  # 突破量能倍数

# 支撑位计算
SUPPORT_SINGLE_MULTIPLIER = 0.98  # 单次探底支撑倍数
SUPPORT_MULTI_MULTIPLIER = 0.97  # 多次探底支撑倍数
BREAKOUT_STOP_MULTIPLIER = 0.97  # 突破止损倍数

# =============================================================================
# 数据获取配置
# =============================================================================
# Tushare API
TUSHARE_RATE_LIMIT_SLEEP = 0.05  # Tushare API 限流延迟（秒）
TUSHARE_BATCH_SIZE = 3000  # Tushare 批量获取数量

# 数据库查询
DB_QUERY_TIMEOUT = 30  # 数据库查询超时（秒）

# =============================================================================
# 报告配置
# =============================================================================
# 报告文件命名
REPORT_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"  # 报告时间戳格式（含毫秒）
REPORT_EXPIRE_DAYS = 30  # 报告过期清理天数

# 报告内容限制
REPORT_MAX_LENGTH = 10000  # 报告最大长度（字符）


# =============================================================================
# 报告清理函数
# =============================================================================
def cleanup_expired_reports():
    """
    清理过期的报告文件
    
    删除超过 REPORT_EXPIRE_DAYS 天的报告文件
    """
    import os
    import logging
    from datetime import datetime, timedelta
    
    log = logging.getLogger(__name__)
    
    if not REPORT_DIR.exists():
        log.info("报告目录不存在，跳过清理")
        return
    
    try:
        cutoff_date = datetime.now() - timedelta(days=REPORT_EXPIRE_DAYS)
        deleted_count = 0
        total_size = 0
        
        for file_path in REPORT_DIR.glob("*.md"):
            try:
                # 从文件名解析日期（格式：YYYYMMDD_HHMMSS_fff_...）
                file_name = file_path.name
                date_str = file_name.split('_')[0] if '_' in file_name else None
                
                if date_str and len(date_str) >= 8:
                    try:
                        file_date = datetime.strptime(date_str[:8], "%Y%m%d")
                        if file_date < cutoff_date:
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            deleted_count += 1
                            total_size += file_size
                            log.info(f"删除过期报告: {file_name} ({file_size / 1024:.1f} KB)")
                    except ValueError:
                        # 文件名格式不正确，跳过
                        pass
            except Exception as e:
                log.warning(f"处理文件 {file_path} 时出错: {e}")
        
        if deleted_count > 0:
            log.info(f"报告清理完成: 删除 {deleted_count} 个文件，释放 {total_size / 1024 / 1024:.2f} MB 空间")
        else:
            log.info("没有需要清理的过期报告")
            
    except Exception as e:
        log.error(f"报告清理失败: {e}")


def get_report_stats():
    """
    获取报告目录统计信息
    
    Returns:
        dict: 包含报告数量、总大小、最新报告时间等信息
    """
    import os
    from datetime import datetime
    
    stats = {
        "total_count": 0,
        "total_size": 0,
        "latest_report": None,
        "oldest_report": None,
        "by_grade": {"S": 0, "A": 0, "B": 0, "C": 0}
    }
    
    if not REPORT_DIR.exists():
        return stats
    
    try:
        report_files = list(REPORT_DIR.glob("*.md"))
        stats["total_count"] = len(report_files)
        
        if report_files:
            for file_path in report_files:
                file_size = file_path.stat().st_size
                stats["total_size"] += file_size
                
                # 解析文件名获取评级
                file_name = file_path.name
                parts = file_name.split('_')
                if len(parts) >= 3:
                    grade = parts[2]
                    if grade in stats["by_grade"]:
                        stats["by_grade"][grade] += 1
            
            # 获取最新和最旧的报告
            report_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            stats["latest_report"] = datetime.fromtimestamp(report_files[0].stat().st_mtime).isoformat()
            stats["oldest_report"] = datetime.fromtimestamp(report_files[-1].stat().st_mtime).isoformat()
    
    except Exception as e:
        log = logging.getLogger(__name__)
        log.error(f"获取报告统计失败: {e}")
    
    return stats

# =============================================================================
# 日志配置
# =============================================================================
LOG_LEVEL = "INFO"  # 日志级别
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# =============================================================================
# 推送配置
# =============================================================================
# Feishu 推送
FEISHU_PUSH_DELAY = 0.5  # Feishu 推送延迟（秒）
FEISHU_MAX_RETRY = 3  # Feishu 推送最大重试次数

# 推送历史
PUSH_HISTORY_RETENTION_DAYS = 90  # 推送历史保留天数

# =============================================================================
# 系统配置
# =============================================================================
# 进程管理
PROCESS_CHECK_INTERVAL = 5  # 进程检查间隔（秒）
PROCESS_START_TIMEOUT = 10  # 进程启动超时（秒）

# 文件操作
FILE_WRITE_RETRY = 3  # 文件写入重试次数
FILE_WRITE_DELAY = 0.1  # 文件写入延迟（秒）

# =============================================================================
# 验证配置
# =============================================================================
# 股票代码验证
TS_CODE_PATTERN = r"^\d{6}\.(SH|SZ)$"  # 股票代码正则表达式
TS_CODE_MIN_LENGTH = 6  # 股票代码最小长度

# 数值验证
MIN_PRICE = 0.01  # 最小价格
MAX_PRICE = 10000  # 最大价格
MIN_VOLUME = 0  # 最小成交量
MAX_VOLUME = 1e12  # 最大成交量

# =============================================================================
# 初始化目录
# =============================================================================
def init_directories():
    """初始化必要的目录"""
    directories = [DB_DIR, REPORT_DIR, LOG_DIR, DATA_DIR, STATIC_DIR]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

# 自动初始化目录
init_directories()
