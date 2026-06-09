# -*- coding: utf-8 -*-
"""
portfolio_health.py —— 持仓健康度分析模块
=============================================

功能：
- 读取 portfolio.json 持仓数据
- 对每只股票调用 analyze_v3_0 分析
- 计算盈亏，生成健康度建议
- 输出标准化数据供推送使用
"""

import os
import sys
import json
import logging
from datetime import datetime

# 添加项目根目录到路径
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from config_loader import load_config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


def check_portfolio(portfolio_file: str = "portfolio.json") -> list:
    """
    持仓健康度分析主函数
    
    返回：
    [
        {
            "ts_code": "600519.SH",
            "name": "贵州茅台",
            "cost": 1250.00,
            "current_price": 1285.00,
            "pnl_pct": 2.8,
            "score": 32,
            "signal": "强信号",
            "suggestion": "继续持有",
            "reason": "主力净流入，股东户数下降",
            "alerts": []
        },
        ...
    ]
    """
    # 1. 加载持仓
    try:
        portfolio_path = os.path.join(ROOT_DIR, portfolio_file)
        if not os.path.exists(portfolio_path):
            log.warning(f"持仓文件不存在: {portfolio_file}")
            return []
        
        with open(portfolio_path, "r", encoding="utf-8") as f:
            portfolio = json.load(f)
    except Exception as e:
        log.error(f"加载持仓失败: {e}")
        return []
    
    if not portfolio:
        log.info("暂无持仓")
        return []
    
    log.info(f"开始分析 {len(portfolio)} 只持仓...")
    
    # 2. 逐个分析
    result = []
    for holding in portfolio:
        try:
            ts_code = holding.get("ts_code", "")
            cost = holding.get("cost", 0.0)
            shares = holding.get("shares", 0)
            
            if not ts_code:
                continue
            
            # 调用分析模块
            from analyze_stock import StockAnalyzer
            analyzer = StockAnalyzer()
            score_card, reasoning = analyzer.analyze_v3_0(
                ts_code=ts_code,
                catalyst_score=0,
                industry_mode="normal"
            )
            total_score = score_card.get("total_score", 0)
            python_score = total_score
            ai_score = 0
            grade = "强信号" if total_score >= 30 else "中信号" if total_score >= 15 else "弱信号"
            ai_report = "\n".join(reasoning)
            
            # 获取当前价格
            current_price = 0.0
            try:
                data = analyzer.fetch_daily_data(ts_code)
                if not data.empty:
                    current_price = float(data.iloc[0]['close'])
            except:
                pass
            
            # 计算盈亏
            pnl_pct = 0.0
            if cost > 0 and current_price > 0:
                pnl_pct = round((current_price - cost) / cost * 100, 2)
            
            # 生成建议（口语化）
            suggestion, reason = generate_suggestion(
                total_score=total_score,
                grade=grade,
                pnl_pct=pnl_pct,
                analyzer=analyzer
            )
            
            # 获取股票名称
            name = holding.get("name", "")
            if not name:
                try:
                    row = analyzer.conn.execute("SELECT name FROM stock_list WHERE ts_code=?", (ts_code,)).fetchone()
                    name = row[0] if row else "未知"
                except:
                    name = "未知"
            
            # 收集结果
            res_item = {
                "ts_code": ts_code,
                "name": name,
                "cost": cost,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "score": total_score,
                "signal": grade,
                "suggestion": suggestion,
                "reason": reason,
                "alerts": []
            }
            result.append(res_item)
            log.info(f"分析完成: {ts_code} {name}, 得分 {total_score}, 建议 {suggestion}")
            
        except Exception as e:
            log.error(f"分析持仓 {holding.get('ts_code', 'unknown')} 失败: {e}")
            continue
    
    log.info(f"持仓健康度分析完成，共 {len(result)} 只股票")
    return result


def generate_suggestion(total_score: int, grade: str, pnl_pct: float, analyzer) -> tuple:
    """
    根据评分和盈亏生成口语化建议
    
    返回：(suggestion, reason)
    """
    # 简单版本的建议逻辑（实际应用中可结合更多因子）
    reason = "根据量化分析结果"
    
    if total_score >= 30:
        suggestion = "继续持有"
        reason = "得分较高，建议继续持有"
    elif total_score >= 20:
        suggestion = "持有观察"
        reason = "得分中等，建议观察"
    elif total_score >= 10:
        suggestion = "建议减仓"
        reason = "得分较低，建议减仓"
    else:
        suggestion = "建议清仓"
        reason = "得分很低，风险较大"
    
    # 结合盈亏补充理由
    if pnl_pct > 0:
        reason += f"，当前浮盈 +{pnl_pct}%"
    elif pnl_pct < 0:
        reason += f"，当前浮亏 {pnl_pct}%"
    
    return (suggestion, reason)


if __name__ == "__main__":
    # 测试运行
    print("=" * 60)
    print("持仓健康度分析测试")
    print("=" * 60)
    
    holdings = check_portfolio("portfolio.json")
    
    if not holdings:
        print("\n暂无持仓")
        sys.exit(0)
    
    print(f"\n分析完成，共 {len(holdings)} 只持仓：\n")
    for holding in holdings:
        print(f"  📌 {holding['ts_code']} {holding['name']}")
        print(f"     成本价: ¥{holding['cost']:.2f} | 当前价: ¥{holding['current_price']:.2f} | 盈亏: {holding['pnl_pct']:+.2f}%")
        print(f"     得分: {holding['score']} 分 | 建议: {holding['suggestion']}")
        print(f"     理由: {holding['reason']}")
        print()
