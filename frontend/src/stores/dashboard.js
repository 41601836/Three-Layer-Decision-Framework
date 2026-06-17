import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchLayer1Diagnosis, fetchHeatmap, fetchSectorDetail } from '../api/index.js'

export const useDashboardStore = defineStore('dashboard', () => {
  // ─── 状态 ──────────────────────────────────────────────────────────────────
  const loading = ref({ layer1: false, heatmap: false, detail: false })
  const layer1Data = ref(null)
  const heatmapData = ref({ strength: [], moneyflow: [], sentiment: [] })
  const tradeDate = ref(null)
  const selectedSector = ref(null)
  const sectorDetail = ref(null)
  const error = ref(null)

  // ─── 动作 ──────────────────────────────────────────────────────────────────
  async function loadLayer1(externalRisk = 'neutral') {
    loading.value.layer1 = true
    error.value = null
    try {
      const res = await fetchLayer1Diagnosis(externalRisk)
      layer1Data.value = res.data.data
    } catch (e) {
      error.value = '第一层诊断加载失败: ' + e.message
      console.error(e)
    } finally {
      loading.value.layer1 = false
    }
  }

  async function loadHeatmap(date = null) {
    loading.value.heatmap = true
    error.value = null
    try {
      const res = await fetchHeatmap(date)
      const d = res.data
      heatmapData.value = d.data || { strength: [], moneyflow: [], sentiment: [] }
      tradeDate.value = d.trade_date
    } catch (e) {
      error.value = '热力图加载失败: ' + e.message
      console.error(e)
    } finally {
      loading.value.heatmap = false
    }
  }

  async function loadSectorDetail(name, date = null) {
    if (!name) return
    selectedSector.value = name
    sectorDetail.value = null
    loading.value.detail = true
    error.value = null
    try {
      const res = await fetchSectorDetail(name, date)
      sectorDetail.value = res.data.data
    } catch (e) {
      error.value = `板块[${name}]详情加载失败: ` + e.message
      console.error(e)
    } finally {
      loading.value.detail = false
    }
  }

  return {
    loading, layer1Data, heatmapData, tradeDate,
    selectedSector, sectorDetail, error,
    loadLayer1, loadHeatmap, loadSectorDetail
  }
})
