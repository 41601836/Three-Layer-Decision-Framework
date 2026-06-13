# -*- coding: utf-8 -*-
"""
test_board_position.py —— 大类板块合并仓位管控模块 (board_position.py) 单元测试与集成验证脚本
===================================================================================

本脚本测试以下场景，确保仓位管控的准确度、警报的级联传导、动态上浮与下调逻辑以及数据缺失下的高风控降级逻辑：
1. 正常持仓场景
2. 风格板块仓位预警场景
3. 风格板块超仓强制减仓场景
4. 动态调整（上浮/下调）场景
5. 数据缺失降级兜底场景
"""

import os
import sys
import json
from decision_framework.board_position import board_position
from config_loader import *


def get_mock_style_res(tech_intra="中性", tech_cross="中性"):
    """
    构造模拟的 style_result
    """
    return {
        "style_group": [
            {
                "style_name": "科技",
                "board_list": ["半导体", "人工智能"],
                "intraday_strength": tech_intra,
                "cross_day_strength": tech_cross,
                "intra_score": 0.50,
                "cross_score": 0.50
            },
            {
                "style_name": "消费",
                "board_list": ["白酒"],
                "intraday_strength": "中性",
                "cross_day_strength": "中性",
                "intra_score": 0.50,
                "cross_score": 0.50
            }
        ],
        "data_missing_list": []
    }


def get_mock_rot_res(strength="中等轮动"):
    """
    构造模拟的 rotation_result
    """
    return {
        "rotate_strength": strength,
        "trade_signal": "短线参与"
    }


def test_scenario_1_normal():
    print("\n>>> 场景 1：仓位完全正常状态测试")
    style_res = get_mock_style_res()
    rot_res = get_mock_rot_res("中等轮动")
    # 科技仓位：10% + 5% = 15% < 32% (正常线)； 消费仓位：5%
    # 全市场仓位：20% < 64% (正常线)
    positions = {
        "半导体": 0.10,
        "人工智能": 0.05,
        "白酒": 0.05
    }
    
    res = board_position.run(style_res, rot_res, positions)
    
    # 断言校验
    tech_info = [x for x in res["style_position"] if x["style_name"] == "科技"][0]
    assert tech_info["merge_pos"] == 0.15, f"科技合并仓位错误: {tech_info['merge_pos']}"
    assert tech_info["static_status"] == "正常", f"科技静态状态错误: {tech_info['static_status']}"
    assert tech_info["dynamic_max"] == STYLE_MAX_POS, f"科技动态上限错误: {tech_info['dynamic_max']}"
    assert "0.0% -" in tech_info["suggest_pos"], f"建议持仓区间格式错误: {tech_info['suggest_pos']}"
    
    assert res["market_total_pos"] == 0.20, f"全市场仓位错误: {res['market_total_pos']}"
    assert res["market_status"] == "正常", f"全市场状态错误: {res['market_status']}"
    assert "正常" in res["risk_notice"], f"风险提示错误: {res['risk_notice']}"
    assert res["flow_status"] == "继续", f"流程状态错误: {res['flow_status']}"
    print("✅ 场景 1 校验通过！")


def test_scenario_2_warning():
    print("\n>>> 场景 2：触发单风格预警测试")
    style_res = get_mock_style_res()
    rot_res = get_mock_rot_res("中等轮动")
    # 科技仓位：25% + 10% = 35%
    # 上限 40%，预警线 32%。因此科技应当触发预警。
    positions = {
        "半导体": 0.25,
        "人工智能": 0.10
    }
    
    res = board_position.run(style_res, rot_res, positions)
    
    tech_info = [x for x in res["style_position"] if x["style_name"] == "科技"][0]
    assert tech_info["merge_pos"] == 0.35, f"科技仓位错误: {tech_info['merge_pos']}"
    assert tech_info["static_status"] == "预警", f"科技静态状态错误: {tech_info['static_status']}"
    assert "预警" in tech_info["suggest_pos"], f"建议持仓区间错误: {tech_info['suggest_pos']}"
    
    assert "已触发预警线" in res["risk_notice"], f"风险提示错误: {res['risk_notice']}"
    assert res["flow_status"] == "继续", f"流程状态错误: {res['flow_status']}"
    print("✅ 场景 2 校验通过！")


