# 推送历史统计表格设计方案

## 一、需求分析

### 1. 核心需求
- 在UI界面增设推送历史统计表格
- 记录推送时间、股票代码、名称、推送理由
- 支持查看、筛选、搜索历史推送记录

### 2. 功能要求
- 实时记录所有飞书推送内容
- 支持按时间、股票代码、名称筛选
- 提供分页或滚动加载功能
- 显示推送状态（成功/失败）
- 支持查看推送详情和原始内容

## 二、数据结构设计

### 1. 数据库表结构
```sql
CREATE TABLE IF NOT EXISTS push_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_time TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    push_reason TEXT NOT NULL,
    push_status TEXT NOT NULL DEFAULT 'success',
    push_content TEXT,
    session_name TEXT,
    total_score REAL,
    grade TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_push_time ON push_history(push_time);
CREATE INDEX IF NOT EXISTS idx_ts_code ON push_history(ts_code);
CREATE INDEX IF NOT EXISTS idx_stock_name ON push_history(stock_name);
```

### 2. 数据字段说明
| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键ID |
| push_time | TEXT | 推送时间（YYYY-MM-DD HH:MM:SS） |
| ts_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| push_reason | TEXT | 推送理由（量化指标、AI分析结论等） |
| push_status | TEXT | 推送状态（success/failed/pending） |
| push_content | TEXT | 推送内容详情（JSON格式） |
| session_name | TEXT | 会话名称（如"盘前分析"、"午盘推送"等） |
| total_score | REAL | 综合评分 |
| grade | TEXT | 评级（S/A/B/C） |
| created_at | TEXT | 记录创建时间 |

## 三、UI界面设计

### 1. 页面布局

```
┌─────────────────────────────────────────────────────────────────┐
│  推送历史统计                                                  │
├─────────┬─────────┬─────────────────────────────────────────────┤
│ 筛选区  │ 搜索框  │ 时间筛选器 ▼  状态筛选器 ▼  评级筛选器 ▼    │
├─────────┴─────────┴─────────────────────────────────────────────┤
│ 表格区                                                         │
│ ┌──────┬──────────┬──────────┬──────────┬──────────┬──────────┐ │
│ │ 序号 │ 推送时间 │ 股票代码 │ 股票名称 │ 推送理由 │ 操作     │ │
│ ├──────┼──────────┼──────────┼──────────┼──────────┼──────────┤ │
│ │ 1    │ 08:40    │ 600519.SH│ 贵州茅台 │ 量化评分88分 │ 查看详情 │ │
│ │ 2    │ 11:25    │ 002415.SZ│ 海康威视 │ AI看好   │ 查看详情 │ │
│ └──────┴──────────┴──────────┴──────────┴──────────┴──────────┘ │
├─────────────────────────────────────────────────────────────────┤
│ 分页区                                                         │
│ 显示 1-10 条，共 128 条  │ 上一页  1 / 13  下一页              │
└─────────────────────────────────────────────────────────────────┘
```

### 2. 表格列设计

| 列名 | 宽度 | 内容说明 |
|------|------|----------|
| 推送时间 | 150px | 显示YYYY-MM-DD HH:MM格式的时间 |
| 股票代码 | 120px | 显示股票代码，如600519.SH |
| 股票名称 | 120px | 显示股票全称 |
| 推送理由 | 300px | 显示推送的核心原因，支持换行显示 |
| 评级 | 80px | 显示S/A/B/C评级，用不同颜色标识 |
| 评分 | 80px | 显示综合评分 |
| 状态 | 80px | 显示推送状态，用图标标识 |
| 操作 | 100px | 查看详情按钮 |

### 3. 交互设计

#### （1）筛选功能
- **时间筛选**：支持按今日、本周、本月、自定义时间范围筛选
- **状态筛选**：支持按成功、失败、全部筛选
- **评级筛选**：支持按S/A/B/C评级筛选
- **搜索功能**：支持按股票代码、名称搜索

#### （2）详情查看
- 点击"查看详情"按钮弹出模态框
- 显示完整的推送内容、AI分析报告、量化指标等
- 支持复制推送内容

#### （3）数据更新
- 实时更新推送记录
- 支持手动刷新
- 自动滚动到最新记录

## 四、前端实现方案

### 1. 组件结构

