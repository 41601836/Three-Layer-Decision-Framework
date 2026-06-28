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

# ① 得分过滤
df = df[df['ml_score'] > -0.005]

# ② 得分排名
df = df.nlargest(20, 'ml_score')

# ③ 行业强度（放宽）
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
industry_rank = {row['industry']: i+1 for i, row in industry_df.iterrows()}
filtered_df = df[df['industry'].isin(strong_industries)]
if len(filtered_df) < 3:
    pass  # 放宽限制
df['industry_rank'] = df['industry'].map(industry_rank).fillna(999)

# ④ 技术面确认
codes = df['ts_code'].tolist()
placeholders = ','.join(['?'] * len(codes))
tech_df = pd.read_sql(f'''
    SELECT ts_code, 
           (close - LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date)) / LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date) AS ret_5d,
           AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5
    FROM daily_prices
    WHERE ts_code IN ({placeholders})
      AND trade_date <= '20260624'
    ORDER BY ts_code, trade_date DESC
''', conn, params=codes)
tech_df = tech_df.groupby('ts_code').first().reset_index()
df = df.merge(tech_df[['ts_code', 'ret_5d', 'ma5']], on='ts_code', how='left')
df = df[(df['ret_5d'] > -0.08) & (df['close'] > df['ma5'])]
print(f'步骤4后: {len(df)}')

# ⑤ 流动性过滤
amount_df = pd.read_sql(f'''
    SELECT ts_code, AVG(amount) / 10000 AS avg_amount_wan
    FROM daily_prices
    WHERE ts_code IN ({placeholders})
      AND trade_date >= date('20260624', '-20 days')
    GROUP BY ts_code
''', conn, params=codes)
df = df.merge(amount_df, on='ts_code', how='left')
high_score_threshold = df['ml_score'].quantile(0.80) if len(df) > 0 else 0
df = df[(df['avg_amount_wan'] >= 2000) | (df['avg_amount_wan'].isna() & (df['ml_score'] >= high_score_threshold))]
print(f'步骤5后: {len(df)}')

# ⑥ 止损空间
low20_df = pd.read_sql(f'''
    SELECT ts_code, MIN(low) AS low_20d
    FROM daily_prices
    WHERE ts_code IN ({placeholders})
      AND trade_date >= date('20260624', '-20 days')
    GROUP BY ts_code
''', conn, params=codes)
df = df.merge(low20_df, on='ts_code', how='left')
print(f'步骤6前 - low_20d数据:')
for _, row in df.iterrows():
    low_20d = row.get('low_20d', 'NaN')
    print(f"  {row['ts_code']}: close={row['close']}, low_20d={low_20d}")

df['drawdown'] = (df['close'] - df['low_20d']) / df['low_20d']
print(f'步骤6前 - drawdown计算:')
for _, row in df.iterrows():
    print(f"  {row['ts_code']}: drawdown={row['drawdown']:.2%}")

df = df[df['drawdown'] < 0.20]
print(f'步骤6后: {len(df)}')

conn.close()