<template>
  <div ref="chartEl" class="treemap-container"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  data: { type: Array, default: () => [] },
  colorScale: { type: Array, default: () => ['#0050ff', '#1a1a2e', '#ff2a2a'] },
  title: { type: String, default: '' },
  unit: { type: String, default: '' },
  colorLabel: { type: String, default: '指标' },
})

const emit = defineEmits(['blockClick'])
const chartEl = ref(null)
let chartInstance = null

function buildOption() {
  if (!props.data.length) {
    return {
      title: { text: props.title + '（暂无数据）', textStyle: { color: '#666', fontSize: 13 } }
    }
  }

  const values = props.data.map(d => d.colorValue || 0)
  let minVal = Math.min(...values)
  let maxVal = Math.max(...values)

  // 强化色差：如果是包含负数的发散型数据（如涨跌幅、净流入），强制以 0 为中心对称
  if (minVal < 0) {
    const absMax = Math.max(Math.abs(minVal), Math.abs(maxVal), 0.01)
    minVal = -absMax
    maxVal = absMax
  } else if (minVal === maxVal) {
    maxVal = minVal + 0.01
  }

  return {
    title: {
      text: props.title,
      left: 8,
      top: 6,
      textStyle: { color: '#e2e8f0', fontSize: 13, fontWeight: 600, fontFamily: 'Inter' },
    },
    tooltip: {
      formatter: (info) => {
        const { name } = info.data || {}
        const value = info.value[0]
        const colorValue = info.value[1]
        return `<div style="font-family:Inter;padding:4px 8px">
          <b style="color:#fff">${name}</b><br>
          成交额: ${value?.toFixed(1)}亿 | ${props.colorLabel}: <b>${colorValue?.toFixed(2)}</b>${props.unit}
        </div>`
      },
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0' },
    },
    visualMap: {
      type: 'continuous',
      min: minVal,
      max: maxVal,
      inRange: { color: props.colorScale },
      show: false,
      dimension: 1,
    },
    series: [{
      type: 'treemap',
      data: props.data.map(d => ({
        name: d.name,
        value: [d.value, d.colorValue || 0],
      })),
      roam: false,
      nodeClick: false,
      label: {
        show: true,
        formatter: (p) => p.data.name,
        color: '#fff',
        fontSize: 11,
        fontFamily: 'Inter',
        overflow: 'truncate',
      },
      emphasis: {
        label: { fontSize: 13, fontWeight: 600 },
        itemStyle: { borderColor: '#ffd700', borderWidth: 2 },
      },
      breadcrumb: { show: false },
      itemStyle: {
        gapWidth: 2,
        borderRadius: 3,
      },
    }],
  }
}

function initChart() {
  if (!chartEl.value) return
  chartInstance = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  chartInstance.setOption(buildOption())

  chartInstance.on('click', (params) => {
    if (params.data?.name) {
      emit('blockClick', params.data.name)
    }
  })
}

function resize() {
  chartInstance?.resize()
}

watch(() => props.data, () => {
  if (chartInstance) chartInstance.setOption(buildOption(), { replaceMerge: ['series'] })
}, { deep: true })

onMounted(() => {
  initChart()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chartInstance?.dispose()
})
</script>

<style scoped>
.treemap-container {
  width: 100%;
  height: 100%;
  min-height: 360px;
}
</style>
