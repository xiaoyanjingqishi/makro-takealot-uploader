/**
 * 业务模块: store_products
 * 导出该领域的业务方法集
 */
export const store_productsMethods = {
  changeStoreProductState(stKey) {
    this.storeProducts.internal_state = stKey;
    this.loadStoreProducts(1);
  },

  async loadStoreProductVerticals() {
    if (!this.selectedStoreId) return;
    try {
      const res = await fetch(`/api/store-products/verticals?store_id=${this.selectedStoreId}`, {
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        this.storeProducts.verticalsList = data.verticals || [];
      }
    } catch (e) {
      console.error('加载类目列表失败:', e);
    }
  },

  resetStoreProductFilters() {
    this.storeProducts.vertical = 'ALL';
    this.storeProducts.inventory_status = 'ALL';
    this.storeProducts.min_inventory = '';
    this.storeProducts.max_inventory = '';
    this.storeProducts.min_price = '';
    this.storeProducts.max_price = '';
    this.storeProducts.date_range = 'ALL';
    this.storeProducts.sync_start = '';
    this.storeProducts.sync_end = '';
    this.storeProducts.sort_by = 'SYNCED_DESC';
    this.storeProducts.search = '';
    this.loadStoreProducts(1);
  },

  onStoreProductDateRangeChange() {
    if (this.storeProducts.date_range !== 'custom') {
      this.storeProducts.sync_start = '';
      this.storeProducts.sync_end = '';
      this.loadStoreProducts(1);
    }
  },

  getStoreProductVerticalName(vCode) {
    const found = (this.storeProducts.verticalsList || []).find(v => v.code === vCode);
    return found ? `${found.name} (${found.count})` : vCode;
  },

  formatStoreInventoryStatusLabel(st) {
    if (st === 'IN_STOCK') return '🟢 充足在库 (>10)';
    if (st === 'LOW_STOCK') return '🟡 低库存 (1~10)';
    if (st === 'OUT_OF_STOCK') return '🔴 缺货售罄 (0)';
    return st;
  },

  formatStoreProductDateRangeLabel(range) {
    if (range === 'today') return '今天同步';
    if (range === '3d') return '近 3 天';
    if (range === '7d') return '近 7 天';
    if (range === '30d') return '近 30 天';
    if (range === 'custom') {
      return `${this.storeProducts.sync_start || '起点'} ~ ${this.storeProducts.sync_end || '至今'}`;
    }
    return range;
  },

  async loadStoreProducts(page = 1) {
    if (!this.selectedStoreId) return;
    this.storeProducts.loading = true;
    this.storeProducts.page = page;
    try {
      let url = `/api/store-products/list?store_id=${this.selectedStoreId}&page=${page}&page_size=${this.storeProducts.pageSize}`;
      if (this.storeProducts.internal_state) {
        url += `&internal_state=${encodeURIComponent(this.storeProducts.internal_state)}`;
      }
      if (this.storeProducts.search) {
        url += `&search=${encodeURIComponent(this.storeProducts.search)}`;
      }
      if (this.storeProducts.vertical && this.storeProducts.vertical !== 'ALL') {
        url += `&vertical=${encodeURIComponent(this.storeProducts.vertical)}`;
      }
      if (this.storeProducts.inventory_status && this.storeProducts.inventory_status !== 'ALL') {
        url += `&inventory_status=${encodeURIComponent(this.storeProducts.inventory_status)}`;
      }
      if (this.storeProducts.min_inventory !== '' && this.storeProducts.min_inventory !== null && this.storeProducts.min_inventory !== undefined) {
        url += `&min_inventory=${encodeURIComponent(this.storeProducts.min_inventory)}`;
      }
      if (this.storeProducts.max_inventory !== '' && this.storeProducts.max_inventory !== null && this.storeProducts.max_inventory !== undefined) {
        url += `&max_inventory=${encodeURIComponent(this.storeProducts.max_inventory)}`;
      }
      if (this.storeProducts.min_price !== '' && this.storeProducts.min_price !== null && this.storeProducts.min_price !== undefined) {
        url += `&min_price=${encodeURIComponent(this.storeProducts.min_price)}`;
      }
      if (this.storeProducts.max_price !== '' && this.storeProducts.max_price !== null && this.storeProducts.max_price !== undefined) {
        url += `&max_price=${encodeURIComponent(this.storeProducts.max_price)}`;
      }
      if (this.storeProducts.date_range && this.storeProducts.date_range !== 'ALL') {
        url += `&date_range=${encodeURIComponent(this.storeProducts.date_range)}`;
      }
      if (this.storeProducts.sync_start) {
        url += `&sync_start=${encodeURIComponent(this.storeProducts.sync_start)}`;
      }
      if (this.storeProducts.sync_end) {
        url += `&sync_end=${encodeURIComponent(this.storeProducts.sync_end)}`;
      }
      if (this.storeProducts.sort_by) {
        url += `&sort_by=${encodeURIComponent(this.storeProducts.sort_by)}`;
      }
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.storeProducts.items = data.items || [];
        this.storeProducts.total = data.total || 0;
        this.storeProducts.counts = data.state_counts || {};
      } else if (res.status === 401) {
        this.checkAuth();
      } else {
        const err = await res.json();
        this.showToast(`❌ 加载商品失败: ${err.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      console.error('加载店铺商品失败:', e);
    } finally {
      this.storeProducts.loading = false;
    }
  },


  async syncStoreProducts() {
    if (!this.selectedStoreId) {
      alert('请先选择要同步的店铺');
      return;
    }
    this.syncingStoreProducts = true;
    try {
      const res = await fetch(`/api/store-products/sync?store_id=${this.selectedStoreId}&concurrency=8&async_task=true`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        if (data.task_id) {
          const storeObj = (this.stores || []).find(s => s.id === this.selectedStoreId);
          const storeName = storeObj ? storeObj.name : `店铺 ${this.selectedStoreId}`;
          this.addOrUpdateBgTask({
            id: data.task_id,
            task_type: 'SYNC_STORE_LISTINGS',
            name: `同步官方商品 [${storeName}] (8线程)`,
            status: 'RUNNING',
            total: 0,
            current: 0,
            progress: 0,
            current_title: '正在探测商品总数并启动并发抓取...',
            message: data.message
          });
          this.ensureBgTasksPolling();
          this.showToast('🚀 已在后台启动多线程同步官方商品，可在顶部任务栏查看实时进度', 'info');
        } else {
          this.showToast(`🎉 ${data.message}`, 'success');
          if (data.state_counts) {
            this.storeProducts.counts = data.state_counts;
          }
          await this.loadStoreProducts(1);
        }
      } else {
        alert('同步商品失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('同步官方商品网络异常: ' + e);
    } finally {
      this.syncingStoreProducts = false;
    }
  },


  startEditInventory(item) {
    this.editingInventoryId = item.id;
    this.editingInventoryVal = item.inventory;
  },


  async saveInlineInventory(item) {
    if (this.editingInventoryId !== item.id) return;
    const newQty = parseInt(this.editingInventoryVal, 10);
    this.editingInventoryId = null;
    if (isNaN(newQty) || newQty < 0) {
      this.showToast('库存必须为非负整数', 'warning');
      return;
    }
    if (newQty === item.inventory) return;

    const oldQty = item.inventory;
    item.inventory = newQty; // 乐观即时更新

    try {
      const res = await fetch('/api/store-products/update-inventory', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          store_id: this.selectedStoreId,
          sku_id: item.sku_id,
          product_id: item.product_id,
          inventory: newQty
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`✅ [${item.sku_id}] 库存已修改为 ${newQty}，已同步至 Makro 官方网关！`, 'success');
      } else {
        item.inventory = oldQty; // 失败回滚
        alert('修改库存失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      item.inventory = oldQty;
      alert('修改库存网络异常: ' + e);
    }
  },


  formatListingState(st) {
    const map = {
      'ACTIVE': '在售中',
      'READY_FOR_ACTIVATION': '待激活',
      'IN_PROGRESS': '审核中',
      'INACTIVE': '已下架',
      'INACTIVATED_BY_FLIPKART': '平台下架',
      'ARCHIVED': '已归档'
    };
    return map[st] || st;
  },


  toggleSelectCurrentPageStoreProducts() {
    if (this.isAllCurrentPageStoreProductsSelected) {
      const pageSkus = (this.storeProducts.items || []).map(it => it.sku_id);
      this.selectedStoreProductSkus = this.selectedStoreProductSkus.filter(s => !pageSkus.includes(s));
      this.isAllStoreProductsSelected = false;
    } else {
      const pageSkus = (this.storeProducts.items || []).map(it => it.sku_id);
      const set = new Set([...this.selectedStoreProductSkus, ...pageSkus]);
      this.selectedStoreProductSkus = Array.from(set);
    }
  },


  toggleSelectAllStoreProducts() {
    this.isAllStoreProductsSelected = !this.isAllStoreProductsSelected;
    if (this.isAllStoreProductsSelected) {
      const pageSkus = (this.storeProducts.items || []).map(it => it.sku_id);
      const set = new Set([...this.selectedStoreProductSkus, ...pageSkus]);
      this.selectedStoreProductSkus = Array.from(set);
    }
  },


  getStoreProductsCurrentFilters() {
    const f = {};
    if (this.storeProducts.internal_state) f.status = this.storeProducts.internal_state;
    if (this.storeProducts.search) f.search = this.storeProducts.search;
    if (this.storeProducts.vertical && this.storeProducts.vertical !== 'ALL') f.vertical = this.storeProducts.vertical;
    if (this.storeProducts.inventory_status && this.storeProducts.inventory_status !== 'ALL') f.inventory_status = this.storeProducts.inventory_status;
    if (this.storeProducts.min_inventory !== '' && this.storeProducts.min_inventory !== null && this.storeProducts.min_inventory !== undefined) {
      f.min_inventory = this.storeProducts.min_inventory;
    }
    if (this.storeProducts.max_inventory !== '' && this.storeProducts.max_inventory !== null && this.storeProducts.max_inventory !== undefined) {
      f.max_inventory = this.storeProducts.max_inventory;
    }
    if (this.storeProducts.min_price !== '' && this.storeProducts.min_price !== null && this.storeProducts.min_price !== undefined) {
      f.min_price = this.storeProducts.min_price;
    }
    if (this.storeProducts.max_price !== '' && this.storeProducts.max_price !== null && this.storeProducts.max_price !== undefined) {
      f.max_price = this.storeProducts.max_price;
    }
    if (this.storeProducts.date_range && this.storeProducts.date_range !== 'ALL') f.date_range = this.storeProducts.date_range;
    if (this.storeProducts.sync_start) f.sync_start = this.storeProducts.sync_start;
    if (this.storeProducts.sync_end) f.sync_end = this.storeProducts.sync_end;
    return f;
  },

  openBatchInventoryModal() {
    if (!this.isAllStoreProductsSelected && this.selectedStoreProductSkus.length === 0) {
      this.showToast('请先勾选需要修改库存的商品', 'warning');
      return;
    }
    this.showBatchInventoryModal = true;
  },


  async submitBatchInventory() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择当前操作店铺', 'error');
      return;
    }
    const qty = parseInt(this.batchInventoryValue, 10);
    if (isNaN(qty) || qty < 0) {
      this.showToast('请输入有效的库存数值 (>= 0)', 'warning');
      return;
    }
    this.isSubmittingBatchInventory = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);
    try {
      const payload = {
        store_id: this.selectedStoreId,
        new_inventory: qty,
        select_all: this.isAllStoreProductsSelected,
        status: this.storeProducts.internal_state,
        sku_ids: this.isAllStoreProductsSelected ? null : this.selectedStoreProductSkus,
        filters: this.isAllStoreProductsSelected ? this.getStoreProductsCurrentFilters() : null
      };
      const res = await fetch('/api/store-products/batch-update-inventory', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      const data = await res.json();
      if (res.ok && data.success && (data.success_count > 0 || data.total_selected === 0)) {
        this.showToast(`🎉 ${data.message}`, 'success');
        this.showBatchInventoryModal = false;
        this.selectedStoreProductSkus = [];
        this.isAllStoreProductsSelected = false;
        await this.loadStoreProducts(this.storeProducts.page);
      } else {
        alert('批量修改库存失败: ' + (data.message || data.detail || '网关未成功确认'));
      }
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') {
        alert('请求超时（60秒）：操作可能仍在后台执行，请稍后刷新页面查看最新库存');
      } else {
        alert('批量修改库存网络异常: ' + e);
      }
    } finally {
      this.isSubmittingBatchInventory = false;
    }
  },

  openBatchDeactivateModal() {
    if (!this.isAllStoreProductsSelected && this.selectedStoreProductSkus.length === 0) {
      this.showToast('请先勾选需要下架的商品', 'warning');
      return;
    }
    const targetItems = this.isAllStoreProductsSelected
      ? []
      : this.storeProducts.items.filter(it => this.selectedStoreProductSkus.includes(it.sku_id));
    this.deactivateForm = {
      targetItems: targetItems,
      targetSkus: this.isAllStoreProductsSelected ? [] : [...this.selectedStoreProductSkus],
      selectAll: this.isAllStoreProductsSelected,
      totalCount: this.isAllStoreProductsSelected ? this.storeProducts.total : this.selectedStoreProductSkus.length,
      filters: this.isAllStoreProductsSelected ? this.getStoreProductsCurrentFilters() : null,
      reason_category: 'OUT_OF_STOCK',
      reason_note: '',
      submitting: false
    };
    this.showDeactivateModal = true;
  },

  openSingleDeactivateModal(item) {
    this.deactivateForm = {
      targetItems: [item],
      targetSkus: [item.sku_id],
      selectAll: false,
      totalCount: 1,
      filters: null,
      reason_category: 'OUT_OF_STOCK',
      reason_note: '',
      submitting: false
    };
    this.showDeactivateModal = true;
  },

  async submitDeactivate() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择当前操作店铺', 'error');
      return;
    }
    this.deactivateForm.submitting = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);
    try {
      const payload = {
        store_id: this.selectedStoreId,
        select_all: Boolean(this.deactivateForm.selectAll),
        sku_ids: this.deactivateForm.selectAll ? null : this.deactivateForm.targetSkus,
        filters: this.deactivateForm.filters,
        reason_category: this.deactivateForm.reason_category,
        reason_note: this.deactivateForm.reason_note
      };
      const res = await fetch('/api/store-products/deactivate', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🛑 ${data.message || '商品已批量下架并清零库存'}`, 'success');
        this.showDeactivateModal = false;
        this.selectedStoreProductSkus = [];
        this.isAllStoreProductsSelected = false;
        await this.loadStoreProducts(this.storeProducts.page);
      } else {
        this.showToast(`❌ 下架失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') {
        this.showToast('下架请求超时（60秒），请刷新页面确认', 'error');
      } else {
        this.showToast('下架网络异常: ' + e, 'error');
      }
    } finally {
      this.deactivateForm.submitting = false;
    }
  },

  openBatchActivateModal() {
    if (!this.isAllStoreProductsSelected && this.selectedStoreProductSkus.length === 0) {
      this.showToast('请先勾选需要上架的商品', 'warning');
      return;
    }
    const targetItems = this.isAllStoreProductsSelected
      ? []
      : this.storeProducts.items.filter(it => this.selectedStoreProductSkus.includes(it.sku_id));
    this.activateForm = {
      targetItems: targetItems,
      targetSkus: this.isAllStoreProductsSelected ? [] : [...this.selectedStoreProductSkus],
      selectAll: this.isAllStoreProductsSelected,
      totalCount: this.isAllStoreProductsSelected ? this.storeProducts.total : this.selectedStoreProductSkus.length,
      filters: this.isAllStoreProductsSelected ? this.getStoreProductsCurrentFilters() : null,
      restore_inventory: true,
      inventory_value: 999,
      submitting: false
    };
    this.showActivateModal = true;
  },

  openSingleActivateModal(item) {
    this.activateForm = {
      targetItems: [item],
      targetSkus: [item.sku_id],
      selectAll: false,
      totalCount: 1,
      filters: null,
      restore_inventory: true,
      inventory_value: item.inventory > 0 ? item.inventory : 999,
      submitting: false
    };
    this.showActivateModal = true;
  },

  async submitActivate() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择当前操作店铺', 'error');
      return;
    }
    this.activateForm.submitting = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);
    try {
      const payload = {
        store_id: this.selectedStoreId,
        select_all: Boolean(this.activateForm.selectAll),
        sku_ids: this.activateForm.selectAll ? null : this.activateForm.targetSkus,
        filters: this.activateForm.filters,
        recovery_inventory: parseInt(this.activateForm.inventory_value, 10) || 500
      };
      const res = await fetch('/api/store-products/activate', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🟢 ${data.message || '商品已批量上架并恢复库存'}`, 'success');
        this.showActivateModal = false;
        this.selectedStoreProductSkus = [];
        this.isAllStoreProductsSelected = false;
        await this.loadStoreProducts(this.storeProducts.page);
      } else {
        this.showToast(`❌ 上架失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') {
        this.showToast('上架请求超时（60秒），请刷新页面确认', 'error');
      } else {
        this.showToast('上架网络异常: ' + e, 'error');
      }
    } finally {
      this.activateForm.submitting = false;
    }
  },

  openBindRepriceModal(item) {
    this.bindRepriceForm = {
      item: item,
      listing_id: item.id,
      seller_sku: item.sku_id,
      title: item.title,
      target_fsn: item.product_id || '',
      target_makro_url: '',
      min_price_floor: Math.max(Math.round((item.ssp || 100) * 0.75 * 100) / 100, 1.0),
      price_strategy: 'MINUS_15',
      auto_reprice: true,
      submitting: false
    };
    this.showBindRepriceModal = true;
  },

  async submitBindReprice() {
    if (!this.bindRepriceForm.target_fsn) {
      this.showToast('请输入竞品 FSN 或 Makro 编号', 'warning');
      return;
    }
    this.bindRepriceForm.submitting = true;
    try {
      const payload = {
        store_id: this.selectedStoreId,
        listing_id: this.bindRepriceForm.listing_id,
        target_fsn: this.bindRepriceForm.target_fsn.trim().toUpperCase(),
        target_makro_url: this.bindRepriceForm.target_makro_url.trim() || null,
        min_price_floor: parseFloat(this.bindRepriceForm.min_price_floor || 0),
        price_strategy: this.bindRepriceForm.price_strategy,
        auto_reprice: this.bindRepriceForm.auto_reprice
      };
      const res = await fetch('/api/reprice/bind-competitor', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🤖 [${this.bindRepriceForm.seller_sku}] 已成功开启 24h 跟价监控！`, 'success');
        this.showBindRepriceModal = false;
        await this.loadStoreProducts(this.storeProducts.page);
      } else {
        this.showToast(`❌ 开启跟价失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('开启跟价网络异常: ' + e, 'error');
    } finally {
      this.bindRepriceForm.submitting = false;
    }
  },

  // 行内直接调整店铺商品售价并三表联动
  async handleInlineStorePriceChange(item, rawVal) {
    try {
      const p = parseFloat(rawVal);
      const oldP = parseFloat(item.ssp || 0);
      if (isNaN(p) || p <= 0) {
        this.showToast('请输入有效的售价 (> 0)', 'warning');
        return;
      }
      if (Math.abs(p - oldP) < 0.001) return;

      const res = await fetch('/api/store-products/adjust-price', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          store_id: this.selectedStoreId,
          listing_id: item.id,
          sku_id: item.sku_id,
          new_price: p,
          sync_makro: true,
          force: false
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        item.ssp = data.new_price;
        this.showToast(`✅ [${item.sku_id}] 售价已同步更新为 R${data.new_price.toFixed(2)}`, 'success');
      } else if (data && data.below_floor) {
        this.priceFloorWarning = {
          type: 'store_product',
          item: item,
          new_price: p,
          min_floor: data.min_price_floor,
          title: item.title || item.sku_id
        };
        this.showPriceFloorWarningModal = true;
      } else {
        this.showToast(`❌ 调整售价失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('调整售价网络异常: ' + e.message, 'error');
    } finally {
      delete item._edit_ssp;
    }
  },

  openBatchStorePriceModal() {
    this.openBatchAdjustPriceModal('store_products');
  }
};


export default store_productsMethods;
