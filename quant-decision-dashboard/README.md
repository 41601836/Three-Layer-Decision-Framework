# 量化决策仪表盘

## 项目简介
量化决策仪表盘是一个用于金融市场分析和决策支持的Web应用程序。该项目提供了一个直观的仪表盘界面，用户可以通过该界面查看市场状态、核心指标、交易指令等信息。

## 项目结构
```
quant-decision-dashboard
├── static
│   └── decision
│       ├── css
│       │   └── common.css        # 全局通用样式
│       ├── index.html            # 仪表盘首页
│       ├── macro.html            # 宏观诊断页面
│       ├── board.html            # 板块分析页面
│       ├── stock.html            # 个股交易页面
│       └── system.html           # 系统设置页面
├── backend
│   ├── web_server.py             # 后端服务主程序
│   ├── api
│   │   └── decision.py           # 决策相关API接口
│   └── requirements.txt          # 项目所需Python库
├── .gitignore                    # 版本控制忽略文件
└── README.md                     # 项目文档说明
```

## 功能说明
- **仪表盘首页**：展示系统状态、市场指数、核心指标等信息。
- **宏观诊断**：提供宏观经济分析工具。
- **板块分析**：分析不同板块的市场表现。
- **个股交易**：提供个股的交易建议和指令。
- **系统设置**：用户可以在此页面进行系统配置。

## 使用方法
1. 克隆项目到本地：
   ```
   git clone <repository-url>
   ```
2. 安装依赖：
   ```
   cd backend
   pip install -r requirements.txt
   ```
3. 启动后端服务：
   ```
   python web_server.py
   ```
4. 打开浏览器访问 `http://localhost:8000/static/decision/index.html` 查看仪表盘。

## 贡献
欢迎任何形式的贡献，您可以通过提交问题、建议或直接提交代码来帮助改进项目。

## 许可证
本项目采用MIT许可证，详细信息请查看LICENSE文件。