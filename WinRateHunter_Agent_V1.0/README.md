# 胜率猎手 Agent (V1.0)

基于遗传算法的量化策略自动进化系统，目标胜率≥70%。

## 功能特性

- **因子池管理器**: 管理20+个量价/资金/筹码/基本面因子
- **策略生成器**: 随机组合因子生成策略
- **回测执行器**: 高效回测引擎
- **进化引擎**: 遗传算法优化策略
- **结果存储**: SQLite数据库持久化

## 快速开始

```bash
# 安装依赖
pip install pandas numpy

# 运行目标胜率70%训练
python hunt_for_70pct.py

# 或使用命令行接口
python -m win_rate_hunter --start 20260101 --end 20260625 --generations 50
```

## 项目结构

```
WinRateHunter_Agent_V1.0/
├── win_rate_hunter/
│   ├── __init__.py
│   ├── __main__.py
│   ├── factor_pool.py
│   ├── strategy.py
│   ├── backtest_executor.py
│   └── evolution_engine.py
├── hunt_for_70pct.py    # 目标胜率70%专项训练
├── run_hunter.py        # 通用训练脚本
└── README.md
```

## 训练参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| --start | 开始日期 | 20260101 |
| --end | 结束日期 | 20260625 |
| --generations | 进化代数 | 50 |
| --population | 种群大小 | 30 |
| --elite | 精英数量 | 5 |

## 输出结果

训练完成后会生成 `training_result_70pct.json`，包含：
- 最佳策略配置
- 回测绩效指标
- 训练历史记录
