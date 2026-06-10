# -*- coding: utf-8 -*-
"""
Volume Chip Strategy v3.0 - 量筹码策略
基于多表数据的横盘吸筹量化识别策略
"""
import os
import sys
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

import pandas as pd
import numpy as np

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filter_engine import FilterEngine
from trade_plan import generate_trade_plan
from .base_strategy import BaseStrategy


class VolumeChipV3Strategy(BaseStrategy):
    """量筹码策略v3.0 - 基于主力资金、股东户数、背离信号的综合选股策略"""
    
    strategy_name: str = "volume_chip_v3"
    strategy_desc: str = "基于多表数据的横盘吸筹量化识别策略"
    version: str = "3.0"
    author: str = "StockAI Team"
    tags: List[str] = ["volume", "chip", "institution", "quantitative"]
    
    def __init__(self, db_path, config):
        super().__init__(db_path, config)
        self.logger = logging.getLogger(__name__)
        
        # 策略配置
        self.lookback_days = self.config.get('lookback_days', 60)
        self.min_score = self.config.get('min_score', 15)
        self.strong_signal_threshold = self.config.get('strong_signal_threshold', 30)
        
        # 初始化过滤引擎
        self.filter_engine = FilterEngine()
        
        # 缓存
        self.stock_cache: Dict[str, Dict] = {}
        self.market_cache: Dict = {}
        self.signal_cache: Dict[str, List[Dict]] = {}
        
    def initialize(self):
        """初始化策略"""
        self.logger.info(f"正在初始化 {self.strategy_name} 策略 v{self.version}")
        
        # 验证数据库连接
        try:
            conn = sqlite3.connect(self.db_path)
            conn.close()
            self.logger.info("数据库连接验证成功")
        except Exception as e:
            self.logger.error(f"数据库连接失败: {str(e)}")
            raise
            
        # 加载配置参数
        self._load_parameters()
        
        self.logger.info(f"{self.strategy_name} 策略初始化完成")
        super().initialize()
        
    def _load_parameters(self):
        """加载策略参数"""
        self.params = {
            # 核心因子权重
            'fund_flow_weight': 15,
            'holder_decrease_weight': 15,
            'divergence_weight': 10,
            
            # 风险因子
            'amplitude_penalty': -5,
            
            # 过滤条件
            'min_market_cap': 10,  # 最小流通市值（亿）
            'max_single_stock_position': 0.2,  # 单股最大仓位
            
            # 止损参数
            'stop_loss_ratio': 0.05,  # 5% 固定止损
            'stop_loss_factor': 0.98,  # 结构止损因子
            
            # 仓位配置
            'position_config': {
                'attack': {'strong': 0.15, 'medium': 0.08},
                'defense': {'strong': 0.05, 'medium': 0.02},
                'empty': {'strong': 0.0, 'medium': 0.0}
            }
        }
        
        # 合并用户配置
        self.params.update(self.config.get('parameters', {}))
        
    def get_signals(self, trade_date: str) -> list:
        """生成指定交易日的交易信号"""
        # 检查缓存
        if trade_date in self.signal_cache:
            self.logger.info(f"从缓存加载 {trade_date} 的交易信号")
            return self.signal_cache[trade_date]
            
        signals = []
        
        try:
            self.logger.info(f"开始生成 {trade_date} 的交易信号")
            
            # 调用过滤引擎执行过滤
            success, filter_results = self.filter_engine.run_filter()
            
            if not success:
                self.logger.error("过滤引擎执行失败")
                return signals
                
            self.logger.info(f"过滤完成，共选出 {len(filter_results)} 只股票")
            
            # 转换为标准信号格式
            for result in filter_results:
                ts_code = result['ts_code']
                score = result['score']
                
                # 确定信号类型
                if score >= self.strong_signal_threshold:
                    signal_type = "strong_buy"
                    signal_strength = "high"
                elif score >= self.min_score:
                    signal_type = "buy"
                    signal_strength = "medium"
                else:
                    continue  # 低于阈值的不生成信号
                
                signal = {
                    'ts_code': ts_code,
                    'signal_type': signal_type,
                    'signal_strength': signal_strength,
                    'score': score,
                    'trade_date': trade_date,
                    'stock_name': result.get('name', ''),
                    'industry': result.get('industry', ''),
                    'close_price': result.get('close', 0),
                    'pct_chg': result.get('pct_chg', 0),
                    'main_money': result.get('main_money', 0),
                    'holder_chg': result.get('holder_chg', 0),
                    'details': result.get('details', {})
                }
                
                signals.append(signal)
                
            self.logger.info(f"信号生成完成，共生成 {len(signals)} 个信号")
            
            # 缓存信号
            self.signal_cache[trade_date] = signals
        
        except Exception as e:
            self.logger.error(f"生成信号失败: {str(e)}")
            
        return signals
        
    def get_market_signals(self, market_data: Optional[Dict] = None) -> List[Dict]:
        """生成市场整体信号"""
        signals = []
        
        try:
            # 获取市场数据
            if market_data is None:
                market_data = self._get_market_data()
                
            # 计算市场温度
            market_temperature = self._calculate_market_temperature(market_data)
            
            # 生成市场信号
            if market_temperature > 0.7:
                signals.append({
                    'signal_type': 'market_bullish',
                    'strength': SignalStrength.STRONG,
                    'reason': f"市场温度: {market_temperature:.1%}，处于过热区间",
                    'suggestion': "适当降低仓位，控制风险"
                })
            elif market_temperature > 0.6:
                signals.append({
                    'signal_type': 'market_bullish',
                    'strength': SignalStrength.MEDIUM,
                    'reason': f"市场温度: {market_temperature:.1%}，处于偏热区间",
                    'suggestion': "保持仓位，谨慎操作"
                })
            elif market_temperature > 0.4:
                signals.append({
                    'signal_type': 'market_neutral',
                    'strength': SignalStrength.MEDIUM,
                    'reason': f"市场温度: {market_temperature:.1%}，处于正常区间",
                    'suggestion': "正常操作，跟随策略"
                })
            else:
                signals.append({
                    'signal_type': 'market_bearish',
                    'strength': SignalStrength.STRONG,
                    'reason': f"市场温度: {market_temperature:.1%}，处于冰冷区间",
                    'suggestion': "保持低仓位，耐心等待"
                })
                
        except Exception as e:
            self.logger.error(f"生成市场信号失败: {str(e)}")
            
        return signals
        
    def get_trade_plan(self, ts_code: str, signal: dict) -> dict:
        """根据股票代码和信号生成交易计划"""
        try:
            # 调用外部交易计划生成函数
            trade_plan = generate_trade_plan(
                ts_code=ts_code,
                score=signal['score'],
                close_price=signal['close_price'],
                market_regime=self.config.get('market_regime', 'neutral')
            )
            
            return trade_plan
            
        except Exception as e:
            self.logger.error(f"生成交易计划失败 {ts_code}: {str(e)}")
            return {
                'ts_code': ts_code,
                'error': str(e),
                'success': False
            }
    
    def get_push_card(self, signal: dict) -> str:
        """生成用于推送的卡片内容（HTML格式）"""
        try:
            ts_code = signal['ts_code']
            stock_name = signal.get('stock_name', ts_code)
            score = signal['score']
            signal_type = signal['signal_type']
            close_price = signal.get('close_price', 0)
            pct_chg = signal.get('pct_chg', 0)
            industry = signal.get('industry', '未知行业')
            main_money = signal.get('main_money', 0)
            holder_chg = signal.get('holder_chg', 0)
            
            # 确定信号标题和颜色
            if signal_type == 'strong_buy':
                signal_title = '🔴 强买入信号'
                card_color = '#ff6b6b'
            elif signal_type == 'buy':
                signal_title = '🟡 买入信号'
                card_color = '#ffd93d'
            else:
                signal_title = '🟢 关注信号'
                card_color = '#6bcf7f'
            
            # 格式化主力资金
            main_money_str = f"{main_money/10000:.1f}万" if main_money != 0 else '无数据'
            holder_chg_str = f"{holder_chg:.2%}" if holder_chg != 0 else '无数据'
            
            # 生成HTML卡片
            # 先计算动态值
            pct_color = '#ff6b6b' if pct_chg >= 0 else '#6bcf7f'
            pct_symbol = '↑' if pct_chg >= 0 else '↓'
            main_money_color = '#6bcf7f' if main_money > 0 else '#ff6b6b'
            holder_chg_color = '#6bcf7f' if holder_chg < 0 else '#ff6b6b'
            
            html = f"""
            <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; max-width: 400px; background: white; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div style="font-size: 18px; font-weight: bold; color: {card_color};">{signal_title}</div>
                    <div style="font-size: 14px; color: #666;">{signal['trade_date']}</div>
                </div>
                
                <div style="margin-bottom: 12px;">
                    <div style="font-size: 16px; font-weight: bold;">{stock_name} ({ts_code})</div>
                    <div style="font-size: 12px; color: #666;">{industry}</div>
                </div>
                
                <div style="display: flex; justify-content: space-between; margin-bottom: 12px;">
                    <div style="text-align: center;">
                        <div style="font-size: 20px; font-weight: bold; color: #333;">{close_price:.2f}</div>
                        <div style="font-size: 12px; color: {pct_color};">
                            {pct_symbol} {abs(pct_chg):.2f}%
                        </div>
                    </div>
                    <div style="text-align: center;">
                        <div style="font-size: 20px; font-weight: bold; color: #333;">{score}</div>
                        <div style="font-size: 12px; color: #666;">综合评分</div>
                    </div>
                </div>
                
                <div style="font-size: 13px; line-height: 1.6;">
                    <div style="margin-bottom: 4px;">📊 主力资金: <span style="color: {main_money_color};">{main_money_str}</span></div>
                    <div style="margin-bottom: 4px;">👥 股东变化: <span style="color: {holder_chg_color};">{holder_chg_str}</span></div>
                </div>
                
                <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid #eee;">
                    <div style="font-size: 12px; color: #999;">📈 策略: 量筹码策略 v{self.version}</div>
                </div>
            </div>
            """
            
            return html.strip()
            
        except Exception as e:
            self.logger.error(f"生成推送卡片失败: {str(e)}")
            return f"<div>生成推送卡片失败: {str(e)}</div>"
    
    def backtest(self, start_date: str, end_date: str, params: Optional[Dict] = None) -> Dict:
        """回测策略"""
        try:
            self.logger.info(f"开始回测 {self.strategy_name} 策略: {start_date} 至 {end_date}")
            
            # 使用指定参数或默认参数
            backtest_params = params or self.params.copy()
            
            # 获取回测数据
            stock_list = self._get_backtest_stock_list()
            all_results = []
            
            for stock_code in stock_list[:20]:  # 限制回测股票数量
                try:
                    stock_data = self._get_stock_data(stock_code, start_date, end_date)
                    if stock_data is None:
                        continue
                        
                    # 模拟交易
                    trade_signals = self._simulate_trading(stock_data, backtest_params)
                    all_results.extend(trade_signals)
                    
                except Exception as e:
                    self.logger.error(f"回测股票失败 {stock_code}: {str(e)}")
                    
            # 计算回测指标
            metrics = self._calculate_backtest_metrics(all_results)
            
            self.logger.info(f"回测完成，收益率: {metrics.get('total_return', 0):.2%}")
            
            return {
                'strategy_name': self.strategy_name,
                'version': self.version,
                'start_date': start_date,
                'end_date': end_date,
                'parameters': backtest_params,
                'metrics': metrics,
                'trades': all_results
            }
            
        except Exception as e:
            self.logger.error(f"回测失败: {str(e)}")
            return {'error': str(e)}
            
    def optimize_parameters(self, start_date: str, end_date: str) -> Dict:
        """优化策略参数"""
        try:
            self.logger.info(f"开始优化 {self.strategy_name} 策略参数")
            
            # 参数搜索空间
            param_space = {
                'fund_flow_weight': [10, 15, 20],
                'holder_decrease_weight': [10, 15, 20],
                'divergence_weight': [5, 10, 15],
                'strong_signal_threshold': [25, 30, 35]
            }
            
            best_params = None
            best_score = -float('inf')
            
            # 网格搜索
            from itertools import product
            
            for params in product(*param_space.values()):
                param_dict = dict(zip(param_space.keys(), params))
                
                # 回测参数组合
                result = self.backtest(start_date, end_date, {'parameters': param_dict})
                
                if 'error' not in result:
                    total_return = result['metrics'].get('total_return', 0)
                    sharpe_ratio = result['metrics'].get('sharpe_ratio', 0)
                    
                    # 综合评分
                    score = total_return * 0.7 + sharpe_ratio * 0.3
                    
                    if score > best_score:
                        best_score = score
                        best_params = param_dict
                        
            self.logger.info(f"参数优化完成，最佳参数: {best_params}")
            
            return best_params or {}
            
        except Exception as e:
            self.logger.error(f"参数优化失败: {str(e)}")
            return {}
            
    def _get_stock_data(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """获取个股数据"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 构建查询日期范围
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=self.lookback_days)).strftime('%Y-%m-%d')
            if end_date is None:
                end_date = datetime.now().strftime('%Y-%m-%d')
                
            query = f"""
                SELECT * FROM stock_daily 
                WHERE stock_code = '{stock_code}' 
                  AND trade_date BETWEEN '{start_date}' AND '{end_date}'
                ORDER BY trade_date DESC
            """
            
            df = pd.read_sql(query, conn)
            conn.close()
            
            if df.empty:
                self.logger.warning(f"未找到股票数据 {stock_code}")
                return None
                
            return df
            
        except Exception as e:
            self.logger.error(f"获取股票数据失败 {stock_code}: {str(e)}")
            return None
            
    def _calculate_score(self, stock_code: str, data: pd.DataFrame) -> Tuple[float, str]:
        """计算个股综合得分"""
        score = 0
        details = []
        
        try:
            # 1. 主力资金净流入得分
            fund_flow_score = self._calculate_fund_flow_score(data)
            score += fund_flow_score
            if fund_flow_score > 0:
                details.append(f"主力资金净流入(+{fund_flow_score})")
            elif fund_flow_score < 0:
                details.append(f"主力资金净流出({fund_flow_score})")
                
            # 2. 股东户数得分
            holder_score = self._calculate_holder_score(stock_code)
            score += holder_score
            if holder_score > 0:
                details.append(f"股东户数下降(+{holder_score})")
            elif holder_score < 0:
                details.append(f"股东户数上升({holder_score})")
                
            # 3. 背离信号得分
            divergence_score = self._calculate_divergence_score(data)
            score += divergence_score
            if divergence_score > 0:
                details.append(f"三日背离(+{divergence_score})")
                
            # 4. 振幅风险扣分
            amplitude_penalty = self._calculate_amplitude_penalty(data)
            score += amplitude_penalty
            if amplitude_penalty < 0:
                details.append(f"振幅异常({amplitude_penalty})")
                
            # 5. 三重流出否决
            if self._check_triple_outflow(stock_code, data):
                score = 0
                details.append("三重流出否决，总分清零")
                
            # 6. 微盘股过滤
            if self._check_small_cap(stock_code):
                score = -float('inf')
                details.append("微盘股过滤，得分无效")
                
        except Exception as e:
            self.logger.error(f"计算得分失败 {stock_code}: {str(e)}")
            
        return max(score, -5), "; ".join(details)
        
    def _calculate_fund_flow_score(self, data: pd.DataFrame) -> float:
        """计算主力资金净流入得分"""
        try:
            # 获取最新3日数据
            recent_data = data.head(3)
            
            # 计算主力资金净流入（特大单+大单）
            main_inflow = recent_data['large_order_buy'].sum() + recent_data['huge_order_buy'].sum()
            main_outflow = recent_data['large_order_sell'].sum() + recent_data['huge_order_sell'].sum()
            net_inflow = main_inflow - main_outflow
            
            # 计算得分
            if net_inflow > 0:
                return self.params['fund_flow_weight']
            else:
                return 0
                
        except Exception as e:
            self.logger.error(f"计算主力资金得分失败: {str(e)}")
            return 0
            
    def _calculate_holder_score(self, stock_code: str) -> float:
        """计算股东户数得分"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = f"""
                SELECT holder_count, report_date 
                FROM stock_holders 
                WHERE stock_code = '{stock_code}'
                ORDER BY report_date DESC
                LIMIT 3
            """
            
            df = pd.read_sql(query, conn)
            conn.close()
            
            if len(df) >= 2:
                # 检查连续下降
                if df['holder_count'].iloc[0] < df['holder_count'].iloc[1]:
                    if len(df) >= 3 and df['holder_count'].iloc[1] < df['holder_count'].iloc[2]:
                        return self.params['holder_decrease_weight']  # 连续3期下降
                    return self.params['holder_decrease_weight'] * 0.8  # 连续2期下降
                    
            return 0
            
        except Exception as e:
            self.logger.error(f"计算股东户数得分失败 {stock_code}: {str(e)}")
            return 0
            
    def _calculate_divergence_score(self, data: pd.DataFrame) -> float:
        """计算背离信号得分"""
        try:
            # 获取最新3日数据
            recent_data = data.head(3)
            
            if len(recent_data) < 3:
                return 0
                
            # 检查三日背离：连续3日主力流入 + 价格下跌
            main_inflow = recent_data['large_order_buy'] + recent_data['huge_order_buy']
            price_change = recent_data['close'].pct_change()
            
            # 主力连续流入且价格连续下跌
            if (main_inflow > 0).all() and (price_change < 0).all():
                return self.params['divergence_weight']
                
            return 0
            
        except Exception as e:
            self.logger.error(f"计算背离信号得分失败: {str(e)}")
            return 0
            
    def _calculate_amplitude_penalty(self, data: pd.DataFrame) -> float:
        """计算振幅风险扣分"""
        try:
            # 计算个股振幅
            stock_amplitude = (data['high'].max() - data['low'].min()) / data['low'].min() * 100
            
            # 计算大盘振幅
            market_amplitude = self._calculate_market_amplitude()
            
            # 振幅异常判断
            if stock_amplitude > 30 and market_amplitude < 10:
                return self.params['amplitude_penalty']
                
            return 0
            
        except Exception as e:
            self.logger.error(f"计算振幅风险扣分失败: {str(e)}")
            return 0
            
    def _check_triple_outflow(self, stock_code: str, data: pd.DataFrame) -> bool:
        """检查三重流出（主力+融资+北向均流出）"""
        try:
            # 主力资金流出
            main_outflow = (data['large_order_buy'].iloc[0] + data['huge_order_buy'].iloc[0]) < \
                          (data['large_order_sell'].iloc[0] + data['huge_order_sell'].iloc[0])
            
            # 融资流出
            conn = sqlite3.connect(self.db_path)
            query = f"SELECT * FROM margin_trading WHERE stock_code = '{stock_code}' ORDER BY trade_date DESC LIMIT 1"
            margin_data = pd.read_sql(query, conn)
            conn.close()
            
            margin_outflow = not margin_data.empty and margin_data['financing_balance'].iloc[0] < margin_data['financing_balance'].shift(1).iloc[0]
            
            # 北向流出
            conn = sqlite3.connect(self.db_path)
            query = f"SELECT * FROM northbound_flow WHERE stock_code = '{stock_code}' ORDER BY trade_date DESC LIMIT 1"
            north_data = pd.read_sql(query, conn)
            conn.close()
            
            north_outflow = not north_data.empty and north_data['net_amount'].iloc[0] < 0
            
            return main_outflow and margin_outflow and north_outflow
            
        except Exception as e:
            self.logger.error(f"检查三重流出失败 {stock_code}: {str(e)}")
            return False
            
    def _check_small_cap(self, stock_code: str) -> bool:
        """检查是否为微盘股"""
        try:
            conn = sqlite3.connect(self.db_path)
            query = f"SELECT free_share FROM stock_basic WHERE stock_code = '{stock_code}'"
            df = pd.read_sql(query, conn)
            conn.close()
            
            if not df.empty and df['free_share'].iloc[0] > 0:
                # 计算流通市值（亿）
                price = self._get_current_price(stock_code)
                if price > 0:
                    market_cap = df['free_share'].iloc[0] * price / 10000
                    return market_cap < self.params['min_market_cap']
                    
        except Exception as e:
            self.logger.error(f"检查微盘股失败 {stock_code}: {str(e)}")
            
        return False
        
    def _calculate_target_price(self, data: pd.DataFrame) -> float:
        """计算目标价"""
        try:
            # 最近20日最高价 * 1.1
            recent_high = data['high'].head(20).max()
            return recent_high * 1.1
            
        except Exception as e:
            self.logger.error(f"计算目标价失败: {str(e)}")
            return data['close'].iloc[0] * 1.1
            
    def _calculate_stop_loss(self, data: pd.DataFrame) -> float:
        """计算止损价"""
        try:
            # 固定止损和结构止损取较紧值
            current_price = data['close'].iloc[0]
            main_stop_loss = current_price * (1 - self.params['stop_loss_ratio'])
            
            # 20日低点 * 0.98
            recent_low = data['low'].head(20).min()
            structure_stop_loss = recent_low * self.params['stop_loss_factor']
            
            return max(main_stop_loss, structure_stop_loss)
            
        except Exception as e:
            self.logger.error(f"计算止损价失败: {str(e)}")
            return current_price * 0.95
            
    def _calculate_position(self, signal_strength: str) -> float:
        """计算建议仓位"""
        try:
            # 获取当前市场模式
            market_mode = self.config.get('market_mode', 'attack')
            
            # 获取仓位配置
            position_config = self.params['position_config'].get(market_mode, {})
            position = position_config.get(signal_strength, 0)
            
            # 不超过单股最大仓位
            return min(position, self.params['max_single_stock_position'])
            
        except Exception as e:
            self.logger.error(f"计算建议仓位失败: {str(e)}")
            return 0
            
    def _get_market_data(self) -> Dict:
        """获取市场数据"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 获取上证指数数据
            query = """
                SELECT * FROM index_daily 
                WHERE index_code = '000001' 
                ORDER BY trade_date DESC 
                LIMIT 30
            """
            index_data = pd.read_sql(query, conn)
            
            # 获取涨跌家数
            query = """
                SELECT * FROM market_status 
                ORDER BY trade_date DESC 
                LIMIT 1
            """
            market_status = pd.read_sql(query, conn)
            
            conn.close()
            
            return {
                'index_data': index_data,
                'market_status': market_status
            }
            
        except Exception as e:
            self.logger.error(f"获取市场数据失败: {str(e)}")
            return {}
            
    def _calculate_market_temperature(self, market_data: Dict) -> float:
        """计算市场温度"""
        try:
            if not market_data or 'market_status' not in market_data:
                return 0.5
                
            market_status = market_data['market_status']
            
            if market_status.empty:
                return 0.5
                
            # 计算上涨家数占比
            up_ratio = market_status['up_stocks'].iloc[0] / (market_status['up_stocks'].iloc[0] + market_status['down_stocks'].iloc[0])
            
            return up_ratio
            
        except Exception as e:
            self.logger.error(f"计算市场温度失败: {str(e)}")
            return 0.5
            
    def _calculate_market_amplitude(self) -> float:
        """计算大盘振幅"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = """
                SELECT * FROM index_daily 
                WHERE index_code = '000001' 
                ORDER BY trade_date DESC 
                LIMIT 20
            """
            df = pd.read_sql(query, conn)
            conn.close()
            
            if df.empty:
                return 0
                
            return (df['high'].max() - df['low'].min()) / df['low'].min() * 100
            
        except Exception as e:
            self.logger.error(f"计算大盘振幅失败: {str(e)}")
            return 0
            
    def _get_current_price(self, stock_code: str) -> float:
        """获取当前价格"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = f"""
                SELECT close FROM stock_daily 
                WHERE stock_code = '{stock_code}' 
                ORDER BY trade_date DESC 
                LIMIT 1
            """
            df = pd.read_sql(query, conn)
            conn.close()
            
            if not df.empty:
                return df['close'].iloc[0]
                
        except Exception as e:
            self.logger.error(f"获取当前价格失败 {stock_code}: {str(e)}")
            
        return 0
            
    def _get_backtest_stock_list(self) -> List[str]:
        """获取回测股票列表"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = """
                SELECT DISTINCT stock_code FROM stock_daily 
                WHERE trade_date >= date('now', '-1 year')
                LIMIT 50
            """
            df = pd.read_sql(query, conn)
            conn.close()
            
            return df['stock_code'].tolist()
            
        except Exception as e:
            self.logger.error(f"获取回测股票列表失败: {str(e)}")
            return []
            
    def _simulate_trading(self, data: pd.DataFrame, params: Dict) -> List[Dict]:
        """模拟交易"""
        trades = []
        
        try:
            # 简单的均线交叉策略作为示例
            data['ma5'] = data['close'].rolling(5).mean()
            data['ma20'] = data['close'].rolling(20).mean()
            
            position = 0
            
            for i in range(20, len(data)):
                # 金叉买入
                if data['ma5'].iloc[i] > data['ma20'].iloc[i] and data['ma5'].iloc[i-1] <= data['ma20'].iloc[i-1] and position == 0:
                    trades.append({
                        'date': data['trade_date'].iloc[i],
                        'action': 'buy',
                        'price': data['close'].iloc[i],
                        'position': 0.1
                    })
                    position = 0.1
                    
                # 死叉卖出
                elif data['ma5'].iloc[i] < data['ma20'].iloc[i] and data['ma5'].iloc[i-1] >= data['ma20'].iloc[i-1] and position > 0:
                    trades.append({
                        'date': data['trade_date'].iloc[i],
                        'action': 'sell',
                        'price': data['close'].iloc[i],
                        'position': 0
                    })
                    position = 0
                    
        except Exception as e:
            self.logger.error(f"模拟交易失败: {str(e)}")
            
        return trades
            
    def _calculate_backtest_metrics(self, trades: List[Dict]) -> Dict:
        """计算回测指标"""
        if not trades:
            return {}
            
        try:
            # 计算收益率
            total_return = 0
            max_drawdown = 0
            winning_rate = 0
            
            if len(trades) >= 2:
                # 简单计算示例
                buy_price = trades[0]['price']
                sell_price = trades[-1]['price']
                total_return = (sell_price - buy_price) / buy_price
                
            return {
                'total_return': total_return,
                'max_drawdown': max_drawdown,
                'winning_rate': winning_rate,
                'total_trades': len(trades) // 2,
                'sharpe_ratio': 0
            }
            
        except Exception as e:
            self.logger.error(f"计算回测指标失败: {str(e)}")
            return {}
            
    def cleanup(self):
        """清理资源"""
        self.stock_cache.clear()
        self.market_cache.clear()
        self.logger.info(f"{self.strategy_name} 策略资源已清理")
        super().cleanup()