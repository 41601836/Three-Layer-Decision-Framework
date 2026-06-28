# 胜率猎手 Agent v1.0 - 75%胜率策略

基于遗传算法的量化策略自动进化系统，已成功进化出75%胜率的交易策略。

## 📊 策略绩效

| 指标 | 数值 |
|------|------|
| **胜率** | 75.0% |
| **总收益** | 20.00% |
| **最大回撤** | 4.98% |
| **信号数** | 73 |
| **交易数** | 12 |
| **训练周期** | 20260101 ~ 20260625 |

## 🏆 最佳策略

**策略名称**: Strategy_0119

### 因子组合

| 因子 | 权重 | 阈值 | 方向 |
|------|------|------|------|
| 20日收益率 | 1.48 | 0.6770 | 越高越好 |
| 获利盘占比 | 0.97 | 0.3208 | 越低越好 |
| 市净率 | 0.61 | 0.6964 | 越低越好 |
| 流通市值(亿) | 0.61 | 0.4795 | 越小越好 |

**持有天数**: 10天

## 📁 项目结构

```
release/v1.0_75pct/
├── __init__.py           # 模块初始化
├── __main__.py           # 命令行入口
├── factor_pool.py        # 因子池管理器 (20+因子)
├── strategy.py           # 策略类和生成器
├── backtest_executor.py  # 回测执行器
├── evolution_engine.py   # 进化引擎
├── hunt_for_70pct.py     # 目标胜率70%训练脚本
├── training_result_70pct.json  # 训练结果
└── hunter_results.db     # SQLite数据库
```

## 🚀 使用方式

```bash
# 验证策略
python -c "
import sys
sys.path.insert(0, '.')
from factor_pool import FactorPool
from strategy import Strategy
from backtest_executor import BacktestExecutor
import json

with open('training_result_70pct.json', 'r') as f:
    data = json.load(f)

fp = FactorPool()
factors = [fp.get_factor_by_name(n) for n in data['best_strategy']['factors']]
strategy = Strategy(
    name=data['best_strategy']['name'],
    factors=factors,
    weights=data['best_strategy']['weights'],
    thresholds=data['best_strategy']['thresholds'],
    hold_days=data['best_strategy']['hold_days']
)

executor = BacktestExecutor('20260101', '20260625')
result = executor.execute(strategy)
print(f'胜率: {result[\"win_rate\"]:.1%}')
"
```

## 📈 训练历史

| 代数 | 胜率 | 收益 | 信号数 |
|------|------|------|--------|
| 初始 | 44.8% | -29.54% | 1620 |
| GEN 3 | 50.0% | 2.79% | 31 |
| GEN 4 | 69.2% | 14.47% | 80 |
| GEN 5 | 75.0% | 20.00% | 73 |

## 🗓️ 分阶段表现

| 阶段 | 周期 | 胜率 | 收益 |
|------|------|------|------|
| 第一阶段 | 1-2月 | 0.0% | 0.00% |
| 第二阶段 | 3-4月 | 100.0% | 15.00% |
| 第三阶段 | 5-6月 | 75.0% | 6.51% |

## ⚠️ 注意事项

1. 策略在第一季度未产生信号，可能与当时市场环境相关
2. 建议在不同市场周期进行验证
3. 实际交易前请进行充分的样本外测试

## 📝 版本信息

- **版本**: v1.0
- **胜率**: 75%
- **目标**: 70%
- **状态**: ✅ 达标
- **日期**: 2026-06-27