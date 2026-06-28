# -*- coding: utf-8 -*-
"""
胜率猎手 Agent - 封装与分发脚本
"""
import os
import shutil
import zipfile

def create_package():
    """创建可分发的胜率猎手包"""
    package_name = "WinRateHunter_Agent_V1.0"
    package_dir = os.path.join(os.getcwd(), package_name)
    
    # 创建目录结构
    os.makedirs(package_dir, exist_ok=True)
    os.makedirs(os.path.join(package_dir, "win_rate_hunter"), exist_ok=True)
    
    # 复制核心模块
    files_to_copy = [
        ("win_rate_hunter/__init__.py", "win_rate_hunter/__init__.py"),
        ("win_rate_hunter/__main__.py", "win_rate_hunter/__main__.py"),
        ("win_rate_hunter/factor_pool.py", "win_rate_hunter/factor_pool.py"),
        ("win_rate_hunter/strategy.py", "win_rate_hunter/strategy.py"),
        ("win_rate_hunter/backtest_executor.py", "win_rate_hunter/backtest_executor.py"),
        ("win_rate_hunter/evolution_engine.py", "win_rate_hunter/evolution_engine.py"),
        ("hunt_for_70pct.py", "hunt_for_70pct.py"),
        ("run_hunter.py", "run_hunter.py"),
    ]
    
    for src, dst in files_to_copy:
        if os.path.exists(src):
            shutil.copy(src, os.path.join(package_dir, dst))
            print(f"✓ 复制: {src}")
        else:
            print(f"✗ 缺失: {src}")
    
    # 创建README
    readme_content = """# 胜率猎手 Agent (V1.0)

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
"""
    
    with open(os.path.join(package_dir, "README.md"), 'w', encoding='utf-8') as f:
        f.write(readme_content)
    
    # 创建ZIP包
    zip_path = f"{package_name}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(package_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.getcwd())
                zf.write(file_path, arcname)
    
    print(f"\n📦 封装完成！")
    print(f"   目录: {package_dir}")
    print(f"   ZIP包: {zip_path}")
    
    return package_dir, zip_path

if __name__ == "__main__":
    create_package()