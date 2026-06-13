# test_macro_revise.py
from decision_framework.macro_revise import macro_revise
from decision_framework.macro_score import macro_score
from decision_framework.macro_query import macro_query

def main():
    print("===== 盘中实时修正机制 测试 =====")
    # 1. 获取盘前原始结果
    pre_score = macro_score.run()
    # 2. 模拟盘前预判基准值（实际业务由上层传入）
    pre_expect = {
        "up_down_ratio": 1.1,
        "top_board_change": 2.5,
        "limit_up_num": 20
    }
    # 3. 执行盘中修正
    revise_res = macro_revise.run(pre_score, pre_expect)

    # 打印结果
    print(f"盘前原始模式: {revise_res['original_mode']}")
    print(f"修正后模式: {revise_res['revised_mode']}")
    print("\n修正明细：")
    for item in revise_res["revise_items"]:
        print(f"{item['rule_name']} | {item['status']} | {item['reason']}")
    print(f"\n板块调整建议: {revise_res['board_adjust']}")
    if revise_res["data_missing_list"]:
        print(f"数据缺失项: {revise_res['data_missing_list']}")

if __name__ == "__main__":
    main()
