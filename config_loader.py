# -*- coding: utf-8 -*-
"""
config_loader.py —— 统一配置加载模块
=====================================

提供全局配置加载功能，支持从 config.json 读取配置。

使用方法：
    from config_loader import load_config, get_config
    
    # 加载配置
    config = load_config()
    
    # 获取配置项
    token = get_config("api.tushare_token")
    webhook = get_config("api.feishu_webhook")
    
    # 检查必填项
    validate_config()
"""

import os
import json
import logging

CONFIG_FILE = "config.json"
_config_cache = None

log = logging.getLogger(__name__)


def load_config() -> dict:
    """
    加载配置文件。
    
    返回：
        配置字典
    """
    global _config_cache
    
    if _config_cache is not None:
        return _config_cache
    
    if not os.path.exists(CONFIG_FILE):
        log.error(f"配置文件 {CONFIG_FILE} 不存在！")
        raise FileNotFoundError(f"配置文件 {CONFIG_FILE} 不存在，请创建并填写必要配置。")
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
        log.info(f"配置文件加载成功: {CONFIG_FILE}")
        return _config_cache
    except json.JSONDecodeError as e:
        log.error(f"配置文件格式错误: {e}")
        raise


def get_config(key: str, default=None):
    """
    获取指定配置项，支持点分隔路径。
    
    参数：
        key: 配置键，支持点分隔（如 "api.tushare_token"）
        default: 默认值
    
    返回：
        配置值
    """
    config = load_config()
    
    try:
        keys = key.split(".")
        value = config
        for k in keys:
            value = value[k]
        return value
    except (KeyError, TypeError):
        if default is not None:
            return default
        log.warning(f"配置项 {key} 不存在")
        return None


def validate_config() -> bool:
    """
    验证必填配置项是否已填写。
    
    返回：
        True 表示验证通过，False 表示有缺失
    """
    config = load_config()
    
    missing = []
    
    # 检查 API 配置
    api = config.get("api", {})
    if not api.get("tushare_token") or api["tushare_token"] == "在此填入你的 Tushare Token":
        missing.append("api.tushare_token")
    if not api.get("feishu_webhook") or "你的机器人ID" in api["feishu_webhook"]:
        missing.append("api.feishu_webhook")
    
    if missing:
        print("\n" + "="*60)
        print("⚠️  配置验证失败")
        print("="*60)
        print("以下必填配置项未填写：")
        for item in missing:
            print(f"  - {item}")
        print("\n请编辑 config.json 文件，填写上述配置项后重新运行。")
        print("="*60 + "\n")
        return False
    
    log.info("配置验证通过")
    return True


def update_config(key: str, value) -> bool:
    """
    更新配置项并保存到文件。
    
    参数：
        key: 配置键，支持点分隔
        value: 新值
    
    返回：
        True 表示成功
    """
    global _config_cache
    
    config = load_config()
    
    keys = key.split(".")
    last_key = keys[-1]
    parent = config
    
    for k in keys[:-1]:
        if k not in parent:
            parent[k] = {}
        parent = parent[k]
    
    parent[last_key] = value
    
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        
        _config_cache = config
        log.info(f"配置项 {key} 更新成功")
        return True
    except Exception as e:
        log.error(f"配置更新失败: {e}")
        return False


# 加载全局模块级配置，方便外部直接 import *
try:
    _loaded_config = load_config()
except Exception:
    _loaded_config = {}

ENABLE_AKSHARE = _loaded_config.get("akshare", {}).get("enable", True)
DATA_DEVIATION_LIMIT = _loaded_config.get("data_factory", {}).get("deviation_limit", _loaded_config.get("akshare", {}).get("deviation_limit", 0.05))
CRAWL_INTERVAL = _loaded_config.get("crawler", {}).get("interval", 2.0)
CRAWL_TIMEOUT = _loaded_config.get("crawler", {}).get("timeout", 10.0)
CRAWL_USER_AGENT = _loaded_config.get("crawler", {}).get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DATA_SOURCE_PRIORITY = _loaded_config.get("data_factory", {}).get("priority", ["tushare", "akshare", "crawl"])
AUTO_SWITCH_SOURCE = _loaded_config.get("data_factory", {}).get("auto_switch", True)
SOURCE_FAIL_MAX = _loaded_config.get("data_factory", {}).get("fail_max", 3)

# 宏观打分配置
MACRO_SCORE_CFG = _loaded_config.get("macro_score", {})
DIM_SCORE_GREEN = MACRO_SCORE_CFG.get("dim_score_green", 1.0)
DIM_SCORE_YELLOW = MACRO_SCORE_CFG.get("dim_score_yellow", 0.5)
DIM_SCORE_RED = MACRO_SCORE_CFG.get("dim_score_red", 0.0)

