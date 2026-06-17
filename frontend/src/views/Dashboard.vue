<template>
  <div class="dashboard">
    <!-- ─── 顶栏 ──────────────────────────────────────────────────────────── -->
    <header class="dash-header">
      <div class="logo">
        <span class="logo-icon">⚡</span>
        <span class="logo-text">投研终端 · 三层决策</span>
      </div>
      <div class="header-right">
        <span class="trade-date">{{ store.tradeDate || '—' }}</span>
        <button class="refresh-btn" @click="refresh" :disabled="anyLoading">
          <span v-if="anyLoading" class="spin">⟳</span>
          <span v-else>↻ 刷新</span>
        </button>
      </div>
    </header>

    <!-- ─── 第一层：宏观诊断摘要 ──────────────────────────────────────────── -->
    <section class="layer-card layer1-card">
      <div class="layer-label">第一层 · 宏观环境</div>
      <div v-if="store.loading.layer1" class="loading-pulse">分析中…</div>
      <div v-else-if="store.layer1Data" class="layer1-summary">
        <!-- 操作模式标签 -->
        <div class="mode-badge" :class="modeBadgeClass">
          {{ store.layer1Data.mode }}
          <small>仓位上限 {{ (store.layer1Data.max_position * 100).toFixed(0) }}%</small>
        </div>
        <!-- 综合评分进度条 -->
        <div class="score-bar-wrap">
          <span class="score-label">综合评分</span>
          <div class="score-bar">
            <div class="score-fill" :style="{ width: (store.layer1Data.score / 6 * 100) + '%' }"></div>
          </div>
          <span class="score-val">{{ store.layer1Data.score }} / 6</span>
        </div>
        <!-- 六维灯位 -->
        <div class="dim-grid">
          <div
            v-for="(dim, k) in store.layer1Data.dimensions"
            :key="k"
            class="dim-chip"
            :class="'light-' + dim.status"
          >
            <span class="dim-dot"></span>{{ k }}
          </div>
        </div>
        <!-- 候选方向 -->
        <div v-if="store.layer1Data.directions?.length" class="directions-row">
          <span class="dir-label">候选方向 →</span>
          <button
            v-for="d in store.layer1Data.directions"
            :key="d.name"
            class="dir-chip"
            @click="selectSector(d.name)"
          >{{ d.name }}</button>
        </div>
      </div>
      <div v-else class="no-data">暂无宏观诊断数据</div>
    </section>

    <!-- ─── 第二层：三张热力图 ─────────────────────────────────────────────── -->
    <section class="layer-card layer2-card">
      <div class="layer-label">第二层 · 板块全景热力图</div>
      <div class="heatmap-hint">点击色块 → 查看板块详细诊断</div>

      <div v-if="store.loading.heatmap" class="loading-pulse">热力图加载中…</div>
      <div v-else class="heatmap-grid">
        <div class="heatmap-cell">
          <div class="heatmap-title">① 基础强弱</div>
          <HeatmapTreemap
            :data="store.heatmapData.strength"
            :colorScale="['#0a2463', '#1a1a2e', '#ff2a2a']"
            unit="%"
            colorLabel="涨跌幅"
            @blockClick="selectSector"
          />
        </div>
        <div class="heatmap-cell">
          <div class="heatmap-title">② 资金流向</div>
          <HeatmapTreemap
            :data="store.heatmapData.moneyflow"
            :colorScale="['#003d99', '#1a1a2e', '#cc1100']"
            unit="亿"
            colorLabel="净流入"
            @blockClick="selectSector"
          />
        </div>
        <div class="heatmap-cell">
          <div class="heatmap-title">③ 情绪结构</div>
          <HeatmapTreemap
            :data="store.heatmapData.sentiment"
            :colorScale="['#1a1a2e', '#ff6b00', '#cc0000']"
            unit="%"
            colorLabel="涨停覆盖率"
            @blockClick="selectSector"
          />
        </div>
      </div>
    </section>

    <!-- ─── 板块详情区 ──────────────────────────────────────────────────────── -->
    <section v-if="store.selectedSector" class="layer-card detail-card">
      <div class="layer-label">
        板块详情 · <strong>{{ store.selectedSector }}</strong>
      </div>

      <div v-if="store.loading.detail" class="loading-pulse">诊断中…</div>
      <div v-else-if="store.sectorDetail" class="detail-body">
        <!-- 阶段 + 策略 -->
        <div class="detail-top">
          <div class="stage-badge" :class="stageBadgeClass">{{ store.sectorDetail.stage }}</div>
          <div class="strategy-card">
            <div class="strategy-primary">{{ store.sectorDetail.strategy.primary }}</div>
            <div class="strategy-pos">仓位上限 {{ (store.sectorDetail.strategy.position_limit * 100).toFixed(0) }}%</div>
            <div class="strategy-desc">{{ store.sectorDetail.strategy.description }}</div>
          </div>
          <!-- 风控标记 -->
          <div class="risk-marks">
            <span v-if="store.sectorDetail.risk_marks.zhongjun_effect" class="risk-chip red">⚠ 中军消耗</span>
            <span v-if="store.sectorDetail.risk_marks.fusion" class="risk-chip red">⚠ 熔断</span>
            <span v-if="store.sectorDetail.risk_marks.siphon" class="risk-chip orange">⚡ 虹吸</span>
            <span v-if="!anyRisk" class="risk-chip green">✓ 风控正常</span>
          </div>
        </div>

        <!-- 五大信号 -->
        <div class="signals-grid">
          <div
            v-for="(sig, name) in store.sectorDetail.signals"
            :key="name"
            class="signal-card"
            :class="'stage-' + sig.stage_hint"
          >
            <div class="signal-name">{{ name }}</div>
            <div class="signal-stage">{{ sig.stage_hint }}</div>
            <div class="signal-detail">{{ sig.detail }}</div>
          </div>
        </div>

        <!-- 判定依据 -->
        <div class="stage-reason">
          <span class="reason-icon">🧠</span> {{ store.sectorDetail.stage_reason }}
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard.js'
import HeatmapTreemap from '../components/HeatmapTreemap.vue'
import { useRouter } from 'vue-router'

