import sqlite3, sys
db = sqlite3.connect('db/stock_daily.db')

# 任务4：检查2025年北向数据
cnt = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2025%'").fetchone()[0]
print(f'[任务4] hsgt_moneyflow 2025年数据: {cnt} 条')
if cnt >= 200:
    print('[任务4] OK: 数据充足')
else:
    print(f'[任务4] WARN: 数据不足，需要运行 python scripts/fetch_hsgt.py --start 20250101')

# 任务3验证：daily_basic是否有circ_mv
print()
print('[任务3] 检查daily_basic表结构...')
try:
    cols = [c[1] for c in db.execute('PRAGMA table_info(daily_basic)').fetchall()]
    has_circ = 'circ_mv' in cols
    print(f'  daily_basic 有 circ_mv 列: {has_circ}')
    if has_circ:
        rows = db.execute('SELECT ts_code, trade_date, circ_mv FROM daily_basic ORDER BY trade_date DESC LIMIT 5').fetchall()
        print(f'  最新数据样本:')
        for r in rows:
            mv_yi = float(r[2]) / 10000 if r[2] else 0
            flag = '微盘' if mv_yi < 10 else '正常'
            print(f'    {r[0]} {r[1]} circ_mv={r[2]:.0f}万 ({mv_yi:.1f}亿) [{flag}]')
    else:
        print('  [WARN] daily_basic 无circ_mv，过滤器将回退到stk_factor表')
except Exception as e:
    print(f'  daily_basic查询异常: {e}')

# 检查stk_factor表是否存在
print()
try:
    r = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stk_factor'").fetchone()
    print(f'[任务3] stk_factor 表存在: {r is not None}')
    if r:
        cols2 = [c[1] for c in db.execute('PRAGMA table_info(stk_factor)').fetchall()]
        print(f'  stk_factor 列: {cols2[:10]}...')
except Exception as e:
    print(f'stk_factor 检查失败: {e}')

db.close()
print()
print('[任务3] filter_engine.py 已更新微盘股过滤（circ_mv<10亿直接排除）')