CAPITAL_DYNAMIC_THRESHOLD = MACRO_SCORE_CFG.get("capital_dynamic_threshold", 30000000000)
WEIGHT_CAPITAL_NORMAL = MACRO_SCORE_CFG.get("weight_capital_normal", 1.0)
WEIGHT_CAPITAL_HIGH = MACRO_SCORE_CFG.get("weight_capital_high", 1.5)

SCORE_ATTACK = MACRO_SCORE_CFG.get("score_attack", 4.0)
SCORE_CAUTIOUS_LOW = MACRO_SCORE_CFG.get("score_cautious_low", 2.5)
SCORE_CAUTIOUS_HIGH = MACRO_SCORE_CFG.get("score_cautious_high", 3.5)

POS_ATTACK = MACRO_SCORE_CFG.get("pos_attack", 0.8)
POS_CAUTIOUS = MACRO_SCORE_CFG.get("pos_cautious", 0.5)
POS_DEFEND = MACRO_SCORE_CFG.get("pos_defend", 0.3)

# ====================== 新增：一票否决 & 环境健康度 配置 ======================
MACRO_VETO_CFG = _loaded_config.get("macro_veto", {})
# 流动性枯竭
AMOUNT_DROP_RATIO = MACRO_VETO_CFG.get("amount_drop_ratio", 0.3)
NORTH_NET_OUT_THRESHOLD = MACRO_VETO_CFG.get("north_net_out_threshold", 500000000)
# 情绪崩塌
LIMIT_DOWN_THRESHOLD = MACRO_VETO_CFG.get("limit_down_threshold", 100)
MAX_BOARD_THRESHOLD = MACRO_VETO_CFG.get("max_board_threshold", 2)
# 外围系统性冲击
US_INDEX_DROP_THRESHOLD = MACRO_VETO_CFG.get("us_index_drop", 3.0)
VIX_RISK_THRESHOLD = MACRO_VETO_CFG.get("vix_risk_threshold", 40)
# 外围盘中闪崩
FOREIGN_INDEX_DROP = MACRO_VETO_CFG.get("foreign_index_drop", 3.0)
A_OPEN_DROP = MACRO_VETO_CFG.get("a_open_drop", 1.5)
# 涨跌家数比基准
UP_DOWN_RATIO_STANDARD = MACRO_VETO_CFG.get("up_down_ratio_standard", 1.0)

# ====================== 新增：盘中实时修正配置 ======================
INTRADAY_REVISE_CFG = _loaded_config.get("intraday_revise", {})
VOLUME_SHRINK_RATIO = INTRADAY_REVISE_CFG.get("volume_shrink_ratio", 0.15)
FOREIGN_BOARD_DROP = INTRADAY_REVISE_CFG.get("foreign_board_drop", 3.0)
ACCEPT_RATIO_THRESHOLD = INTRADAY_REVISE_CFG.get("accept_ratio_threshold", 0.3)
EMOTION_DEVIATE_RATIO = INTRADAY_REVISE_CFG.get("emotion_deviate_ratio", 0.10)

# ====================== 新增：板块打分&优先级&虹吸规则配置 ======================
BOARD_RULE_CFG = _loaded_config.get("board_rule", {})
TOP_BOARD_COUNT = BOARD_RULE_CFG.get("top_board_count", 5)
BOARD_SCORE_FULL = BOARD_RULE_CFG.get("score_full", 1.0)
BOARD_SCORE_HALF = BOARD_RULE_CFG.get("score_half", 0.5)
BOARD_SCORE_ZERO = BOARD_RULE_CFG.get("score_zero", 0.0)
FLOW_5D_100B = BOARD_RULE_CFG.get("flow_5d_100b", 10000000000)
FLOW_5D_50B = BOARD_RULE_CFG.get("flow_5d_50b", 5000000000)
COVER_FULL = BOARD_RULE_CFG.get("cover_full", 0.12)
COVER_HALF = BOARD_RULE_CFG.get("cover_half", 0.08)
RETREAT_3M = BOARD_RULE_CFG.get("retreat_3m", 0.15)
WEEK_RISE_LIMIT = BOARD_RULE_CFG.get("week_rise_limit", 0.05)
SIPHON_MULTIPLE = BOARD_RULE_CFG.get("siphon_multiple", 2.0)

