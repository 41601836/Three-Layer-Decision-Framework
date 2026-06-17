<template>
  <div class="app-layout">
    <!-- 左侧导航 -->
    <aside class="sidebar">
      <div class="logo">
        <span class="logo-icon">📈</span>
        <span class="logo-text">StockAI</span>
        <span class="logo-version">v2.0</span>
      </div>
      
      <nav class="nav-menu">
        <router-link 
          v-for="route in menuRoutes" 
          :key="route.path"
          :to="route.path"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">{{ route.meta?.icon || '📄' }}</span>
          <span class="nav-label">{{ route.meta?.title || route.name }}</span>
        </router-link>
      </nav>
      
      <div class="sidebar-footer">
        <div class="system-status">
          <span class="status-dot green"></span>
          <span class="status-text">系统运行中</span>
        </div>
        <div class="api-status">
          <span class="status-dot" :class="ollamaStatus ? 'green' : 'gray'"></span>
          <span class="status-text">Ollama {{ ollamaStatus ? '已连接' : '未连接' }}</span>
        </div>
      </div>
    </aside>
    
    <!-- 主内容区 -->
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const ollamaStatus = ref(false)

const menuRoutes = computed(() => {
  return router.options.routes.filter(r => r.path !== '/')
})

// 检查 Ollama 状态
async function checkOllama() {
  try {
    const res = await fetch('http://localhost:8002/api/v1/ai/health')
    ollamaStatus.value = res.ok
  } catch {
    ollamaStatus.value = false
  }
}
checkOllama()
</script>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  background: #0b1120;
}

/* 侧边栏 - 宽度 220px，深色主题 */
.sidebar {
  width: 220px;
  min-height: 100vh;
  background: #1a2332;
  color: #c8d0dc;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  height: 100vh;
}

.logo {
  padding: 20px 20px 16px;
  border-bottom: 1px solid #2a3344;
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo-icon {
  font-size: 24px;
}

.logo-text {
  font-size: 18px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: 0.5px;
}

.logo-version {
  font-size: 10px;
  color: #6a7a8e;
  background: #2a3344;
  padding: 2px 8px;
  border-radius: 10px;
  margin-left: auto;
}

.nav-menu {
  flex: 1;
  padding: 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  color: #8c9aab;
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s;
}

.nav-item:hover {
  background: #2a3344;
  color: #e8edf3;
}

.nav-item.active {
  background: #2a3a6a;
  color: #ffffff;
}

.nav-icon {
  font-size: 16px;
  width: 24px;
  text-align: center;
}

.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid #2a3344;
  font-size: 12px;
}

.system-status,
.api-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.status-dot.green {
  background: #4caf50;
}

.status-dot.gray {
  background: #555;
}

.status-text {
  color: #6a7a8e;
}

.main-content {
  flex: 1;
  padding: 24px 32px;
  overflow-y: auto;
  max-height: 100vh;
}
</style>