```javascript
// PushHistoryTable.vue
<template>
  <div class="push-history-container">
    <div class="push-history-header">
      <h3>推送历史统计</h3>
      <div class="filter-bar">
        <input type="text" v-model="searchKeyword" placeholder="搜索股票代码或名称" />
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
        </select>
        <select v-model="gradeFilter">
          <option value="">全部评级</option>
          <option value="S">S级</option>
          <option value="A">A级</option>
          <option value="B">B级</option>
          <option value="C">C级</option>
        </select>
        <button @click="refreshData">刷新</button>
      </div>
    </div>
    
    <div class="table-container">
      <table class="push-history-table">
        <thead>
          <tr>
            <th>推送时间</th>
            <th>股票代码</th>
            <th>股票名称</th>
            <th>推送理由</th>
            <th>评级</th>
            <th>评分</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in pushHistory" :key="item.id">
            <td>{{ formatTime(item.push_time) }}</td>
            <td>{{ item.ts_code }}</td>
            <td>{{ item.stock_name }}</td>
            <td class="reason-cell">{{ item.push_reason }}</td>
            <td :class="`grade-${item.grade.toLowerCase()}`">{{ item.grade }}</td>
            <td>{{ item.total_score }}</td>
            <td :class="`status-${item.push_status}`">
              <span v-if="item.push_status === 'success'" class="status-icon">✅</span>
              <span v-else class="status-icon">❌</span>
            </td>
            <td><button @click="viewDetail(item)">查看详情</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    
    <div class="pagination">
      <span>显示 {{ startIndex }}-{{ endIndex }} 条，共 {{ totalCount }} 条</span>
      <button @click="prevPage" :disabled="currentPage === 1">上一页</button>
      <span>{{ currentPage }} / {{ totalPages }}</span>
      <button @click="nextPage" :disabled="currentPage === totalPages">下一页</button>
    </div>
    
    <PushDetailModal 
      :show="showDetail" 
      :data="selectedItem"
      @close="showDetail = false"
    />
  </div>
</template>
```

### 2. 样式设计

```css
.push-history-container {
  padding: 20px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
}

.push-history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.filter-bar {
  display: flex;
  gap: 10px;
  align-items: center;
}

.filter-bar input, .filter-bar select {
  padding: 8px 12px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.table-container {
  overflow-x: auto;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border: 1px solid var(--glass-border);
}

.push-history-table {
  width: 100%;
  border-collapse: collapse;
}

.push-history-table th {
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  color: var(--text-muted);
  border-bottom: 1px solid var(--glass-border);
  background: var(--bg-elevated);
}

.push-history-table td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--glass-border);
}

.reason-cell {
  max-width: 300px;
  white-space: pre-wrap;
  line-height: 1.4;
}

.grade-s { color: var(--red-500); font-weight: 600; }
.grade-a { color: var(--orange-500); font-weight: 600; }
.grade-b { color: var(--yellow-500); font-weight: 600; }
.grade-c { color: var(--green-500); font-weight: 600; }

.status-success { color: var(--green-500); }
.status-failed { color: var(--red-500); }

.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 20px;
  padding: 12px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border: 1px solid var(--glass-border);
}
```

## 五、后端实现方案

### 1. API接口设计

#### （1）获取推送历史列表
```
GET /api/push-history
参数：
- page: 页码，默认1
- page_size: 每页数量，默认20
- search: 搜索关键词
- status: 状态筛选
- grade: 评级筛选
- start_time: 开始时间
- end_time: 结束时间

返回：
{
  "code": 0,
  "message": "success",
  "data": {
    "list": [
      {
        "id": 1,
        "push_time": "2024-01-01 08:40:00",
        "ts_code": "600519.SH",
        "stock_name": "贵州茅台",
        "push_reason": "量化评分88分，AI看好",
        "push_status": "success",
        "total_score": 88,
        "grade": "S",
        "session_name": "盘前分析"
      }
    ],
    "total": 128,
    "page": 1,
    "page_size": 20
  }
}
```

#### （2）获取推送详情
```
GET /api/push-history/:id

返回：
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "push_time": "2024-01-01 08:40:00",
    "ts_code": "600519.SH",
    "stock_name": "贵州茅台",
    "push_reason": "量化评分88分，AI看好",
    "push_status": "success",
    "push_content": "完整的推送内容...",
    "total_score": 88,
    "grade": "S",
    "session_name": "盘前分析",
    "created_at": "2024-01-01 08:40:00"
  }
}
```

#### （3）记录推送历史
```
POST /api/push-history

请求体：
{
  "ts_code": "600519.SH",
  "stock_name": "贵州茅台",
  "push_reason": "量化评分88分，AI看好",
  "push_content": "完整的推送内容...",
  "session_name": "盘前分析",
  "total_score": 88,
  "grade": "S",
  "push_status": "success"
}
```

### 2. 数据库操作

