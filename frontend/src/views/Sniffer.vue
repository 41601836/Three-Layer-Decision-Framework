<template>
  <div class="sniffer-page">
    <div class="header">
      <div class="title-section">
        <h2>🔍 主力嗅探 (v3 Final)</h2>
        <span class="subtitle">横盘吸筹量化识别</span>
      </div>
      <div class="actions">
        <button @click="runSniffing" class="action-btn primary" :disabled="loading">
          {{ loading ? '⏳ 嗅探中...' : '🎯 开始深度嗅探' }}
        </button>
      </div>
    </div>

    <!-- 顶部状态面板 -->
    <div v-if="result" class="dashboard-panel">
      <div class="stat-card">
        <span class="label">数据基准日</span>
        <span class="value">{{ result.data_date }}</span>
      </div>
      <div class="stat-card">
        <span class="label">当前宏观评分</span>
        <span class="value">{{ result.macro_score }} 分</span>
      </div>
      <div class="stat-card highlight">
        <span class="label">建议仓位上限</span>
        <span class="value">{{ result.position_limit }}</span>
      </div>
      <div class="stat-card">
        <span class="label">符合条件标的</span>
        <span class="value">{{ result.total_count }} 只</span>
      </div>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <p>正在联合量价、大单资金、股东户数表执行复杂筛查...</p>
    </div>
    
    <div v-else-if="result && result.candidates.length > 0" class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th>信号类型</th>
            <th>代码</th>
            <th>名称</th>
            <th>所属板块</th>
            <th>触发因子得分</th>
            <th>触发信号</th>
            <th>建议止损位</th>
            <th width="120">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in result.candidates" :key="item.ts_code" :class="item.signal_type === '强信号' ? 'row-strong' : ''">
            <td>
              <span :class="['signal-badge', item.signal_type === '强信号' ? 'strong' : 'medium']">
                {{ item.signal_type }}
              </span>
            </td>
            <td class="font-mono">{{ item.ts_code }}</td>
            <td class="font-bold">{{ item.name }}</td>
            <td>{{ item.industry || '-' }}</td>
            <td>
              <div class="score-display">
                <span class="score-number" :class="item.score >= 80 ? 'text-green' : 'text-blue'">{{ item.score }}</span>
              </div>
            </td>
            <td>
              <div class="tags-container">
                <span v-for="sig in item.signals" :key="sig" class="sig-tag">{{ sig }}</span>
              </div>
            </td>
            <td>
              <span class="stop-loss">¥ {{ item.stop_loss }}</span>
            </td>
            <td class="cell action">
              <button @click="openAIInterpret(item)" class="ai-btn-small" :disabled="aiLoadingMap[item.ts_code]">
                {{ aiLoadingMap[item.ts_code] ? '⏳' : '🧠' }} 解读
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    
    <div v-else-if="result && result.candidates.length === 0" class="empty-state">
      <div class="icon">🛡️</div>
      <p>今日未嗅探到符合“大单流入+股东下降”的横盘吸筹标的。</p>
      <span class="sub-empty">严格的条件是资产安全的保障。</span>
    </div>

    <!-- AI 解读侧边栏 -->
    <AIDrawer
      :visible="drawerVisible"
      :stockData="drawerCurrentStock"
      :interpretation="drawerCurrentInterpretation"
      :loading="drawerLoading"
      :error="drawerError"
      :stockList="drawerStockList"
      :currentIndex="drawerIndex"
      @close="drawerVisible = false"
      @prev="drawerIndex--"
      @next="drawerIndex++"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import AIDrawer from '../components/AIDrawer.vue'

const loading = ref(false)
const result = ref(null)

const aiLoadingMap = ref({})
const interpretationCache = ref({})

const drawerVisible = ref(false)
const drawerStockList = ref([])
const drawerIndex = ref(0)
const drawerLoading = ref(false)
const drawerError = ref('')

const drawerCurrentStock = computed(() => {
  if (drawerStockList.value.length === 0 || !result.value) return null
  const ts_code = drawerStockList.value[drawerIndex.value]
  return result.value.candidates.find(c => c.ts_code === ts_code) || { ts_code, name: ts_code }
})

const drawerCurrentInterpretation = computed(() => {
  if (!drawerCurrentStock.value) return ''
  return interpretationCache.value[drawerCurrentStock.value.ts_code] || ''
})

watch(drawerIndex, async (newIdx) => {
  if (drawerStockList.value.length === 0) return
  const ts_code = drawerStockList.value[newIdx]
  if (!interpretationCache.value[ts_code]) {
    await fetchAIInterpretation(ts_code)
  }
})

