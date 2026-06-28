# -*- coding: utf-8 -*-
"""
show_stock_analysis.py —— 股票主力大单、股东人数变化及板块强度展示脚本
=====================================================================
用法:
  python scripts/show_stock_analysis.py --code <ts_code> [--date <YYYYMMDD>]

示例:
  python scripts/show_stock_analysis.py --code 600519.SH --date 20251231
"""

import os
import sys
import sqlite3
import argparse
import pandas as pd
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

from industry_strength import calc_industry_strength_for_period

def get_stock_basic(cursor, ts_code):
    cursor.execute("SELECT name, industry FROM stock_list WHERE ts_code = ?", (ts_code,))
    row = cursor.fetchone()
    if row:
        return row[0], row[1]
    return "未知", "未知"

def show_analysis(ts_code, target_date=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. 确认目标日期
    if not target_date:
        cursor.execute("SELECT MAX(trade_date) FROM daily_prices WHERE ts_code = ?", (ts_code,))
        row = cursor.fetchone()
        target_date = row[0] if row and row[0] else datetime.now().strftime("%Y%m%d")
        
    name, industry = get_stock_basic(cursor, ts_code)
    
    print(f"\n======================================================================")
    print(f"       📊 StockAI 极客透视 - {name} ({ts_code}) | 研判日期: {target_date}")
    print(f"======================================================================\n")
    print(f"【所属板块】: {industry}")
    
    # 2. 板块强度因子查询
    try:
        # 计算该日期下近5日行业强度排行
        df_ind = calc_industry_strength_for_period(conn, n_days=5, target_date=target_date)
        if not df_ind.empty and industry in df_ind["industry"].values:
            ind_info = df_ind[df_ind["industry"] == industry].iloc[0]
            tier_map = {"main": "🟢 主线行业 (主力积极攻势)", "backup": "🟡 备选行业 (板块蓄势中)", "avoid": "🔴 回避行业 (退潮/失血板块)"}
            tier_desc = tier_map.get(ind_info["tier"], ind_info["tier"])
            
            print(f"【板块强度评估】:")
            print(f"  - 行业综合排名  : 第 {ind_info['rank']} 名 (共 {len(df_ind)} 个行业)")
            print(f"  - 综合热度评分  : {int(ind_info['composite_score']*100)} 分")
            print(f"  - 行业所处梯队  : {tier_desc}")
            print(f"  - 近5日主力资金 : {ind_info['net_mf_amount']:.2f} 亿元 {'📈' if ind_info['net_mf_amount'] > 0 else '📉'}")
            print(f"  - 近5日上涨占比 : {ind_info['rise_ratio']:.1f}%")
            print(f"  - 近5日成交占比 : {ind_info['volume_ratio']:.2f}%")
        else:
            print("【板块强度评估】: ⚠️ 无法获取该行业的强度排行数据（数据不足或未分类）")
    except Exception as e:
        print(f"【板块强度评估】: ❌ 计算失败: {e}")
        
    print("\n----------------------------------------------------------------------")
    
    # 3. 主力资金流向变化情况 (近5个交易日)
    print("【主力大单资金流入变化 (近 5 个交易日)】:")
    try:
        df_money = pd.read_sql("""
            SELECT trade_date, 
                   buy_elg_amount, sell_elg_amount, 
                   buy_lg_amount, sell_lg_amount
            FROM moneyflow
            WHERE ts_code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 5
        """, conn, params=(ts_code, target_date))
        
        if not df_money.empty:
            df_money = df_money.iloc[::-1] # 转回正序展示
            df_money["buy_elg_amount"] = pd.to_numeric(df_money["buy_elg_amount"]).fillna(0)
            df_money["sell_elg_amount"] = pd.to_numeric(df_money["sell_elg_amount"]).fillna(0)
            df_money["buy_lg_amount"] = pd.to_numeric(df_money["buy_lg_amount"]).fillna(0)
            df_money["sell_lg_amount"] = pd.to_numeric(df_money["sell_lg_amount"]).fillna(0)
            
            # 主力 = 超大单 (elg) + 大单 (lg)
            df_money["buy_main"] = df_money["buy_elg_amount"] + df_money["buy_lg_amount"]
            df_money["sell_main"] = df_money["sell_elg_amount"] + df_money["sell_lg_amount"]
            df_money["net_main_wan"] = df_money["buy_main"] - df_money["sell_main"] # 单位：万元
            
            for idx, r in df_money.iterrows():
                net_val = r["net_main_wan"]
                direction = "🟢 净流入" if net_val > 0 else "🔴 净流出"
                # 把万元转换成更好读的形式
                if abs(net_val) >= 10000:
                    val_str = f"{net_val / 10000:.2f} 亿元"
                else:
                    val_str = f"{net_val:.2f} 万元"
                    
                # 绘制小能量柱
                bar_size = min(int(abs(net_val) / 500), 15)
                bar = ("█" * bar_size) if bar_size > 0 else "▏"
                color_bar = f"\033[32m{bar}\033[0m" if net_val > 0 else f"\033[31m{bar}\033[0m"
                
                print(f"  - {r['trade_date']}: {direction} {val_str:<12} {color_bar}")
        else:
            print("  ⚠️ 无主力资金流向记录。")
    except Exception as e:
        print(f"  ❌ 查询资金流失败: {e}")
        
    print("\n----------------------------------------------------------------------")
    
    # 4. 股东人数变化情况 (最近3次披露)
    print("【股东人数变化与筹码集中度研判】:")
    try:
        df_holder = pd.read_sql("""
            SELECT ann_date, end_date, holder_num
            FROM stk_holdernumber
            WHERE ts_code = ? AND end_date <= ?
            ORDER BY end_date DESC LIMIT 3
        """, conn, params=(ts_code, target_date))
        
        if not df_holder.empty:
            df_holder = df_holder.iloc[::-1] # 转回正序展示
            df_holder["holder_num"] = pd.to_numeric(df_holder["holder_num"]).fillna(0)
            df_holder["prev_num"] = df_holder["holder_num"].shift(1)
            df_holder["pct_change"] = ((df_holder["holder_num"] - df_holder["prev_num"]) / df_holder["prev_num"]) * 100
            
            for idx, r in df_holder.iterrows():
                ann_date_str = r['ann_date'] if r['ann_date'] else "未披露"
                h_num = int(r['holder_num'])
                pct_val = r['pct_change']
                
                if pd.isna(pct_val):
                    change_desc = "(初始对比点)"
                else:
                    arrow = "📉 筹码集中" if pct_val < 0 else "📈 筹码分散"
                    change_desc = f"{arrow} {pct_val:+.2f}%"
                
                print(f"  - 截止日期: {r['end_date']} (公告日: {ann_date_str})")
                print(f"    股东户数: {h_num:,} 户 | {change_desc}")
                
            # 给出筹码诊断意见
            if len(df_holder) >= 2:
                latest_pct = df_holder.iloc[-1]["pct_change"]
                if not pd.isna(latest_pct):
                    if latest_pct < -3.0:
                        print(f"\n  🔥 [机构动向研判]: 筹码正以较快速度向少数主力/私募机构集中，吸筹迹象显著！")
                    elif latest_pct > 3.0:
                        print(f"\n  ⚠️ [机构动向研判]: 股东人数明显上升，筹码正在向散户流失，警惕主力派发风险！")
                    else:
                        print(f"\n  ⚖️ [机构动向研判]: 筹码集中度变动微弱，处于区间震荡磨盘阶段。")
        else:
            print("  ⚠️ 无股东人数记录（该股票可能近期未更新披露）。")
    except Exception as e:
        print(f"  ❌ 查询股东人数失败: {e}")
        
    print(f"======================================================================\n")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="展示股票主力大单及股东人数透视报告")
    parser.add_argument("--code", default="600519.SH", help="股票TS代码 (e.g. 600519.SH)")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD (默认最新)")
    args = parser.parse_args()
    
    show_analysis(args.code, args.date)