def test_scenario_3_forced_reduce():
    print("\n>>> 场景 3：单风格超仓强制减仓与流程终止测试")
    style_res = get_mock_style_res()
    rot_res = get_mock_rot_res("中等轮动")
    # 科技仓位：30% + 12% = 42% > 40% (上限)
    positions = {
        "半导体": 0.30,
        "人工智能": 0.12
    }
    
    res = board_position.run(style_res, rot_res, positions)
    
    tech_info = [x for x in res["style_position"] if x["style_name"] == "科技"][0]
    assert tech_info["merge_pos"] == 0.42, f"科技仓位错误: {tech_info['merge_pos']}"
    assert tech_info["static_status"] == "强制减仓", f"科技静态状态错误: {tech_info['static_status']}"
    assert "强制减仓" in tech_info["suggest_pos"], f"建议区间错误: {tech_info['suggest_pos']}"
    
    assert res["flow_status"] == "终止", f"流程状态错误: {res['flow_status']}"
    assert "请执行强制减仓" in res["risk_notice"], f"风险提示错误: {res['risk_notice']}"
    print("✅ 场景 3 校验通过！")


def test_scenario_4_dynamic_adjust():
    print("\n>>> 场景 4：动态微调上限测试 (强弱联动)")
    positions = {"半导体": 0.10}
    
    # 1. 科技风格强势时，动态上限应当上浮 1.1 倍 (44%)
    style_res_strong = get_mock_style_res(tech_intra="强势")
    rot_res = get_mock_rot_res("中等轮动")
    res_strong = board_position.run(style_res_strong, rot_res, positions)
    tech_info_strong = [x for x in res_strong["style_position"] if x["style_name"] == "科技"][0]
    expected_strong_max = round(STYLE_MAX_POS * STRONG_COEFF, 4)
    assert tech_info_strong["dynamic_max"] == expected_strong_max, f"强势上限错误: {tech_info_strong['dynamic_max']} != {expected_strong_max}"
    
    # 2. 全市场强轮动时，科技大类虽然不是强势，但也应由于联动规则而上浮上限
    style_res_normal = get_mock_style_res()
    rot_res_strong = get_mock_rot_res("强轮动")
    res_m_strong = board_position.run(style_res_normal, rot_res_strong, positions)
    tech_info_m_strong = [x for x in res_m_strong["style_position"] if x["style_name"] == "科技"][0]
    assert tech_info_m_strong["dynamic_max"] == expected_strong_max, f"全市场强轮动上浮失败: {tech_info_m_strong['dynamic_max']}"
    
    # 3. 科技风格弱势且市场无强轮动时，动态上限下调为 0.9 倍 (36%)
    style_res_weak = get_mock_style_res(tech_intra="弱势", tech_cross="弱势")
    rot_res_weak = get_mock_rot_res("弱轮动")
    res_weak = board_position.run(style_res_weak, rot_res_weak, positions)
    tech_info_weak = [x for x in res_weak["style_position"] if x["style_name"] == "科技"][0]
    expected_weak_max = round(STYLE_MAX_POS * WEAK_COEFF, 4)
    assert tech_info_weak["dynamic_max"] == expected_weak_max, f"弱势下调上限错误: {tech_info_weak['dynamic_max']} != {expected_weak_max}"
    
    print("✅ 场景 4 校验通过！")


def test_scenario_5_data_missing():
    print("\n>>> 场景 5：不传仓位与缺失项分析降级测试")
    # 临时重命名 portfolio.json 模拟文件缺失
    has_portfolio = os.path.exists("portfolio.json")
    if has_portfolio:
        os.rename("portfolio.json", "portfolio.json.tmp")
        
    style_res = get_mock_style_res()
    rot_res = get_mock_rot_res("中等轮动")
    
    try:
        res = board_position.run(style_res, rot_res, None)
        # 应该捕获文件缺失，默认空仓运行
        assert "板块持仓数据(文件不存在)" in res["data_missing_list"], f"未记录缺失项: {res['data_missing_list']}"
        assert res["market_total_pos"] == 0.0, f"默认总仓位错误: {res['market_total_pos']}"
        assert res["market_status"] == "正常", f"状态应该判定正常: {res['market_status']}"
        assert res["flow_status"] == "继续", f"应允许继续流程: {res['flow_status']}"
    finally:
        # 还原 portfolio.json
        if has_portfolio and os.path.exists("portfolio.json.tmp"):
            os.rename("portfolio.json.tmp", "portfolio.json")
            
    print("✅ 场景 5 校验通过！")


def main():
    print("===== 第二层子模块4：大类板块仓位管控 自测程序 =====")
    try:
        test_scenario_1_normal()
        test_scenario_2_warning()
        test_scenario_3_forced_reduce()
        test_scenario_4_dynamic_adjust()
        test_scenario_5_data_missing()
        print("\n🎉 恭喜！大类板块仓位管控模块全场景测试 100% 成功通过！")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n🚨 测试断言失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n🚨 测试运行时异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
