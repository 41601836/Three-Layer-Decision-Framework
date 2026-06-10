#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据更新调度器 - 按照时间计划自动执行数据拉取任务
"""
import os
import sys
import time
import json
import logging
import subprocess
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# 添加项目根目录到Python路径
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(ROOT_DIR, 'logs', 'data_scheduler.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

class DataUpdateScheduler:
    def __init__(self):
        self.config = self._load_config()
        self.running_tasks = []
        self.lock = threading.Lock()
        
    def _load_config(self) -> Dict:
        """加载调度配置"""
        return {
            # 并行线程配置
            'max_workers': {
                'pre_market': 8,
                'mid_day': 16,
                'after_market': 16,
                'night_maintenance': 8
            },
            # 时间计划
            'schedule': [
                {
                    'name': '盘前数据更新',
                    'time': '06:00',
                    'tasks': ['daily', 'moneyflow', 'holder'],
                    'max_workers': 8,
                    'timeout': 90,
                    'enabled': True
                },
                {
                    'name': '午盘数据更新',
                    'time': '11:30',
                    'tasks': ['daily_incremental', 'moneyflow'],
                    'max_workers': 16,
                    'timeout': 20,
                    'enabled': True
                },
                {
                    'name': '尾盘数据更新',
                    'time': '14:00',
                    'tasks': ['daily_realtime', 'monitor'],
                    'max_workers': 8,
                    'timeout': 30,
                    'enabled': True
                },
                {
                    'name': '盘后数据更新',
                    'time': '15:30',
                    'tasks': ['full_update', 'hsgt', 'margin', 'block'],
                    'max_workers': 16,
                    'timeout': 60,
                    'enabled': True
                },
                {
                    'name': '夜间维护',
                    'time': '20:00',
                    'tasks': ['history_complement', 'db_optimize'],
                    'max_workers': 8,
                    'timeout': 120,
                    'enabled': True
                }
            ],
            # 任务定义
            'task_definitions': {
                'daily': {
                    'script': 'fetch_daily.py',
                    'args': ['--skip-holder', '--skip-daily-basic', '--skip-margin', '--skip-block'],
                    'description': '日线数据拉取'
                },
                'daily_incremental': {
                    'script': 'fetch_daily.py',
                    'args': ['--incremental', '--skip-holder', '--skip-daily-basic'],
                    'description': '日线数据增量更新'
                },
                'daily_realtime': {
                    'script': 'fetch_daily.py',
                    'args': ['--realtime', '--skip-history', '--skip-holder'],
                    'description': '实时数据同步'
                },
                'moneyflow': {
                    'script': 'fetch_daily.py',
                    'args': ['--only-moneyflow'],
                    'description': '资金流向数据'
                },
                'holder': {
                    'script': 'fetch_daily.py',
                    'args': ['--only-holder'],
                    'description': '股东户数数据'
                },
                'hsgt': {
                    'script': 'fetch_hsgt.py',
                    'args': [],
                    'description': '北向资金数据'
                },
                'margin': {
                    'script': 'fetch_daily.py',
                    'args': ['--only-margin'],
                    'description': '融资融券数据'
                },
                'block': {
                    'script': 'fetch_daily.py',
                    'args': ['--only-block'],
                    'description': '大宗交易数据'
                },
                'full_update': {
                    'script': 'fetch_daily.py',
                    'args': [],
                    'description': '全量数据更新'
                },
                'history_complement': {
                    'script': 'fetch_daily.py',
                    'args': ['--history-complement'],
                    'description': '历史数据补全'
                },
                'db_optimize': {
                    'script': 'db_maintenance.py',
                    'args': ['--optimize', '--backup'],
                    'description': '数据库优化与备份'
                },
                'monitor': {
                    'script': 'market_monitor.py',
                    'args': [],
                    'description': '市场异动监测'
                }
            }
        }
    
    def _get_task_command(self, task_name: str, max_workers: int = 8) -> List[str]:
        """获取任务执行命令"""
        task_def = self.config['task_definitions'].get(task_name)
        if not task_def:
            raise ValueError(f'未知任务: {task_name}')
        
        script_path = os.path.join(ROOT_DIR, 'scripts', task_def['script'])
        args = task_def['args'] + ['--workers', str(max_workers)]
        
        return [sys.executable, script_path] + args
    
    def _run_task(self, task_name: str, max_workers: int = 8, timeout: int = 60) -> Dict:
        """执行单个任务"""
        start_time = datetime.now()
        result = {
            'task_name': task_name,
            'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': None,
            'duration': None,
            'success': False,
            'error_message': None
        }
        
        try:
            log.info(f'开始执行任务: {task_name}')
            command = self._get_task_command(task_name, max_workers)
            
            # 执行任务
            proc = subprocess.run(
                command,
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=timeout * 60  # 转换为秒
            )
            
            end_time = datetime.now()
            result['end_time'] = end_time.strftime('%Y-%m-%d %H:%M:%S')
            result['duration'] = (end_time - start_time).total_seconds() / 60  # 分钟
            
            if proc.returncode == 0:
                result['success'] = True
                log.info(f'任务执行成功: {task_name} (耗时: {result["duration"]:.1f}分钟)')
            else:
                result['error_message'] = proc.stderr[:500]  # 只保存前500个字符
                log.error(f'任务执行失败: {task_name} (错误: {result["error_message"]})')
                
        except subprocess.TimeoutExpired:
            end_time = datetime.now()
            result['end_time'] = end_time.strftime('%Y-%m-%d %H:%M:%S')
            result['duration'] = (end_time - start_time).total_seconds() / 60
            result['error_message'] = f'任务超时 (超过 {timeout} 分钟)'
            log.error(f'任务超时: {task_name}')
            
        except Exception as e:
            end_time = datetime.now()
            result['end_time'] = end_time.strftime('%Y-%m-%d %H:%M:%S')
            result['duration'] = (end_time - start_time).total_seconds() / 60
            result['error_message'] = str(e)
            log.error(f'任务执行异常: {task_name} - {str(e)}')
            
        return result
    
    def _run_schedule_item(self, schedule_item: Dict) -> List[Dict]:
        """执行一个调度项中的所有任务"""
        log.info(f'开始执行调度: {schedule_item["name"]}')
        
        results = []
        tasks = schedule_item['tasks']
        max_workers = schedule_item['max_workers']
        timeout = schedule_item['timeout']
        
        # 并行执行任务
        threads = []
        task_results = []
        
        def task_wrapper(task_name):
            result = self._run_task(task_name, max_workers, timeout)
            task_results.append(result)
        
        for task_name in tasks:
            thread = threading.Thread(target=task_wrapper, args=(task_name,))
            threads.append(thread)
            thread.start()
        
        # 等待所有任务完成
        for thread in threads:
            thread.join()
        
        results.extend(task_results)
        
        # 记录调度结果
        success_count = sum(1 for r in results if r['success'])
        total_count = len(results)
        
        log.info(f'调度完成: {schedule_item["name"]} - {success_count}/{total_count} 任务成功')
        
        return results
    
    def _get_next_run_time(self, target_time_str: str) -> datetime:
        """计算下一次运行时间"""
        now = datetime.now()
        target_time = datetime.strptime(target_time_str, '%H:%M').time()
        next_run = datetime.combine(now.date(), target_time)
        
        if now.time() > target_time:
            # 今天的时间已过，明天运行
            next_run += timedelta(days=1)
            
        return next_run
    
    def _wait_until(self, target_time: datetime) -> bool:
        """等待直到目标时间"""
        now = datetime.now()
        if now >= target_time:
            return False
            
        wait_seconds = (target_time - now).total_seconds()
        log.info(f'等待 {wait_seconds:.0f} 秒直到 {target_time.strftime("%Y-%m-%d %H:%M:%S")}')
        
        # 分段等待，每60秒检查一次
        while wait_seconds > 0:
            sleep_time = min(60, wait_seconds)
            time.sleep(sleep_time)
            wait_seconds -= sleep_time
            
            # 检查是否需要退出
            if not self.running:
                return False
                
        return True
    
    def _is_trading_day(self) -> bool:
        """判断是否为交易日"""
        # 这里可以实现更复杂的交易日判断逻辑
        # 目前简化为周一至周五
        weekday = datetime.now().weekday()
        return weekday >= 0 and weekday <= 4
    
    def run_schedule(self):
        """运行调度器"""
        self.running = True
        log.info('数据更新调度器启动')
        
        while self.running:
            try:
                now = datetime.now()
                
                # 检查是否为交易日
                if not self._is_trading_day():
                    log.info('非交易日，跳过数据更新')
                    # 等待到下一个交易日
                    next_trading_day = now + timedelta(days=(7 - now.weekday()) % 7)
                    next_trading_day = next_trading_day.replace(hour=6, minute=0, second=0, microsecond=0)
                    self._wait_until(next_trading_day)
                    continue
                
                # 检查是否有需要立即执行的任务
                for schedule_item in self.config['schedule']:
                    if not schedule_item['enabled']:
                        continue
                        
                    target_time = datetime.strptime(schedule_item['time'], '%H:%M').time()
                    current_time = now.time()
                    
                    # 检查是否在任务执行时间窗口内（前后5分钟）
                    time_diff = abs(
                        (current_time.hour * 60 + current_time.minute) - 
                        (target_time.hour * 60 + target_time.minute)
                    )
                    
                    if time_diff <= 5:
                        # 执行任务
                        self._run_schedule_item(schedule_item)
                        # 标记为已执行，避免重复执行
                        schedule_item['last_run'] = now.strftime('%Y-%m-%d')
                
                # 计算下一个调度时间
                next_run_times = []
                for schedule_item in self.config['schedule']:
                    if schedule_item['enabled']:
                        next_run = self._get_next_run_time(schedule_item['time'])
                        next_run_times.append(next_run)
                
                if next_run_times:
                    next_run = min(next_run_times)
                    self._wait_until(next_run)
                else:
                    # 没有启用的调度任务，等待60秒
                    time.sleep(60)
                    
            except KeyboardInterrupt:
                log.info('收到中断信号，正在退出...')
                self.running = False
            except Exception as e:
                log.error(f'调度器异常: {str(e)}', exc_info=True)
                # 等待60秒后继续
                time.sleep(60)
        
        log.info('数据更新调度器已停止')
    
    def stop(self):
        """停止调度器"""
        self.running = False
    
    def run_immediate(self, task_names: List[str], max_workers: int = 8) -> List[Dict]:
        """立即执行指定任务"""
        results = []
        
        for task_name in task_names:
            result = self._run_task(task_name, max_workers)
            results.append(result)
        
        return results

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='数据更新调度器')
    parser.add_argument('--run-now', nargs='+', help='立即执行指定任务')
    parser.add_argument('--task', help='立即执行单个任务')
    parser.add_argument('--schedule', action='store_true', help='运行调度器')
    
    args = parser.parse_args()
    
    scheduler = DataUpdateScheduler()
    
    if args.run_now:
        log.info(f'立即执行任务: {args.run_now}')
        results = scheduler.run_immediate(args.run_now)
        
        print('\n任务执行结果:')
        print('-' * 60)
        for result in results:
            status = '✅ 成功' if result['success'] else '❌ 失败'
            duration = result['duration'] if result['duration'] else 'N/A'
            print(f'{result["task_name"]:20} {status} 耗时: {duration:.1f}分钟')
            if not result['success'] and result['error_message']:
                print(f'     错误: {result["error_message"]}')
                
    elif args.task:
        log.info(f'立即执行任务: {args.task}')
        result = scheduler.run_immediate([args.task])[0]
        
        print('\n任务执行结果:')
        print('-' * 60)
        status = '✅ 成功' if result['success'] else '❌ 失败'
        duration = result['duration'] if result['duration'] else 'N/A'
        print(f'{result["task_name"]:20} {status} 耗时: {duration:.1f}分钟')
        if not result['success'] and result['error_message']:
            print(f'     错误: {result["error_message"]}')
            
    elif args.schedule:
        log.info('启动数据更新调度器')
        try:
            scheduler.run_schedule()
        except KeyboardInterrupt:
            log.info('用户中断，调度器停止')
            
    else:
        parser.print_help()

if __name__ == '__main__':
    import argparse
    main()