import sqlite3
import pandas as pd
from ml_pipeline import load_model, fetch_today_factors, neutralize_factors

conn = sqlite3.connect('db/stock_daily.db')
model, scaler_params, industry_means = load_model()

df = fetch_today_factors(conn, '20260624')
df = df.dropna(subset=['pb', 'pe', 'price_range'])
df = neutralize_factors(df, scaler_params, industry_means)
X = df[['factor_pb', 'factor_price_range', 'factor_pe']].values
df['ml_score'] = model.predict(X)

# 查看林业板块的股票
forest_stocks = df[df['industry'] == '林业'].sort_values('ml_score', ascending=False)
print('=== 林业板块股票 ===')
print(forest_stocks[['ts_code', 'name', 'ml_score', 'pb', 'pe']])

# 查看行业强度排名
industry_df = pd.read_sql('''
    SELECT sl.industry, AVG(dp.pct_chg) as avg_chg 
    FROM daily_prices dp 
    JOIN stock_list sl ON dp.ts_code = sl.ts_code 
    WHERE dp.trade_date = '20260624' 
      AND sl.industry IS NOT NULL 
    GROUP BY sl.industry 
    ORDER BY avg_chg DESC
''', conn)
print('\n=== 行业强度排名前15 ===')
print(industry_df.head(15))

# 检查林业是否在前10
print(f'\n林业行业排名: {industry_df[industry_df["industry"] == "林业"].index[0] + 1}')

conn.close()