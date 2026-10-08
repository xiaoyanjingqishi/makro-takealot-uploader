/**
 * 业务模块: tasks
 * 导出该领域的业务方法集
 */
export const tasksMethods = {
  async loadTasks() {
    try {
      let url = `/api/tasks?limit=100`;
      if (this.logFilterType) url += `&task_type=${encodeURIComponent(this.logFilterType)}`;
      if (this.logFilterStatus) url += `&status=${encodeURIComponent(this.logFilterStatus)}`;
      if (this.logSearchInput) url += `&search=${encodeURIComponent(this.logSearchInput)}`;
      const res = await fetch(url);
      const list = await res.json();
      if (Array.isArray(list)) {
        for (let i = 0; i < list.length; i++) {
          const t = list[i];
          t._typeInfo = this.getTaskTypeInfo(t.task_type);
          t._statusBadgeClass = this.getStatusBadgeClass(t.status);
          t._createdTime = this.formatDateTime(t.created_at);
          t._finishedTime = (t.finished_at && t.finished_at !== t.created_at) ? this.formatDateTime(t.finished_at) : '';
        }
      }
      this.tasks = list;
    } catch (e) {
      console.error(e);
    }
  },


  async checkActiveTasks() {
    try {
      await this.pollActiveTasks();
      if (this.activeBgTasks.some(t => t.status === 'RUNNING')) {
        this.ensureBgTasksPolling();
      }
    } catch (e) {
      console.error('检查活跃任务异常:', e);
    }
  },


  ensureBgTasksPolling() {
    if (this.bgTasksPollTimer) return;
    this.bgTasksPollTimer = setInterval(async () => {
      await this.pollActiveTasks();
    }, 800);
  },


  stopBgTasksPolling() {
    if (this.bgTasksPollTimer) {
      clearInterval(this.bgTasksPollTimer);
      this.bgTasksPollTimer = null;
    }
  },


  async pollActiveTasks() {
    try {
      const res = await fetch('/api/tasks/active');
      if (!res.ok) return;
      const data = await res.json();
      const serverTasks = data.tasks || (data.task ? [data.task] : []);

      let hasRunning = false;
      for (const st of serverTasks) {
        if (st.status === 'RUNNING') hasRunning = true;
        const idx = this.activeBgTasks.findIndex(x => x.id === st.id);
        if (idx >= 0) {
          const prev = this.activeBgTasks[idx];
          // 如果任务状态从未完成变为已结束，触发数据表格刷新与定时关闭
          if (prev.status === 'RUNNING' && st.status !== 'RUNNING') {
            this.loadProducts();
            this.loadTasks();
            if (this.currentTab === 'piggyback') {
              this.loadPiggybackItems(this.piggyback.page);
            }
            setTimeout(() => {
              this.dismissBgTask(st.id);
            }, 12000);
          }
          this.activeBgTasks.splice(idx, 1, { ...prev, ...st });
        } else {
          this.activeBgTasks.push(st);
          if (st.status !== 'RUNNING') {
            setTimeout(() => {
              this.dismissBgTask(st.id);
            }, 12000);
          }
        }
      }

      const anyLocalRunning = this.activeBgTasks.some(t => t.status === 'RUNNING');
      this.runningFullCruise = this.activeBgTasks.some(t => t.task_type === 'FULL_CRUISE_INSPECTION' && t.status === 'RUNNING');
      if (!hasRunning && !anyLocalRunning) {
        this.stopBgTasksPolling();
      }
    } catch (err) {
      console.error('轮询后台任务异常:', err);
    }
  },


  addOrUpdateBgTask(task) {
    const idx = this.activeBgTasks.findIndex(x => x.id === task.id);
    if (idx >= 0) {
      this.activeBgTasks.splice(idx, 1, { ...this.activeBgTasks[idx], ...task });
    } else {
      this.activeBgTasks.unshift(task);
      if (window.scrollY > 80) {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    }
  },


  dismissBgTask(taskId) {
    this.activeBgTasks = this.activeBgTasks.filter(t => t.id !== taskId);
    if (this.activeBgTasks.length === 0) {
      this.stopBgTasksPolling();
    }
  },


  async cancelBgTask(task) {
    if (!confirm(`确定要终止当前任务【${task.name}】吗？`)) return;
    try {
      const res = await fetch(`/api/tasks/${task.id}/cancel`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        task.status = 'CANCELLED';
        task.message = '已请求取消任务';
        this.loadProducts();
        this.loadTasks();
      }
    } catch (e) {
      alert('取消任务失败: ' + e);
    }
  },


  formatJsonLog(logData) {
    if (!logData) return '暂无详细回执数据';
    try {
      if (typeof logData === 'object') {
        return JSON.stringify(logData, null, 2);
      }
      const parsed = JSON.parse(logData);
      return JSON.stringify(parsed, null, 2);
    } catch (e) {
      return String(logData);
    }
  },


  openLogDetail(t) {
    this.activeLogDetail = t;
    this.showLogDetailModal = true;
  },


  getTaskTypeInfo(type) {
    if (!this._taskTypeMap) {
      this._taskTypeMap = {
        'COLLECT': { text: '📥 商品采集', bg: 'bg-blue-50 text-blue-700 border-blue-200' },
        'CLEAN': { text: '⚡ 单品清洗', bg: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
        'BATCH_CLEAN': { text: '⚡ 批量AI清洗', bg: 'bg-indigo-100 text-indigo-800 border-indigo-300' },
        'COMPLIANCE': { text: '🛡️ 单品合规', bg: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
        'BATCH_COMPLIANCE': { text: '🛡️ 批量合规', bg: 'bg-emerald-100 text-emerald-800 border-emerald-300' },
        'BATCH_PRICE': { text: '💰 批量改价', bg: 'bg-amber-50 text-amber-700 border-amber-200' },
        'BATCH_DELETE': { text: '🗑️ 批量删除', bg: 'bg-rose-50 text-rose-700 border-rose-200' },
        'DELETE': { text: '🗑️ 单品删除', bg: 'bg-rose-50 text-rose-700 border-rose-200' },
        'UPDATE': { text: '✏️ 属性修改', bg: 'bg-slate-100 text-slate-700 border-slate-200' },
        'SUBMIT_LISTING': { text: '🚀 单品上品', bg: 'bg-purple-50 text-purple-700 border-purple-200' },
        'BATCH_PUBLISH': { text: '🚀 批量上品', bg: 'bg-purple-100 text-purple-800 border-purple-300' },
        'ABANDON': { text: '🗑️ 移入弃用', bg: 'bg-slate-100 text-slate-700 border-slate-300' },
        'RESTORE': { text: '♻️ 恢复单品', bg: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
        'BATCH_RESTORE': { text: '♻️ 批量恢复', bg: 'bg-emerald-100 text-emerald-800 border-emerald-300' },
        'SETTINGS_UPDATE': { text: '⚙️ 配置更新', bg: 'bg-slate-100 text-slate-600 border-slate-300' },
        'FULL_CRUISE_INSPECTION': { text: '⚡ 全量巡检', bg: 'bg-indigo-100 text-indigo-800 border-indigo-300' },
        'MAKRO_REPRICE': { text: '🤖 自动跟价', bg: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
      };
    }
    return this._taskTypeMap[type] || { text: type, bg: 'bg-slate-100 text-slate-700 border-slate-200' };
  },
};

export default tasksMethods;
