# -*- coding: utf-8 -*-
import os
import sys
import time
import sqlite3
import pandas as pd
import tushare as ts

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

try:
    from scripts.tokens import TOKEN as TUSHARE_TOKEN
except ImportError:
    try:
        from tokens import TOKEN as TUSHARE_TOKEN
    except ImportError:
        TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

if not TUSHARE_TOKEN:
    print("❌ 未找到 Tushare Token")
    sys.exit(1)

pro = ts.pro_api(TUSHARE_TOKEN)
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 获取 2025 年所有的交易日（从 daily_prices 获取）
    cursor.execute("""
        SELECT DISTINCT trade_date 
        FROM daily_prices 
        WHERE trade_date BETWEEN '20250101' AND '20251231'
        ORDER BY trade_date
    """)
    trade_dates = [r[0] for r in cursor.fetchall()]
    print(f"2025年总共有 {len(trade_dates)} 个交易日。")

    # 检查哪些交易日在 moneyflow 中已经有足够多的数据（比如 > 4000 条）
    cursor.execute("""
        SELECT trade_date, COUNT(*) 
        FROM moneyflow 
        WHERE trade_date BETWEEN '20250101' AND '20251231'
        GROUP BY trade_date
    """)
    existing_counts = dict(cursor.fetchall())

    missing_dates = []
    for d in trade_dates:
        count = existing_counts.get(d, 0)
        if count < 4000:
            missing_dates.append(d)

    print(f"其中缺失或数据量不足（<4000条）的交易日有 {len(missing_dates)} 个。")
    if not missing_dates:
        print("✅ 所有交易日资金流数据已完整，无需补拉。")
        conn.close()
        return

    print("开始补拉缺失日期的资金流数据...")
    success_count = 0
    
    for idx, date_str in enumerate(missing_dates, 1):
        print(f"[{idx}/{len(missing_dates)}] 正在拉取 {date_str} 的资金流向...")
        try:
            df = pro.moneyflow(trade_date=date_str)
            time.sleep(0.1) # 避开频次限制
            
            if df is not None and not df.empty:
                def _safe(row, field):
                    val = getattr(row, field, None)
                    return None if val is None or (isinstance(val, float) and val != val) else val

                rows = [
                    (r.ts_code, r.trade_date,
                     _safe(r,'buy_sm_vol'),   _safe(r,'buy_sm_amount'),
                     _safe(r,'sell_sm_vol'),  _safe(r,'sell_sm_amount'),
                     _safe(r,'buy_md_vol'),   _safe(r,'buy_md_amount'),
                     _safe(r,'sell_md_vol'),  _safe(r,'sell_md_amount'),
                     _safe(r,'buy_lg_vol'),   _safe(r,'buy_lg_amount'),
                     _safe(r,'sell_lg_vol'),  _safe(r,'sell_lg_amount'),
                     _safe(r,'buy_elg_vol'),  _safe(r,'buy_elg_amount'),
                     _safe(r,'sell_elg_vol'), _safe(r,'sell_elg_amount'),
                     _safe(r,'net_mf_vol'),   _safe(r,'net_mf_amount'))
                    for r in df.itertuples(index=False)
                ]
                
                cursor.executemany("""
                    INSERT OR REPLACE INTO moneyflow
                      (ts_code, trade_date,
                       buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
                       buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
                       buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
                       buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
                       net_mf_vol, net_mf_amount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
                print(f"  成功写入 {len(rows)} 条记录。")
                success_count += 1
            else:
                print(f"  警告：{date_str} 获取的数据为空。")
        except Exception as e:
            print(f"  ❌ 拉取失败: {e}")
            # 如果是积分不足或权限限制，可能需要排查
            if "接口" in str(e) or "权限" in str(e) or "积分" in str(e):
                print("检测到重大接口权限/积分限制错误，终止任务。")
                break

    print(f"完成。成功补拉 {success_count}/{len(missing_dates)} 个交易日。")
    conn.close()

if __name__ == "__main__":
    main()
