/**
 * 业务模块: reprice
 * Makro 在售商品 24h 智能跟价监控与巡航系统
 */
export const repriceMethods = {
  // 1. 加载跟价监控商品列表与大盘 KPI
  async loadRepriceItems(page = 1) {
    if (!this.selectedStoreId) return;
    this.reprice.loading = true;
    this.reprice.page = page;
    try {
      let url = `/api/reprice/list?store_id=${this.selectedStoreId}&page=${page}&page_size=${this.reprice.pageSize}`;
      if (this.reprice.source_type && this.reprice.source_type !== 'ALL') {
        url += `&source_type=${encodeURIComponent(this.reprice.source_type)}`;
      }
      if (this.reprice.buybox_status && this.reprice.buybox_status !== 'ALL') {
        url += `&buybox_status=${encodeURIComponent(this.reprice.buybox_status)}`;
      }
      if (this.reprice.compliance_status && this.reprice.compliance_status !== 'ALL') {
        url += `&compliance_status=${encodeURIComponent(this.reprice.compliance_status)}`;
      }
      if (this.reprice.auto_reprice && this.reprice.auto_reprice !== 'ALL') {
        url += `&auto_reprice=${encodeURIComponent(this.reprice.auto_reprice)}`;
      }
      if (this.reprice.monitor_status && this.reprice.monitor_status !== 'ALL') {
        url += `&monitor_status=${encodeURIComponent(this.reprice.monitor_status)}`;
      }
      if (this.reprice.search && this.reprice.search.trim()) {
        url += `&search=${encodeURIComponent(this.reprice.search.trim())}`;
      }
      if (this.reprice.min_price !== '' && this.reprice.min_price !== null && this.reprice.min_price !== undefined) {
        url += `&min_my_price=${encodeURIComponent(this.reprice.min_price)}`;
      }
      if (this.reprice.max_price !== '' && this.reprice.max_price !== null && this.reprice.max_price !== undefined) {
        url += `&max_my_price=${encodeURIComponent(this.reprice.max_price)}`;
      }

      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.reprice.items = data.items || [];
        this.reprice.total = data.total || 0;
        if (data.kpi) {
          this.reprice.kpi = data.kpi;
        }
      } else if (res.status === 401) {
        this.checkAuth();
      } else {
        const err = await res.json();
        this.showToast(`❌ 加载跟价列表失败: ${err.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      console.error('加载跟价列表失败:', e);
    } finally {
      this.reprice.loading = false;
    }
  },

  // 2. 加载调度器状态
  async loadRepriceSchedulerStatus() {
    try {
      const res = await fetch('/api/reprice/status', { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.reprice.scheduler_enabled = data.scheduler_enabled;
        this.reprice.scheduler_interval = data.interval_seconds || 1800;
      }
    } catch (e) {
      console.warn('获取调度器状态异常:', e);
    }
  },

  // 3. 启停 24h 自动巡航定时器
  async toggleRepriceScheduler() {
    const nextState = !this.reprice.scheduler_enabled;
    try {
      const res = await fetch(`/api/reprice/scheduler/toggle?enable=${nextState}`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.reprice.scheduler_enabled = data.enabled;
        this.showToast(data.message, 'success');
      } else {
        this.showToast(`❌ 切换失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('切换巡航调度器异常: ' + e, 'error');
    }
  },

  // 0. 安全网络请求助手 (带 45s 超时熔断保护，彻底杜绝挂死与 500 非 JSON 纯文本引发 SyntaxError)
  async safeRepriceFetch(url, options = {}, timeoutMs = 45000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const headers = options.headers || this.getAuthHeaders();
    try {
      const res = await fetch(url, {
        ...options,
        headers,
        signal: options.signal || controller.signal
      });
      clearTimeout(timeoutId);
      let data = null;
      let text = '';
      try {
        text = await res.text();
        data = JSON.parse(text);
      } catch (_) {
        if (!res.ok) {
          throw new Error(`服务异常 (HTTP ${res.status}): ${text.slice(0, 150) || res.statusText || '未知错误'}`);
        }
      }
      return { res, data, text };
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') {
        throw new Error(`请求超时 (${Math.round(timeoutMs / 1000)}秒)：服务端可能仍在处理中，请稍后刷新查看`);
      }
      throw e;
    }
  },

  // 4. KPI 战况卡片快速筛选 (支持 Toggle 反选与互斥重置)
  selectRepriceKpiFilter(filterKey) {
    if (filterKey === 'total') {
      this.reprice.buybox_status = 'ALL';
      this.reprice.compliance_status = 'ALL';
      this.reprice.monitor_status = 'ALL';
      this.reprice.auto_reprice = 'ALL';
    } else if (filterKey === 'winning') {
      this.reprice.buybox_status = this.reprice.buybox_status === 'WINNING' ? 'ALL' : 'WINNING';
      this.reprice.compliance_status = 'ALL';
    } else if (filterKey === 'losing') {
      this.reprice.buybox_status = this.reprice.buybox_status === 'LOSING' ? 'ALL' : 'LOSING';
      this.reprice.compliance_status = 'ALL';
    } else if (filterKey === 'floor_hit') {
      this.reprice.buybox_status = this.reprice.buybox_status === 'FLOOR_HIT' ? 'ALL' : 'FLOOR_HIT';
      this.reprice.compliance_status = 'ALL';
    } else if (filterKey === 'no_competitor') {
      this.reprice.buybox_status = this.reprice.buybox_status === 'NO_COMPETITOR' ? 'ALL' : 'NO_COMPETITOR';
      this.reprice.compliance_status = 'ALL';
    } else if (filterKey === 'blocked') {
      this.reprice.compliance_status = this.reprice.compliance_status === 'PROHIBITED' ? 'ALL' : 'PROHIBITED';
      this.reprice.buybox_status = 'ALL';
    } else if (filterKey === 'missing_floor') {
      this.reprice.buybox_status = this.reprice.buybox_status === 'MISSING_FLOOR' ? 'ALL' : 'MISSING_FLOOR';
    } else if (filterKey === 'active_reprice') {
      this.reprice.auto_reprice = this.reprice.auto_reprice === 'true' ? 'ALL' : 'true';
    }
    this.loadRepriceItems(1);
  },

  // 4.1 一键重置所有跟价筛选条件
  resetRepriceFilters() {
    this.reprice.source_type = 'ALL';
    this.reprice.buybox_status = 'ALL';
    this.reprice.compliance_status = 'ALL';
    this.reprice.monitor_status = 'ALL';
    this.reprice.auto_reprice = 'ALL';
    this.reprice.search = '';
    this.reprice.min_price = '';
    this.reprice.max_price = '';
    this.loadRepriceItems(1);
  },

  // 5. 来源筛选切换 (支持 Toggle 反选)
  changeRepriceSource(sourceType) {
    this.reprice.source_type = this.reprice.source_type === sourceType ? 'ALL' : sourceType;
    this.loadRepriceItems(1);
  },

  // 6. Buybox 筛选切换 (支持 Toggle 反选)
  changeRepriceBuybox(buyboxStatus) {
    this.reprice.buybox_status = this.reprice.buybox_status === buyboxStatus ? 'ALL' : buyboxStatus;
    this.loadRepriceItems(1);
  },

  // 7. 合规筛选切换 (支持 Toggle 反选)
  changeRepriceCompliance(complianceStatus) {
    this.reprice.compliance_status = this.reprice.compliance_status === complianceStatus ? 'ALL' : complianceStatus;
    this.loadRepriceItems(1);
  },

  // 8. 监控生命周期筛选切换 (支持 Toggle 反选)
  changeRepriceMonitor(monitorStatus) {
    this.reprice.monitor_status = this.reprice.monitor_status === monitorStatus ? 'ALL' : monitorStatus;
    this.loadRepriceItems(1);
  },

  // 8.1 自动跟价状态筛选切换 (支持 Toggle 反选)
  changeRepriceAuto(autoStatus) {
    this.reprice.auto_reprice = this.reprice.auto_reprice === autoStatus ? 'ALL' : autoStatus;
    this.loadRepriceItems(1);
  },

  // 9. 勾选当前页
  toggleSelectCurrentPageReprice() {
    if (this.isAllCurrentPageRepriceSelected) {
      const pageIds = (this.reprice.items || []).map(it => it.id);
      this.reprice.selectedIds = this.reprice.selectedIds.filter(id => !pageIds.includes(id));
      this.reprice.selectAll = false;
    } else {
      const pageIds = (this.reprice.items || []).map(it => it.id);
      const set = new Set([...this.reprice.selectedIds, ...pageIds]);
      this.reprice.selectedIds = Array.from(set);
    }
  },

  // 10. 跨页全选切换 (调用 /api/reprice/all-ids 获取全盘匹配 ID)
  async toggleSelectAllReprice() {
    this.reprice.selectAll = !this.reprice.selectAll;
    if (this.reprice.selectAll) {
      this.reprice.loadingAllIds = true;
      try {
        let url = `/api/reprice/all-ids?store_id=${this.selectedStoreId}`;
        if (this.reprice.source_type && this.reprice.source_type !== 'ALL') {
          url += `&source_type=${encodeURIComponent(this.reprice.source_type)}`;
        }
        if (this.reprice.buybox_status && this.reprice.buybox_status !== 'ALL') {
          url += `&buybox_status=${encodeURIComponent(this.reprice.buybox_status)}`;
        }
        if (this.reprice.compliance_status && this.reprice.compliance_status !== 'ALL') {
          url += `&compliance_status=${encodeURIComponent(this.reprice.compliance_status)}`;
        }
        if (this.reprice.auto_reprice && this.reprice.auto_reprice !== 'ALL') {
          url += `&auto_reprice=${encodeURIComponent(this.reprice.auto_reprice)}`;
        }
        if (this.reprice.monitor_status && this.reprice.monitor_status !== 'ALL') {
          url += `&monitor_status=${encodeURIComponent(this.reprice.monitor_status)}`;
        }
        if (this.reprice.search && this.reprice.search.trim()) {
          url += `&search=${encodeURIComponent(this.reprice.search.trim())}`;
        }
        if (this.reprice.min_price !== '' && this.reprice.min_price !== null && this.reprice.min_price !== undefined) {
          url += `&min_my_price=${encodeURIComponent(this.reprice.min_price)}`;
        }
        if (this.reprice.max_price !== '' && this.reprice.max_price !== null && this.reprice.max_price !== undefined) {
          url += `&max_my_price=${encodeURIComponent(this.reprice.max_price)}`;
        }

        const res = await fetch(url, { headers: this.getAuthHeaders() });
        if (res.ok) {
          const data = await res.json();
          this.reprice.selectedIds = data.ids || [];
          this.showToast(`⚡ 已成功勾选全盘 ${this.reprice.selectedIds.length} 件商品！`, 'success');
        } else {
          const err = await res.json().catch(() => ({}));
          this.showToast(`获取全盘商品失败: ${err.detail || res.statusText}`, 'error');
        }
      } catch (e) {
        this.showToast('获取全盘商品网络异常: ' + e.message, 'error');
      } finally {
        this.reprice.loadingAllIds = false;
      }
    } else {
      this.reprice.selectedIds = [];
    }
  },

  // 11. 单品即时巡检与跟价 (健壮容错 + 数据平铺更新)
  async triggerSingleReprice(item) {
    item._reprising = true;
    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/trigger-item/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (res.ok && data && data.success) {
        const curPrice = data.current_price || data.new_price || (data.result && (data.result.current_price || data.result.new_price)) || item.price;
        const action = data.action || (data.result && data.result.action) || 'INSPECTED';
        const reason = data.reason || (data.result && data.result.reason) || '';
        this.showToast(`🎯 [${item.seller_sku}] 巡检完成: ${action} (现价 R${curPrice})`, 'success');
        // 更新行内数据
        item.buybox_status = data.buybox_status || (data.result && data.result.buybox_status) || item.buybox_status;
        item.current_competitor_price = data.competitor_price !== undefined ? data.competitor_price : (data.result && data.result.competitor_price);
        item.current_competitor_seller = data.competitor_seller || (data.result && data.result.competitor_seller);
        item.price = curPrice;
        item.last_reprice_result = `${action}: ${reason}`;
        item.last_reprice_at = '刚刚';
      } else {
        this.showToast(`❌ 巡检失败: ${(data && (data.detail || data.reason)) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('即时巡检网络异常: ' + e.message, 'error');
    } finally {
      item._reprising = false;
    }
  },

  // 12. 批量即时跟价巡检 (异步调度并展示实时进度条)
  async triggerBatchReprice(targetIds = null) {
    const ids = (Array.isArray(targetIds) && targetIds.length > 0) ? targetIds : this.reprice.selectedIds;
    if (!ids || ids.length === 0) {
      this.showToast('请先勾选需要跟价巡检的商品', 'warning');
      return;
    }
    this.reprice.batchRunning = true;
    try {
      const { res, data } = await this.safeRepriceFetch('/api/reprice/trigger-batch', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ids: ids,
          store_id: this.selectedStoreId,
          force: false
        })
      });
      if (res.ok && data && (data.success || data.task_id)) {
        const taskId = data.task_id;
        if (taskId) {
          this.addOrUpdateBgTask({
            id: taskId,
            name: `批量即时巡检 (${ids.length} 件)`,
            task_type: 'FULL_CRUISE_INSPECTION',
            status: 'RUNNING',
            current: 0,
            total: ids.length,
            progress: 0,
            success_count: 0,
            fail_count: 0,
            blocked_count: 0,
            failed_items: [],
            breakdown: {}
          });
          this.reprice.currentCruiseTaskId = taskId;
          this.ensureBgTasksPolling();
        }
        this.showToast(`🚀 ${data.message || '批量巡检任务已启动'}`, 'success');
        this.reprice.selectedIds = [];
        this.reprice.selectAll = false;
        setTimeout(() => this.loadRepriceItems(this.reprice.page), 2500);
      } else {
        this.showToast(`❌ 启动批量巡检失败: ${(data && (data.detail || data.message)) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('批量巡检网络异常: ' + e.message, 'error');
    } finally {
      this.reprice.batchRunning = false;
    }
  },

  // 13. 全店大巡航 (异步调度并展示实时进度条)
  async triggerFullCruise() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择目标店铺', 'warning');
      return;
    }
    if (!confirm('确认立即对当前店铺全部在售跟价商品启动 24h 全速大巡航？')) return;

    this.reprice.fullCruiseRunning = true;
    try {
      const { res, data } = await this.safeRepriceFetch('/api/reprice/trigger-full-cruise', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          store_id: this.selectedStoreId,
          concurrency: 8
        })
      });
      if (res.ok && data && data.success) {
        const taskId = data.task_id;
        if (taskId) {
          this.addOrUpdateBgTask({
            id: taskId,
            name: `全店大巡航 [${data.store_name || '当前店铺'}] (${data.total || 0} 件)`,
            task_type: 'FULL_CRUISE_INSPECTION',
            status: 'RUNNING',
            current: 0,
            total: data.total || 0,
            progress: 0,
            success_count: 0,
            fail_count: 0,
            blocked_count: 0,
            failed_items: [],
            breakdown: {}
          });
          this.reprice.currentCruiseTaskId = taskId;
          this.ensureBgTasksPolling();
        }
        this.showToast(`🔥 ${data.message || '全店大巡航已启动'}！`, 'success');
        setTimeout(() => this.loadRepriceItems(1), 3000);
      } else {
        this.showToast(`❌ 启动全店巡航失败: ${(data && (data.detail || data.message)) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('全店大巡航网络异常: ' + e.message, 'error');
    } finally {
      this.reprice.fullCruiseRunning = false;
    }
  },

  // 获取当前与跟价监控相关的巡检任务 (优先运行中，或最近刚结束)
  getRepriceCruiseTask() {
    if (!this.activeBgTasks || this.activeBgTasks.length === 0) return null;
    if (this.reprice && this.reprice.currentCruiseTaskId) {
      const found = this.activeBgTasks.find(t => t.id === this.reprice.currentCruiseTaskId);
      if (found) return found;
    }
    const running = this.activeBgTasks.find(t => t.task_type === 'FULL_CRUISE_INSPECTION' && t.status === 'RUNNING');
    if (running) return running;
    return this.activeBgTasks.find(t => t.task_type === 'FULL_CRUISE_INSPECTION') || null;
  },

  // 重试巡检失败的商品项
  async retryFailedCruiseItems(task) {
    if (!task || !task.failed_items || task.failed_items.length === 0) {
      this.showToast('当前任务中暂无明确记录的失败项', 'info');
      return;
    }
    const failedIds = task.failed_items.map(x => x.id).filter(Boolean);
    if (failedIds.length === 0) {
      this.showToast('失败项缺少可识别的商品 ID', 'warning');
      return;
    }
    if (!confirm(`确定重新对巡检失败的 ${failedIds.length} 件商品发起即时巡检探测吗？`)) return;
    await this.triggerBatchReprice(failedIds);
  },

  // 关闭巡检进度条卡片
  dismissRepriceCruiseTask(task) {
    if (!task) return;
    this.dismissBgTask(task.id);
    if (this.reprice && this.reprice.currentCruiseTaskId === task.id) {
      this.reprice.currentCruiseTaskId = null;
    }
  },

  // 切换失败明细列表折叠展开
  toggleCruiseFailureDetails() {
    this.reprice.showCruiseFailureDetails = !this.reprice.showCruiseFailureDetails;
  },

  // 14. 行内开关自动跟价
  async toggleItemAutoReprice(item) {
    const nextVal = !item.auto_reprice;
    const oldVal = item.auto_reprice;
    item.auto_reprice = nextVal;
    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/config/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ auto_reprice: nextVal })
      });
      if (res.ok && data && data.success) {
        this.showToast(`✅ [${item.seller_sku}] 自动跟价已${nextVal ? '开启' : '关闭'}`, 'success');
      } else {
        item.auto_reprice = oldVal;
        this.showToast(`❌ 设置失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      item.auto_reprice = oldVal;
      this.showToast('网络异常: ' + e.message, 'error');
    }
  },

  // 14.1 行内快速修改跟价策略 (彻底修复手动修改后刷新丢失与 CUSTOM 丢失问题)
  async saveInlineStrategy(item, targetStrategy) {
    let finalStrategy = targetStrategy;
    if (finalStrategy === '__CUSTOM_INPUT__') {
      const input = prompt(
        `请输入单品 [${item.seller_sku}] 自定义跟价差额或比例：\n\n• 输入数值例如 1.5 或 -1.5 表示比竞对低 R1.5\n• 输入例如 +2 表示比竞对高 R2\n• 输入例如 3% 或 pct:3 表示比竞对低 3%`,
        '-1.0'
      );
      if (!input || !input.trim()) {
        await this.loadRepriceItems(this.reprice.page);
        return;
      }
      const raw = input.trim();
      if (raw.endsWith('%') || raw.toLowerCase().startsWith('pct:')) {
        const num = parseFloat(raw.replace('%', '').replace(/pct:/i, ''));
        if (isNaN(num) || num <= 0) {
          this.showToast('请输入有效的让利百分比 (大于 0)', 'warning');
          await this.loadRepriceItems(this.reprice.page);
          return;
        }
        finalStrategy = `PERCENT_${num}`;
      } else {
        let num = parseFloat(raw);
        if (isNaN(num)) {
          this.showToast('请输入有效的差额数值', 'warning');
          await this.loadRepriceItems(this.reprice.page);
          return;
        }
        let diff = num;
        if (!raw.startsWith('+') && !raw.startsWith('-')) {
          diff = -Math.abs(num);
        }
        finalStrategy = `CUSTOM:${diff > 0 ? '+' : ''}${diff}`;
      }
    }

    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/config/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ price_strategy: finalStrategy })
      });
      if (res.ok && data && data.success) {
        item.price_strategy = finalStrategy;
        this.showToast(`✅ [${item.seller_sku}] 跟价策略已更新为: ${this.formatRepriceStrategy(finalStrategy)}`, 'success');
      } else {
        this.showToast(`❌ 策略更新失败: ${(data && data.detail) || '未知错误'}`, 'error');
        await this.loadRepriceItems(this.reprice.page);
      }
    } catch (e) {
      this.showToast('更新策略网络异常: ' + e.message, 'error');
      await this.loadRepriceItems(this.reprice.page);
    }
  },

  // 15. 行内快速修改底价
  async saveInlineFloor(item) {
    const floor = parseFloat(item.min_price_floor);
    if (isNaN(floor) || floor < 0) {
      this.showToast('请输入有效的保本底价 (>= 0)', 'warning');
      return;
    }
    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/config/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ min_price_floor: floor })
      });
      if (res.ok && data && data.success) {
        this.showToast(`✅ [${item.seller_sku}] 底价已更新为 R${floor}`, 'success');
      } else {
        this.showToast(`❌ 底价更新失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('更新底价网络异常: ' + e.message, 'error');
    }
  },

  // 16. 打开批量策略弹窗 (初始化全量预设与自定义输入参数)
  openBatchRepriceStrategyModal() {
    if (this.reprice.selectedIds.length === 0) {
      this.showToast('请先勾选需要调整策略的商品', 'warning');
      return;
    }
    this.batchRepriceStrategyForm = {
      mode: 'PRESET', // 'PRESET' | 'CUSTOM_AMOUNT' | 'CUSTOM_PERCENT'
      preset: 'MINUS_15',
      custom_direction: '-',
      custom_amount: 1.5,
      custom_percent: 3.0,
      price_strategy: 'MINUS_15',
      submitting: false
    };
    this.showBatchRepriceStrategyModal = true;
  },

  // 17. 提交批量策略修改 (支持预设、自定义金额与百分比)
  async submitBatchRepriceStrategy() {
    this.batchRepriceStrategyForm.submitting = true;
    try {
      let finalStrategy = 'MINUS_15';
      const mode = this.batchRepriceStrategyForm.mode;
      if (mode === 'PRESET') {
        finalStrategy = this.batchRepriceStrategyForm.preset || 'MINUS_15';
      } else if (mode === 'CUSTOM_AMOUNT') {
        const amt = parseFloat(this.batchRepriceStrategyForm.custom_amount);
        if (isNaN(amt) || amt < 0) {
          this.showToast('请输入有效的自定义差额 (>= 0)', 'warning');
          this.batchRepriceStrategyForm.submitting = false;
          return;
        }
        const dir = this.batchRepriceStrategyForm.custom_direction === '+' ? '+' : '-';
        finalStrategy = `CUSTOM:${dir}${Math.abs(amt)}`;
      } else if (mode === 'CUSTOM_PERCENT') {
        const pct = parseFloat(this.batchRepriceStrategyForm.custom_percent);
        if (isNaN(pct) || pct <= 0) {
          this.showToast('请输入有效的让利百分比 (> 0)', 'warning');
          this.batchRepriceStrategyForm.submitting = false;
          return;
        }
        finalStrategy = `PERCENT_${pct}`;
      }

      const { res, data } = await this.safeRepriceFetch('/api/reprice/batch-strategy', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ids: this.reprice.selectedIds,
          price_strategy: finalStrategy
        })
      });
      if (res.ok && data && data.success) {
        this.showToast(`🎉 成功更新 ${data.updated_count} 件商品的跟价策略为 [${this.formatRepriceStrategy(finalStrategy)}]！`, 'success');
        this.showBatchRepriceStrategyModal = false;
        this.reprice.selectedIds = [];
        this.reprice.selectAll = false;
        await this.loadRepriceItems(this.reprice.page);
      } else {
        this.showToast(`❌ 批量策略设置失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('网络异常: ' + e.message, 'error');
    } finally {
      this.batchRepriceStrategyForm.submitting = false;
    }
  },

  // 18. 打开批量底价弹窗
  openBatchRepriceFloorModal() {
    if (this.reprice.selectedIds.length === 0) {
      this.showToast('请先勾选需要批量设底价的商品', 'warning');
      return;
    }
    this.batchRepriceFloorForm = {
      mode: 'PERCENT', // 'PERCENT' | 'FIXED'
      percent: 75,
      fixed_floor: 100,
      submitting: false
    };
    this.showBatchRepriceFloorModal = true;
  },

  // 19. 提交批量底价设置 (后端服务端全量批量计算，支持跨页全选)
  async submitBatchRepriceFloor() {
    this.batchRepriceFloorForm.submitting = true;
    try {
      const isPercent = this.batchRepriceFloorForm.mode === 'PERCENT';
      const pct = parseFloat(this.batchRepriceFloorForm.percent);
      const fixed = parseFloat(this.batchRepriceFloorForm.fixed_floor);

      if (isPercent && (isNaN(pct) || pct <= 0)) {
        this.showToast('请输入有效的售价比例 (> 0)', 'warning');
        this.batchRepriceFloorForm.submitting = false;
        return;
      }
      if (!isPercent && (isNaN(fixed) || fixed < 0)) {
        this.showToast('请输入有效的固定底价 (>= 0)', 'warning');
        this.batchRepriceFloorForm.submitting = false;
        return;
      }

      const payload = {
        ids: this.reprice.selectedIds,
        mode: this.batchRepriceFloorForm.mode,
        percent: isPercent ? pct : undefined,
        min_price_floor: !isPercent ? fixed : undefined
      };

      const { res, data } = await this.safeRepriceFetch('/api/reprice/batch-floor', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
      });

      if (res.ok && data && data.success) {
        this.showToast(`🎉 成功更新 ${data.updated_count} 件商品的保本底价！`, 'success');
        this.showBatchRepriceFloorModal = false;
        this.reprice.selectedIds = [];
        this.reprice.selectAll = false;
        await this.loadRepriceItems(this.reprice.page);
      } else {
        this.showToast(`❌ 批量底价设置失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('批量底价设置异常: ' + e.message, 'error');
    } finally {
      this.batchRepriceFloorForm.submitting = false;
    }
  },

  // 20. 批量启停自动跟价
  async batchToggleReprice(enable) {
    if (this.reprice.selectedIds.length === 0) {
      this.showToast('请先勾选需要操作的商品', 'warning');
      return;
    }
    try {
      const { res, data } = await this.safeRepriceFetch('/api/reprice/batch-toggle', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ids: this.reprice.selectedIds,
          auto_reprice: enable
        })
      });
      if (res.ok && data && data.success) {
        this.showToast(`🎉 成功${enable ? '开启' : '关闭'} ${data.updated_count} 件商品的自动跟价`, 'success');
        this.reprice.selectedIds = [];
        this.reprice.selectAll = false;
        await this.loadRepriceItems(this.reprice.page);
      } else {
        this.showToast(`❌ 批量切换失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('网络异常: ' + e.message, 'error');
    }
  },

  // 20.1 打开批量下架弹窗
  openBatchDelistModal() {
    if (this.reprice.selectedIds.length === 0 && !this.reprice.selectAll) {
      this.showToast('请先勾选需要下架的商品', 'warning');
      return;
    }
    this.batchDelistForm = {
      reason_category: 'MANUAL_OFFLINE',
      reason_note: '',
      submitting: false
    };
    this.showBatchDelistModal = true;
  },

  // 20.2 提交批量下架
  async submitBatchDelist() {
    if (this.reprice.selectedIds.length === 0 && !this.reprice.selectAll) {
      this.showToast('请先勾选需要下架的商品', 'warning');
      return;
    }
    this.batchDelistForm.submitting = true;
    try {
      const payload = {
        store_id: this.selectedStoreId,
        ids: this.reprice.selectedIds,
        select_all: !!this.reprice.selectAll,
        filters: {
          store_id: this.selectedStoreId,
          source_type: this.reprice.source_type,
          buybox_status: this.reprice.buybox_status,
          compliance_status: this.reprice.compliance_status,
          auto_reprice: this.reprice.auto_reprice,
          monitor_status: this.reprice.monitor_status,
          search: this.reprice.search,
          min_my_price: this.reprice.min_price,
          max_my_price: this.reprice.max_price
        },
        reason_category: this.batchDelistForm.reason_category || 'MANUAL_OFFLINE',
        note: this.batchDelistForm.reason_note || ''
      };

      const { res, data } = await this.safeRepriceFetch('/api/reprice/batch-delist', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
      });

      if (res.ok && data && data.success) {
        this.showToast(`🎉 ${data.message || `成功下架 ${data.delisted_count} 件商品！`}`, 'success');
        this.showBatchDelistModal = false;
        this.reprice.selectedIds = [];
        this.reprice.selectAll = false;
        await this.loadRepriceItems(1);
      } else {
        this.showToast(`❌ 批量下架失败: ${(data && data.detail) || (data && data.message) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('批量下架异常: ' + e.message, 'error');
    } finally {
      this.batchDelistForm.submitting = false;
    }
  },

  // 21. 单品侵权长效复检
  async checkSingleRepriceCompliance(item) {
    item._checkingCompliance = true;
    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/compliance-check/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (res.ok && data && data.success) {
        item.compliance_status = data.status;
        item.compliance_details = data.details;
        item.last_compliance_check_at = '刚刚';
        if (this.currentRepriceComplianceItem && this.currentRepriceComplianceItem.id === item.id) {
          this.currentRepriceComplianceItem.compliance_status = data.status;
          this.currentRepriceComplianceItem.compliance_details = data.details;
        }
        if (data.is_blocked) {
          item.monitor_status = 'BLOCKED';
          item.auto_reprice = false;
          this.showToast(`🚨 [${item.seller_sku}] 侵权复检命中禁售红线！已自动触发安全熔断暂停调价！`, 'error');
        } else if (data.status === 'RISK') {
          this.showToast(`🟡 [${item.seller_sku}] 侵权复检提示有中低风险，请关注`, 'warning');
        } else {
          this.showToast(`🟢 [${item.seller_sku}] 侵权复检合格通过，安全！`, 'success');
        }
      } else {
        this.showToast(`❌ 合规复检失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('合规复检异常: ' + e.message, 'error');
    } finally {
      item._checkingCompliance = false;
    }
  },

  // 21.1 打开 AI 合规与侵权质检详情弹窗
  openRepriceComplianceDetails(item) {
    let details = item.compliance_details;
    if (typeof details === 'string') {
      try { details = JSON.parse(details); } catch(e) { details = {}; }
    }
    this.currentRepriceComplianceItem = { ...item, compliance_details: details };
    this.repriceArbitrationNotes = (details && details.human_arbitration && details.human_arbitration.notes) || '';
    this.showRepriceComplianceModal = true;
  },

  // 21.2 提交人工终审裁定并双向同步跟品库
  async submitRepriceArbitration(item, verdict) {
    if (!item) return;
    this.isArbitratingReprice = true;
    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/arbitrate/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          human_verdict: verdict,
          human_notes: this.repriceArbitrationNotes || ''
        })
      });
      if (res.ok && data && data.success) {
        item.compliance_status = data.compliance_status;
        item.compliance_details = data.compliance_details;
        if (this.currentRepriceComplianceItem && this.currentRepriceComplianceItem.id === item.id) {
          this.currentRepriceComplianceItem.compliance_status = data.compliance_status;
          this.currentRepriceComplianceItem.compliance_details = data.compliance_details;
        }
        if (verdict === 'PROHIBITED') {
          item.monitor_status = 'BLOCKED';
          item.auto_reprice = false;
        } else if (verdict === 'SAFE' && item.monitor_status === 'BLOCKED') {
          item.monitor_status = 'ACTIVE';
          item.auto_reprice = true;
        }
        this.showToast(`终审裁定完成: 已裁定为 [${verdict}]，并已双向同步跟品池`, 'success');
        await this.loadRepriceItems(this.reprice.page);
      } else {
        this.showToast(`❌ 终审裁定失败: ${(data && (data.detail || data.message)) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('终审网络异常: ' + e.message, 'error');
    } finally {
      this.isArbitratingReprice = false;
    }
  },

  // 22. 批量侵权长效复检
  async batchCheckRepriceCompliance() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择目标店铺', 'warning');
      return;
    }
    const targetIds = this.reprice.selectedIds.length > 0 ? this.reprice.selectedIds : null;
    const confirmMsg = targetIds 
      ? `确认对已勾选的 ${targetIds.length} 件在售监控商品发起批量侵权长效复检？` 
      : `确认对当前店铺全部在售监控商品发起全量侵权长效复检？`;
    if (!confirm(confirmMsg)) return;

    try {
      const { res, data } = await this.safeRepriceFetch('/api/reprice/batch-compliance-check', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ids: targetIds,
          store_id: targetIds ? null : this.selectedStoreId
        })
      });
      if (res.ok && data && data.success) {
        this.showToast(`🛡️ ${data.message}！如有侵权将自动熔断`, 'success');
        this.reprice.selectedIds = [];
        this.reprice.selectAll = false;
      } else {
        this.showToast(`❌ 启动合规复检失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('批量复检网络异常: ' + e.message, 'error');
    }
  },

  // 23. 熔断/禁售商品一键官方下架
  openQuickDelistModal(item) {
    this.quickDelistForm = {
      item: item,
      reason_category: 'INFRINGING_RISK',
      reason_note: `侵权风控熔断：SKU [${item.seller_sku}] 命中文本/品牌/图像禁售红线，官方紧急下架避险`,
      submitting: false
    };
    this.showQuickDelistModal = true;
  },

  // 24. 提交一键官方下架
  async submitQuickDelist() {
    if (!this.quickDelistForm.item) return;
    const item = this.quickDelistForm.item;
    this.quickDelistForm.submitting = true;
    try {
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/quick-delist/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (res.ok && data && data.success) {
        this.showToast(`🛑 [${item.seller_sku}] 已成功从 Makro 官方下架并同步置为已下架！`, 'success');
        this.showQuickDelistModal = false;
        await this.loadRepriceItems(this.reprice.page);
      } else {
        this.showToast(`❌ 官方下架失败: ${(data && (data.detail || data.message)) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('官方下架网络异常: ' + e.message, 'error');
    } finally {
      this.quickDelistForm.submitting = false;
    }
  },

  // 25. 打开单品高级配置弹窗
  openRepriceConfigModal(item) {
    const rawSt = item.price_strategy || 'MINUS_15';
    let customDir = '-';
    let customAmt = 1.5;
    let customPct = 3.0;
    if (rawSt.startsWith('CUSTOM:')) {
      const val = parseFloat(rawSt.replace('CUSTOM:', ''));
      if (!isNaN(val)) {
        customDir = val >= 0 ? '+' : '-';
        customAmt = Math.abs(val);
      }
    } else if (rawSt.startsWith('PERCENT_')) {
      const val = parseFloat(rawSt.replace('PERCENT_', ''));
      if (!isNaN(val)) {
        customPct = val;
      }
    }

    this.repriceConfigForm = {
      id: item.id,
      seller_sku: item.seller_sku,
      title: item.title,
      price: item.price,
      target_fsn: item.target_fsn || '',
      target_makro_url: item.target_makro_url || '',
      min_price_floor: item.min_price_floor || 0,
      max_price_ceiling: item.max_price_ceiling || 0,
      price_strategy: rawSt,
      custom_direction: customDir,
      custom_amount: customAmt,
      custom_percent: customPct,
      auto_reprice: item.auto_reprice,
      submitting: false
    };
    this.showRepriceConfigModal = true;
  },

  // 26. 提交单品配置
  async submitRepriceConfig() {
    this.repriceConfigForm.submitting = true;
    try {
      let finalStrategy = this.repriceConfigForm.price_strategy;
      if (finalStrategy === '__CUSTOM_AMOUNT__') {
        const amt = parseFloat(this.repriceConfigForm.custom_amount);
        const dir = this.repriceConfigForm.custom_direction === '+' ? '+' : '-';
        finalStrategy = `CUSTOM:${dir}${Math.abs(amt || 1.0)}`;
      } else if (finalStrategy === '__CUSTOM_PERCENT__') {
        const pct = parseFloat(this.repriceConfigForm.custom_percent);
        finalStrategy = `PERCENT_${Math.abs(pct || 3.0)}`;
      }

      const payload = {
        target_fsn: this.repriceConfigForm.target_fsn,
        target_makro_url: this.repriceConfigForm.target_makro_url,
        min_price_floor: parseFloat(this.repriceConfigForm.min_price_floor),
        max_price_ceiling: parseFloat(this.repriceConfigForm.max_price_ceiling || 0),
        price_strategy: finalStrategy,
        auto_reprice: this.repriceConfigForm.auto_reprice
      };
      const { res, data } = await this.safeRepriceFetch(`/api/reprice/config/${this.repriceConfigForm.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
      });
      if (res.ok && data && data.success) {
        this.showToast(`✅ [${this.repriceConfigForm.seller_sku}] 跟价配置保存成功！`, 'success');
        this.showRepriceConfigModal = false;
        await this.loadRepriceItems(this.reprice.page);
      } else {
        this.showToast(`❌ 保存配置失败: ${(data && data.detail) || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('保存配置网络异常: ' + e.message, 'error');
    } finally {
      this.repriceConfigForm.submitting = false;
    }
  },

  // 27. 打开巡航与调价日志抽屉
  async openRepriceLogsDrawer(item = null) {
    this.repriceLogsDrawer = {
      show: true,
      item: item,
      loading: true,
      items: [],
      total: 0,
      page: 1,
      pageSize: 20
    };
    await this.loadRepriceLogs(1);
  },

  // 28. 加载跟价日志
  async loadRepriceLogs(page = 1) {
    this.repriceLogsDrawer.loading = true;
    this.repriceLogsDrawer.page = page;
    try {
      let url = `/api/reprice/logs?page=${page}&page_size=${this.repriceLogsDrawer.pageSize}`;
      if (this.selectedStoreId) url += `&store_id=${this.selectedStoreId}`;
      if (this.repriceLogsDrawer.item) url += `&reprice_id=${this.repriceLogsDrawer.item.id}`;

      const { res, data } = await this.safeRepriceFetch(url, { headers: this.getAuthHeaders() });
      if (res.ok && data) {
        this.repriceLogsDrawer.items = data.items || [];
        this.repriceLogsDrawer.total = data.total || 0;
      }
    } catch (e) {
      console.error('加载跟价日志异常:', e);
    } finally {
      this.repriceLogsDrawer.loading = false;
    }
  },

  // 29. 辅助格式化方法
  formatRepriceSource(type) {
    const map = {
      'PIGGYBACK': '🎯 跟品挂靠',
      'SELF_PUBLISHED': '📦 选品自建',
      'STORE_SYNCED': '🏬 店铺老品'
    };
    return map[type] || type || '未知';
  },

  formatBuyboxStatus(st) {
    const map = {
      'WINNING': { label: '🥇 夺车赢价', bg: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
      'LOSING': { label: '🚨 被抢车', bg: 'bg-rose-50 text-rose-700 border-rose-200 animate-pulse' },
      'FLOOR_HIT': { label: '🛡️ 触底保本', bg: 'bg-amber-50 text-amber-700 border-amber-200' },
      'NO_COMPETITOR': { label: '💎 独家在售', bg: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
      'MISSING_FLOOR': { label: '⚠️ 缺底价高危', bg: 'bg-orange-50 text-orange-700 border-orange-200' },
      'BLOCKED': { label: '🛑 熔断拦截', bg: 'bg-rose-100 text-rose-800 border-rose-300' },
      'UNKNOWN': { label: '⏳ 待巡检', bg: 'bg-slate-100 text-slate-600 border-slate-200' }
    };
    return map[st] || { label: st || '未知', bg: 'bg-slate-100 text-slate-600 border-slate-200' };
  },

  formatRepriceStrategy(st) {
    if (!st) return '⚡ 低 R1.00';
    const map = {
      'MINUS_15': '🔥 低 R15.00',
      'MINUS_2': '🚀 低 R2.00',
      'MINUS_1': '⚡ 低 R1.00',
      'MINUS_0.5': '⚡ 低 R0.50',
      'PERCENT_2': '📊 低 2%',
      'PERCENT_5': '📊 低 5%',
      'MANUAL': '🤝 平价跟卖 (R0.00)'
    };
    if (map[st]) return map[st];
    if (st.startsWith('CUSTOM:')) {
      const val = parseFloat(st.replace('CUSTOM:', ''));
      if (!isNaN(val)) {
        if (val < 0) return `✏️ 自定义 低 R${Math.abs(val).toFixed(2)}`;
        if (val > 0) return `✏️ 自定义 高 R${val.toFixed(2)}`;
        return `🤝 平价跟卖 (R0.00)`;
      }
    }
    if (st.startsWith('PERCENT_')) {
      const val = parseFloat(st.replace('PERCENT_', ''));
      if (!isNaN(val)) return `📊 自定义 低 ${val}%`;
    }
    if (st.startsWith('MINUS_')) {
      const val = parseFloat(st.replace('MINUS_', '').replace('_', '.'));
      if (!isNaN(val)) return `⚡ 低 R${val.toFixed(2)}`;
    }
    return st;
  },

  // 18. 行内直接调整售价并全端三表联动
  async handleInlinePriceChange(item, rawVal) {
    try {
      const p = parseFloat(rawVal);
      const oldP = parseFloat(item.price || item.ssp || 0);
      if (isNaN(p) || p <= 0) {
        this.showToast('请输入有效的售价 (> 0)', 'warning');
        return;
      }
      if (Math.abs(p - oldP) < 0.001) return;

      const res = await fetch('/api/reprice/adjust-price', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          id: item.id,
          new_price: p,
          sync_makro: true,
          force: false
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        item.price = data.new_price;
        item.ssp = data.new_price;
        if (item.listing) item.listing.ssp = data.new_price;
        this.showToast(`✅ [${item.seller_sku}] 售价已同步调整为 R${data.new_price.toFixed(2)}`, 'success');
      } else if (data && data.below_floor) {
        this.priceFloorWarning = {
          type: 'reprice',
          item: item,
          new_price: p,
          min_floor: data.min_price_floor,
          title: item.title || item.seller_sku
        };
        this.showPriceFloorWarningModal = true;
      } else {
        this.showToast(`❌ 调整售价失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('调整售价网络异常: ' + e.message, 'error');
    } finally {
      delete item._edit_price;
    }
  },

  // 19. 确认强制修改低于底价的售价
  async confirmForceAdjustPrice() {
    if (!this.priceFloorWarning || !this.priceFloorWarning.item) return;
    const w = this.priceFloorWarning;
    const item = w.item;
    try {
      if (w.type === 'reprice') {
        const res = await fetch('/api/reprice/adjust-price', {
          method: 'POST',
          headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            id: item.id,
            new_price: w.new_price,
            sync_makro: true,
            force: true
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          item.price = data.new_price;
          item.ssp = data.new_price;
          if (item.listing) item.listing.ssp = data.new_price;
          this.showToast(`⚠️ [${item.seller_sku}] 已强制调价为 R${data.new_price.toFixed(2)} (低于保本底价)`, 'warning');
        } else {
          this.showToast(`❌ 调价失败: ${data.detail || data.message || '未知错误'}`, 'error');
        }
      } else if (w.type === 'store_product') {
        const res = await fetch('/api/store-products/adjust-price', {
          method: 'POST',
          headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            store_id: this.selectedStoreId,
            listing_id: item.id,
            sku_id: item.sku_id,
            new_price: w.new_price,
            sync_makro: true,
            force: true
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          item.ssp = data.new_price;
          this.showToast(`⚠️ [${item.sku_id}] 已强制调价为 R${data.new_price.toFixed(2)} (低于保本底价)`, 'warning');
        } else {
          this.showToast(`❌ 调价失败: ${data.detail || data.message || '未知错误'}`, 'error');
        }
      }
    } catch (e) {
      this.showToast('强制调价网络异常: ' + e.message, 'error');
    } finally {
      this.showPriceFloorWarningModal = false;
    }
  },

  // 20. 打开批量改价弹窗 (支持跟价大盘与店铺商品双入口)
  openBatchAdjustPriceModal(source = 'reprice') {
    const sel = source === 'reprice' ? this.reprice.selectedIds : this.selectedStoreProductSkus;
    if (!sel || sel.length === 0) {
      this.showToast('请先勾选需要调整售价的商品', 'warning');
      return;
    }
    this.batchAdjustPriceForm = {
      source: source,
      mode: 'FIXED',
      value: 99.0,
      sync_makro: true,
      force: false,
      submitting: false
    };
    this.showBatchAdjustPriceModal = true;
  },

  // 21. 提交批量改价
  async submitBatchAdjustPrice() {
    this.batchAdjustPriceForm.submitting = true;
    const form = this.batchAdjustPriceForm;
    try {
      let url = '';
      let payload = {};
      if (form.source === 'reprice') {
        url = '/api/reprice/batch-adjust-price';
        payload = {
          store_id: this.selectedStoreId,
          ids: this.reprice.selectedIds,
          mode: form.mode,
          value: parseFloat(form.value),
          sync_makro: form.sync_makro,
          force: form.force
        };
      } else {
        url = '/api/store-products/batch-adjust-price';
        payload = {
          store_id: this.selectedStoreId,
          sku_ids: this.selectedStoreProductSkus,
          mode: form.mode,
          value: parseFloat(form.value),
          sync_makro: form.sync_makro,
          force: form.force
        };
      }

      const res = await fetch(url, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        this.showBatchAdjustPriceModal = false;
        if (form.source === 'reprice') {
          await this.loadRepriceItems(this.reprice.page);
        } else {
          await this.loadStoreProducts(this.storeProducts.page);
        }
      } else {
        this.showToast(`❌ 批量调价失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('批量调价网络异常: ' + e.message, 'error');
    } finally {
      this.batchAdjustPriceForm.submitting = false;
    }
  }

};

export default repriceMethods;
