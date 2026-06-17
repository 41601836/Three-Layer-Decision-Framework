import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'
import StrategyABC from '../views/StrategyABC.vue'
import MondayWave from '../views/MondayWave.vue'
import Sniffer from '../views/Sniffer.vue'
import Backtest from '../views/Backtest.vue'
import Settings from '../views/Settings.vue'

const routes = [
  {
    path: '/',
    name: '总览',
    component: Dashboard,
    meta: { icon: '📊', title: '市场总览' }
  },
  {
    path: '/strategy',
    name: '策略选股',
    component: StrategyABC,
    meta: { icon: '🎯', title: '策略选股' }
  },
  {
    path: '/monday',
    name: '周一战法',
    component: MondayWave,
    meta: { icon: '📆', title: '周一战法' }
  },
  {
    path: '/sniffer',
    name: '主力嗅探',
    component: Sniffer,
    meta: { icon: '🔍', title: '主力嗅探' }
  },
  {
    path: '/backtest',
    name: '回测中心',
    component: Backtest,
    meta: { icon: '📈', title: '回测中心' }
  },
  {
    path: '/settings',
    name: '系统设置',
    component: Settings,
    meta: { icon: '⚙️', title: '系统设置' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
