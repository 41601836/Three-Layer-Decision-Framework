# StockAI Funnel — 三层决策系统

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Ollama](https://img.shields.io/badge/LLM-Qwen2.5--7B-green?logo=ollama)
![SQLite](https://img.shields.io/badge/Database-SQLite-lightgrey?logo=sqlite)
![飞书](https://img.shields.io/badge/推送-飞书卡片-blue)
![License](https://img.shields.io/badge/License-MIT-yellow)

**A 股自动化量化分析系统 · 本地大模型驱动 · 三层漏斗架构**

</div>

---

## 📌 项目简介

StockAI Funnel 是一个运行在本地的 A 股智能分析系统，核心设计原则是**极度节省算力**。  
通过「三层漏斗」架构，将 5000+ 只全市场股票逐步收窄，最终只对高得分标的调用本地大模型进行深度分析，结果通过**飞书交互卡片**推送。

```
全市场 5000+ 只
    │
    ▼  【第一层】SQL 预筛选 + Python 并行打分     < 3 秒
    │   涨幅活跃 + 换手率达标 + 成交额充足 → Top-50
    │
    ▼  【第二层】Ollama AI 深度分析               仅对 ≥80 分股票
    │   本地 qwen2.5:7b · Few-Shot Prompt · 并发 3路
    │   输出：一句话决策 + 操作方向 + 止损价
    │
    ▼  【第三层】飞书交互卡片推送
        汇总卡片（分级表格）+ 个股详情卡片（颜色分级）
```

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🛡️ 严格算力保护 | `score < 80` 直接拦截，**绝不**调用 Ollama |
| ⚡ SQL 预筛选 | 联合索引 + 联表查询，全市场过滤 **< 0.5s** |
| 🔀 并行打分 | `ThreadPoolExecutor` 8 线程并行，速度提升 8x |
| 🤖 Ollama 健康检查 | 启动前 ping，服务未起 → **红色报错立即退出** |
| 📊 飞书卡片 | 交互式卡片，颜色分级（🔴S级 / 🟠A级），含完整 AI 报告 |
| 🕐 无人值守 | 四时间点自动调度（08:40 / 11:25 / 14:30 / 19:30） |
| 🖥️ Web 控制台 | FastAPI + 暗色玻璃拟态 UI，实时日志、一键触发 |

---

## 🏗️ 项目结构

```
StockAI/
├── main.py                  # 三层漏斗主程序（v4.0）
├── filter_engine.py         # 第一层：Python 硬过滤 + 评分引擎
├── web_server.py            # FastAPI Web 控制台后端
├── config.py                # 全局参数配置
├── create_indexes.py        # 数据库索引初始化（首次运行必须执行）
├── analyze_stock.py         # 单股深度分析工具
├── start.bat                # 一键启动脚本（Windows）
├── requirements.txt         # Python 依赖
│
├── scripts/
│   ├── scheduler.py         # 定时调度器（4个交易日时间点）
│   ├── ai_report.py         # 第二层：Ollama AI 报告生成
│   ├── feishu_bot.py        # 第三层：飞书交互卡片推送（v3.0）
│   ├── scanner.py           # 全市场扫描引擎
│   ├── fetch_daily.py       # Tushare 数据拉取
│   ├── tokens.example.py    # API 密钥模板（复制为 tokens.py 后填写）
│   └── ts_token.py          # （本地，已 gitignore）
│
├── static/
│   └── index.html           # Web 控制台前端（暗色玻璃拟态 UI）
│
└── db/                      # SQLite 数据库（本地，已 gitignore）
    └── stock_daily.db
```

---

## 🚀 快速开始

### 1. 环境准备

```bash
# Python 3.10+
pip install -r requirements.txt

# 安装并启动 Ollama
# https://ollama.com/download
ollama pull qwen2.5:7b-instruct-q4_K_M
ollama serve
```

### 2. 配置密钥

```bash
# 复制模板
cp scripts/tokens.example.py scripts/tokens.py

# 编辑 scripts/tokens.py，填入：
# - Tushare API Token（https://tushare.pro）
# - 飞书 Webhook URL
```

### 3. 初始化数据库

```bash
# 拉取历史数据（首次运行，约需数小时）
python scripts/fetch_daily.py --mode full

# 创建查询索引（必须执行一次，之后 SQL 查询从 54s → <0.5s）
python create_indexes.py
```

### 4. 启动系统

```bash
# 方式一：Web 控制台（推荐）
python web_server.py
# 访问 http://localhost:8080

# 方式二：直接运行一次分析
python main.py --session 手动触发

# 方式三：挂机自动调度
python scripts/scheduler.py

# 方式四：Windows 一键启动
start.bat
```

### 5. 验证 Ollama 连通性

```bash
python main.py --check-ollama
```

---

## ⚙️ 参数调整

### `main.py` 全局参数

```python
AI_TRIGGER_SCORE = 80    # 低于此分不调用 Ollama（核心门槛）
MAX_CONCURRENT   = 3     # Ollama 并发数（防显存溢出）
TOP_N            = 50    # 打分后取 Top-N 进入第二层
SCORE_WORKERS    = 8     # 并行打分线程数
PRE_SCREEN_PCT_MIN  = 1.0  # 预筛选涨幅下限（%）
PRE_SCREEN_PCT_MAX  = 9.5  # 预筛选涨幅上限（%, 9.5防追涨停）
PRE_SCREEN_TURN_MIN = 2.0  # 预筛选换手率下限（%）
```

### CLI 参数

```bash
python main.py \
  --session  "尾盘信号" \
  --market   "放量" \        # 放量/缩量/平量
  --sector   "正常" \        # 正常/超配/欠配
  --threshold 75  \          # 临时覆盖 AI 触发门槛
  --pct-min  1.5  \          # 调整预筛选涨幅下限
  --turn-min 3.0             # 调整换手率门槛
```

---

## 📊 飞书卡片样式

| 评级 | 分数 | 卡片颜色 | 含义 |
|------|------|---------|------|
| 🔴 S 级 | ≥ 85 分 | 红色 Header | 强烈关注，优先跟踪 |
| 🟠 A 级 | 80-84 分 | 橙色 Header | 纳入观察，等待时机 |
| 🟡 B 级 | 60-79 分 | 黄色 | 参考，不建议操作 |

每张卡片包含：
- **综合得分**（进度条 + 数字）
- **AI 核心结论**（一句话决策）
- **操作方向** / **防守止损价** / **评分构成**
- **完整 Markdown 诊断报告**

---

## 🔑 数据源

- **行情数据**：[Tushare Pro](https://tushare.pro)（需 5000+ 积分）
- **AI 推理**：本地 [Ollama](https://ollama.com) + `qwen2.5:7b-instruct-q4_K_M`
- **推送**：飞书自定义机器人 Webhook

---

## ⚠️ 免责声明

本系统仅供学习研究使用，不构成任何投资建议。  
股票市场存在风险，投资需谨慎。

---

## 📄 License

MIT License © 2025
