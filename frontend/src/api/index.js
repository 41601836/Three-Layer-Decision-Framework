// API 请求封装
import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

export const fetchLayer1Diagnosis = (externalRisk = 'neutral') =>
  api.post('/layer1/diagnosis', { external_risk: externalRisk, force_refresh: false })

export const fetchHeatmap = (tradeDate = null) =>
  api.get('/layer2/heatmap', { params: tradeDate ? { trade_date: tradeDate } : {} })

export const fetchSectorDetail = (sectorName, tradeDate = null) =>
  api.get('/layer2/sector_detail', {
    params: { sector_name: sectorName, ...(tradeDate ? { trade_date: tradeDate } : {}) }
  })

export default api