# ====================== 新增：板块梯队&中军结构配置 ======================
BOARD_STRUCT_CFG = _loaded_config.get("board_structure", {})
LEADER_MIN_BOARD = BOARD_STRUCT_CFG.get("leader_min_board", 3)
LADDER_BREAK_THRESHOLD = BOARD_STRUCT_CFG.get("ladder_break_threshold", 2)
MAIN_TURNOVER_RATIO = BOARD_STRUCT_CFG.get("main_turnover_ratio", 0.08)
MAIN_FLOAT_THRESHOLD = BOARD_STRUCT_CFG.get("main_float_threshold", 5000000000)

# ====================== 新增：板块风格&热度配置 ======================
BOARD_STYLE_CFG = _loaded_config.get("board_style", {})
STYLE_MAP = BOARD_STYLE_CFG.get("style_map", {})
INTRA_STRONG = BOARD_STYLE_CFG.get("intraday_strength", {}).get("strong_threshold", 0.7)
INTRA_WEAK = BOARD_STYLE_CFG.get("intraday_strength", {}).get("weak_threshold", 0.3)
CROSS_STRONG = BOARD_STYLE_CFG.get("cross_day_strength", {}).get("strong_threshold", 0.65)
CROSS_WEAK = BOARD_STYLE_CFG.get("cross_day_strength", {}).get("weak_threshold", 0.35)
CROSS_DAYS = BOARD_STYLE_CFG.get("cross_day_days", 3)

# ====================== 新增：板块轮动配置 ======================
ROTATION_CFG = _loaded_config.get("board_rotation", {})
INTRADAY_AMPLITUDE = ROTATION_CFG.get("intraday_amplitude", 0.08)
PULL_BACK_RATIO = ROTATION_CFG.get("pull_back_ratio", 0.05)
CROSS_DAYS = ROTATION_CFG.get("cross_days", 3)
HOT_SWITCH_FREQ = ROTATION_CFG.get("hot_switch_freq", 2)
HIGH_POS_GAIN = ROTATION_CFG.get("high_position_gain", 0.20)
LOW_POS_GAIN = ROTATION_CFG.get("low_position_gain", 0.05)
STRONG_ROTATE_NUM = ROTATION_CFG.get("strong_rotate_num", 2)
MID_ROTATE_NUM = ROTATION_CFG.get("mid_rotate_num", 1)

# ====================== 新增：大类板块仓位管控配置 ======================
POSITION_CFG = _loaded_config.get("board_position", {})
STYLE_MAX_POS = POSITION_CFG.get("style_max_pos", 0.4)
MARKET_MAX_POS = POSITION_CFG.get("market_max_pos", 0.8)
WARN_RATIO = POSITION_CFG.get("warn_ratio", 0.8)
STRONG_COEFF = POSITION_CFG.get("strong_coeff", 1.1)
WEAK_COEFF = POSITION_CFG.get("weak_coeff", 0.9)

# ====================== 新增：跨板块联动 & 二次资金虹吸配置 ======================
LINK_SIPHON_CFG = _loaded_config.get("board_link_siphon", {})
LINK_CORR_STRONG = LINK_SIPHON_CFG.get("link_corr_strong", 0.8)
LINK_CORR_MID = LINK_SIPHON_CFG.get("link_corr_mid", 0.5)
SYNC_PROB_STRONG = LINK_SIPHON_CFG.get("sync_prob_strong", 0.7)
SYNC_PROB_MID = LINK_SIPHON_CFG.get("sync_prob_mid", 0.4)
FUND_SAME_RATIO = LINK_SIPHON_CFG.get("fund_same_ratio", 0.6)
SIPHON_ABSORB_STRONG = LINK_SIPHON_CFG.get("siphon_absorb_strong", 0.4)
SIPHON_ABSORB_MID = LINK_SIPHON_CFG.get("siphon_absorb_mid", 0.2)
LOSS_RATE_STRONG = LINK_SIPHON_CFG.get("loss_rate_strong", 0.3)
LOSS_RATE_MID = LINK_SIPHON_CFG.get("loss_rate_mid", 0.15)

# ====================== 新增：个股初筛配置 ======================
STOCK_FILTER_CFG = _loaded_config.get("stock_filter", {})
MA_PERIOD = STOCK_FILTER_CFG.get("ma_period", 20)
TURNOVER_MIN = STOCK_FILTER_CFG.get("turnover_min", 0.02)
TURNOVER_MAX = STOCK_FILTER_CFG.get("turnover_max", 0.15)
STAGE_GAIN_LIMIT = STOCK_FILTER_CFG.get("stage_gain_limit", 0.25)
VOLUME_RATIO = STOCK_FILTER_CFG.get("volume_ratio", 1.2)

