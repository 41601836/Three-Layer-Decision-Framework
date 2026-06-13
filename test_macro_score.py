# -*- coding: utf-8 -*-
# test_macro_score.py
from decision_framework.macro_score import macro_score

def main():
    print("===== 六大维度打分 & 综合评分卡 测试 =====")
    # 执行完整打分流程
    result = macro_score.run()

    # 打印维度明细
    print("\n【六大维度打分明细】")
    for dim in result["dimensions"]:
        print(f"{dim['name']} | 得分:{dim['score']} | 灯态:{dim['light']} | 原因:{dim['reason']}")

    # 打印综合结果
    print("\n【综合判定结果】")
    print(f"综合总分: {result['total_score']}")
    print(f"操作模式: {result['operate_mode']}")
    print(f"仓位上限: {result['position_limit']}")
    print(f"流程状态: {result['flow_status']}")

    # 打印数据缺失项
    if result["data_missing_list"]:
        print(f"\n【数据缺失项】: {result['data_missing_list']}")
    else:
        print("\n【数据缺失项】: 无")

if __name__ == "__main__":
    main()
