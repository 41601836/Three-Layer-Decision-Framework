# -*- coding: utf-8 -*-
"""
stockai_tray.pyw —— 陈明的专属量化助手 - 系统托盘应用
=====================================================

提供系统托盘图标和右键菜单，方便用户快速操作：
  - 查看今日简报
  - 管理持仓（图形化界面）
  - 快速分析股票
  - 退出应用
"""

import os
import sys
import json
import subprocess
from datetime import datetime

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("需要安装 pystray 和 pillow：pip install pystray pillow")
    sys.exit(1)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT_DIR, "config.json")
PORTFOLIO_FILE = os.path.join(ROOT_DIR, "portfolio.json")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")

sys.path.insert(0, ROOT_DIR)
from washout_analyst import analyze, analyze_and_save


def create_image():
    """创建托盘图标（绿色圆形带股票图标）"""
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), (0, 128, 0))
    dc = ImageDraw.Draw(image)
    dc.ellipse((10, 10, 54, 54), fill=(0, 200, 0), outline=(0, 100, 0), width=2)
    dc.line((20, 32, 32, 20), fill=(255, 255, 255), width=3)
    dc.line((32, 20, 44, 44), fill=(255, 255, 255), width=3)
    return image


def view_today_brief(icon, item):
    """查看今日简报（打开最新报告）"""
    try:
        if not os.path.exists(REPORTS_DIR):
            print("报告目录不存在")
            return
        
        files = [f for f in os.listdir(REPORTS_DIR) if f.endswith('.md')]
        if not files:
            print("暂无报告")
            return
        
        latest_file = max(files, key=lambda x: os.path.getmtime(os.path.join(REPORTS_DIR, x)))
        latest_path = os.path.join(REPORTS_DIR, latest_file)
        
        if os.name == 'nt':
            os.startfile(latest_path)
        else:
            subprocess.run(['xdg-open', latest_path], check=True)
        print(f"打开今日简报: {latest_file}")
        
    except Exception as e:
        print(f"打开简报失败: {e}")


