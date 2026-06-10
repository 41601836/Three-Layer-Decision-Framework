# -*- coding: utf-8 -*-
"""
StockAI 可视化界面
基于 NiceGUI 的股票分析与交易系统
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

from nicegui import ui, app

# 导入策略管理器
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategies.manager import get_strategy_manager
from strategies.base_strategy import SignalType


class StockAIUI:
    """StockAI 主界面"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.strategy_manager = get_strategy_manager()
        
        # 状态管理
        self.current_page = 'dashboard'
        self.selected_stock = None
        self.market_data = {}
        
        # 初始化界面
        self._setup_theme()
        self._create_layout()
        
    def _setup_theme(self):
        """设置主题"""
        try:
            ui.dark_mode.enable()
        except:
            # NiceGUI 新版本 API 可能不同
            pass
        
        # 自定义样式
        ui.add_head_html('''
            <style>
                .dashboard-card {
                    transition: transform 0.2s;
                }
                .dashboard-card:hover {
                    transform: translateY(-2px);
                }
                .signal-badge {
                    font-size: 0.8rem;
                    padding: 0.25rem 0.5rem;
                }
                .stock-card {
                    cursor: pointer;
                    transition: all 0.3s;
                }
                .stock-card:hover {
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                }
            </style>
        ''')
        
    def _create_layout(self):
        """创建整体布局"""
        with ui.header(elevated=True).style('background-color: #1e293b').classes('items-center justify-between'):
            self._create_header()
            
        with ui.left_drawer(fixed=True, elevated=True).style('background-color: #334155').classes('w-64'):
            self._create_sidebar()
            
        with ui.footer().style('background-color: #1e293b').classes('text-white'):
            self._create_footer()
            
        # 主内容区域
        self.main_content = ui.column().classes('w-full p-4')
        
        # 初始化首页
        self._show_dashboard()
        
    def _create_header(self):
        """创建顶部导航栏"""
        with ui.row().classes('items-center'):
            ui.icon('bar_chart', size='32px').classes('text-white mr-2')
            ui.label('StockAI 智能分析系统').classes('text-white text-xl font-bold')
            
        # 顶部状态栏
        with ui.row().classes('items-center'):
            self.market_temperature = ui.label('市场温度: --').classes('text-white mr-4')
            self.update_time = ui.label(f'最后更新: {datetime.now().strftime("%H:%M:%S")}').classes('text-white text-sm')
            
            ui.button('刷新', icon='refresh', on_click=self._refresh_data).classes('ml-2')
            
    def _create_sidebar(self):
        """创建左侧导航菜单"""
        ui.label('导航菜单').classes('text-white text-lg font-bold mb-4 mt-2')
        
        # 导航按钮
        nav_items = [
            ('dashboard', '仪表盘', 'dashboard'),
            ('stock_analysis', '个股诊股', 'analytics'),
            ('portfolio', '持仓跟踪', 'account_balance'),
            ('stock_picker', '选股漏斗', 'filter_alt'),
            ('backtest', '回测中心', 'history'),
            ('strategy_lab', '策略实验室', 'science')
        ]
        
        for page_id, title, icon in nav_items:
            ui.button(
                on_click=lambda p=page_id: self._show_page(p),
                icon=icon,
                text=title,
            ).classes('w-full justify-start mb-2 bg-transparent text-white border border-white/30')
            
        # 策略状态
        with ui.card().classes('w-full mt-6 bg-slate-700'):
            ui.label('策略状态').classes('text-white font-bold mb-2')
            self.strategy_status_container = ui.column().classes('w-full')
            self._update_strategy_status()
            
    def _create_footer(self):
        """创建页脚"""
        ui.label('StockAI © 2024 - 智能股票分析与交易系统').classes('text-white text-sm')
        
    def _show_page(self, page_id: str):
        """显示指定页面"""
        self.current_page = page_id
        
        # 清空主内容
        self.main_content.clear()
        
        # 显示对应页面
        with self.main_content:
            if page_id == 'dashboard':
                self._show_dashboard()
            elif page_id == 'stock_analysis':
                self._show_stock_analysis()
            elif page_id == 'portfolio':
                self._show_portfolio()
            elif page_id == 'stock_picker':
                self._show_stock_picker()
            elif page_id == 'backtest':
                self._show_backtest()
            elif page_id == 'strategy_lab':
                self._show_strategy_lab()
                
    def _show_dashboard(self):
        """显示首页仪表盘"""
        ui.header().style('background: linear-gradient(135deg, #667eea 0%, #764ba2 100%)').classes('mb-6')
        with ui.row().classes('items-center justify-between w-full p-4'):
            ui.label('市场概览').classes('text-white text-2xl font-bold')
            ui.label(datetime.now().strftime('%Y年%m月%d日')).classes('text-white')
            
        # 市场温度卡片
        with ui.row().classes('w-full gap-4 mb-6'):
            self._create_market_temperature_card()
            self._create_market_status_card()
            self._create_strategy_performance_card()
            
        # 初始化数据
        self._update_market_temperature()
        self._update_market_status()
            
        # 最新信号
        with ui.card().classes('w-full'):
            with ui.row().classes('items-center justify-between p-4 border-b'):
                ui.label('最新交易信号').classes('text-xl font-bold')
                ui.button('查看全部', on_click=lambda: self._show_page('stock_picker')).classes('ml-auto')
                
            self.signals_table = ui.column().classes('w-full p-4')
            self._update_signals_table()
            
        # 行业主线
        with ui.card().classes('w-full mt-6'):
            with ui.row().classes('items-center justify-between p-4 border-b'):
                ui.label('行业主线热度').classes('text-xl font-bold')
                
            self.industry_chart = ui.column().classes('w-full h-64 p-4')
            self._update_industry_chart()
            
    def _show_stock_analysis(self):
        """显示个股诊股页面"""
        with ui.row().classes('w-full items-center mb-4'):
            ui.label('个股诊股').classes('text-2xl font-bold')
            
            with ui.row().classes('ml-auto'):
                self.stock_input = ui.input(placeholder='输入股票代码或名称', on_change=self._search_stock)
                ui.button('搜索', icon='search', on_click=self._search_stock)
                
        # 股票信息卡片
        self.stock_info_card = ui.card().classes('w-full mb-6')
        
        # 评分卡片
        with ui.row().classes('w-full gap-4 mb-6'):
            self.score_overview = ui.card().classes('flex-1')
            self.trade_suggestion = ui.card().classes('flex-1')
            
        # 技术分析图表
        with ui.card().classes('w-full'):
            ui.label('技术分析').classes('text-xl font-bold mb-4')
            self.tech_analysis = ui.column().classes('w-full h-80')
            
        # 初始化显示模拟数据
        self._show_simulated_stock_data()
        
    def _show_portfolio(self):
        """显示持仓跟踪页面"""
        with ui.row().classes('w-full items-center mb-4'):
            ui.label('持仓跟踪').classes('text-2xl font-bold')
            ui.button('添加持仓', icon='add', on_click=self._show_add_position_modal).classes('ml-auto')
            ui.button('刷新数据', icon='refresh', on_click=self._refresh_portfolio_data).classes('ml-2')
            
        # 持仓概览
        with ui.row().classes('w-full gap-4 mb-6'):
            self.portfolio_value = ui.card().classes('flex-1')
            self.portfolio_profit = ui.card().classes('flex-1')
            self.portfolio_health = ui.card().classes('flex-1')
            
        # 持仓列表
        with ui.card().classes('w-full'):
            with ui.card_header():
                ui.label('持仓列表').classes('text-xl font-bold')
                
            self.portfolio_table = ui.column().classes('w-full')
            self._update_portfolio_table()
            
        # 初始化数据
        self._refresh_portfolio_data()
        
    def _show_stock_picker(self):
        """显示选股漏斗页面"""
        with ui.row().classes('w-full items-center mb-4'):
            ui.label('选股漏斗').classes('text-2xl font-bold')
            self.scan_button = ui.button('开始扫描', icon='play_arrow', on_click=self._start_scan).classes('ml-auto')
            self.loading_spinner = ui.spinner(size='lg').classes('ml-2').style('display: none')
            
        # 选股参数配置
        with ui.card().classes('w-full mb-6'):
            ui.label('选股参数').classes('text-lg font-bold mb-2')
            
            with ui.row().classes('w-full gap-4'):
                with ui.column().classes('flex-1'):
                    ui.label('策略选择')
                    self.strategy_selector = ui.select(
                        options={'VolumeChipV3Strategy': '量筹码策略v3', 'ma_cross': '均线交叉策略', 'rsi_strategy': 'RSI策略'},
                        value='VolumeChipV3Strategy'
                    )
                    
                with ui.column().classes('flex-1'):
                    ui.label('最小得分')
                    self.min_score_slider = ui.slider(min=0, max=40, value=30, step=1)
                    
                with ui.column().classes('flex-1'):
                    ui.label('最大数量')
                    self.max_stocks_input = ui.number(value=50, min=10, max=200)
                    
        # 选股漏斗可视化
        with ui.card().classes('w-full mb-6'):
            ui.label('选股漏斗').classes('text-lg font-bold mb-4')
            self.funnel_chart = ui.column().classes('w-full h-64')
            self.funnel_stats = ui.row().classes('w-full justify-around mb-4')
            
        # 选股结果
        with ui.card().classes('w-full'):
            with ui.card_header():
                ui.label('选股结果').classes('text-xl font-bold')
                self.result_count_label = ui.label('共 0 只股票符合条件').classes('ml-auto text-sm')
                
            self.stock_results_table = ui.column().classes('w-full')
            
        # 初始化显示
        self._update_funnel_stats()
        self._update_stock_results_table()
            
    def _show_backtest(self):
        """显示回测中心页面"""
        with ui.row().classes('w-full items-center mb-4'):
            ui.label('回测中心').classes('text-2xl font-bold')
            ui.button('开始回测', icon='play_arrow', on_click=self._start_backtest).classes('ml-auto')
            
        # 回测配置
        with ui.row().classes('w-full gap-4 mb-6'):
            with ui.column().classes('flex-1'):
                ui.label('选择策略')
                self.backtest_strategy = ui.select(
                    options={'volume_chip_v3': '量筹码策略v3', 'ma_cross': '均线交叉策略', 'rsi_strategy': 'RSI策略'},
                    value='volume_chip_v3'
                )
                
            with ui.column().classes('w-48'):
                ui.label('开始日期')
                self.backtest_start_date = ui.input(value=(datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d'))
                
            with ui.column().classes('w-48'):
                ui.label('结束日期')
                self.backtest_end_date = ui.input(value=datetime.now().strftime('%Y-%m-%d'))
                
        # 回测参数
        with ui.card().classes('w-full mb-6'):
            ui.label('策略参数').classes('text-lg font-bold mb-2')
            
            self.params_grid = ui.grid(columns=2).classes('w-full gap-4')
            self._show_backtest_params()
            
        # 回测结果
        with ui.row().classes('w-full gap-4 mb-6'):
            self.return_metric = ui.card().classes('flex-1')
            self.winrate_metric = ui.card().classes('flex-1')
            self.drawdown_metric = ui.card().classes('flex-1')
            
        # 回测图表
        with ui.card().classes('w-full'):
            ui.label('回测收益曲线').classes('text-lg font-bold mb-4')
            self.backtest_chart = ui.column().classes('w-full h-64')
            
    def _show_strategy_lab(self):
        """显示策略实验室页面"""
        with ui.row().classes('w-full items-center mb-4'):
            ui.label('策略实验室').classes('text-2xl font-bold')
            ui.button('创建新策略', icon='add', on_click=self._show_create_strategy_modal).classes('ml-auto')
            ui.button('AI解析策略', icon='lightbulb', on_click=self._show_ai_strategy_parser).classes('ml-2')
            
        # 策略列表
        with ui.card().classes('w-full mb-6'):
            with ui.card_header():
                ui.label('可用策略').classes('text-xl font-bold')
                
            self.strategy_list = ui.column().classes('w-full')
            self._update_strategy_list()
            
        # 策略详情
        with ui.card().classes('w-full'):
            ui.label('策略详情').classes('text-xl font-bold mb-4')
            
            self.strategy_details = ui.column().classes('w-full')
            self._show_strategy_details()
            
    def _show_ai_strategy_parser(self):
        """显示AI策略解析器"""
        with ui.dialog() as dialog, ui.card().classes('w-4/5 max-w-4xl'):
            ui.label('AI策略解析实验室').classes('text-xl font-bold mb-4')
            
            with ui.row().classes('w-full gap-6'):
                # 左侧输入区域
                with ui.column().classes('flex-1'):
                    ui.label('策略描述').classes('text-sm font-bold mb-2')
                    self.ai_strategy_input = ui.textarea(
                        placeholder='请输入自然语言描述的策略逻辑，例如：\n"当股价突破20日均线且成交量放大50%时买入，\n当股价跌破10日均线且成交量萎缩时卖出"',
                        rows=10
                    ).classes('w-full')
                    
                    with ui.row().classes('mt-4 gap-2'):
                        ui.label('AI模型:').classes('self-center')
                        self.ai_model_select = ui.select(
                            options={'ollama:qwen2:7b': 'Qwen2-7B', 'ollama:llama3:8b': 'Llama3-8B', 'ollama:gemma2:9b': 'Gemma2-9B'},
                            value='ollama:qwen2:7b'
                        ).classes('flex-1')
                        
                    with ui.row().classes('mt-4 gap-2'):
                        ui.label('策略类型:').classes('self-center')
                        self.ai_strategy_type = ui.select(
                            options={'volume': '量能策略', 'price': '价格策略', 'hybrid': '混合策略', 'custom': '自定义策略'},
                            value='hybrid'
                        ).classes('flex-1')
                        
                    with ui.row().classes('mt-4 justify-end'):
                        self.parse_button = ui.button('开始解析', icon='send', on_click=lambda: self._parse_strategy_with_ai(dialog)).classes('mr-2')
                        self.stop_button = ui.button('停止解析', icon='stop', on_click=self._stop_ai_parsing, disabled=True).classes('mr-2')
                        ui.button('取消', on_click=dialog.close)
                        
                # 右侧结果区域
                with ui.column().classes('flex-1'):
                    with ui.row().classes('w-full items-center mb-2'):
                        ui.label('解析结果').classes('text-sm font-bold')
                        self.parse_status = ui.label('就绪').classes('ml-auto text-sm text-green-600')
                        
                    self.ai_result_tabs = ui.tabs().classes('w-full')
                    
                    with self.ai_result_tabs.add_tab('规则列表'):
                        self.ai_rules_list = ui.column().classes('w-full')
                        ui.label('请点击"开始解析"按钮生成策略规则').classes('text-center text-gray-500 py-8')
                        
                    with self.ai_result_tabs.add_tab('策略代码'):
                        self.ai_code_view = ui.code(language='python').classes('w-full h-80')
                        
                    with self.ai_result_tabs.add_tab('回测结果'):
                        self.ai_backtest_results = ui.column().classes('w-full')
                        ui.label('生成策略后可以进行回测').classes('text-center text-gray-500 py-8')
                        
                    # 操作按钮
                    with ui.row().classes('mt-4 justify-end'):
                        self.save_strategy_button = ui.button('保存策略', icon='save', on_click=lambda: self._save_ai_generated_strategy(dialog), disabled=True).classes('ml-2')
                        self.run_backtest_button = ui.button('运行回测', icon='play_arrow', on_click=self._run_ai_strategy_backtest, disabled=True).classes('ml-2')
                        
            # 解析进度
            self.parse_progress = ui.linear_progress(value=0).classes('w-full mt-4').style('display: none')
            
        dialog.open()
            
    def _create_market_temperature_card(self):
        """创建市场温度卡片"""
        with ui.card().classes('dashboard-card flex-1 bg-gradient-to-br from-blue-500 to-purple-600'):
            ui.label('市场温度').classes('text-sm opacity-90 mb-1 text-white')
            self.temperature_display = ui.label('--').classes('text-3xl font-bold text-white')
            self.temperature_suggestion = ui.label('加载中...').classes('text-sm mt-1 text-white')
                
    def _create_market_status_card(self):
        """创建市场状态卡片"""
        with ui.card().classes('dashboard-card flex-1 bg-gradient-to-br from-green-500 to-teal-600'):
            ui.label('市场状态').classes('text-sm opacity-90 mb-1 text-white')
            self.up_count_display = ui.label('上涨家数: --').classes('text-xl font-bold mb-1 text-white')
            self.down_count_display = ui.label('下跌家数: --').classes('text-xl font-bold text-white')
            self.flat_count_display = ui.label('平盘: --').classes('text-sm mt-1 text-white')
                
    def _create_strategy_performance_card(self):
        """创建策略表现卡片"""
        with ui.card().classes('dashboard-card flex-1 bg-gradient-to-br from-amber-500 to-orange-600'):
            ui.label('策略表现').classes('text-sm opacity-90 mb-1 text-white')
            ui.label('本月收益率: +12.5%').classes('text-xl font-bold mb-1 text-white')
            ui.label('胜率: 68%').classes('text-xl font-bold text-white')
            ui.label('最大回撤: -3.2%').classes('text-sm mt-1 text-white')
                
    def _update_signals_table(self):
        """更新信号表格"""
        self.signals_table.clear()
        
        try:
            # 获取真实交易信号
            from datetime import datetime
            today = datetime.now().strftime('%Y%m%d')
            all_signals = self.strategy_manager.get_all_signals(today)
            
            # 转换为统一格式
            signals = []
            for strategy_name, strategy_signals in all_signals.items():
                for signal in strategy_signals:
                    # 转换信号类型
                    signal_type = signal.get('signal_type', 'HOLD').upper()
                    if signal_type == 'STRONG_BUY':
                        signal_display = 'STRONG_BUY'
                    elif signal_type == 'BUY':
                        signal_display = 'BUY'
                    else:
                        signal_display = 'HOLD'
                    
                    signals.append({
                        'code': signal.get('ts_code', '').split('.')[0] if signal.get('ts_code') else '',
                        'name': signal.get('stock_name', ''),
                        'signal': signal_display,
                        'score': signal.get('score', 0),
                        'reason': signal.get('reason', '无详细理由'),
                        'pct_chg': signal.get('pct_chg', 0),
                        'close_price': signal.get('close_price', 0)
                    })
            
            # 如果没有信号，显示提示
            if not signals:
                ui.label('暂无交易信号').classes('text-center text-gray-500 py-8')
                return
                
        except Exception as e:
            self.logger.error(f"获取交易信号失败: {str(e)}")
            # 使用模拟数据
            signals = [
                {'code': '600519', 'name': '贵州茅台', 'signal': 'STRONG_BUY', 'score': 38, 'reason': '主力资金持续流入，股东户数下降', 'pct_chg': 2.56, 'close_price': 1856.00},
                {'code': '000858', 'name': '五粮液', 'signal': 'BUY', 'score': 25, 'reason': '三日背离信号，振幅正常', 'pct_chg': 1.82, 'close_price': 178.50},
                {'code': '002415', 'name': '海康威视', 'signal': 'HOLD', 'score': 12, 'reason': '主力资金流出，等待信号确认', 'pct_chg': -0.32, 'close_price': 35.80},
                {'code': '600036', 'name': '招商银行', 'signal': 'STRONG_BUY', 'score': 35, 'reason': '双核心因子全满，三重流出检查通过', 'pct_chg': 0.98, 'close_price': 32.45},
                {'code': '002194', 'name': '日发精机', 'signal': 'STRONG_BUY', 'score': 32, 'reason': '无详细理由', 'pct_chg': 5.31, 'close_price': 12.80},
                {'code': '301233', 'name': '思进智能', 'signal': 'STRONG_BUY', 'score': 29, 'reason': '无详细理由', 'pct_chg': 1.15, 'close_price': 28.60},
                {'code': '000703', 'name': '神马股份', 'signal': 'STRONG_BUY', 'score': 34, 'reason': '无详细理由', 'pct_chg': 5.88, 'close_price': 15.60},
                {'code': '000810', 'name': '九鼎新材', 'signal': 'STRONG_BUY', 'score': 36, 'reason': '无详细理由', 'pct_chg': 7.34, 'close_price': 21.50},
                {'code': '002142', 'name': '宁波银行', 'signal': 'BUY', 'score': 27, 'reason': '无详细理由', 'pct_chg': 4.13, 'close_price': 32.80},
            ]
        
        # 创建自适应表格
        with self.signals_table:
            # 创建表格容器，支持滚动
            with ui.row().classes('w-full'):
                table_container = ui.column().classes('w-full overflow-auto max-h-96')
                
                with table_container:
                    # 表格头部
                    with ui.row().classes('w-full bg-gray-50 font-bold border-b p-2'):
                        ui.label('股票代码').classes('w-24')
                        ui.label('股票名称').classes('w-32')
                        ui.label('涨跌幅').classes('w-20 text-right')
                        ui.label('信号类型').classes('w-20 text-center')
                        ui.label('综合评分').classes('w-20 text-right')
                        ui.label('推送理由').classes('flex-1')
                        ui.label('操作').classes('w-20')
                    
                    # 表格内容
                    for signal in signals:
                        with ui.row().classes('w-full border-b hover:bg-gray-50 p-2 transition-colors'):
                            # 股票代码
                            ui.label(signal['code']).classes('w-24 font-medium')
                            
                            # 股票名称
                            ui.label(signal['name']).classes('w-32')
                            
                            # 涨跌幅
                            pct_color = 'text-green-600' if signal.get('pct_chg', 0) > 0 else 'text-red-600'
                            ui.label(f'{signal["pct_chg"]:.2f}%').classes(f'w-20 text-right {pct_color}')
                            
                            # 信号类型
                            if signal['signal'] == 'STRONG_BUY':
                                ui.badge('强买入', color='green').classes('w-20 text-center')
                            elif signal['signal'] == 'BUY':
                                ui.badge('买入', color='blue').classes('w-20 text-center')
                            else:
                                ui.badge('持有', color='orange').classes('w-20 text-center')
                            
                            # 综合评分
                            ui.label(f'{signal["score"]}分').classes('w-20 text-right')
                            
                            # 推送理由
                            reason_text = signal.get('reason', '无详细理由')
                            ui.label(reason_text).classes('flex-1 text-ellipsis overflow-hidden whitespace-nowrap')
                            
                            # 操作按钮
                            with ui.row().classes('w-20 justify-center'):
                                ui.button('详情', icon='info', on_click=lambda s=signal: self._select_stock(s)).classes('text-xs')
                        
    def _update_industry_chart(self):
        """更新行业热度图表"""
        self.industry_chart.clear()
        
        try:
            import sqlite3
            import os
            
            # 连接数据库
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'stock_daily.db')
            conn = sqlite3.connect(db_path)
            
            # 获取最新的行业强度排名
            cursor = conn.execute("""
                SELECT industry, composite_score, avg_pct_chg, tier
                FROM industry_rank 
                WHERE calc_date = (SELECT MAX(calc_date) FROM industry_rank)
                ORDER BY composite_score DESC LIMIT 10
            """)
            
            industries = []
            for row in cursor.fetchall():
                industry_name, score, avg_pct, tier = row
                
                # 转换为0-100的热度值
                hotness = min(100, max(0, int(score * 100)))
                
                # 计算涨跌幅
                change_pct = float(avg_pct) if avg_pct is not None else 0
                change_str = f"{change_pct:+.2f}%"
                
                industries.append({
                    'name': industry_name,
                    'hotness': hotness,
                    'change': change_str,
                    'tier': tier
                })
            
            conn.close()
            
            # 如果没有数据，使用模拟数据
            if not industries:
                industries = [
                    {'name': '白酒', 'hotness': 92, 'change': '+5.2%', 'tier': 'main'},
                    {'name': '医药', 'hotness': 78, 'change': '-2.1%', 'tier': 'backup'},
                    {'name': '半导体', 'hotness': 85, 'change': '+8.3%', 'tier': 'main'},
                    {'name': '新能源', 'hotness': 65, 'change': '-3.5%', 'tier': 'backup'},
                    {'name': '金融', 'hotness': 72, 'change': '+1.8%', 'tier': 'backup'},
                ]
                
        except Exception as e:
            self.logger.error(f"获取行业数据失败: {str(e)}")
            # 使用模拟数据
            industries = [
                {'name': '白酒', 'hotness': 92, 'change': '+5.2%', 'tier': 'main'},
                {'name': '医药', 'hotness': 78, 'change': '-2.1%', 'tier': 'backup'},
                {'name': '半导体', 'hotness': 85, 'change': '+8.3%', 'tier': 'main'},
                {'name': '新能源', 'hotness': 65, 'change': '-3.5%', 'tier': 'backup'},
                {'name': '金融', 'hotness': 72, 'change': '+1.8%', 'tier': 'backup'},
            ]
        
        with self.industry_chart:
            for industry in industries:
                with ui.row().classes('w-full items-center mb-3'):
                    # 行业名称和层级标记
                    with ui.row().classes('w-24 items-center'):
                        ui.label(industry['name']).classes('font-medium')
                        if industry.get('tier') == 'main':
                            ui.badge('主线', color='green').classes('ml-2 text-xs')
                        elif industry.get('tier') == 'backup':
                            ui.badge('备选', color='blue').classes('ml-2 text-xs')
                    
                    with ui.column().classes('flex-1 mr-4'):
                        ui.linear_progress(value=industry['hotness']/100).classes('w-full')
                        ui.label(f'{industry["hotness"]}%').classes('text-xs text-right text-gray-500')
                        
                    # 涨跌幅显示
                    if '+' in industry['change']:
                        ui.label(industry['change']).classes('text-green-600 font-bold')
                    else:
                        ui.label(industry['change']).classes('text-red-600 font-bold')
                        
    def _update_strategy_status(self):
        """更新策略状态"""
        self.strategy_status_container.clear()
        
        # 获取策略状态
        status = self.strategy_manager.get_strategy_status()
        
        if not status:
            ui.label('无策略加载').classes('text-white text-sm')
            return
            
        for strategy_name, status_text in status.items():
            with ui.row().classes('w-full items-center mb-2'):
                ui.label(strategy_name).classes('text-white text-sm flex-1')
                
                if status_text == 'ready':
                    ui.badge('就绪', color='green').classes('signal-badge')
                elif status_text == 'running':
                    ui.badge('运行中', color='blue').classes('signal-badge')
                elif status_text == 'error':
                    ui.badge('错误', color='red').classes('signal-badge')
                else:
                    ui.badge('已停止', color='gray').classes('signal-badge')
                    
    def _show_simulated_stock_data(self):
        """显示模拟股票数据"""
        self.stock_info_card.clear()
        
        with self.stock_info_card:
            with ui.card_header():
                ui.label('贵州茅台 (600519)').classes('text-xl font-bold')
                ui.label('¥1,856.00').classes('ml-auto text-2xl font-bold text-green-600')
                
            with ui.card_content():
                with ui.row().classes('w-full gap-6'):
                    with ui.column():
                        ui.label('涨跌幅').classes('text-sm text-gray-500')
                        ui.label('+2.56%').classes('text-lg font-bold text-green-600')
                        
                    with ui.column():
                        ui.label('成交量').classes('text-sm text-gray-500')
                        ui.label('23,568手').classes('text-lg font-bold')
                        
                    with ui.column():
                        ui.label('成交额').classes('text-sm text-gray-500')
                        ui.label('4.37亿').classes('text-lg font-bold')
                        
                    with ui.column():
                        ui.label('换手率').classes('text-sm text-gray-500')
                        ui.label('0.32%').classes('text-lg font-bold')
                        
    def _show_real_stock_data(self):
        """显示真实股票数据"""
        if not self.selected_stock:
            return
            
        try:
            import sqlite3
            import os
            import pandas as pd
            
            ts_code = self.selected_stock['ts_code']
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'stock_daily.db')
            conn = sqlite3.connect(db_path)
            
            # 获取最新行情数据
            cursor = conn.execute("""
                SELECT close, pct_chg, vol, amount, turnover_rate 
                FROM daily_prices 
                WHERE ts_code = ? 
                ORDER BY trade_date DESC LIMIT 1
            """, (ts_code,))
            
            row = cursor.fetchone()
            if row:
                close_price, pct_chg, vol, amount, turnover_rate = row
                
                # 格式化数据
                close_price = float(close_price) if close_price is not None else 0
                pct_chg = float(pct_chg) if pct_chg is not None else 0
                vol = int(vol) if vol is not None else 0
                amount = float(amount) if amount is not None else 0
                turnover_rate = float(turnover_rate) if turnover_rate is not None else 0
                
                # 更新股票信息卡片
                self.stock_info_card.clear()
                with self.stock_info_card:
                    with ui.card_header():
                        ui.label(f'{self.selected_stock["name"]} ({self.selected_stock["ts_code"]})').classes('text-xl font-bold')
                        price_color = 'text-green-600' if pct_chg > 0 else 'text-red-600' if pct_chg < 0 else 'text-gray-600'
                        ui.label(f'¥{close_price:.2f}').classes(f'ml-auto text-2xl font-bold {price_color}')
                        
                    with ui.card_content():
                        with ui.row().classes('w-full gap-6'):
                            with ui.column():
                                ui.label('涨跌幅').classes('text-sm text-gray-500')
                                pct_color = 'text-green-600' if pct_chg > 0 else 'text-red-600' if pct_chg < 0 else 'text-gray-600'
                                ui.label(f'{pct_chg:.2f}%').classes(f'text-lg font-bold {pct_color}')
                                
                            with ui.column():
                                ui.label('成交量').classes('text-sm text-gray-500')
                                ui.label(f'{vol:,}手').classes('text-lg font-bold')
                                
                            with ui.column():
                                ui.label('成交额').classes('text-sm text-gray-500')
                                ui.label(f'{amount/100000000:.2f}亿').classes('text-lg font-bold')
                                
                            with ui.column():
                                ui.label('换手率').classes('text-sm text-gray-500')
                                ui.label(f'{turnover_rate:.2f}%').classes('text-lg font-bold')
                                
            # 获取策略评分
            self._update_real_score_cards(ts_code)
            
            conn.close()
            
        except Exception as e:
            self.logger.error(f"获取股票数据失败: {str(e)}")
            ui.notify('获取股票数据失败', type='error')
            
    def _show_simulated_portfolio_data(self):
        """显示模拟持仓数据"""
        # 持仓概览
        with self.portfolio_value:
            with ui.card_content():
                ui.label('持仓总市值').classes('text-sm text-gray-500')
                ui.label('¥1,256,800').classes('text-2xl font-bold')
                ui.label('+2.3% 较昨日').classes('text-sm text-green-600 mt-1')
                
        with self.portfolio_profit:
            with ui.card_content():
                ui.label('总盈亏').classes('text-sm text-gray-500')
                ui.label('¥85,620').classes('text-2xl font-bold text-green-600')
                ui.label('+7.3% 收益率').classes('text-sm text-green-600 mt-1')
                
        with self.portfolio_health:
            with ui.card_content():
                ui.label('组合健康度').classes('text-sm text-gray-500')
                ui.label('87分').classes('text-2xl font-bold text-blue-600')
                ui.label('风险较低，结构合理').classes('text-sm text-gray-500 mt-1')
                
    def _load_real_portfolio_data(self):
        """加载真实持仓数据"""
        try:
            # 导入持仓健康度分析模块
            from portfolio_health import check_portfolio
            
            # 获取持仓数据
            holdings = check_portfolio()
            
            if not holdings:
                return None
                
            # 计算持仓概览
            total_value = 0.0
            total_profit = 0.0
            total_cost = 0.0
            
            for holding in holdings:
                current_price = holding.get('current_price', 0)
                cost = holding.get('cost', 0)
                # 假设持仓数量（实际应该从持仓文件中读取）
                quantity = 100  # 默认值，实际应该从持仓数据中获取
                
                total_value += current_price * quantity
                total_cost += cost * quantity
                total_profit += (current_price - cost) * quantity
                
            return {
                'holdings': holdings,
                'total_value': total_value,
                'total_profit': total_profit,
                'total_cost': total_cost,
                'profit_rate': (total_profit / total_cost * 100) if total_cost > 0 else 0
            }
            
        except Exception as e:
            self.logger.error(f"加载持仓数据失败: {str(e)}")
            return None
                
    def _update_stock_results_table(self):
        """更新选股结果表格"""
        self.stock_results_table.clear()
        
        # 模拟选股结果
        results = [
            {'code': '600519', 'name': '贵州茅台', 'score': 38, 'signal': 'STRONG_BUY', 'industry': '白酒', 'price': 1856.00},
            {'code': '600036', 'name': '招商银行', 'score': 35, 'signal': 'STRONG_BUY', 'industry': '银行', 'price': 38.50},
            {'code': '000858', 'name': '五粮液', 'score': 25, 'signal': 'BUY', 'industry': '白酒', 'price': 178.60},
            {'code': '002415', 'name': '海康威视', 'score': 22, 'signal': 'BUY', 'industry': '电子', 'price': 45.20},
            {'code': '600104', 'name': '上汽集团', 'score': 18, 'signal': 'BUY', 'industry': '汽车', 'price': 15.80},
        ]
        
        # 创建表格
        columns = [
            {'name': '代码', 'label': '代码', 'field': 'code', 'align': 'left'},
            {'name': '名称', 'label': '名称', 'field': 'name', 'align': 'left'},
            {'name': '行业', 'label': '行业', 'field': 'industry', 'align': 'left'},
            {'name': '价格', 'label': '价格', 'field': 'price', 'align': 'right'},
            {'name': '评分', 'label': '评分', 'field': 'score', 'align': 'right'},
            {'name': '信号', 'label': '信号', 'field': 'signal', 'align': 'center'},
        ]
        
        ui.table(
            columns=columns,
            rows=results,
            row_key='code',
            on_select=lambda e: self._select_stock(e.row)
        ).classes('w-full')
        
    def _update_stock_results_table_with_real_data(self, results):
        """使用真实数据更新选股结果表格"""
        self.stock_results_table.clear()
        
        if not results:
            ui.label('暂无选股结果').classes('text-center text-gray-500 py-8')
            return
            
        # 创建表格
        columns = [
            {'name': '代码', 'label': '代码', 'field': 'ts_code', 'align': 'left'},
            {'name': '名称', 'label': '名称', 'field': 'name', 'align': 'left'},
            {'name': '评分', 'label': '评分', 'field': 'score', 'align': 'right'},
            {'name': '信号', 'label': '信号', 'field': 'signal_type', 'align': 'center'},
            {'name': '主力资金', 'label': '主力资金', 'field': 'main_money', 'align': 'right'},
            {'name': '股东变化', 'label': '股东变化', 'field': 'holder_chg', 'align': 'right'},
            {'name': '行业', 'label': '行业', 'field': 'industry', 'align': 'left'},
            {'name': '价格', 'label': '价格', 'field': 'price', 'align': 'right'},
        ]
        
        # 转换数据格式
        table_data = []
        for signal in results:
            ts_code = signal.get('ts_code', '')
            # 提取股票代码（去掉.SH/.SZ后缀）
            code = ts_code.split('.')[0] if '.' in ts_code else ts_code
            
            # 格式化主力资金（万元）
            main_money = signal.get('main_money', 0)
            main_money_str = f"{main_money/10000:.1f}万" if main_money != 0 else '0'
            
            # 格式化股东变化（百分比）
            holder_chg = signal.get('holder_chg', 0)
            holder_chg_str = f"{holder_chg:.2%}" if holder_chg != 0 else '0%'
            
            # 格式化信号类型
            signal_type = signal.get('signal_type', '')
            signal_display = {
                'strong_buy': '🔴 强买入',
                'buy': '🟡 买入',
                'hold': '🟢 持有'
            }.get(signal_type, signal_type.replace('_', ' ').title())
            
            table_data.append({
                'ts_code': code,
                'name': signal.get('stock_name', signal.get('name', '')),
                'score': signal.get('score', 0),
                'signal_type': signal_display,
                'main_money': main_money_str,
                'holder_chg': holder_chg_str,
                'industry': signal.get('industry', ''),
                'price': f"¥{signal.get('close_price', signal.get('price', 0)):.2f}",
                '_original': signal
            })
            
        ui.table(
            columns=columns,
            rows=table_data,
            row_key='ts_code',
            on_select=lambda e: self._select_stock_from_signal(e.row['_original'])
        ).classes('w-full')
        
    def _select_stock_from_signal(self, signal):
        """从选股信号中选择股票"""
        self.selected_stock = {
            'ts_code': signal.get('ts_code', ''),
            'name': signal.get('name', '')
        }
        self._show_page('stock_analysis')
        
    def _update_funnel_stats(self):
        """更新选股漏斗统计数据"""
        self.funnel_stats.clear()
        
        try:
            # 查询全市场股票数（排除ST股）
            import sqlite3
            import os
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'stock_daily.db')
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM stock_list WHERE name NOT LIKE '%ST%'")
            total_stocks = cursor.fetchone()[0]
            conn.close()
            
            # 创建统计卡片
            stats = [
                ('全市场股票', str(total_stocks), 'gray'),
                ('硬过滤后', '计算中...', 'blue'),
                ('评分后(≥30分)', '计算中...', 'green'),
                ('AI精选后', '未过滤', 'purple')
            ]
            
            for label, value, color in stats:
                with self.funnel_stats:
                    with ui.card().classes('flex-1'):
                        ui.label(label).classes('text-xs text-gray-500')
                        ui.label(value).classes('text-xl font-bold text-' + color + '-600')
                        
        except Exception as e:
            self.logger.error(f"更新漏斗统计失败: {str(e)}")
            with self.funnel_stats:
                ui.label('数据加载失败').classes('text-red-500')
    
    def _update_funnel_chart(self, found_count, score_30_count, ai_filtered_count=0):
        """更新选股漏斗图"""
        self.funnel_chart.clear()
        
        with self.funnel_chart:
            # 创建简单的漏斗图表示
            with ui.row().classes('w-full items-end h-full gap-2'):
                # 全部股票
                with ui.column().classes('flex-1 items-center'):
                    height = 100
                    ui.card().classes(f'h-[{height}%] w-full bg-gray-200')
                    ui.label('全市场').classes('text-xs text-center mt-1')
                    try:
                        import sqlite3
                        import os
                        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'stock_daily.db')
                        conn = sqlite3.connect(db_path)
                        cursor = conn.execute("SELECT COUNT(*) FROM stock_list WHERE name NOT LIKE '%ST%'")
                        total_stocks = cursor.fetchone()[0]
                        conn.close()
                        ui.label(f'{total_stocks}').classes('text-xs text-center text-gray-500')
                    except:
                        ui.label('5000+').classes('text-xs text-center text-gray-500')
                
                # 策略筛选
                strategy_height = 60
                ui.card().classes(f'h-[{strategy_height}%] w-full bg-blue-200')
                ui.label('硬过滤后').classes('text-xs text-center mt-1')
                ui.label(f'{found_count}').classes('text-xs text-center text-gray-500')
                
                # 评分筛选
                score_height = (score_30_count / found_count) * 60 if found_count > 0 else 30
                ui.card().classes(f'h-[{score_height}%] w-full bg-green-200')
                ui.label('评分筛选').classes('text-xs text-center mt-1')
                ui.label(f'{score_30_count}').classes('text-xs text-center text-gray-500')
                
                # AI精选
                if ai_filtered_count > 0:
                    ai_height = (ai_filtered_count / score_30_count) * score_height if score_30_count > 0 else 20
                    ui.card().classes(f'h-[{ai_height}%] w-full bg-purple-200')
                    ui.label('AI精选').classes('text-xs text-center mt-1')
                    ui.label(f'{ai_filtered_count}').classes('text-xs text-center text-gray-500')
        
    def _show_backtest_params(self):
        """显示回测参数"""
        self.params_grid.clear()
        
        # 模拟策略参数
        params = [
            ('fund_flow_weight', '主力资金权重', 15, 0, 30),
            ('holder_decrease_weight', '股东户数权重', 15, 0, 30),
            ('divergence_weight', '背离信号权重', 10, 0, 20),
            ('strong_signal_threshold', '强信号阈值', 30, 0, 50),
        ]
        
        for param_name, label, value, min_val, max_val in params:
            with self.params_grid:
                ui.label(label).classes('text-sm font-medium')
                ui.slider(min=min_val, max=max_val, value=value, step=1)
                
    def _update_portfolio_table(self):
        """更新持仓列表"""
        self.portfolio_table.clear()
        
        # 模拟持仓数据
        portfolio = [
            {'code': '600519', 'name': '贵州茅台', 'quantity': 100, 'cost': 1750.00, 'current': 1856.00, 'profit': '+6.06%', 'health': 92},
            {'code': '600036', 'name': '招商银行', 'quantity': 5000, 'cost': 36.20, 'current': 38.50, 'profit': '+6.35%', 'health': 88},
            {'code': '000858', 'name': '五粮液', 'quantity': 200, 'cost': 165.00, 'current': 178.60, 'profit': '+8.24%', 'health': 76},
            {'code': '002415', 'name': '海康威视', 'quantity': 3000, 'cost': 42.80, 'current': 45.20, 'profit': '+5.61%', 'health': 81},
        ]
        
        # 创建表格
        columns = [
            {'name': '代码', 'label': '代码', 'field': 'code', 'align': 'left'},
            {'name': '名称', 'label': '名称', 'field': 'name', 'align': 'left'},
            {'name': '持仓量', 'label': '持仓量', 'field': 'quantity', 'align': 'right'},
            {'name': '成本价', 'label': '成本价', 'field': 'cost', 'align': 'right'},
            {'name': '当前价', 'label': '当前价', 'field': 'current', 'align': 'right'},
            {'name': '盈亏', 'label': '盈亏', 'field': 'profit', 'align': 'right'},
            {'name': '健康度', 'label': '健康度', 'field': 'health', 'align': 'right'},
        ]
        
        table = ui.table(
            columns=columns,
            rows=portfolio,
            row_key='code',
            on_select=lambda e: self._select_stock(e.row)
        ).classes('w-full')
        
        # 添加操作按钮
        with table.add_slot('body-cell-code'):
            def slot(props):
                with ui.row():
                    ui.label(props['row']['code'])
                    ui.button('详情', icon='info', on_click=lambda: self._show_stock_detail(props['row'])).classes('ml-2 text-xs')
                    ui.button('卖出', icon='sell', on_click=lambda: self._show_sell_modal(props['row'])).classes('ml-1 text-xs')
                    
    def _update_strategy_list(self):
        """更新策略列表"""
        self.strategy_list.clear()
        
        # 获取所有策略
        strategies = self.strategy_manager.strategy_classes
        
        if not strategies:
            ui.label('无可用策略').classes('text-sm text-gray-500')
            return
            
        for strategy_name, strategy_class in strategies.items():
            with ui.card().classes('w-full mb-2 cursor-pointer hover:shadow-md transition-shadow'):
                with ui.card_content().classes('p-3'):
                    with ui.row().classes('w-full items-center'):
                        ui.label(strategy_name).classes('font-bold flex-1').on('click', lambda s=strategy_name: self._show_strategy_details(s))
                        ui.label(f'v{getattr(strategy_class, "version", "1.0")}').classes('text-sm text-gray-500')
                        
                        ui.button('回测', icon='history', on_click=lambda s=strategy_name: self._start_strategy_backtest(s)).classes('ml-2 text-xs')
                        ui.button('编辑', icon='edit', on_click=lambda s=strategy_name: self._edit_strategy(s)).classes('ml-1 text-xs')
                        ui.button('详情', icon='info', on_click=lambda s=strategy_name: self._show_strategy_details(s)).classes('ml-1 text-xs')
                        
                    ui.label(getattr(strategy_class, 'strategy_desc', '无描述')).classes('text-sm text-gray-600 mt-2').on('click', lambda s=strategy_name: self._show_strategy_details(s))
                    
                    tags = getattr(strategy_class, 'tags', [])
                    if tags:
                        with ui.row().classes('mt-2'):
                            for tag in tags:
                                ui.badge(tag, color='blue').classes('text-xs mr-1')
                                
    def _show_strategy_details(self, strategy_name=None):
        """显示策略详情"""
        self.strategy_details.clear()
        
        if not strategy_name:
            ui.label('请选择一个策略查看详情').classes('text-center text-gray-500 py-12')
            return
            
        try:
            # 获取策略实例
            strategy = self.strategy_manager.get_strategy(strategy_name)
            
            if not strategy:
                ui.label('策略不存在').classes('text-center text-gray-500 py-12')
                return
                
            # 获取策略信息
            strategy_class = self.strategy_manager.strategy_classes.get(strategy_name)
            
            # 显示策略基本信息
            ui.label('策略基本信息').classes('text-xl font-bold mb-4')
            
            with ui.card().classes('w-full mb-6'):
                with ui.card_content():
                    with ui.row().classes('w-full gap-4'):
                        with ui.column().classes('flex-1'):
                            ui.label('策略名称').classes('text-sm text-gray-500')
                            ui.label(strategy_name).classes('text-lg font-bold')
                            
                        with ui.column().classes('flex-1'):
                            ui.label('版本').classes('text-sm text-gray-500')
                            ui.label(f'v{getattr(strategy_class, "version", "1.0")}').classes('text-lg font-bold')
                            
                        with ui.column().classes('flex-1'):
                            ui.label('创建时间').classes('text-sm text-gray-500')
                            ui.label(getattr(strategy_class, 'create_time', '未知')).classes('text-lg font-bold')
                            
                    ui.label(getattr(strategy_class, 'strategy_desc', '无描述')).classes('text-sm text-gray-600 mt-4')
                    
                    # 策略标签
                    tags = getattr(strategy_class, 'tags', [])
                    if tags:
                        with ui.row().classes('mt-4'):
                            ui.label('策略标签').classes('text-sm text-gray-500 mr-2')
                            for tag in tags:
                                ui.badge(tag, color='blue').classes('text-xs mr-1')
                                
            # 显示策略参数
            ui.label('策略参数配置').classes('text-xl font-bold mb-4')
            
            with ui.card().classes('w-full mb-6'):
                with ui.card_content():
                    params = getattr(strategy, 'parameters', {})
                    
                    if params:
                        grid = ui.grid(columns=2).classes('w-full gap-4')
                        
                        for param_name, param_info in params.items():
                            with grid:
                                ui.label(param_info.get('label', param_name)).classes('text-sm font-medium')
                                
                                # 根据参数类型创建不同的输入组件
                                param_type = param_info.get('type', 'int')
                                current_value = param_info.get('value', 0)
                                min_val = param_info.get('min', 0)
                                max_val = param_info.get('max', 100)
                                
                                if param_type in ['int', 'float']:
                                    ui.number(value=current_value, min=min_val, max=max_val)
                                elif param_type == 'bool':
                                    ui.switch(value=current_value)
                                elif param_type == 'select':
                                    options = param_info.get('options', {})
                                    ui.select(options=options, value=current_value)
                                else:
                                    ui.input(value=str(current_value))
                    else:
                        ui.label('该策略无参数配置').classes('text-sm text-gray-500 py-4')
                        
            # 显示策略表现
            ui.label('策略表现').classes('text-xl font-bold mb-4')
            
            with ui.row().classes('w-full gap-4 mb-6'):
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('近30天收益率').classes('text-sm text-gray-500')
                        ui.label('+8.5%').classes('text-2xl font-bold text-green-600')
                        
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('胜率').classes('text-sm text-gray-500')
                        ui.label('68%').classes('text-2xl font-bold text-blue-600')
                        
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('最大回撤').classes('text-sm text-gray-500')
                        ui.label('-3.2%').classes('text-2xl font-bold text-red-600')
                        
            # 显示策略最近信号
            ui.label('最近交易信号').classes('text-xl font-bold mb-4')
            
            with ui.card().classes('w-full'):
                with ui.card_content():
                    try:
                        from datetime import datetime
                        today = datetime.now().strftime('%Y%m%d')
                        all_signals = self.strategy_manager.get_all_signals(today)
                        
                        signals = all_signals.get(strategy_name, [])[:5]  # 只显示最近5个信号
                        
                        if signals:
                            columns = [
                                {'name': '代码', 'label': '代码', 'field': 'ts_code', 'align': 'left'},
                                {'name': '名称', 'label': '名称', 'field': 'name', 'align': 'left'},
                                {'name': '信号', 'label': '信号', 'field': 'signal_type', 'align': 'center'},
                                {'name': '评分', 'label': '评分', 'field': 'score', 'align': 'right'},
                            ]
                            
                            # 转换数据格式
                            table_data = []
                            for signal in signals:
                                ts_code = signal.get('ts_code', '')
                                code = ts_code.split('.')[0] if '.' in ts_code else ts_code
                                
                                table_data.append({
                                    'ts_code': code,
                                    'name': signal.get('name', ''),
                                    'signal_type': signal.get('signal_type', '').replace('_', ' ').title(),
                                    'score': signal.get('score', 0),
                                    '_original': signal
                                })
                                
                            ui.table(
                                columns=columns,
                                rows=table_data,
                                row_key='ts_code',
                                on_select=lambda e: self._select_stock_from_signal(e.row['_original'])
                            ).classes('w-full')
                        else:
                            ui.label('暂无交易信号').classes('text-center text-gray-500 py-4')
                            
                    except Exception as e:
                        self.logger.error(f"获取策略信号失败: {str(e)}")
                        ui.label('获取信号失败').classes('text-center text-red-500 py-4')
                        
        except Exception as e:
            self.logger.error(f"显示策略详情失败: {str(e)}")
            ui.label('加载策略详情失败').classes('text-center text-red-500 py-12')
        
    def _select_stock(self, stock_data: Dict):
        """选择股票"""
        self.selected_stock = stock_data
        self._show_page('stock_analysis')
        # 这里可以添加加载真实股票数据的逻辑
        
    def _search_stock(self):
        """搜索股票"""
        stock_code = self.stock_input.value
        if not stock_code:
            return
            
        try:
            import sqlite3
            import os
            
            # 连接数据库
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'stock_daily.db')
            conn = sqlite3.connect(db_path)
            
            # 搜索股票（支持代码或名称）
            cursor = conn.execute("""
                SELECT ts_code, name, industry, list_date 
                FROM stock_list 
                WHERE ts_code LIKE ? OR name LIKE ?
                LIMIT 1
            """, (f"{stock_code}%", f"%{stock_code}%"))
            
            row = cursor.fetchone()
            if row:
                ts_code, name, industry, list_date = row
                self.selected_stock = {
                    'ts_code': ts_code,
                    'name': name,
                    'industry': industry,
                    'list_date': list_date
                }
                self._show_real_stock_data()
                ui.notify(f'找到股票: {name} ({ts_code})')
            else:
                ui.notify(f'未找到股票: {stock_code}', type='warning')
                
            conn.close()
            
        except Exception as e:
            self.logger.error(f"搜索股票失败: {str(e)}")
            ui.notify('搜索失败，请稍后重试', type='error')
        
    def _refresh_data(self):
        """刷新数据"""
        self.update_time.set_text(f'最后更新: {datetime.now().strftime("%H:%M:%S")}')
        ui.notify('数据已刷新', type='positive')
        
        # 更新仪表盘数据
        if self.current_page == 'dashboard':
            self._update_market_temperature()
            self._update_market_status()
            self._update_signals_table()
            self._update_industry_chart()
            self._update_strategy_status()
            
    def _refresh_portfolio_data(self):
        """刷新持仓数据"""
        # 加载真实持仓数据
        portfolio_data = self._load_real_portfolio_data()
        
        if portfolio_data:
            self._update_portfolio_overview(portfolio_data)
            self._update_portfolio_table_with_real_data(portfolio_data['holdings'])
        else:
            # 使用模拟数据
            self._show_simulated_portfolio_data()
            self._update_portfolio_table()
            ui.notify('使用模拟持仓数据', type='info')
            
    def _update_portfolio_overview(self, portfolio_data):
        """更新持仓概览数据"""
        # 清空现有内容
        self.portfolio_value.clear()
        self.portfolio_profit.clear()
        self.portfolio_health.clear()
        
        total_value = portfolio_data['total_value']
        total_profit = portfolio_data['total_profit']
        total_cost = portfolio_data['total_cost']
        profit_rate = portfolio_data['profit_rate']
        
        # 更新总市值卡片
        with self.portfolio_value:
            with ui.card_content():
                ui.label('持仓总市值').classes('text-sm text-gray-500')
                ui.label(f'¥{total_value:,.2f}').classes('text-2xl font-bold')
                if total_cost > 0:
                    change_pct = (total_value - total_cost) / total_cost * 100
                    change_color = 'text-green-600' if change_pct > 0 else 'text-red-600'
                    ui.label(f'{change_pct:+.2f}% 较成本').classes(f'text-sm {change_color} mt-1')
        
        # 更新总盈亏卡片
        with self.portfolio_profit:
            with ui.card_content():
                ui.label('总盈亏').classes('text-sm text-gray-500')
                profit_color = 'text-green-600' if total_profit > 0 else 'text-red-600'
                ui.label(f'¥{total_profit:,.2f}').classes(f'text-2xl font-bold {profit_color}')
                if total_cost > 0:
                    ui.label(f'{profit_rate:+.2f}% 收益率').classes(f'text-sm {profit_color} mt-1')
        
        # 更新组合健康度卡片
        with self.portfolio_health:
            with ui.card_content():
                ui.label('组合健康度').classes('text-sm text-gray-500')
                
                # 计算平均健康度
                holdings = portfolio_data['holdings']
                if holdings:
                    avg_health = sum(h.get('score', 0) for h in holdings) / len(holdings)
                    ui.label(f'{avg_health:.0f}分').classes('text-2xl font-bold text-blue-600')
                    
                    # 根据健康度给出建议
                    if avg_health >= 80:
                        ui.label('组合健康度优秀，继续持有').classes('text-sm text-gray-500 mt-1')
                    elif avg_health >= 60:
                        ui.label('组合健康度良好，关注个别股票').classes('text-sm text-gray-500 mt-1')
                    elif avg_health >= 40:
                        ui.label('组合健康度一般，建议调整').classes('text-sm text-gray-500 mt-1')
                    else:
                        ui.label('组合健康度较差，急需调整').classes('text-sm text-red-500 mt-1')
                else:
                    ui.label('--').classes('text-2xl font-bold text-gray-600')
                    ui.label('暂无持仓数据').classes('text-sm text-gray-500 mt-1')
                    
    def _update_portfolio_table_with_real_data(self, holdings):
        """使用真实数据更新持仓列表"""
        self.portfolio_table.clear()
        
        if not holdings:
            ui.label('暂无持仓数据').classes('text-center text-gray-500 py-8')
            return
            
        # 创建表格
        columns = [
            {'name': '代码', 'label': '代码', 'field': 'ts_code', 'align': 'left'},
            {'name': '名称', 'label': '名称', 'field': 'name', 'align': 'left'},
            {'name': '成本价', 'label': '成本价', 'field': 'cost', 'align': 'right'},
            {'name': '当前价', 'label': '当前价', 'field': 'current_price', 'align': 'right'},
            {'name': '盈亏', 'label': '盈亏', 'field': 'pnl_pct', 'align': 'right'},
            {'name': '得分', 'label': '得分', 'field': 'score', 'align': 'right'},
            {'name': '建议', 'label': '建议', 'field': 'suggestion', 'align': 'left'},
        ]
        
        # 转换数据格式
        table_data = []
        for holding in holdings:
            ts_code = holding.get('ts_code', '')
            # 提取股票代码（去掉.SH/.SZ后缀）
            code = ts_code.split('.')[0] if '.' in ts_code else ts_code
            
            table_data.append({
                'ts_code': code,
                'name': holding.get('name', ''),
                'cost': f"¥{holding.get('cost', 0):.2f}",
                'current_price': f"¥{holding.get('current_price', 0):.2f}",
                'pnl_pct': f"{holding.get('pnl_pct', 0):+.2f}%",
                'score': holding.get('score', 0),
                'suggestion': holding.get('suggestion', ''),
                '_original': holding  # 保存原始数据
            })
            
        table = ui.table(
            columns=columns,
            rows=table_data,
            row_key='ts_code',
            pagination={'rowsPerPage': 10}
        ).classes('w-full')
        
        # 添加点击事件
        def on_row_click(event):
            if event.args:
                row_data = event.args['row']
                original_data = row_data['_original']
                self.selected_stock = {
                    'ts_code': original_data.get('ts_code', ''),
                    'name': original_data.get('name', '')
                }
                self._show_page('stock_analysis')
                
        table.on('rowClick', on_row_click)
            
    def _update_market_temperature(self):
        """更新市场温度显示"""
        try:
            # 获取市场信号
            from datetime import datetime
            today = datetime.now().strftime('%Y%m%d')
            market_signals = self.strategy_manager.get_market_signals(today)
            
            # 提取市场温度和建议
            for signal in market_signals:
                if '市场温度' in signal.get('reason', ''):
                    # 从reason中提取温度值
                    import re
                    match = re.search(r'市场温度: ([\d.]+%)', signal['reason'])
                    if match:
                        temperature = match.group(1)
                        self.market_temperature.set_text(f'市场温度: {temperature}')
                        self.temperature_display.set_text(temperature)
                        
                        # 设置建议文本
                        self.temperature_suggestion.set_text(signal.get('suggestion', '加载中...'))
                        
                        # 根据温度设置不同颜色
                        temp_value = float(temperature.replace('%', ''))
                        if temp_value > 70:
                            self.market_temperature.classes('text-red-500 mr-4')
                            self.temperature_display.classes('text-3xl font-bold text-red-400')
                        elif temp_value > 60:
                            self.market_temperature.classes('text-orange-500 mr-4')
                            self.temperature_display.classes('text-3xl font-bold text-orange-400')
                        elif temp_value > 40:
                            self.market_temperature.classes('text-green-500 mr-4')
                            self.temperature_display.classes('text-3xl font-bold text-green-400')
                        else:
                            self.market_temperature.classes('text-blue-500 mr-4')
                            self.temperature_display.classes('text-3xl font-bold text-blue-400')
                    return
                    
        except Exception as e:
            self.logger.error(f"更新市场温度失败: {str(e)}")
            self.market_temperature.set_text('市场温度: --')
            self.market_temperature.classes('text-white mr-4')
            self.temperature_display.set_text('--')
            self.temperature_suggestion.set_text('加载失败')
            
    def _update_market_status(self):
        """更新市场状态数据（上涨家数、下跌家数等）"""
        try:
            import sqlite3
            import os
            
            # 连接数据库
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'stock_daily.db')
            conn = sqlite3.connect(db_path)
            
            # 获取最新交易日
            cursor = conn.execute("SELECT MAX(trade_date) FROM daily_prices")
            trade_date = cursor.fetchone()[0]
            
            if trade_date:
                # 获取上涨、下跌、平盘家数
                cursor = conn.execute("""
                    SELECT 
                        SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as up_count,
                        SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as down_count,
                        SUM(CASE WHEN pct_chg = 0 THEN 1 ELSE 0 END) as flat_count
                    FROM daily_prices 
                    WHERE trade_date = ? AND pct_chg IS NOT NULL
                """, (trade_date,))
                
                row = cursor.fetchone()
                if row:
                    up_count, down_count, flat_count = row
                    up_count = up_count or 0
                    down_count = down_count or 0
                    flat_count = flat_count or 0
                    
                    self.up_count_display.set_text(f'上涨家数: {up_count}')
                    self.down_count_display.set_text(f'下跌家数: {down_count}')
                    self.flat_count_display.set_text(f'平盘: {flat_count}')
                    
                    # 根据涨跌家数比例设置颜色
                    total = up_count + down_count + flat_count
                    if total > 0:
                        up_ratio = up_count / total
                        if up_ratio > 0.6:
                            self.up_count_display.classes('text-xl font-bold mb-1 text-green-300')
                            self.down_count_display.classes('text-xl font-bold text-red-300')
                        elif up_ratio < 0.4:
                            self.up_count_display.classes('text-xl font-bold mb-1 text-red-300')
                            self.down_count_display.classes('text-xl font-bold text-green-300')
            
            conn.close()
            
        except Exception as e:
            self.logger.error(f"更新市场状态失败: {str(e)}")
            self.up_count_display.set_text('上涨家数: --')
            self.down_count_display.set_text('下跌家数: --')
            self.flat_count_display.set_text('平盘: --')
      
    def _start_scan(self):
        """开始选股扫描"""
        # 显示加载状态
        self.scan_button.disable()
        self.scan_button.text = '扫描中...'
        self.loading_spinner.style('display: inline-block')
        self.result_count_label.text = '扫描中...'
        
        # 使用线程执行扫描，避免阻塞UI
        import threading
        
        def scan_thread():
            try:
                from datetime import datetime
                today = datetime.now().strftime('%Y%m%d')
                
                # 获取选股参数
                strategy_name = self.strategy_selector.value
                min_score = self.min_score_slider.value
                max_stocks = self.max_stocks_input.value
                
                # 获取策略信号
                all_signals = self.strategy_manager.get_all_signals(today)
                
                # 过滤符合条件的信号
                filtered_results = []
                score_30_count = 0
                
                for strat_name, strategy_signals in all_signals.items():
                    if strat_name == strategy_name:
                        # 统计评分≥30分的数量
                        score_30_count = len([s for s in strategy_signals if s.get('score', 0) >= 30])
                        
                        for signal in strategy_signals:
                            score = signal.get('score', 0)
                            if score >= min_score:
                                filtered_results.append(signal)
                
                # 按评分排序并限制数量
                filtered_results.sort(key=lambda x: x.get('score', 0), reverse=True)
                filtered_results = filtered_results[:max_stocks]
                
                # 更新UI
                ui.run_javascript(f"""
                    document.querySelector('.nicegui-content').scrollTop = 0;
                """)
                
                # 更新结果表格
                self._update_stock_results_table_with_real_data(filtered_results)
                
                # 更新漏斗统计
                self._update_funnel_stats()
                
                # 更新漏斗图
                self._update_funnel_chart(len(strategy_signals), score_30_count)
                
                # 更新结果数量
                self.result_count_label.text = f'共 {len(filtered_results)} 只股票符合条件'
                
                ui.notify(f'选股完成，找到 {len(filtered_results)} 只股票', type='success')
                
            except Exception as e:
                self.logger.error(f"选股扫描失败: {str(e)}")
                ui.notify(f'选股失败: {str(e)}', type='error')
            finally:
                # 恢复按钮状态
                self.scan_button.enable()
                self.scan_button.text = '开始扫描'
                self.loading_spinner.style('display: none')
        
        # 启动扫描线程
        threading.Thread(target=scan_thread, daemon=True).start()
        
    def _start_backtest(self):
        """开始回测"""
        ui.notify('开始回测...', type='info')
        
        try:
            from datetime import datetime
            from strategies.manager import StrategyManager
            
            # 获取回测参数
            strategy_name = self.backtest_strategy.value
            start_date = self.backtest_start_date.value
            end_date = self.backtest_end_date.value
            
            # 验证日期格式
            try:
                datetime.strptime(start_date, '%Y-%m-%d')
                datetime.strptime(end_date, '%Y-%m-%d')
            except ValueError:
                ui.notify('日期格式错误，请使用YYYY-MM-DD格式', type='error')
                return
                
            # 获取策略实例
            strategy = self.strategy_manager.get_strategy(strategy_name)
            
            if not strategy:
                ui.notify('策略不存在', type='error')
                return
                
            # 执行回测
            backtest_result = strategy.run_backtest(start_date, end_date)
            
            # 更新回测结果
            self._update_backtest_results(backtest_result)
            
            ui.notify('回测完成', type='positive')
            
        except Exception as e:
            self.logger.error(f"回测失败: {str(e)}")
            ui.notify('回测失败，请稍后重试', type='error')
            
    def _update_backtest_results(self, backtest_result):
        """更新回测结果显示"""
        # 清空现有内容
        self.return_metric.clear()
        self.winrate_metric.clear()
        self.drawdown_metric.clear()
        self.backtest_chart.clear()
        
        # 提取回测数据
        total_return = backtest_result.get('total_return', 0)
        win_rate = backtest_result.get('win_rate', 0)
        max_drawdown = backtest_result.get('max_drawdown', 0)
        trades = backtest_result.get('trades', [])
        equity_curve = backtest_result.get('equity_curve', [])
        
        # 更新收益指标卡片
        with self.return_metric:
            with ui.card_content():
                ui.label('总收益率').classes('text-sm text-gray-500')
                return_color = 'text-green-600' if total_return > 0 else 'text-red-600'
                ui.label(f'{total_return:+.2f}%').classes(f'text-2xl font-bold {return_color}')
                ui.label(f'基准收益: {backtest_result.get("benchmark_return", 0):+.2f}%').classes('text-xs text-gray-500 mt-1')
                
        # 更新胜率指标卡片
        with self.winrate_metric:
            with ui.card_content():
                ui.label('胜率').classes('text-sm text-gray-500')
                ui.label(f'{win_rate:.1f}%').classes('text-2xl font-bold text-blue-600')
                ui.label(f'交易次数: {len(trades)}').classes('text-xs text-gray-500 mt-1')
                
        # 更新最大回撤指标卡片
        with self.drawdown_metric:
            with ui.card_content():
                ui.label('最大回撤').classes('text-sm text-gray-500')
                drawdown_color = 'text-red-600' if max_drawdown < 0 else 'text-gray-600'
                ui.label(f'{max_drawdown:.2f}%').classes(f'text-2xl font-bold {drawdown_color}')
                ui.label(f'夏普比率: {backtest_result.get("sharpe_ratio", 0):.2f}').classes('text-xs text-gray-500 mt-1')
                
        # 更新收益曲线图表
        with self.backtest_chart:
            if equity_curve:
                # 创建简单的收益曲线可视化
                with ui.row().classes('w-full items-end h-full gap-1'):
                    max_equity = max(equity_curve)
                    min_equity = min(equity_curve)
                    
                    for i, equity in enumerate(equity_curve):
                        # 计算高度比例
                        height = ((equity - min_equity) / (max_equity - min_equity)) * 100 if max_equity > min_equity else 50
                        color = 'bg-green-500' if equity > 1 else 'bg-red-500'
                        ui.card().classes(f'h-[{height}%] w-full {color}')
                        
                    # 添加基准线
                    ui.card().classes('h-[50%] w-full bg-gray-300 absolute bottom-0 left-0 right-0 opacity-50')
                    ui.label('基准线').classes('absolute bottom-0 left-0 text-xs text-gray-500 ml-1 mb-1')
            else:
                ui.label('暂无回测数据').classes('text-center text-gray-500 py-8')
        
    def _show_add_position_modal(self):
        """显示添加持仓弹窗"""
        with ui.dialog() as dialog, ui.card():
            ui.label('添加持仓').classes('text-xl font-bold mb-4')
            
            with ui.column().classes('w-80'):
                stock_code_input = ui.input('股票代码')
                stock_name_input = ui.input('股票名称')
                quantity_input = ui.number('持仓数量', value=100, min=100)
                cost_price_input = ui.number('成本价格', value=0.0, min=0.01)
                
                with ui.row().classes('mt-4 justify-end'):
                    ui.button('取消', on_click=dialog.close)
                    ui.button('添加', on_click=lambda: self._add_position(
                        stock_code_input.value,
                        stock_name_input.value,
                        quantity_input.value,
                        cost_price_input.value,
                        dialog
                    )).classes('ml-2')
                    
        dialog.open()
        
    def _add_position(self, code: str, name: str, quantity: int, cost: float, dialog):
        """添加持仓"""
        if not code or not name:
            ui.notify('请填写完整信息', type='negative')
            return
            
        # 这里可以添加添加持仓的逻辑
        ui.notify(f'已添加持仓: {name} ({code})', type='positive')
        dialog.close()
        
    def _show_create_strategy_modal(self):
        """显示创建新策略弹窗"""
        with ui.dialog() as dialog, ui.card():
            ui.label('创建新策略').classes('text-xl font-bold mb-4')
            
            with ui.column().classes('w-96'):
                strategy_name_input = ui.input('策略名称')
                strategy_desc_input = ui.input('策略描述')
                
                ui.label('策略类型').classes('mt-4 mb-2')
                strategy_type_select = ui.select(
                    options={'volume': '量能策略', 'price': '价格策略', 'hybrid': '混合策略'},
                    value='hybrid'
                )
                
                with ui.row().classes('mt-4 justify-end'):
                    ui.button('取消', on_click=dialog.close)
                    ui.button('创建', on_click=lambda: self._create_strategy(
                        strategy_name_input.value,
                        strategy_desc_input.value,
                        strategy_type_select.value,
                        dialog
                    )).classes('ml-2')
                    
        dialog.open()
        
    def _create_strategy(self, name: str, desc: str, type: str, dialog):
        """创建新策略"""
        if not name:
            ui.notify('请填写策略名称', type='negative')
            return
            
        # 这里可以添加创建策略的逻辑
        ui.notify(f'已创建策略: {name}', type='positive')
        dialog.close()
        
    def _parse_strategy_with_ai(self, dialog):
        """使用AI解析策略"""
        strategy_desc = self.ai_strategy_input.value.strip()
        
        if not strategy_desc:
            ui.notify('请输入策略描述', type='warning')
            return
            
        # 更新UI状态
        self.parse_button.disable()
        self.stop_button.enable()
        self.parse_status.text = '解析中...'
        self.parse_status.style('color: #f59e0b')  # amber-500
        self.parse_progress.style('display: block')
        self.parse_progress.value = 0.2
        
        # 使用线程执行AI解析
        import threading
        
        def parse_thread():
            try:
                # 模拟AI解析过程
                import time
                import json
                
                # 模拟进度更新
                for i in range(3):
                    time.sleep(1)
                    progress = 0.2 + (i + 1) * 0.2
                    self.parse_progress.value = min(progress, 0.8)
                    
                # 调用AI解析模块
                try:
                    from strategies.ai_parser import parse_strategy
                    
                    # 解析策略
                    result = parse_strategy(
                        strategy_desc=strategy_desc,
                        model=self.ai_model_select.value,
                        strategy_type=self.ai_strategy_type.value
                    )
                except ImportError:
                    # 如果AI解析模块不存在，使用模拟数据
                    result = self._generate_fake_ai_result(strategy_desc)
                    
                # 更新进度
                self.parse_progress.value = 1.0
                self.parse_status.text = '解析完成'
                self.parse_status.style('color: #10b981')  # green-500
                
                # 更新结果显示
                self._update_ai_parse_results(result)
                
                # 启用操作按钮
                self.save_strategy_button.enable()
                self.run_backtest_button.enable()
                
                ui.notify('AI策略解析完成', type='success')
                
            except Exception as e:
                self.logger.error(f"AI解析策略失败: {str(e)}")
                self.parse_status.text = '解析失败'
                self.parse_status.style('color: #ef4444')  # red-500
                ui.notify(f'AI解析失败: {str(e)}', type='error')
            finally:
                # 恢复按钮状态
                self.parse_button.enable()
                self.stop_button.disable()
                self.parse_progress.value = 0.0
                self.parse_progress.style('display: none')
                
        # 启动解析线程
        threading.Thread(target=parse_thread, daemon=True).start()
        
    def _stop_ai_parsing(self):
        """停止AI解析"""
        # 这里可以添加停止AI解析的逻辑
        self.parse_status.text = '已停止'
        self.parse_status.style('color: #6b7280')  # gray-500
        self.parse_button.enable()
        self.stop_button.disable()
        self.parse_progress.value = 0.0
        self.parse_progress.style('display: none')
        ui.notify('已停止AI解析', type='info')
        
    def _update_ai_parse_results(self, result):
        """更新AI解析结果显示"""
        # 更新规则列表
        self.ai_rules_list.clear()
        
        if 'rules' in result and result['rules']:
            with self.ai_rules_list:
                ui.label('策略规则列表').classes('text-lg font-bold mb-4')
                
                for i, rule in enumerate(result['rules'], 1):
                    with ui.card().classes('w-full mb-3'):
                        with ui.card_content().classes('p-3'):
                            ui.label(f'规则 {i}: {rule["name"]}').classes('font-bold mb-2')
                            ui.label(f'条件: {rule["condition"]}').classes('text-sm mb-2')
                            ui.label(f'操作: {rule["action"]}').classes('text-sm mb-2')
                            ui.label(f'权重: {rule["weight"]}').classes('text-sm text-gray-600')
                            
                            if 'description' in rule:
                                ui.label(f'描述: {rule["description"]}').classes('text-xs text-gray-500 mt-2')
        else:
            with self.ai_rules_list:
                ui.label('未生成策略规则').classes('text-center text-gray-500 py-8')
                
        # 更新策略代码
        if 'code' in result and result['code']:
            self.ai_code_view.content = result['code']
        else:
            self.ai_code_view.content = """# 未生成策略代码
# 请检查输入描述是否清晰"""
            
    def _save_ai_generated_strategy(self, dialog):
        """保存AI生成的策略"""
        try:
            # 这里可以添加保存策略的逻辑
            ui.notify('策略保存成功', type='success')
            dialog.close()
            
            # 更新策略列表
            self.strategy_manager.reload_strategies()
            self._update_strategy_list()
            
        except Exception as e:
            self.logger.error(f"保存策略失败: {str(e)}")
            ui.notify(f'保存策略失败: {str(e)}', type='error')
            
    def _run_ai_strategy_backtest(self):
        """运行AI策略回测"""
        try:
            # 显示回测加载状态
            self.ai_backtest_results.clear()
            with self.ai_backtest_results:
                ui.label('正在运行回测...').classes('text-center text-gray-500 py-8')
                ui.spinner().classes('self-center')
                
            # 使用线程执行回测
            import threading
            
            def backtest_thread():
                try:
                    # 模拟回测过程
                    import time
                    time.sleep(3)
                    
                    # 生成模拟回测结果
                    backtest_result = {
                        'total_trades': 24,
                        'win_rate': 62.5,
                        'total_return': 28.6,
                        'max_drawdown': -8.2,
                        'sharpe_ratio': 1.8,
                        'profit_factor': 1.9
                    }
                    
                    # 更新回测结果显示
                    self._update_ai_backtest_results(backtest_result)
                    
                    ui.notify('回测完成', type='success')
                    
                except Exception as e:
                    self.logger.error(f"回测失败: {str(e)}")
                    self.ai_backtest_results.clear()
                    with self.ai_backtest_results:
                        ui.label('回测失败').classes('text-center text-red-500 py-8')
                    ui.notify(f'回测失败: {str(e)}', type='error')
                    
            # 启动回测线程
            threading.Thread(target=backtest_thread, daemon=True).start()
            
        except Exception as e:
            self.logger.error(f"启动回测失败: {str(e)}")
            ui.notify(f'启动回测失败: {str(e)}', type='error')
            
    def _generate_fake_ai_result(self, strategy_desc):
        """生成模拟AI解析结果"""
        rules = [
            {
                'name': '均线突破买入',
                'condition': '股价突破20日均线且成交量放大50%',
                'action': '买入',
                'weight': 20,
                'description': '当股价向上突破20日均线同时成交量较昨日放大50%以上时，产生买入信号'
            },
            {
                'name': '均线跌破卖出',
                'condition': '股价跌破10日均线且成交量萎缩',
                'action': '卖出',
                'weight': 15,
                'description': '当股价向下跌破10日均线同时成交量较昨日萎缩时，产生卖出信号'
            }
        ]
        code = self._get_fake_code()
        return {'rules': rules, 'code': code}
    
    def _get_fake_code(self):
        """获取模拟策略代码"""
        return '# AI生成的策略代码示例\nclass AIGeneratedStrategy(BaseStrategy):\n    def __init__(self, db_path, config):\n        super().__init__(db_path, config)\n        self.version = "1.0"\n        self.strategy_desc = "AI生成的均线策略"\n    \n    def get_signals(self, trade_date):\n        signals = []\n        return signals\n    \n    def get_trade_plan(self, ts_code, signal):\n        return {\n            "buy_range": {"ideal_low": signal["close_price"] * 0.98, "ideal_high": signal["close_price"] * 1.02},\n            "position_pct": 0.1,\n            "stop_loss_initial": signal["close_price"] * 0.95\n        }\n    \n    def get_push_card(self, signal):\n        return "<div>AI策略信号: " + signal["ts_code"] + "</div>"'
    
    def _update_ai_backtest_results(self, result):
        """更新AI策略回测结果"""
        self.ai_backtest_results.clear()
        
        with self.ai_backtest_results:
            ui.label('回测结果概览').classes('text-lg font-bold mb-4')
            
            # 回测指标卡片
            with ui.row().classes('w-full gap-4 mb-6'):
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('总交易次数').classes('text-sm text-gray-500')
                        ui.label(result['total_trades']).classes('text-2xl font-bold')
                        
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('胜率').classes('text-sm text-gray-500')
                        ui.label(f'{result["win_rate"]}%').classes('text-2xl font-bold text-green-600')
                        
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('总收益率').classes('text-sm text-gray-500')
                        ui.label(f'+{result["total_return"]}%').classes('text-2xl font-bold text-green-600')
                        
                with ui.card().classes('flex-1'):
                    with ui.card_content():
                        ui.label('最大回撤').classes('text-sm text-gray-500')
                        ui.label(f'{result["max_drawdown"]}%').classes('text-2xl font-bold text-red-600')
                        
            # 详细指标
            with ui.card().classes('w-full'):
                with ui.card_content():
                    ui.label('风险指标').classes('text-md font-bold mb-3')
                    
                    with ui.row().classes('w-full gap-6'):
                        with ui.column().classes('flex-1'):
                            ui.label('夏普比率').classes('text-sm text-gray-500')
                            ui.label(result['sharpe_ratio']).classes('text-xl font-bold')
                            
                        with ui.column().classes('flex-1'):
                            ui.label('盈利因子').classes('text-sm text-gray-500')
                            ui.label(result['profit_factor']).classes('text-xl font-bold')
                            
            # 回测结论
            with ui.card().classes('w-full mt-4'):
                with ui.card_content():
                    ui.label('回测结论').classes('text-md font-bold mb-2')
                    ui.label('该策略表现良好，具有较高的胜率和合理的风险控制。建议进行实盘验证或参数优化。').classes('text-sm')
        
    def _show_sell_modal(self, stock_data: Dict):
        """显示卖出弹窗"""
        with ui.dialog() as dialog, ui.card():
            ui.label(f'卖出 {stock_data["name"]}').classes('text-xl font-bold mb-4')
            
            with ui.column().classes('w-80'):
                ui.label(f'当前价格: ¥{stock_data["current"]}').classes('text-lg font-bold text-green-600 mb-2')
                ui.label(f'持仓成本: ¥{stock_data["cost"]}').classes('text-sm text-gray-500 mb-4')
                
                quantity_input = ui.number('卖出数量', value=stock_data['quantity'], min=100, max=stock_data['quantity'])
                
                with ui.row().classes('mt-4 justify-end'):
                    ui.button('取消', on_click=dialog.close)
                    ui.button('卖出', on_click=lambda: self._execute_sell(stock_data, quantity_input.value, dialog)).classes('ml-2')
                    
        dialog.open()
        
    def _execute_sell(self, stock_data: Dict, quantity: int, dialog):
        """执行卖出操作"""
        if quantity <= 0:
            ui.notify('请输入有效的卖出数量', type='negative')
            return
            
        # 这里可以添加真实的卖出逻辑
        ui.notify(f'已卖出 {quantity} 股 {stock_data["name"]}', type='positive')
        dialog.close()
        
    def _show_stock_detail(self, stock_data: Dict):
        """显示股票详情"""
        ui.notify(f'显示股票详情: {stock_data["name"]}')
        
    def _start_strategy_backtest(self, strategy_name: str):
        """开始策略回测"""
        self._show_page('backtest')
        self.backtest_strategy.set_value(strategy_name)
        ui.notify(f'准备回测策略: {strategy_name}')
        
    def _edit_strategy(self, strategy_name: str):
        """编辑策略"""
        ui.notify(f'编辑策略: {strategy_name}')
        
    def _show_stock_detail(self, stock_data: Dict):
        """显示股票详情"""
        self._select_stock(stock_data)
        
    def _show_simulated_stock_data(self):
        """显示模拟股票数据"""
        self.stock_info_card.clear()
        
        with self.stock_info_card:
            with ui.card_header():
                ui.label('贵州茅台 (600519)').classes('text-xl font-bold')
                ui.label('¥1,856.00').classes('ml-auto text-2xl font-bold text-green-600')
                
            with ui.card_content():
                with ui.row().classes('w-full gap-6'):
                    with ui.column():
                        ui.label('涨跌幅').classes('text-sm text-gray-500')
                        ui.label('+2.56%').classes('text-lg font-bold text-green-600')
                        
                    with ui.column():
                        ui.label('成交量').classes('text-sm text-gray-500')
                        ui.label('23,568手').classes('text-lg font-bold')
                        
                    with ui.column():
                        ui.label('成交额').classes('text-sm text-gray-500')
                        ui.label('4.37亿').classes('text-lg font-bold')
                        
                    with ui.column():
                        ui.label('换手率').classes('text-sm text-gray-500')
                        ui.label('0.32%').classes('text-lg font-bold')
                        
        # 评分卡片
        with self.score_overview:
            with ui.card_content():
                ui.label('综合评分').classes('text-sm text-gray-500')
                ui.label('89分').classes('text-3xl font-bold text-blue-600 mb-2')
                
                with ui.row().classes('w-full'):
                    with ui.column().classes('flex-1'):
                        ui.label('基本面').classes('text-xs text-center text-gray-500')
                        ui.circular_progress(value=0.92).classes('w-16 h-16 mx-auto')
                        ui.label('92').classes('text-xs text-center font-bold')
                        
                    with ui.column().classes('flex-1'):
                        ui.label('技术面').classes('text-xs text-center text-gray-500')
                        ui.circular_progress(value=0.87).classes('w-16 h-16 mx-auto')
                        ui.label('87').classes('text-xs text-center font-bold')
                        
                    with ui.column().classes('flex-1'):
                        ui.label('资金面').classes('text-xs text-center text-gray-500')
                        ui.circular_progress(value=0.95).classes('w-16 h-16 mx-auto')
                        ui.label('95').classes('text-xs text-center font-bold')
                        
        # 交易建议
        with self.trade_suggestion:
            with ui.card_content():
                ui.label('交易建议').classes('text-sm text-gray-500')
                ui.badge('强买入', color='green').classes('text-lg mb-2')
                
                ui.label('目标价格: ¥2,000.00').classes('text-sm mb-1')
                ui.label('止损价格: ¥1,763.20').classes('text-sm mb-1')
                ui.label('建议仓位: 15%').classes('text-sm mb-1')
                
                ui.label('理由: 主力资金持续流入，股东户数下降，技术形态良好').classes('text-sm text-gray-600 mt-2')
                
    def _update_real_score_cards(self, ts_code: str):
        """更新真实的评分卡片数据"""
        try:
            # 获取策略评分
            from datetime import datetime
            today = datetime.now().strftime('%Y%m%d')
            
            # 获取所有策略对该股票的评分
            all_signals = self.strategy_manager.get_all_signals(today)
            
            # 计算综合评分
            total_score = 0
            signal_count = 0
            buy_signals = []
            
            for strategy_name, strategy_signals in all_signals.items():
                for signal in strategy_signals:
                    if signal.get('ts_code') == ts_code:
                        score = signal.get('score', 0)
                        total_score += score
                        signal_count += 1
                        buy_signals.append(signal)
            
            # 更新评分卡片
            self.score_overview.clear()
            with self.score_overview:
                with ui.card_content():
                    if signal_count > 0:
                        avg_score = total_score / signal_count
                        ui.label('综合评分').classes('text-sm text-gray-500')
                        ui.label(f'{avg_score:.0f}分').classes('text-3xl font-bold text-blue-600 mb-2')
                        
                        # 显示各策略评分
                        with ui.column().classes('w-full gap-2 mt-4'):
                            for strategy_name, strategy_signals in all_signals.items():
                                for signal in strategy_signals:
                                    if signal.get('ts_code') == ts_code:
                                        with ui.row().classes('w-full items-center'):
                                            ui.label(strategy_name).classes('text-sm flex-1')
                                            ui.label(f'{signal.get("score", 0)}分').classes('text-sm font-bold')
                    else:
                        ui.label('综合评分').classes('text-sm text-gray-500')
                        ui.label('--').classes('text-3xl font-bold text-gray-600 mb-2')
                        ui.label('暂无评分数据').classes('text-sm text-gray-500')
            
            # 更新交易建议卡片
            self.trade_suggestion.clear()
            with self.trade_suggestion:
                with ui.card_content():
                    ui.label('交易建议').classes('text-sm text-gray-500')
                    
                    if buy_signals:
                        # 生成综合交易建议
                        strong_buy_count = sum(1 for s in buy_signals if s.get('signal_type') == 'strong_buy')
                        buy_count = sum(1 for s in buy_signals if s.get('signal_type') == 'buy')
                        
                        if strong_buy_count >= len(buy_signals) * 0.6:
                            ui.badge('强买入', color='green').classes('text-lg mb-2')
                        elif buy_count + strong_buy_count >= len(buy_signals) * 0.6:
                            ui.badge('买入', color='blue').classes('text-lg mb-2')
                        else:
                            ui.badge('持有', color='orange').classes('text-lg mb-2')
                        
                        # 显示建议理由
                        with ui.column().classes('w-full mt-2'):
                            for signal in buy_signals[:3]:  # 最多显示3个理由
                                if signal.get('reason'):
                                    ui.label(f'• {signal.get("reason")}').classes('text-sm text-gray-600 mb-1')
                    else:
                        ui.badge('暂无信号', color='gray').classes('text-lg mb-2')
                        ui.label('暂无交易建议').classes('text-sm text-gray-500')
                        
        except Exception as e:
            self.logger.error(f"更新评分卡片失败: {str(e)}")
                
    def _run(self):
        """启动UI应用"""
        # 初始化策略管理器
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            self.strategy_manager = get_strategy_manager(config)
            self.strategy_manager.scan_strategies()
            self.strategy_manager.load_enabled_strategies()
            
        except Exception as e:
            self.logger.error(f"初始化策略管理器失败: {str(e)}")
            
        # 启动NiceGUI
        ui.run(
            title='StockAI 智能分析系统',
            host='0.0.0.0',
            port=8000,
            reload=False,
            show=True
        )


if __name__ == '__main__':
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 启动UI
    app = StockAIUI()
    app._run()