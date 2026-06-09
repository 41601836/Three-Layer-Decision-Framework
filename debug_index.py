# -*- coding: utf-8 -*-
"""调试：检查akshare返回的上证指数数据格式"""
import pandas as pd
import akshare as ak

# 拉取数据
df = ak.stock_zh_index_daily(symbol="sh000001")
print("Columns:", df.columns.tolist())
print("\nHead:")
print(df.head())
print("\nData types:")
print(df.dtypes)