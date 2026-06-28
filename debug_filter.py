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

print(f'步骤0 - 初始数据: {len(df)}')
print(f'得分>0的数量: {len(df[df["ml_score"] > 0])}')

# ① 得分过滤
df = df[df['ml_score'] > -0.005]
print(f'步骤1 - 得分> -0.005: {len(df)}')

# ② 得分排名
df = df.nlargest(20, 'ml_score')
print(f'步骤2 - 得分排名后(取前20): {len(df)}')
print('前20名行业分布:')
print(df['industry'].value_counts())

# ③ 行业强度
industry_df = pd.read_sql('''
    SELECT sl.industry, AVG(dp.pct_chg) as avg_chg 
    FROM daily_prices dp 
    JOIN stock_list sl ON dp.ts_code = sl.ts_code 
    WHERE dp.trade_date = '20260624' 
      AND sl.industry IS NOT NULL 
    GROUP BY sl.industry 
    ORDER BY avg_chg DESC
''', conn)
strong_industries = industry_df.head(15)['industry'].tolist()
print(f'步骤3 - 强势行业数量: {len(strong_industries)}')
print(f'强势行业列表: {strong_industries}')

df = df[df['industry'].isin(strong_industries)]
print(f'步骤3 - 行业过滤后: {len(df)}')

conn.close()