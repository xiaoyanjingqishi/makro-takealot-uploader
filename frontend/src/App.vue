<template>
  <div class="min-h-screen bg-slate-50 text-slate-800 flex flex-col">
    <!-- 顶部导航 -->
    <header class="bg-white border-b border-slate-200 h-16 flex items-center px-6 justify-between sticky top-0 z-50">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center text-white font-black text-xl shadow-md">
          M
        </div>
        <div>
          <h1 class="text-base font-bold text-slate-900 leading-tight">Makro 智能搬品系统</h1>
          <p class="text-xs text-slate-500">Takealot 选品 &rarr; AI 清洗 &rarr; Makro 上品</p>
        </div>
      </div>
      <div class="flex items-center space-x-2">
        <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5 animate-pulse"></span>
          服务在线
        </span>
      </div>
    </header>
    
    <main class="flex-1 max-w-7xl w-full mx-auto p-6">
      <div class="bg-white p-6 rounded-xl border border-slate-200 shadow-sm text-center py-12">
        <h2 class="text-lg font-bold text-slate-800 mb-2">系统已就绪</h2>
        <p class="text-xs text-slate-500 mb-6">可直接在浏览器访问 http://localhost:8000 进入完整控制台</p>
        <a href="http://localhost:8000" class="px-5 py-2.5 bg-blue-600 text-white text-xs font-bold rounded-lg shadow-sm hover:bg-blue-700">
          前往 Web 控制台
        </a>
      </div>
    </main>
  </div>
</template>

<script setup>
</script>
