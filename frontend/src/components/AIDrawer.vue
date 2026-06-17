<template>
  <!-- 遮罩层 -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="drawer-overlay" @click="closeDrawer"></div>
    </Transition>
  </Teleport>

  <!-- 抽屉主体 -->
  <Teleport to="body">
    <Transition name="slide">
      <div v-if="visible" class="ai-drawer">
        <!-- 头部 -->
        <div class="drawer-header">
          <div class="stock-info">
            <span class="stock-name">{{ stockData?.name || '--' }}</span>
            <span class="stock-code">{{ stockData?.ts_code || '' }}</span>
            <span class="stock-price" v-if="stockData?.close">
              ¥{{ stockData.close }}
            </span>
            <span class="stock-change" :class="getChangeClass(stockData?.pct_chg)">
              {{ formatChange(stockData?.pct_chg) }}
            </span>
          </div>
          <div class="header-actions">
            <!-- 上一只/下一只 -->
            <button 
              class="nav-btn" 
              @click="prevStock" 
              :disabled="!hasPrev"
              title="上一只"
            >
              ‹
            </button>
            <button 
              class="nav-btn" 
              @click="nextStock" 
              :disabled="!hasNext"
              title="下一只"
            >
              ›
            </button>
            <button class="close-btn" @click="closeDrawer">✕</button>
          </div>
        </div>

        <!-- 主体 -->
        <div class="drawer-body">
          <!-- 加载状态 -->
          <div v-if="loading" class="loading-state">
            <div class="spinner"></div>
            <p>🧠 AI 分析师正在研读数据...</p>
          </div>

          <!-- 错误状态 -->
          <div v-else-if="error" class="error-state">
            <span class="error-icon">⚠️</span>
            <p>{{ error }}</p>
          </div>

          <!-- 空状态 -->
          <div v-else-if="!interpretation" class="empty-state">
            <span class="empty-icon">📭</span>
            <p>暂无解读数据</p>
          </div>

          <!-- 研报内容 -->
          <div v-else class="interpretation-content" v-html="renderedMarkdown"></div>
        </div>

        <!-- 底部导航指示器 -->
        <div class="drawer-footer" v-if="stockList.length > 1">
          <span class="nav-indicator">
            {{ currentIndex + 1 }} / {{ stockList.length }}
          </span>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { marked } from 'marked'

// ============ Props ============
const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  stockData: {
    type: Object,
    default: null
  },
  interpretation: {
    type: String,
    default: ''
  },
  loading: {
    type: Boolean,
    default: false
  },
  error: {
    type: String,
    default: ''
  },
  stockList: {
    type: Array,
    default: () => []
  },
  currentIndex: {
    type: Number,
    default: 0
  }
})

// ============ Emits ============
const emit = defineEmits([
  'close',
  'prev',
  'next'
])

// ============ Computed ============
const renderedMarkdown = computed(() => {
  if (!props.interpretation) return ''
  try {
    return marked(props.interpretation)
  } catch {
    return props.interpretation
  }
})

const hasPrev = computed(() => props.currentIndex > 0)
const hasNext = computed(() => props.currentIndex < props.stockList.length - 1)

// ============ Methods ============
function closeDrawer() {
  emit('close')
}

function prevStock() {
  if (hasPrev.value) emit('prev')
}

function nextStock() {
  if (hasNext.value) emit('next')
}

function getChangeClass(pct) {
  if (pct > 0) return 'positive'
  if (pct < 0) return 'negative'
  return 'zero'
}

function formatChange(pct) {
  if (pct === undefined || pct === null) return ''
  return (pct > 0 ? '+' : '') + pct.toFixed(2) + '%'
}

// ============ Styles ============
</script>

<style scoped>
/* 遮罩层 */
.drawer-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.5);
  z-index: 9998;
}

/* 抽屉 */
.ai-drawer {
  position: fixed;
  top: 0;
  right: 0;
  width: 440px;
  height: 100vh;
  background: #ffffff;
  z-index: 9999;
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
}

/* 头部 */
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 24px;
  border-bottom: 1px solid #eef2f6;
  flex-shrink: 0;
  min-height: 72px;
}

.stock-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stock-name {
  font-size: 18px;
  font-weight: 600;
  color: #1a2332;
}

.stock-code {
  font-size: 13px;
  color: #8c9aab;
  font-weight: 400;
}

.stock-price {
  font-size: 16px;
  font-weight: 600;
  color: #1a2332;
  margin-left: 6px;
}

.stock-change {
  font-size: 14px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 4px;
}

.stock-change.positive {
  color: #e74c3c;
  background: #fef0ef;
}

.stock-change.negative {
  color: #2e7d32;
  background: #e8f5e9;
}

.stock-change.zero {
  color: #8c9aab;
  background: #f5f6f8;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.nav-btn {
  width: 32px;
  height: 32px;
  border: 1px solid #e0e4e8;
  background: white;
  border-radius: 6px;
  font-size: 18px;
  cursor: pointer;
  color: #4a5a6e;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.nav-btn:hover:not(:disabled) {
  background: #f0f4f8;
  border-color: #b0bcc8;
}

.nav-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  font-size: 20px;
  cursor: pointer;
  color: #8c9aab;
  border-radius: 6px;
  transition: background 0.2s;
}

.close-btn:hover {
  background: #f0f4f8;
  color: #1a2332;
}

/* 主体 */
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

/* 加载状态 */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
  gap: 16px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #eef2f6;
  border-top: 4px solid #4a6cf7;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-state p {
  color: #8c9aab;
  font-size: 14px;
  margin: 0;
}

/* 错误状态 */
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
  gap: 12px;
}

.error-icon {
  font-size: 40px;
}

.error-state p {
  color: #e74c3c;
  font-size: 14px;
  text-align: center;
  margin: 0;
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
  gap: 12px;
}

.empty-icon {
  font-size: 40px;
}

.empty-state p {
  color: #8c9aab;
  font-size: 14px;
  margin: 0;
}

/* 研报内容 */
.interpretation-content {
  font-size: 14px;
  line-height: 1.8;
  color: #1a2332;
}

.interpretation-content :deep(h1),
.interpretation-content :deep(h2),
.interpretation-content :deep(h3) {
  font-size: 16px;
  font-weight: 600;
  margin: 16px 0 8px 0;
  color: #1a2332;
}

.interpretation-content :deep(h1:first-child),
.interpretation-content :deep(h2:first-child),
.interpretation-content :deep(h3:first-child) {
  margin-top: 0;
}

.interpretation-content :deep(p) {
  margin: 8px 0;
}

.interpretation-content :deep(ul),
.interpretation-content :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}

.interpretation-content :deep(li) {
  margin: 4px 0;
}

.interpretation-content :deep(strong) {
  color: #1a2332;
}

.interpretation-content :deep(blockquote) {
  border-left: 3px solid #4a6cf7;
  padding-left: 14px;
  margin: 12px 0;
  color: #4a5a6e;
  background: #f8f9fc;
  padding: 12px 16px;
  border-radius: 4px;
}

/* 底部 */
.drawer-footer {
  flex-shrink: 0;
  padding: 12px 24px;
  border-top: 1px solid #eef2f6;
  text-align: center;
}

.nav-indicator {
  font-size: 13px;
  color: #8c9aab;
}

/* 动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-enter-active,
.slide-leave-active {
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
}
</style>