def quick_analyze_stock(icon, item):
    """快速分析股票（弹出输入框）"""
    try:
        from tkinter import simpledialog
        
        root = tk.Tk()
        root.withdraw()
        
        ts_code = simpledialog.askstring("快速分析", "请输入股票代码（如 600519.SH）：")
        
        if ts_code:
            from analyze_stock import StockAnalyzer
            
            analyzer = StockAnalyzer(ts_code.strip())
            total_score, python_score, ai_score, grade, report = analyzer.analyze_v3_0(
                ts_code=ts_code.strip(),
                catalyst_score=0,
                industry_mode="normal"
            )
            
            report_content = f"""# {analyzer.get_stock_name()} ({ts_code}) 诊断报告

## 综合评级：{grade}
- 综合得分：{total_score} 分
- Python 基础分：{python_score} 分
- AI 加分：{ai_score} 分

---

## AI 分析结论
{report}
"""
            
            temp_path = os.path.join(REPORTS_DIR, f"quick_analysis_{ts_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
            os.makedirs(REPORTS_DIR, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(report_content)
            
            if os.name == 'nt':
                os.startfile(temp_path)
            else:
                subprocess.run(['xdg-open', temp_path], check=True)
            
            print(f"分析完成: {ts_code}")
        
    except Exception as e:
        print(f"分析失败: {e}")


def load_portfolio():
    """加载持仓数据"""
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_portfolio(portfolio):
    """保存持仓数据"""
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)


def manage_portfolio(icon, item):
    """管理持仓（图形化界面）"""
    class PortfolioManager:
        def __init__(self, root):
            self.root = root
            self.root.title("持仓管理 - 陈明的量化助手")
            self.root.geometry("600x450")
            
            self.portfolio = load_portfolio()
            
            self.create_widgets()
            self.refresh_list()
        
        def create_widgets(self):
            frame = ttk.Frame(self.root, padding="10")
            frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            
            columns = ("ts_code", "name", "cost", "shares")
            self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
            self.tree.heading("ts_code", text="股票代码")
            self.tree.heading("name", text="股票名称")
            self.tree.heading("cost", text="成本价")
            self.tree.heading("shares", text="持股数")
            self.tree.column("ts_code", width=100)
            self.tree.column("name", width=150)
            self.tree.column("cost", width=80)
            self.tree.column("shares", width=80)
            self.tree.grid(row=0, column=0, columnspan=4, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
            
            scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
            self.tree.configure(yscrollcommand=scrollbar.set)
            scrollbar.grid(row=0, column=4, sticky=(tk.N, tk.S))
            
            btn_frame = ttk.Frame(frame)
            btn_frame.grid(row=1, column=0, columnspan=5, pady=10)
            
            ttk.Button(btn_frame, text="添加持仓", command=self.add_holding).grid(row=0, column=0, padx=5)
            ttk.Button(btn_frame, text="删除选中", command=self.delete_holding).grid(row=0, column=1, padx=5)
            ttk.Button(btn_frame, text="编辑持仓", command=self.edit_holding).grid(row=0, column=2, padx=5)
            ttk.Button(btn_frame, text="保存", command=self.save).grid(row=0, column=3, padx=5)
            ttk.Button(btn_frame, text="关闭", command=self.root.destroy).grid(row=0, column=4, padx=5)
        
        def refresh_list(self):
            for item in self.tree.get_children():
                self.tree.delete(item)
            for holding in self.portfolio:
                self.tree.insert("", tk.END, values=(
                    holding.get("ts_code", ""),
                    holding.get("name", ""),
                    holding.get("cost", 0),
                    holding.get("shares", 0)
                ))
        
        def add_holding(self):
            dialog = tk.Toplevel(self.root)
            dialog.title("添加持仓")
            dialog.geometry("300x250")
            dialog.transient(self.root)
            dialog.grab_set()
            
            ttk.Label(dialog, text="股票代码:").grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
            ts_code_var = tk.StringVar()
            ttk.Entry(dialog, textvariable=ts_code_var, width=20).grid(row=0, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="股票名称:").grid(row=1, column=0, padx=10, pady=10, sticky=tk.W)
            name_var = tk.StringVar()
            ttk.Entry(dialog, textvariable=name_var, width=20).grid(row=1, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="成本价:").grid(row=2, column=0, padx=10, pady=10, sticky=tk.W)
            cost_var = tk.DoubleVar(value=0.0)
            ttk.Entry(dialog, textvariable=cost_var, width=20).grid(row=2, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="持股数:").grid(row=3, column=0, padx=10, pady=10, sticky=tk.W)
            shares_var = tk.IntVar(value=100)
            ttk.Entry(dialog, textvariable=shares_var, width=20).grid(row=3, column=1, padx=10, pady=10)
            
            def confirm():
                try:
                    holding = {
                        "ts_code": ts_code_var.get().strip(),
                        "name": name_var.get().strip(),
                        "cost": float(cost_var.get()),
                        "shares": int(shares_var.get())
                    }
                    if not holding["ts_code"]:
                        messagebox.showerror("错误", "请输入股票代码")
                        return
                    self.portfolio.append(holding)
                    self.refresh_list()
                    dialog.destroy()
                except Exception as e:
                    messagebox.showerror("错误", f"输入无效: {e}")
            
            ttk.Button(dialog, text="确定", command=confirm).grid(row=4, column=0, columnspan=2, pady=10)
        
        def delete_holding(self):
            selected = self.tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请先选择要删除的持仓")
                return
            if messagebox.askyesno("确认", "确定要删除选中的持仓吗？"):
                for item in selected:
                    idx = self.tree.index(item)
                    if 0 <= idx < len(self.portfolio):
                        del self.portfolio[idx]
                self.refresh_list()
        
        def edit_holding(self):
            selected = self.tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请先选择要编辑的持仓")
                return
            
            idx = self.tree.index(selected[0])
            if idx < 0 or idx >= len(self.portfolio):
                return
            
            holding = self.portfolio[idx]
            dialog = tk.Toplevel(self.root)
            dialog.title("编辑持仓")
            dialog.geometry("300x250")
            dialog.transient(self.root)
            dialog.grab_set()
            
            ttk.Label(dialog, text="股票代码:").grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
            ts_code_var = tk.StringVar(value=holding.get("ts_code", ""))
            ttk.Entry(dialog, textvariable=ts_code_var, width=20).grid(row=0, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="股票名称:").grid(row=1, column=0, padx=10, pady=10, sticky=tk.W)
            name_var = tk.StringVar(value=holding.get("name", ""))
            ttk.Entry(dialog, textvariable=name_var, width=20).grid(row=1, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="成本价:").grid(row=2, column=0, padx=10, pady=10, sticky=tk.W)
            cost_var = tk.DoubleVar(value=holding.get("cost", 0.0))
            ttk.Entry(dialog, textvariable=cost_var, width=20).grid(row=2, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="持股数:").grid(row=3, column=0, padx=10, pady=10, sticky=tk.W)
            shares_var = tk.IntVar(value=holding.get("shares", 0))
            ttk.Entry(dialog, textvariable=shares_var, width=20).grid(row=3, column=1, padx=10, pady=10)
            
            def confirm():
                try:
                    self.portfolio[idx] = {
                        "ts_code": ts_code_var.get().strip(),
                        "name": name_var.get().strip(),
                        "cost": float(cost_var.get()),
                        "shares": int(shares_var.get())
                    }
                    self.refresh_list()
                    dialog.destroy()
                except Exception as e:
                    messagebox.showerror("错误", f"输入无效: {e}")
            
            ttk.Button(dialog, text="确定", command=confirm).grid(row=4, column=0, columnspan=2, pady=10)
        
        def save(self):
            save_portfolio(self.portfolio)
            messagebox.showinfo("成功", f"持仓已保存到 {PORTFOLIO_FILE}")
    
    root = tk.Tk()
    app = PortfolioManager(root)
    root.mainloop()


def on_washout_input(icon, item):
    """洗盘分析（输入代码）"""
    try:
        from tkinter import simpledialog
        
        root = tk.Tk()
        root.withdraw()
        
        ts_code = simpledialog.askstring("洗盘分析", "请输入股票代码（如 600519.SH）：")
        
        if ts_code:
            ts_code = ts_code.strip()
            print(f"开始洗盘分析: {ts_code}")
            
            filepath = analyze_and_save(ts_code)
            
            if os.name == 'nt':
                os.startfile(filepath)
            else:
                subprocess.run(['xdg-open', filepath], check=True)
            
            print(f"洗盘分析完成，报告已打开")
        
    except Exception as e:
        print(f"洗盘分析失败: {e}")


def on_washout_portfolio(icon, item):
    """洗盘分析（选择持仓）"""
    try:
        portfolio = load_portfolio()
        
        if not portfolio:
            messagebox.showwarning("提示", "暂无持仓，请先添加")
            return
        
        root = tk.Tk()
        root.withdraw()
        
        class StockSelector:
            def __init__(self, master, stocks):
                self.master = master
                self.master.title("选择股票")
                self.master.geometry("400x300")
                
                self.stocks = stocks
                self.selected_code = None
                
                frame = ttk.Frame(master, padding="10")
                frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
                
                ttk.Label(frame, text="请选择要分析的股票：").grid(row=0, column=0, sticky=tk.W, pady=5)
                
                self.listbox = tk.Listbox(frame, width=50, height=10)
                self.listbox.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
                
                for stock in stocks:
                    name = stock.get("name", stock.get("ts_code", ""))
                    self.listbox.insert(tk.END, f"{stock['ts_code']} - {name}")
                
                btn_frame = ttk.Frame(frame)
                btn_frame.grid(row=2, column=0, pady=10)
                
                ttk.Button(btn_frame, text="确定", command=self.select).grid(row=0, column=0, padx=5)
                ttk.Button(btn_frame, text="取消", command=self.cancel).grid(row=0, column=1, padx=5)
            
            def select(self):
                selection = self.listbox.curselection()
                if selection:
                    idx = selection[0]
                    self.selected_code = self.stocks[idx]['ts_code']
                self.master.destroy()
            
            def cancel(self):
                self.master.destroy()
        
        selector = StockSelector(root, portfolio)
        root.mainloop()
        
        if selector.selected_code:
            print(f"开始洗盘分析: {selector.selected_code}")
            
            filepath = analyze_and_save(selector.selected_code)
            
            if os.name == 'nt':
                os.startfile(filepath)
            else:
                subprocess.run(['xdg-open', filepath], check=True)
            
            print(f"洗盘分析完成，报告已打开")
        
    except Exception as e:
        print(f"洗盘分析失败: {e}")


def quit_app(icon, item):
    """退出应用"""
    icon.stop()
    sys.exit(0)


def main():
    """启动托盘应用"""
    def menu_factory():
        return pystray.Menu(
            item('📋 查看今日简报', view_today_brief),
            item('📊 管理持仓', manage_portfolio),
            item('🔍 快速分析股票', quick_analyze_stock),
            pystray.Menu.SEPARATOR,
            item('🧹 洗盘分析（输入代码）', on_washout_input),
            item('🧹 洗盘分析（选择持仓）', on_washout_portfolio),
            pystray.Menu.SEPARATOR,
            item('❌ 退出', quit_app),
        )
    
    icon = pystray.Icon(
        "StockAI",
        icon=create_image(),
        menu=menu_factory(),
        title="陈明的量化助手"
    )
    
    print(f"陈明的专属量化助手启动成功 ({datetime.now()})")
    print("右键点击托盘图标访问菜单")
    
    icon.run()


if __name__ == "__main__":
    main()