const store = useDashboardStore()
const router = useRouter()

const anyLoading = computed(() =>
  store.loading.layer1 || store.loading.heatmap || store.loading.detail
)

const anyRisk = computed(() => {
  const rm = store.sectorDetail?.risk_marks
  return rm && (rm.zhongjun_effect || rm.fusion || rm.siphon)
})

const modeBadgeClass = computed(() => {
  const m = store.layer1Data?.mode
  if (m === '进攻') return 'mode-attack'
  if (m === '防守') return 'mode-defend'
  return 'mode-caution'
})

const stageBadgeClass = computed(() => {
  const s = store.sectorDetail?.stage
  if (s?.includes('高潮')) return 'stage-peak'
  if (s?.includes('扩散')) return 'stage-expand'
  if (s?.includes('退潮')) return 'stage-retreat'
  return 'stage-sprout'
})

function selectSector(name) {
  store.loadSectorDetail(name, store.tradeDate)
  // 如果 vue-router 存在，跳转到策略页
  if (router) {
    router.push({
      path: '/strategy',
      query: { sector: name }
    })
  }
}

async function refresh() {
  await Promise.all([
    store.loadLayer1(),
    store.loadHeatmap(),
  ])
}

onMounted(() => {
  refresh()
})
</script>

<style>
/* ─── 全局重置 ───────────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0b0e1a;
  color: #e2e8f0;
  font-family: 'Inter', system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.6;
  min-height: 100vh;
}
button { cursor: pointer; border: none; background: none; }

/* ─── 布局 ───────────────────────────────────────────────────────────────── */
.dashboard {
  max-width: 1600px;
  margin: 0 auto;
  padding: 0 16px 40px;
}