# ====================== 新增：个股多维度打分配置 ======================
STOCK_SCORE_CFG = _loaded_config.get("stock_score", {})
WEIGHT_FUNDAMENTAL = STOCK_SCORE_CFG.get("weight_fundamental", 0.4)
WEIGHT_CAPITAL = STOCK_SCORE_CFG.get("weight_capital", 0.35)
WEIGHT_CHIP = STOCK_SCORE_CFG.get("weight_chip", 0.25)

PROFIT_HIGH = STOCK_SCORE_CFG.get("profit_high", 0.3)
PROFIT_MID = STOCK_SCORE_CFG.get("profit_mid", 0.1)

VAL_LOW = STOCK_SCORE_CFG.get("val_low", 0.2)
VAL_MID = STOCK_SCORE_CFG.get("val_mid", 0.6)

NET_IN_HIGH = STOCK_SCORE_CFG.get("net_in_high", 0.03)
NET_IN_MID = STOCK_SCORE_CFG.get("net_in_mid", 0.0)

BIG_ORDER_HIGH = STOCK_SCORE_CFG.get("big_order_high", 0.2)
BIG_ORDER_MID = STOCK_SCORE_CFG.get("big_order_mid", 0.1)

TURNOVER_STABLE = STOCK_SCORE_CFG.get("turnover_stable", 0.2)
TURNOVER_MID = STOCK_SCORE_CFG.get("turnover_mid", 0.4)

SCORE_EXCELLENT = STOCK_SCORE_CFG.get("score_excellent", 0.8)
SCORE_GOOD = STOCK_SCORE_CFG.get("score_good", 0.6)
SCORE_NORMAL = STOCK_SCORE_CFG.get("score_normal", 0.4)

# ====================== 新增：个股入场/止损止盈/动态仓位配置 ======================
STOCK_TRADE_CFG = _loaded_config.get("stock_trade_risk", {})
SUPPORT_PROXIMITY = STOCK_TRADE_CFG.get("support_proximity", 0.03)
PRESSURE_PROXIMITY = STOCK_TRADE_CFG.get("pressure_proximity", 0.04)

FIXED_STOP_LOSS = STOCK_TRADE_CFG.get("fixed_stop_loss", 0.05)
TRAILING_STEP = STOCK_TRADE_CFG.get("trailing_step", 0.02)
FIRST_TAKE_PROFIT = STOCK_TRADE_CFG.get("first_take_profit", 0.08)
SECOND_TAKE_PROFIT = STOCK_TRADE_CFG.get("second_take_profit", 0.15)
DEFENSE_STOP_ADJUST = STOCK_TRADE_CFG.get("defense_stop_adjust", 0.02)
DEFENSE_PROFIT_ADJUST = STOCK_TRADE_CFG.get("defense_profit_adjust", 0.03)

BASE_POSITION = STOCK_TRADE_CFG.get("base_position", 0.12)
SINGLE_MAX_POS = STOCK_TRADE_CFG.get("single_max_pos", 0.20)

COEFF_EXCELLENT = STOCK_TRADE_CFG.get("coeff_excellent", 1.2)
COEFF_GOOD = STOCK_TRADE_CFG.get("coeff_good", 1.0)
COEFF_NORMAL = STOCK_TRADE_CFG.get("coeff_normal", 0.8)
COEFF_WEAK = STOCK_TRADE_CFG.get("coeff_weak", 0.6)

COEFF_BOARD_STRONG = STOCK_TRADE_CFG.get("coeff_board_strong", 1.1)
COEFF_BOARD_WEAK = STOCK_TRADE_CFG.get("coeff_board_weak", 0.8)

COEFF_ATTACK = STOCK_TRADE_CFG.get("coeff_attack", 1.1)
COEFF_CAUTIOUS = STOCK_TRADE_CFG.get("coeff_cautious", 0.9)
COEFF_DEFEND = STOCK_TRADE_CFG.get("coeff_defend", 0.5)


if __name__ == "__main__":
    import sys
    
    # 设置日志
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    
    try:
        config = load_config()
        print("\n当前配置概览:")
        print(f"策略名称: {config.get('strategy', {}).get('name', '未配置')}")
        print(f"策略版本: {config.get('strategy', {}).get('version', '未配置')}")
        print(f"Ollama模型: {config.get('ollama', {}).get('model', '未配置')}")
        print(f"Tushare Token: {'已配置' if config.get('api', {}).get('tushare_token') and '填入' not in config['api']['tushare_token'] else '未配置'}")
        print(f"飞书 Webhook: {'已配置' if config.get('api', {}).get('feishu_webhook') and '机器人ID' not in config['api']['feishu_webhook'] else '未配置'}")
        
        print("\n验证配置...")
        if validate_config():
            print("✅ 配置验证通过")
            sys.exit(0)
        else:
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        sys.exit(1)