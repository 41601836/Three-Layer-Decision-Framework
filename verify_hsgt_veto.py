import sqlite3
db = sqlite3.connect('db/stock_daily.db')
r2023 = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2023%'").fetchone()[0]
r2024 = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2024%'").fetchone()[0]
r2025 = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2025%'").fetchone()[0]
rmin  = db.execute("SELECT MIN(trade_date) FROM hsgt_moneyflow").fetchone()[0]
rmax  = db.execute("SELECT MAX(trade_date) FROM hsgt_moneyflow").fetchone()[0]
total = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow").fetchone()[0]
print(f"总计: {total} 条")
print(f"2023: {r2023} | 2024: {r2024} | 2025: {r2025}")
print(f"Range: {rmin} ~ {rmax}")
# 验证analyze_stock中的三重否决读取
import sys
sys.path.insert(0, '.')
try:
    from analyze_stock import StockAnalyzer
    a = StockAnalyzer()
    # 检查 _check_triple_outflow 方法是否存在
    has_triple = hasattr(a, '_check_triple_outflow') or hasattr(a, 'check_triple_outflow')
    print(f"StockAnalyzer 三重否决方法存在: {has_triple}")
    # 扫描实际使用北向的代码路径
    import inspect
    src = inspect.getsource(a.analyze_v3_0)
    has_hsgt_read = 'hsgt' in src.lower() or 'north' in src.lower()
    print(f"analyze_v3_0 含北向读取: {has_hsgt_read}")
    a.close()
except Exception as e:
    print(f"StockAnalyzer 检查失败: {e}")
db.close()
