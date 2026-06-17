import akshare as ak

print("VIX:", ak.futures_global_hist(symbol="VIX").tail(1))
try:
    print("Brent:", ak.futures_global_hist(symbol="Brent").tail(1))
except Exception as e:
    print("Brent fail:", e)

try:
    print("US DJIA:", ak.stock_us_daily(symbol="DJI").tail(1))
except Exception as e:
    print("US DJIA fail:", e)
    
try:
    print("PMI:", ak.macro_cn_pmi().tail(1))
except Exception as e:
    print("PMI fail:", e)
