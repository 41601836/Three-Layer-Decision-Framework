<template>
  <div class="strategy-page">
    <div class="header">
      <h2>策略精选 - {{ selectedSector || '全市场' }}</h2>
      <div v-if="selectedStocks.length > 0" class="batch-actions">
        <span>已选 {{ selectedStocks.length }} 只股票</span>
        <button @click="batchInterpret" class="ai-btn-batch" :disabled="aiLoading">
          {{ aiLoading ? '⏳ 解读中...' : '🧠 批量解读' }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <p>正在执行策略诊断并筛选股票池...</p>
    </div>
    
    <div v-else-if="candidates.length > 0" class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th width="40">
              <input type="checkbox" :checked="isAllSelected" @change="toggleSelectAll" />
            </th>
            <th>代码</th>
            <th>名称</th>
            <th>策略评分</th>
            <th width="120">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="candidate in candidates" :key="candidate.ts_code">
            <td>
              <input type="checkbox" :value="candidate.ts_code" v-model="selectedStocks" />
            </td>
            <td class="font-mono">{{ candidate.ts_code }}</td>
            <td>{{ candidate.name || '未知' }}</td>
            <td>
              <div class="score-bar-container">
                <div class="score-bar" :style="{ width: candidate.score + '%' }"></div>
                <span>{{ candidate.score }}</span>
              </div>
            </td>
            <td class="cell action">
              <button 
                @click="openAIInterpret(candidate)" 
                class="ai-btn-small"
                :disabled="aiLoadingMap[candidate.ts_code]"
              >
                {{ aiLoadingMap[candidate.ts_code] ? '⏳' : '🧠' }} 解读
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty-state">
      <p>该板块当前没有符合策略的标的。</p>
    </div>

    <!-- AI 侧边栏 -->
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
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import AIDrawer from '../components/AIDrawer.vue'

const route = useRoute()
const selectedSector = ref('')
const loading = ref(false)
const candidates = ref([])

// 表格多选相关
const selectedStocks = ref([])

const isAllSelected = computed(() => {
  return candidates.value.length > 0 && selectedStocks.value.length === candidates.value.length
})

function toggleSelectAll() {
  if (isAllSelected.value) {
    selectedStocks.value = []
  } else {
    selectedStocks.value = candidates.value.map(c => c.ts_code)
  }
}

// AI 解读相关状态
const aiLoadingMap = ref({}) // { ts_code: boolean }
const interpretationCache = ref({}) // { ts_code: result_string }

const drawerVisible = ref(false)
const drawerStockList = ref([])
const drawerIndex = ref(0)
const drawerLoading = ref(false)
const drawerError = ref('')

const drawerCurrentStock = computed(() => {
  if (drawerStockList.value.length === 0) return null
  const ts_code = drawerStockList.value[drawerIndex.value]
  return candidates.value.find(c => c.ts_code === ts_code) || { ts_code, name: ts_code }
})

const drawerCurrentInterpretation = computed(() => {
  if (!drawerCurrentStock.value) return ''
  return interpretationCache.value[drawerCurrentStock.value.ts_code] || ''
})

// 监听 currentIndex 变化，自动加载对应股票的数据
watch(drawerIndex, async (newIdx) => {
  if (drawerStockList.value.length === 0) return
  const ts_code = drawerStockList.value[newIdx]
  if (!interpretationCache.value[ts_code]) {
    await fetchAIInterpretation(ts_code)
  }
})

// 加载 AI 解读数据
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
        strategy_context: `退潮期-${selectedSector.value || '全市场'}`
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

const aiLoading = computed(() => {
  return selectedStocks.value.some(code => aiLoadingMap.value[code])
})

function batchInterpret() {
  if (selectedStocks.value.length === 0) return
  
  drawerStockList.value = [...selectedStocks.value]
  drawerIndex.value = 0
  drawerVisible.value = true
  
  // 并发拉取未缓存的项
  drawerStockList.value.forEach(ts_code => {
    if (!interpretationCache.value[ts_code]) {
      fetchAIInterpretation(ts_code)
    }
  })
}

async function runStrategy() {
  loading.value = true
  try {
    const res = await fetch('http://localhost:8002/api/v1/layer3/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_type: 'B',
        sector: selectedSector.value
      })
    })
    const data = await res.json()
    if (data.code === 0 && data.data.candidates) {
      candidates.value = data.data.candidates
    }
  } catch (e) {
    console.error("Layer3 报错", e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  const sector = route.query.sector
  if (sector) {
    selectedSector.value = sector
  } else {
    selectedSector.value = '人工智能' // mock default
  }
  runStrategy()
})
</script>

<style scoped>
.strategy-page {
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

.header h2 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0;
  color: #1a2332;
}

.batch-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #ffffff;
  padding: 8px 16px;
  border-radius: 8px;
  border: 1px solid #eef2f6;
}

.batch-actions span {
  font-size: 0.9rem;
  color: #8c9aab;
}

.ai-btn-batch {
  background: linear-gradient(135deg, #4a6cf7 0%, #3054f5 100%);
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: opacity 0.2s;
}

.ai-btn-batch:hover:not(:disabled) {
  opacity: 0.9;
}

.ai-btn-batch:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.cell.action {
  text-align: right;
}

.ai-btn-small {
  background: rgba(74, 108, 247, 0.1);
  color: #4a6cf7;
  border: 1px solid rgba(74, 108, 247, 0.2);
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: all 0.2s;
}

.ai-btn-small:hover:not(:disabled) {
  background: rgba(74, 108, 247, 0.2);
}

.ai-btn-small:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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
  padding: 12px 16px;
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

.data-table tr:hover {
  background: #f8f9fc;
}

.font-mono {
  font-family: 'SF Mono', monospace;
  color: #8c9aab;
}

.score-bar-container {
  display: flex;
  align-items: center;
  gap: 12px;
}

.score-bar {
  height: 6px;
  background: #4a6cf7;
  border-radius: 3px;
  min-width: 4px;
}

.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: #8c9aab;
  gap: 16px;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #eef2f6;
  border-radius: 50%;
  border-top-color: #4a6cf7;
  animation: spin 1s ease-in-out infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
