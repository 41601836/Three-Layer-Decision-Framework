# -*- coding: utf-8 -*-
from decision_framework.macro_query import macro_query

def main():
    print("===== 第一层宏观数据查询测试 =====")
    # 1. 全球宏观
    print("\n1. 全球宏观指标：")
    print(macro_query.get_global_macro())
    # 2. 市场资金
    print("\n2. 市场资金指标：")
    print(macro_query.get_market_capital())
    # 3. 市场情绪
    print("\n3. 市场情绪指标：")
    print(macro_query.get_market_sentiment())
    # 4. 盘中快照
    print("\n4. 10:30半日快照：")
    print(macro_query.get_snapshot_1030())

if __name__ == "__main__":
    main()