/* ─── 顶栏 ───────────────────────────────────────────────────────────────── */
.dash-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 0;
  border-bottom: 1px solid #1e293b;
  margin-bottom: 20px;
}
.logo { display: flex; align-items: center; gap: 10px; }
.logo-icon { font-size: 24px; }
.logo-text { font-size: 18px; font-weight: 700; letter-spacing: 0.5px;
  background: linear-gradient(90deg, #60a5fa, #a78bfa);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.header-right { display: flex; align-items: center; gap: 14px; }
.trade-date { color: #64748b; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
.refresh-btn {
  padding: 6px 14px; border-radius: 8px;
  background: #1e293b; color: #94a3b8;
  font-size: 13px; font-family: 'Inter', sans-serif;
  transition: all 0.2s;
}
.refresh-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.spin { display: inline-block; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ─── 卡片 ───────────────────────────────────────────────────────────────── */
.layer-card {
  background: #111827;
  border: 1px solid #1e293b;
  border-radius: 16px;
  padding: 20px 24px;
  margin-bottom: 16px;
  transition: border-color 0.2s;
}
.layer-card:hover { border-color: #334155; }
.layer-label {
  font-size: 11px; font-weight: 600; letter-spacing: 1px;
  color: #64748b; text-transform: uppercase; margin-bottom: 14px;
}

/* ─── 第一层 ─────────────────────────────────────────────────────────────── */
.layer1-summary { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.mode-badge {
  display: flex; flex-direction: column; align-items: center;
  padding: 10px 20px; border-radius: 10px; font-size: 18px; font-weight: 700;
  min-width: 100px; text-align: center;
}
.mode-badge small { font-size: 11px; font-weight: 400; margin-top: 2px; }
.mode-attack { background: rgba(239,68,68,0.15); color: #ef4444; border: 1px solid #ef4444; }
.mode-defend { background: rgba(59,130,246,0.15); color: #3b82f6; border: 1px solid #3b82f6; }
.mode-caution { background: rgba(234,179,8,0.15); color: #eab308; border: 1px solid #eab308; }

.score-bar-wrap { display: flex; align-items: center; gap: 10px; }
.score-label { font-size: 12px; color: #64748b; white-space: nowrap; }
.score-bar { width: 120px; height: 6px; background: #1e293b; border-radius: 3px; overflow: hidden; }
.score-fill { height: 100%; background: linear-gradient(90deg, #3b82f6, #a78bfa); border-radius: 3px;
  transition: width 0.6s ease; }
.score-val { font-size: 12px; color: #94a3b8; font-family: 'JetBrains Mono', monospace; }

.dim-grid { display: flex; gap: 8px; flex-wrap: wrap; }
.dim-chip { display: flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 20px; font-size: 12px; }
.dim-dot { width: 8px; height: 8px; border-radius: 50%; }
.light-绿灯 { background: rgba(34,197,94,0.1); color: #22c55e; }
.light-绿灯 .dim-dot { background: #22c55e; }
.light-黄灯 { background: rgba(234,179,8,0.1); color: #eab308; }
.light-黄灯 .dim-dot { background: #eab308; }
.light-红灯 { background: rgba(239,68,68,0.1); color: #ef4444; }
.light-红灯 .dim-dot { background: #ef4444; }

.directions-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dir-label { font-size: 12px; color: #64748b; }
.dir-chip { padding: 4px 12px; border-radius: 6px; background: #1e293b;
  color: #94a3b8; font-size: 12px; transition: all 0.2s; }
.dir-chip:hover { background: #334155; color: #e2e8f0; }

/* ─── 热力图 ─────────────────────────────────────────────────────────────── */
.heatmap-hint { font-size: 12px; color: #475569; margin-bottom: 12px; }
.heatmap-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.heatmap-cell { background: #0f172a; border-radius: 10px; padding: 10px; }
.heatmap-title {
  font-size: 12px; font-weight: 600; color: #64748b;
  letter-spacing: 0.5px; margin-bottom: 8px;
}

/* ─── 板块详情 ───────────────────────────────────────────────────────────── */
.detail-body { display: flex; flex-direction: column; gap: 16px; }
.detail-top { display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap; }

.stage-badge {
  padding: 10px 20px; border-radius: 10px; font-size: 16px; font-weight: 700;
  white-space: nowrap;
}
.stage-peak { background: rgba(239,68,68,0.15); color: #ef4444; border: 1px solid #ef4444; }
.stage-expand { background: rgba(234,179,8,0.15); color: #eab308; border: 1px solid #eab308; }
.stage-retreat { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid #475569; }
.stage-sprout { background: rgba(34,197,94,0.15); color: #22c55e; border: 1px solid #22c55e; }

.strategy-card { flex: 1; min-width: 200px; }
.strategy-primary { font-size: 15px; font-weight: 600; color: #60a5fa; margin-bottom: 2px; }
.strategy-pos { font-size: 12px; color: #a78bfa; margin-bottom: 4px; }
.strategy-desc { font-size: 12px; color: #64748b; line-height: 1.5; }

.risk-marks { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.risk-chip { padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 500; }
.risk-chip.red { background: rgba(239,68,68,0.1); color: #ef4444; }
.risk-chip.orange { background: rgba(251,146,60,0.1); color: #fb923c; }
.risk-chip.green { background: rgba(34,197,94,0.1); color: #22c55e; }

.signals-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.signal-card {
  background: #0f172a; border-radius: 10px; padding: 12px;
  border-left: 3px solid #334155;
  transition: border-color 0.2s;
}
.signal-card.stage-扩散 { border-left-color: #eab308; }
.signal-card.stage-高潮 { border-left-color: #ef4444; }
.signal-card.stage-退潮 { border-left-color: #475569; }
.signal-card.stage-萌芽 { border-left-color: #22c55e; }

.signal-name { font-size: 12px; font-weight: 600; color: #94a3b8; margin-bottom: 4px; }
.signal-stage { font-size: 14px; font-weight: 700; color: #e2e8f0; margin-bottom: 6px; }
.signal-detail { font-size: 11px; color: #64748b; line-height: 1.5; }

.stage-reason {
  background: #0f172a; border-radius: 8px; padding: 12px 16px;
  font-size: 12px; color: #64748b; border-left: 3px solid #334155;
  line-height: 1.7;
}
.reason-icon { font-style: normal; }

/* ─── 通用 ───────────────────────────────────────────────────────────────── */
.loading-pulse {
  color: #475569; font-size: 13px; padding: 20px 0;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
.no-data { color: #475569; font-size: 13px; padding: 10px 0; }

@media (max-width: 768px) {
  .heatmap-grid { grid-template-columns: 1fr; }
  .layer1-summary { flex-direction: column; align-items: flex-start; }
}
</style>