async function runSniffing() {
  loading.value = true
  result.value = null
  try {
    const res = await fetch('http://localhost:8002/api/v1/sniffer/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    })
    const data = await res.json()
    if (data.code === 0) {
      result.value = data.data
    }
  } catch (e) {
    console.error("嗅探请求失败:", e)
  } finally {
    loading.value = false
  }
}

async function fetchAIInterpretation(ts_code) {
  if (interpretationCache.value[ts_code]) return
  
  aiLoadingMap.value[ts_code] = true
  if (drawerCurrentStock.value?.ts_code === ts_code) {
    drawerLoading.value = true
    drawerError.value = ''
  }
  
  try {
    const res = await fetch('http://localhost:8002/api/v1/ai/interpret', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ts_code: ts_code,
        strategy_context: `主力嗅探-吸筹诊断`
      })
    })
    const data = await res.json()
    if (data.code === 0) {
      interpretationCache.value[ts_code] = data.data.interpretation
    } else {
      throw new Error(data.msg || '获取失败')
    }
  } catch (e) {
    console.error(e)
    if (drawerCurrentStock.value?.ts_code === ts_code) {
      drawerError.value = `解读拉取失败: ${e.message}`
    }
  } finally {
    aiLoadingMap.value[ts_code] = false
    if (drawerCurrentStock.value?.ts_code === ts_code) {
      drawerLoading.value = false
    }
  }
}

function openAIInterpret(stock) {
  drawerStockList.value = [stock.ts_code]
  drawerIndex.value = 0
  drawerVisible.value = true
  if (!interpretationCache.value[stock.ts_code]) {
    fetchAIInterpretation(stock.ts_code)
  }
}
</script>

<style scoped>
.sniffer-page {
  padding: 24px;
  color: #1a2332;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.title-section h2 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: #1a2332;
}

.subtitle {
  font-size: 0.9rem;
  color: #8c9aab;
}

.action-btn.primary {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 600;
  font-size: 1rem;
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.2);
  transition: all 0.2s;
}

.action-btn.primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px rgba(16, 185, 129, 0.3);
}

.action-btn.primary:disabled {
  opacity: 0.7;
  cursor: not-allowed;
  transform: none;
}

.dashboard-panel {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: #ffffff;
  padding: 16px 20px;
  border-radius: 12px;
  border: 1px solid #eef2f6;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stat-card.highlight {
  background: linear-gradient(135deg, rgba(74, 108, 247, 0.05) 0%, rgba(74, 108, 247, 0.1) 100%);
  border-color: rgba(74, 108, 247, 0.2);
}

.stat-card .label {
  font-size: 0.85rem;
  color: #8c9aab;
  font-weight: 500;
}

.stat-card .value {
  font-size: 1.25rem;
  font-weight: 700;
  color: #1a2332;
}

.stat-card.highlight .value {
  color: #4a6cf7;
}

.table-container {
  background: #ffffff;
  border-radius: 12px;
  border: 1px solid #eef2f6;
  overflow: hidden;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
}

.data-table th, .data-table td {
  padding: 14px 16px;
  text-align: left;
  border-bottom: 1px solid #eef2f6;
  color: #1a2332;
}

.data-table th {
  background: #f8f9fc;
  color: #4a5a6e;
  font-weight: 500;
  font-size: 0.9rem;
}

.row-strong {
  background: rgba(16, 185, 129, 0.03);
}

.data-table tr:hover {
  background: #f8f9fc;
}

.signal-badge {
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 600;
}

.signal-badge.strong {
  background: rgba(16, 185, 129, 0.1);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.2);
}

.signal-badge.medium {
  background: rgba(245, 158, 11, 0.1);
  color: #f59e0b;
  border: 1px solid rgba(245, 158, 11, 0.2);
}

.font-mono {
  font-family: 'SF Mono', monospace;
  color: #8c9aab;
}

.font-bold {
  font-weight: 600;
}

.score-number {
  font-weight: 700;
  font-size: 1.1rem;
}

.text-green { color: #10b981; }
.text-blue { color: #4a6cf7; }

.tags-container {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.sig-tag {
  background: #f1f5f9;
  color: #475569;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
}

.stop-loss {
  font-family: 'SF Mono', monospace;
  font-weight: 600;
  color: #ef4444;
}

.cell.action {
  text-align: right;
}

.ai-btn-small {
  background: rgba(74, 108, 247, 0.1);
  color: #4a6cf7;
  border: 1px solid rgba(74, 108, 247, 0.2);
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  transition: all 0.2s;
}

.ai-btn-small:hover:not(:disabled) {
  background: rgba(74, 108, 247, 0.2);
}

.ai-btn-small:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: #8c9aab;
  gap: 16px;
}

.empty-state .icon {
  font-size: 3rem;
  margin-bottom: 8px;
}

.empty-state .sub-empty {
  font-size: 0.85rem;
  opacity: 0.8;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #eef2f6;
  border-radius: 50%;
  border-top-color: #10b981;
  animation: spin 1s ease-in-out infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
