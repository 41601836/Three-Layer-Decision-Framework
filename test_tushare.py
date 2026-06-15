#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tushare 账号连接测试脚本
检查 API 连接、Token 有效性、数据获取能力
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tushare as ts
import pandas as pd
from datetime import datetime, timedelta

def test_tushare_connection():
    print("=" * 60)
    print("     Tushare 账号连接测试")
    print("=" * 60)
    
    # 1. 初始化 Tushare
    try:
        pro = ts.pro_api()
        print("✅ Tushare API 初始化成功")
    except Exception as e:
        print(f"❌ Tushare API 初始化失败: {e}")
        return False
    
    # 2. 获取账号信息
    try:
        # 尝试不同的接口名
        try:
            user_info = pro.userinfo()
        except:
            user_info = pro.user_info()
        
        print(f"\n📋 账号信息:")
        if 'user_id' in user_info.columns:
            print(f"   用户ID: {user_info['user_id'].values[0]}")
        if 'user_name' in user_info.columns:
            print(f"   用户昵称: {user_info['user_name'].values[0]}")
        if 'score' in user_info.columns:
            print(f"   积分: {user_info['score'].values[0]}")
        if 'level' in user_info.columns:
            print(f"   等级: {user_info['level'].values[0]}")
        if 'remain_times' in user_info.columns:
            print(f"   剩余调用次数: {user_info['remain_times'].values[0]}")
        if 'access_list' in user_info.columns:
            print(f"   权限列表: {user_info['access_list'].values[0]}")
        print("✅ 账号信息获取成功")
    except Exception as e:
        print(f"⚠️ 获取账号信息失败: {e}")
        print("   (某些版本的 Tushare 可能没有此接口)")
    
    # 3. 测试基础数据接口
    print("\n" + "=" * 60)
    print("     基础数据接口测试")
    print("=" * 60)
    
    # 测试交易日历
    try:
        today = datetime.now().strftime("%Y%m%d")
        cal = pro.trade_cal(start_date=today, end_date=today)
        print(f"📅 交易日历接口: {'✅' if not cal.empty else '❌'}")
        if not cal.empty:
            print(f"   今日 {today} 是否交易日: {'是' if cal['is_open'].values[0] == 1 else '否'}")
    except Exception as e:
        print(f"❌ 交易日历接口失败: {e}")
    
    # 测试股票列表
    try:
        stocks = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,list_date')
        print(f"📊 股票列表接口: ✅ (获取到 {len(stocks)} 只股票)")
    except Exception as e:
        print(f"❌ 股票列表接口失败: {e}")
    
    # 4. 测试日线数据接口
    print("\n" + "=" * 60)
    print("     日线数据接口测试")
    print("=" * 60)
    
    try:
        # 获取最近一个交易日的数据
        last_date = datetime.now() - timedelta(days=1)
        while last_date.weekday() >= 5:  # 跳过周末
            last_date -= timedelta(days=1)
        trade_date = last_date.strftime("%Y%m%d")
        
        # 测试拉取单只股票
        df = pro.daily(ts_code='600519.SH', start_date=trade_date, end_date=trade_date)
        if not df.empty:
            print(f"📈 日线数据接口: ✅")
            print(f"   股票: {df['ts_code'].values[0]}")
            print(f"   日期: {df['trade_date'].values[0]}")
            print(f"   收盘价: {df['close'].values[0]}")
        else:
            print(f"⚠️ 日线数据接口: 数据为空 (可能非交易日)")
    except Exception as e:
        if "no privilege" in str(e).lower():
            print(f"❌ 日线数据接口失败: 权限不足 ({e})")
        elif "authentication" in str(e).lower():
            print(f"❌ 日线数据接口失败: 认证失败 ({e})")
        else:
            print(f"❌ 日线数据接口失败: {e}")
    
    # 5. 测试指数数据接口
    print("\n" + "=" * 60)
    print("     指数数据接口测试")
    print("=" * 60)
    
    try:
        index_df = pro.index_daily(ts_code='000001.SH', start_date=trade_date, end_date=trade_date)
        if not index_df.empty:
            print(f"📉 指数数据接口: ✅")
            print(f"   指数: {index_df['ts_code'].values[0]}")
            print(f"   日期: {index_df['trade_date'].values[0]}")
            print(f"   收盘价: {index_df['close'].values[0]}")
        else:
            print(f"⚠️ 指数数据接口: 数据为空")
    except Exception as e:
        print(f"❌ 指数数据接口失败: {e}")
    
    print("\n" + "=" * 60)
    print("     测试完成")
    print("=" * 60)
    return True

if __name__ == "__main__":
    test_tushare_connection()