```python
# push_history.py
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

def init_push_history_db():
    """初始化推送历史数据库"""
    conn = sqlite3.connect('db/stock_daily.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            push_time TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            push_reason TEXT NOT NULL,
            push_status TEXT NOT NULL DEFAULT 'success',
            push_content TEXT,
            session_name TEXT,
            total_score REAL,
            grade TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_push_time ON push_history(push_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ts_code ON push_history(ts_code)")
    
    conn.commit()
    conn.close()

def add_push_record(
    ts_code: str,
    stock_name: str,
    push_reason: str,
    push_content: str = None,
    session_name: str = None,
    total_score: float = None,
    grade: str = None,
    push_status: str = 'success'
) -> int:
    """添加推送记录"""
    conn = sqlite3.connect('db/stock_daily.db')
    cursor = conn.cursor()
    
    push_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("""
        INSERT INTO push_history 
        (push_time, ts_code, stock_name, push_reason, push_content, 
         session_name, total_score, grade, push_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        push_time, ts_code, stock_name, push_reason, push_content,
        session_name, total_score, grade, push_status
    ))
    
    record_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return record_id

def get_push_history(
    page: int = 1,
    page_size: int = 20,
    search: str = None,
    status: str = None,
    grade: str = None,
    start_time: str = None,
    end_time: str = None
) -> Dict:
    """获取推送历史列表"""
    conn = sqlite3.connect('db/stock_daily.db')
    cursor = conn.cursor()
    
    query = "SELECT * FROM push_history WHERE 1=1"
    params = []
    
    if search:
        query += " AND (ts_code LIKE ? OR stock_name LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    
    if status:
        query += " AND push_status = ?"
        params.append(status)
    
    if grade:
        query += " AND grade = ?"
        params.append(grade)
    
    if start_time:
        query += " AND push_time >= ?"
        params.append(start_time)
    
    if end_time:
        query += " AND push_time <= ?"
        params.append(end_time)
    
    # 分页
    offset = (page - 1) * page_size
    query += " ORDER BY push_time DESC LIMIT ? OFFSET ?"
    params.extend([page_size, offset])
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    # 获取总数
    count_query = "SELECT COUNT(*) FROM push_history WHERE 1=1"
    count_params = params[:-2]  # 移除分页参数
    cursor.execute(count_query, count_params)
    total_count = cursor.fetchone()[0]
    
    conn.close()
    
    # 转换为字典格式
    columns = [desc[0] for desc in cursor.description]
    result_list = [dict(zip(columns, row)) for row in rows]
    
    return {
        'list': result_list,
        'total': total_count,
        'page': page,
        'page_size': page_size
    }
```

## 六、集成方案

### 1. 飞书推送集成

```python
# 在feishu_bot.py中添加记录推送历史的逻辑
from push_history import add_push_record

def send_stock_report_v2(
    ts_code: str,
    name: str,
    total_score: float,
    python_score: float,
    ai_score: float,
    report_md: str,
    # ... 其他参数
) -> bool:
    """发送股票报告"""
    # ... 原有推送逻辑 ...
    
    success = _post(payload)
    
    # 记录推送历史
    if success:
        push_reason = f"量化评分{total_score}分，评级{grade}"
        add_push_record(
            ts_code=ts_code,
            stock_name=name,
            push_reason=push_reason,
            push_content=json.dumps(payload),
            session_name=session_name,
            total_score=total_score,
            grade=grade,
            push_status='success' if success else 'failed'
        )
    
    return success
```

### 2. 前端页面集成

```html
<!-- 在index.html中添加推送历史页面 -->
<div id="push-history-view" class="view-content" style="display: none;">
  <div class="card">
    <div class="card-header">
      <div class="card-title">
        <div class="card-title-icon icon-blue">📊</div>
        推送历史统计
      </div>
    </div>
    <div class="card-body">
      <div id="push-history-container"></div>
    </div>
  </div>
</div>
```

```javascript
// 在index.html的script标签中添加
function loadPushHistory() {
  fetch('/api/push-history?page=1&page_size=20')
    .then(response => response.json())
    .then(data => {
      renderPushHistory(data.data);
    })
    .catch(error => {
      console.error('加载推送历史失败:', error);
    });
}

function renderPushHistory(data) {
  const container = document.getElementById('push-history-container');
  // 渲染表格逻辑...
}
```

## 七、测试计划

### 1. 功能测试
- [ ] 推送记录自动保存功能
- [ ] 推送历史列表显示
- [ ] 筛选和搜索功能
- [ ] 详情查看功能
- [ ] 分页功能

### 2. 性能测试
- [ ] 1000条数据加载时间
- [ ] 筛选搜索响应时间
- [ ] 页面渲染性能

### 3. 兼容性测试
- [ ] 主流浏览器兼容性
- [ ] 不同屏幕尺寸适配
- [ ] 移动端适配

## 八、上线计划

### 1. 第一阶段（1-2天）
- 完成数据库表结构设计
- 实现后端API接口
- 完成飞书推送集成

### 2. 第二阶段（2-3天）
- 实现前端表格组件
- 完成筛选搜索功能
- 实现详情查看功能

### 3. 第三阶段（1天）
- 集成测试
- Bug修复
- 性能优化

### 4. 第四阶段（1天）
- 用户验收测试
- 上线部署
- 监控维护

---
**版本**：v1.0  
**制定日期**：2026-06-10  
**更新日期**：2026-06-10