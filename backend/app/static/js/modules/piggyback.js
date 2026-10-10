/**
 * 业务模块: piggyback
 * 导出该领域的业务方法集
 */
export const piggybackMethods = {
  onPiggybackStoreChange() {
    this.selectedStoreId = this.piggyback.store_id;
    if (this.piggyback.store_id) {
      localStorage.setItem('makro_selected_store_id', this.piggyback.store_id);
    }
    this.clearPiggybackSelection();
    this.loadPiggybackItems(1);
    this.loadPiggybackVerticals();
  },

  async loadPiggybackKpi() {
    try {
      const params = new URLSearchParams();
      if (this.piggyback.store_id) params.append('store_id', this.piggyback.store_id);
      if (this.piggyback.user_id) params.append('user_id', this.piggyback.user_id);
      if (this.piggyback.search) params.append('search', this.piggyback.search.trim());
      if (this.piggyback.vertical && this.piggyback.vertical !== 'ALL') params.append('vertical', this.piggyback.vertical);
      if (this.piggyback.inventory_status && this.piggyback.inventory_status !== 'ALL') params.append('inventory_status', this.piggyback.inventory_status);
      if (this.piggyback.auto_reprice && this.piggyback.auto_reprice !== 'ALL') params.append('auto_reprice', this.piggyback.auto_reprice);
      if (this.piggyback.has_floor_price && this.piggyback.has_floor_price !== 'ALL') params.append('has_floor_price', this.piggyback.has_floor_price);
      if (this.piggyback.min_price !== '' && !isNaN(this.piggyback.min_price)) params.append('min_price', this.piggyback.min_price);
      if (this.piggyback.max_price !== '' && !isNaN(this.piggyback.max_price)) params.append('max_price', this.piggyback.max_price);
      if (this.piggyback.date_range && this.piggyback.date_range !== 'ALL') params.append('date_range', this.piggyback.date_range);
      if (this.piggyback.brand_nature && this.piggyback.brand_nature !== 'ALL') params.append('brand_nature', this.piggyback.brand_nature);
      if (this.piggyback.is_white_label && this.piggyback.is_white_label !== 'ALL') params.append('is_white_label', this.piggyback.is_white_label);
      if (this.piggyback.has_image_logo && this.piggyback.has_image_logo !== 'ALL') params.append('has_image_logo', this.piggyback.has_image_logo);
      if (this.piggyback.image_prohibited && this.piggyback.image_prohibited !== 'ALL') params.append('image_prohibited', this.piggyback.image_prohibited);
      if (this.piggyback.violation_type && this.piggyback.violation_type !== 'ALL') params.append('violation_type', this.piggyback.violation_type);

      const res = await fetch(`/api/piggyback/kpi-stats?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        this.piggyback.kpi = data;
      }
    } catch (e) {
      console.warn('加载跟品 KPI 异常:', e);
    }
  },


  selectKpiFilter(filterType) {
    this.clearPiggybackSelection();
    if (filterType === 'total') {
      this.piggyback.stage = 'ALL';
      this.piggyback.buybox_status = 'ALL';
    } else if (filterType === 'active') {
      this.piggyback.stage = 'ACTIVE_MONITOR';
      this.piggyback.buybox_status = 'ALL';
    } else if (filterType === 'no_competitor') {
      this.piggyback.stage = 'ACTIVE_MONITOR';
      this.piggyback.buybox_status = 'NO_COMPETITOR';
    } else if (filterType === 'winning') {
      this.piggyback.stage = 'ACTIVE_MONITOR';
      this.piggyback.buybox_status = 'WINNING';
    } else if (filterType === 'losing') {
      this.piggyback.stage = 'ACTIVE_MONITOR';
      this.piggyback.buybox_status = 'LOSING';
    } else if (filterType === 'floor_hit') {
      this.piggyback.stage = 'ACTIVE_MONITOR';
      this.piggyback.buybox_status = 'FLOOR_HIT';
    } else if (filterType === 'missing_floor') {
      this.piggyback.stage = 'ACTIVE_MONITOR';
      this.piggyback.buybox_status = 'MISSING_FLOOR';
    }
    this.loadPiggybackItems(1);
  },


  changePiggybackStage(stage) {
    this.clearPiggybackSelection();
    this.piggyback.stage = stage;
    this.piggyback.buybox_status = 'ALL';
    this.loadPiggybackItems(1);
  },


  startInlineEdit(item, field) {
    this.inlineEditing = {
      id: item.id,
      field: field,
      tempValue: item[field] !== undefined ? item[field] : 0,
      saving: false
    };
    this.$nextTick(() => {
      const input = this.$refs.inlineInput;
      if (input) {
        if (Array.isArray(input)) input[0]?.focus();
        else input.focus();
      }
    });
  },


  cancelInlineEdit() {
    this.inlineEditing = { id: null, field: null, tempValue: null, saving: false };
  },


  async saveInlineEdit(item) {
    if (!this.inlineEditing.id || this.inlineEditing.id !== item.id) return;
    const field = this.inlineEditing.field;
    const newVal = parseFloat(this.inlineEditing.tempValue);
    if (isNaN(newVal)) {
      this.cancelInlineEdit();
      return;
    }
    const oldVal = item[field];
    if (Math.abs(newVal - (oldVal || 0)) < 0.001) {
      this.cancelInlineEdit();
      return;
    }
    
    item[field] = newVal;
    this.cancelInlineEdit();
    
    try {
      const payload = { [field]: newVal };
      const res = await fetch(`/api/piggyback/items/${item.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '更新失败');
      
      // 行内微动效提示
      item._flashSuccess = field;
      setTimeout(() => { item._flashSuccess = null; }, 1500);

      if (data.sync_message) {
        this.showToast(data.sync_message, 'success');
      } else {
        this.showToast(`已更新 ${field === 'target_price' ? '跟品售价' : '保本底价'}`, 'success');
      }
      this.loadPiggybackKpi();
    } catch (e) {
      item[field] = oldVal;
      this.showToast('修改失败: ' + e.message, 'error');
    }
  },


  openBatchFloorModal() {
    if (this.piggyback.selectedIds.length === 0) {
      this.showToast('请先勾选需要设置保本底价的商品', 'warning');
      return;
    }
    this.showBatchFloorModal = true;
  },


  async handleBatchSetFloor() {
    if (this.piggyback.selectedIds.length === 0) return;
    this.submittingBatchFloor = true;
    try {
      const res = await fetch('/api/piggyback/batch-set-floor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ids: this.piggyback.selectedIds,
          mode: this.batchFloorForm.mode,
          value: this.batchFloorForm.value,
          auto_enable_reprice: this.batchFloorForm.auto_enable_reprice
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '批量设置失败');
      this.showToast(`🛡️ 成功为 ${data.updated_count} 件商品计算并配置保本底价！`, 'success');
      this.showBatchFloorModal = false;
      this.loadPiggybackItems(this.piggyback.page);
      this.loadPiggybackKpi();
    } catch (e) {
      this.showToast('批量设底价失败: ' + e.message, 'error');
    } finally {
      this.submittingBatchFloor = false;
    }
  },


  async batchToggleAutoReprice(enable) {
    if (this.piggyback.selectedIds.length === 0) return;
    const ids = this.piggyback.selectedIds;
    try {
      let updated = 0;
      for (const id of ids) {
        const res = await fetch(`/api/reprice/config/${id}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auto_reprice: enable })
        });
        if (res.ok) updated++;
      }
      this.showToast(`已批量${enable ? '开启' : '关闭'} ${updated} 件商品的自动跟价`, 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量修改跟价状态异常: ' + e.message, 'error');
    }
  },


  async loadPiggybackItems(page = 1, resetSelection = false) {
    if (resetSelection) {
      this.clearPiggybackSelection();
    }
    this.loadingPiggyback = true;
    this.piggyback.page = page;
    try {
      const params = new URLSearchParams({
        page: page,
        page_size: this.piggyback.pageSize
      });
      if (this.piggyback.status && this.piggyback.status !== 'ALL') {
        params.append('status', this.piggyback.status);
      }
      if (this.piggyback.stage && this.piggyback.stage !== 'ALL') {
        params.append('stage', this.piggyback.stage);
      }
      if (this.piggyback.buybox_status && this.piggyback.buybox_status !== 'ALL') {
        params.append('buybox_status', this.piggyback.buybox_status);
      }
      if (this.piggyback.compliance_status && this.piggyback.compliance_status !== 'ALL') {
        params.append('compliance_status', this.piggyback.compliance_status);
      }
      if (this.piggyback.store_id) {
        params.append('store_id', this.piggyback.store_id);
      }
      if (this.piggyback.user_id) {
        params.append('user_id', this.piggyback.user_id);
      }
      if (this.piggyback.search) {
        params.append('search', this.piggyback.search.trim());
      }
      if (this.piggyback.vertical && this.piggyback.vertical !== 'ALL') {
        params.append('vertical', this.piggyback.vertical);
      }
      if (this.piggyback.inventory_status && this.piggyback.inventory_status !== 'ALL') {
        params.append('inventory_status', this.piggyback.inventory_status);
      }
      if (this.piggyback.auto_reprice && this.piggyback.auto_reprice !== 'ALL') {
        params.append('auto_reprice', this.piggyback.auto_reprice);
      }
      if (this.piggyback.has_floor_price && this.piggyback.has_floor_price !== 'ALL') {
        params.append('has_floor_price', this.piggyback.has_floor_price);
      }
      if (this.piggyback.min_price !== '' && !isNaN(this.piggyback.min_price)) {
        params.append('min_price', this.piggyback.min_price);
      }
      if (this.piggyback.max_price !== '' && !isNaN(this.piggyback.max_price)) {
        params.append('max_price', this.piggyback.max_price);
      }
      if (this.piggyback.date_range && this.piggyback.date_range !== 'ALL') {
        params.append('date_range', this.piggyback.date_range);
      }
      if (this.piggyback.sort_by) {
        params.append('sort_by', this.piggyback.sort_by);
      }
      if (this.piggyback.brand_nature && this.piggyback.brand_nature !== 'ALL') {
        params.append('brand_nature', this.piggyback.brand_nature);
      }
      if (this.piggyback.is_white_label && this.piggyback.is_white_label !== 'ALL') {
        params.append('is_white_label', this.piggyback.is_white_label);
      }
      if (this.piggyback.has_image_logo && this.piggyback.has_image_logo !== 'ALL') {
        params.append('has_image_logo', this.piggyback.has_image_logo);
      }
      if (this.piggyback.image_prohibited && this.piggyback.image_prohibited !== 'ALL') {
        params.append('image_prohibited', this.piggyback.image_prohibited);
      }
      if (this.piggyback.violation_type && this.piggyback.violation_type !== 'ALL') {
        params.append('violation_type', this.piggyback.violation_type);
      }

      const res = await fetch(`/api/piggyback/items?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.piggyback.items = data.items || [];
      this.piggyback.total = data.total || 0;
      if (data.kpi) {
        this.piggyback.kpi = data.kpi;
      }
      if (data.stats) {
        this.piggyback.stats = data.stats;
      }

      // 动态同步当前页的全选状态（保留已有跨页已选商品，翻页不重置）
      const pageIds = this.piggyback.items.map(it => it.id);
      this.piggyback.selectAll = pageIds.length > 0 && 
        pageIds.every(id => this.piggyback.selectedIds.includes(id));
    } catch (e) {
      console.error('加载跟品列表异常:', e);
      this.showToast('加载跟品商品列表失败: ' + e.message, 'error');
    } finally {
      this.loadingPiggyback = false;
    }
  },


  getMakroProductUrl(item) {
    if (item.makro_url && item.makro_url.includes('/p/')) {
      return item.makro_url;
    }
    const fsn = item.makro_product_id;
    const itm = item.item_id || fsn;
    return `https://www.makro.co.za/-/p/${itm}?pid=${fsn}`;
  },


  savePiggybackAutoCompliance() {
    localStorage.setItem('makro_piggyback_auto_compliance', this.piggyback.autoCompliance ? 'true' : 'false');
    this.showToast(this.piggyback.autoCompliance ? '已开启采集后自动AI侵权检测' : '已关闭自动检测，改为手动批量质检', 'info');
  },


  changePiggybackState(st) {
    this.piggyback.status = st;
    this.clearPiggybackSelection();
    this.loadPiggybackItems(1);
  },


  changePiggybackCompliance(cst) {
    this.piggyback.compliance_status = cst;
    this.clearPiggybackSelection();
    this.loadPiggybackItems(1);
  },

  getCurrentPageSelectedCount() {
    if (!this.piggyback.items || !this.piggyback.selectedIds) return 0;
    const pageIdSet = new Set(this.piggyback.items.map(it => it.id));
    return this.piggyback.selectedIds.filter(id => pageIdSet.has(id)).length;
  },

  isPiggybackPageIndeterminate() {
    if (!this.piggyback.items || this.piggyback.items.length === 0) return false;
    const count = this.getCurrentPageSelectedCount();
    return count > 0 && count < this.piggyback.items.length;
  },


  toggleSelectAllPiggyback() {
    this.piggyback.selectAll = !this.piggyback.selectAll;
    if (this.piggyback.selectAll) {
      const curIds = this.piggyback.items.map(it => it.id);
      const idSet = new Set(this.piggyback.selectedIds);
      curIds.forEach(id => idSet.add(id));
      this.piggyback.selectedIds = Array.from(idSet);
    } else {
      const curIds = new Set(this.piggyback.items.map(it => it.id));
      this.piggyback.selectedIds = this.piggyback.selectedIds.filter(id => !curIds.has(id));
      this.piggyback.isAllFilteredSelected = false;
    }
  },


  handlePiggybackItemCheckboxChange() {
    const pageIds = this.piggyback.items.map(it => it.id);
    this.piggyback.selectAll = pageIds.length > 0 && 
      pageIds.every(id => this.piggyback.selectedIds.includes(id));
    if (this.piggyback.isAllFilteredSelected && this.piggyback.selectedIds.length < this.piggyback.total) {
      this.piggyback.isAllFilteredSelected = false;
    }
  },


  clearPiggybackSelection() {
    this.piggyback.selectedIds = [];
    this.piggyback.selectAll = false;
    this.piggyback.isAllFilteredSelected = false;
  },


  async selectAllFilteredPiggyback() {
    this.loadingPiggyback = true;
    try {
      const params = new URLSearchParams();
      if (this.piggyback.status && this.piggyback.status !== 'ALL') params.append('status', this.piggyback.status);
      if (this.piggyback.stage && this.piggyback.stage !== 'ALL') params.append('stage', this.piggyback.stage);
      if (this.piggyback.buybox_status && this.piggyback.buybox_status !== 'ALL') params.append('buybox_status', this.piggyback.buybox_status);
      if (this.piggyback.compliance_status && this.piggyback.compliance_status !== 'ALL') params.append('compliance_status', this.piggyback.compliance_status);
      if (this.piggyback.store_id) params.append('store_id', this.piggyback.store_id);
      if (this.piggyback.user_id) params.append('user_id', this.piggyback.user_id);
      if (this.piggyback.search) params.append('search', this.piggyback.search.trim());
      if (this.piggyback.vertical && this.piggyback.vertical !== 'ALL') params.append('vertical', this.piggyback.vertical);
      if (this.piggyback.inventory_status && this.piggyback.inventory_status !== 'ALL') params.append('inventory_status', this.piggyback.inventory_status);
      if (this.piggyback.auto_reprice && this.piggyback.auto_reprice !== 'ALL') params.append('auto_reprice', this.piggyback.auto_reprice);
      if (this.piggyback.has_floor_price && this.piggyback.has_floor_price !== 'ALL') params.append('has_floor_price', this.piggyback.has_floor_price);
      if (this.piggyback.min_price !== '' && !isNaN(this.piggyback.min_price)) params.append('min_price', this.piggyback.min_price);
      if (this.piggyback.max_price !== '' && !isNaN(this.piggyback.max_price)) params.append('max_price', this.piggyback.max_price);
      if (this.piggyback.date_range && this.piggyback.date_range !== 'ALL') params.append('date_range', this.piggyback.date_range);
      if (this.piggyback.brand_nature && this.piggyback.brand_nature !== 'ALL') params.append('brand_nature', this.piggyback.brand_nature);
      if (this.piggyback.is_white_label && this.piggyback.is_white_label !== 'ALL') params.append('is_white_label', this.piggyback.is_white_label);
      if (this.piggyback.has_image_logo && this.piggyback.has_image_logo !== 'ALL') params.append('has_image_logo', this.piggyback.has_image_logo);
      if (this.piggyback.image_prohibited && this.piggyback.image_prohibited !== 'ALL') params.append('image_prohibited', this.piggyback.image_prohibited);
      if (this.piggyback.violation_type && this.piggyback.violation_type !== 'ALL') params.append('violation_type', this.piggyback.violation_type);

      const res = await fetch(`/api/piggyback/ids?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.piggyback.selectedIds = data.ids || [];
      this.piggyback.selectAll = true;
      this.piggyback.isAllFilteredSelected = true;
      this.showToast(`✅ 已跨页成功勾选符合当前条件的全部 ${this.piggyback.selectedIds.length} 件商品`, 'success');
    } catch (e) {
      this.showToast('跨页全选获取商品失败: ' + e.message, 'error');
    } finally {
      this.loadingPiggyback = false;
    }
  },


  async handleCollectPiggyback() {
    if (!this.collectPiggybackForm.url_or_fsn) return;
    this.collectingPiggyback = true;
    try {
      const payload = {
        ...this.collectPiggybackForm,
        auto_compliance: this.piggyback.autoCompliance
      };
      const res = await fetch('/api/piggyback/collect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '采集失败');
      this.showToast(data.message || '采集录入成功！', 'success');
      this.showCollectPiggybackModal = false;
      this.collectPiggybackForm.url_or_fsn = '';
      this.loadPiggybackItems(1);
    } catch (e) {
      this.showToast(e.message, 'error');
    } finally {
      this.collectingPiggyback = false;
    }
  },


  async handleBatchCollectPiggyback() {
    const lines = this.batchCollectPiggybackForm.raw_text.split('\n').map(x => x.trim()).filter(Boolean);
    if (lines.length === 0) return;
    this.batchCollectingPiggyback = true;
    try {
      const res = await fetch('/api/piggyback/batch-collect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: lines,
          store_id: this.batchCollectPiggybackForm.store_id,
          price_strategy: this.batchCollectPiggybackForm.price_strategy,
          auto_compliance: this.piggyback.autoCompliance
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '批量导入失败');
      this.showToast(`批量导入完成: 成功 ${data.success_count} 件, 失败 ${data.failed_count} 件`, 'success');
      this.showBatchCollectPiggybackModal = false;
      this.batchCollectPiggybackForm.raw_text = '';
      this.loadPiggybackItems(1);
    } catch (e) {
      this.showToast(e.message, 'error');
    } finally {
      this.batchCollectingPiggyback = false;
    }
  },


  openBatchPricingModal() {
    if (this.piggyback.selectedIds.length === 0) {
      this.showToast('请先勾选需要批量配置跟价公式的商品', 'warning');
      return;
    }
    this.showBatchPricingPiggybackModal = true;
  },


  async handleBatchApplyPricing() {
    if (this.piggyback.selectedIds.length === 0) return;
    this.applyingBatchPricing = true;
    try {
      let finalStrategy = this.batchPricingForm.price_strategy;
      let customDelta = null;
      if (finalStrategy === 'CUSTOM') {
        customDelta = this.batchPricingForm.custom_delta || 0.0;
        finalStrategy = `CUSTOM:${customDelta >= 0 ? '+' : ''}${customDelta}`;
      }
      const res = await fetch('/api/piggyback/batch-apply-pricing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ids: this.piggyback.selectedIds,
          price_strategy: finalStrategy,
          min_price_floor: this.batchPricingForm.min_price_floor || null,
          custom_delta: customDelta,
          sync_to_makro: this.batchPricingForm.sync_to_makro
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '调价失败');
      this.showToast(`成功应用跟价公式至 ${data.updated_count} 件商品 (实时同步官方: ${data.synced_count || 0} 件)`, 'success');
      this.showBatchPricingPiggybackModal = false;
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast(e.message, 'error');
    } finally {
      this.applyingBatchPricing = false;
    }
  },


  getStrategyLabel(st) {
    if (!st) return '比竞对低 R1.00';
    const found = this.strategyQuickOptions.find(o => o.value === st);
    if (found) return found.label;
    if (st.startsWith('MINUS_')) return `比竞对低 R${parseFloat(st.replace('MINUS_', '').replace('_', '.')).toFixed(2)}`;
    if (st.startsWith('PERCENT_')) return `比竞对低 ${st.replace('PERCENT_', '')}%`;
    if (st.startsWith('CUSTOM:')) return `自定义差额 R${st.split(':')[1]}`;
    if (st === 'MANUAL') return '平价跟卖';
    return st;
  },


  getStrategyShortTag(item) {
    const st = item.price_strategy || 'MINUS_1';
    const found = this.strategyQuickOptions.find(o => o.value === st);
    if (found) return found.short;
    if (st.startsWith('MINUS_')) return `-R${parseFloat(st.replace('MINUS_', '').replace('_', '.')).toFixed(2)}`;
    if (st.startsWith('PERCENT_')) return `-${st.replace('PERCENT_', '')}%`;
    if (st.startsWith('CUSTOM:')) return `R${parseFloat(st.split(':')[1]) >= 0 ? '+' : ''}${st.split(':')[1]}`;
    if (st === 'MANUAL') return '平价';
    if (item.original_price > item.target_price) return `-R${(item.original_price - item.target_price).toFixed(2)}`;
    return '平价';
  },


  toggleStrategyDropdown(item) {
    if (this.activeStrategyDropdownId === item.id) {
      this.activeStrategyDropdownId = null;
    } else {
      this.activeStrategyDropdownId = item.id;
    }
  },


  closeStrategyDropdown() {
    this.activeStrategyDropdownId = null;
  },


  async selectItemStrategy(item, strategy, customDelta = null) {
    this.activeStrategyDropdownId = null;
    const oldStrategy = item.price_strategy;
    item.price_strategy = strategy;
    try {
      const payload = { price_strategy: strategy };
      const res = await fetch(`/api/piggyback/items/${item.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '保存失败');
      if (data.item) {
        item.target_price = data.item.target_price;
        item.target_mrp = data.item.target_mrp;
        item.price_strategy = data.item.price_strategy;
      }
      item._flashSuccess = 'strategy';
      setTimeout(() => { item._flashSuccess = null; }, 1500);
      this.showToast(`已更新跟价公式: ${this.getStrategyLabel(strategy)} (最新售价: R${item.target_price.toFixed(2)})`, 'success');
    } catch (e) {
      item.price_strategy = oldStrategy;
      this.showToast('修改跟价公式失败: ' + e.message, 'error');
    }
  },


  openCustomStrategyPrompt(item) {
    this.activeStrategyDropdownId = null;
    const raw = prompt('请输入自定义减额或加额兰特数值 (例如输入 -1.5 代表降R1.50，+2 代表涨R2.00):', '-1.5');
    if (raw === null || raw.trim() === '') return;
    const val = parseFloat(raw.trim());
    if (isNaN(val)) {
      this.showToast('请输入有效的数值', 'warning');
      return;
    }
    const st = `CUSTOM:${val >= 0 ? '+' : ''}${val}`;
    this.selectItemStrategy(item, st, val);
  },


  async handleCheckCompliance(item) {
    this.showToast('正在调用双 AI 执行文本+主图视觉侵权与合规全项质检...', 'info');
    try {
      const res = await fetch(`/api/piggyback/check-compliance/${item.id}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '检测失败');
      item.compliance_status = data.compliance_status;
      item.compliance_details = data.details;
      if (this.currentComplianceItem && this.currentComplianceItem.id === item.id) {
        this.currentComplianceItem.compliance_status = data.compliance_status;
        this.currentComplianceItem.compliance_details = data.details;
      }
      this.showToast(`合规检测完成: 判定为 ${data.compliance_status}`, 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('检测失败: ' + e.message, 'error');
    }
  },


  async handleBatchCheckCompliance() {
    if (this.piggyback.selectedIds.length === 0) return;
    const count = this.piggyback.selectedIds.length;
    this.showToast(`正在分发 ${count} 件商品的批量AI质检任务...`, 'info');
    try {
      const res = await fetch('/api/piggyback/batch-check-compliance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: this.piggyback.selectedIds })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '批量检测启动失败');
      if (data.task_id) {
        const conc = data.concurrency || 100;
        this.addOrUpdateBgTask({
          id: data.task_id,
          name: data.task_name || `批量跟品AI合规质检 (${conc}线程并发 · 共 ${count} 件)`,
          task_type: 'PIGGYBACK_COMPLIANCE',
          status: 'RUNNING',
          progress: 0,
          current: 0,
          total: count,
          current_title: `正在以 ${conc} 线程并发初始化AI合规质检引擎...`
        });
        this.showFullTasksBanner = true;
        this.ensureBgTasksPolling();
        this.showToast(`🛡️ ${data.message || `已启动批量AI合规质检任务 (${conc} 线程并发)，顶部进度条正在实时推进`}`, 'success');
      } else {
        this.showToast(data.message || '质检完成', 'success');
        this.loadPiggybackItems(this.piggyback.page);
      }
    } catch (e) {
      this.showToast('批量质检异常: ' + e.message, 'error');
    }
  },


  async handlePublishPiggyback(item) {
    if (item.compliance_status === 'PROHIBITED') {
      this.showToast('⛔ 【合规红线拦截】该商品被判定为侵权禁售品，系统严禁跟卖！', 'error');
      return;
    }
    if (!confirm(`确认要将商品「${item.title.substring(0, 30)}...」挂靠至店铺「${item.store_name}」发布在售吗？`)) return;
    this.showToast('正在向 Makro 网关提交挂靠并激活库存...', 'info');
    item.status = 'SUBMITTING';
    try {
      const res = await fetch(`/api/piggyback/publish/${item.id}`, { method: 'POST' });
      let data = {};
      try {
        data = await res.json();
      } catch (jsonErr) {
        const rawText = await res.text().catch(() => '');
        data = { detail: rawText || `HTTP ${res.status}` };
      }
      if (!res.ok) throw new Error(data.detail || '跟品失败');
      this.showToast('🚀 挂靠跟品成功！已上线在售', 'success');
      item.status = 'ACTIVE';
      item.makro_listing_id = data.result ? data.result.listing_id : null;
    } catch (e) {
      item.status = 'FAILED';
      item.error_message = e.message;
      this.showToast('跟品挂靠失败: ' + e.message, 'error');
    }
  },


  openBatchPublishPiggybackModal() {
    if (this.piggyback.selectedIds.length === 0) return;
    this.batchPublishPiggybackStoreMode = 'target';
    if (this.piggyback.store_id) {
      this.batchPublishPiggybackTargetStoreId = this.piggyback.store_id;
    } else if (this.activeStores.length > 0 && !this.batchPublishPiggybackTargetStoreId) {
      this.batchPublishPiggybackTargetStoreId = this.activeStores[0].id;
    }
    this.showBatchPublishPiggybackModal = true;
  },


  async executeBatchPublishPiggyback() {
    if (this.piggyback.selectedIds.length === 0 || this.isBatchPublishingPiggyback) return;
    let validIds = [...this.piggyback.selectedIds];
    const prohibitedOnPage = new Set(this.piggyback.items.filter(it => it.compliance_status === 'PROHIBITED').map(it => it.id));
    validIds = validIds.filter(id => !prohibitedOnPage.has(id));
    if (validIds.length === 0) {
      this.showToast('⛔ 选中的商品均为【红线侵权禁售】商品，已被系统拦截，无法挂靠！', 'error');
      return;
    }
    const targetStoreId = this.batchPublishPiggybackStoreMode === 'target' ? this.batchPublishPiggybackTargetStoreId : null;
    this.isBatchPublishingPiggyback = true;
    this.showToast('正在向后台任务引擎分发批量挂靠任务...', 'info');
    try {
      const res = await fetch('/api/piggyback/batch-publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: validIds, store_id: targetStoreId })
      });
      let data = {};
      try {
        data = await res.json();
      } catch (jsonErr) {
        const rawText = await res.text().catch(() => '');
        data = { detail: rawText || `HTTP ${res.status}` };
      }
      if (!res.ok) throw new Error(data.detail || '启动批量跟品失败');
      this.showBatchPublishPiggybackModal = false;
      if (data.task_id) {
        this.addOrUpdateBgTask({
          id: data.task_id,
          name: `批量跟品挂靠 (${validIds.length} 件)`,
          task_type: 'MAKRO_PIGGYBACK',
          status: 'RUNNING',
          progress: 0,
          current: 0,
          total: validIds.length,
          current_title: '正在调度跟卖网关与库存同步...'
        });
        this.showFullTasksBanner = true;
        this.ensureBgTasksPolling();
      }
      this.showToast(data.message || '已成功启动异步批量挂靠任务', 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量跟品启动异常: ' + e.message, 'error');
    } finally {
      this.isBatchPublishingPiggyback = false;
    }
  },


  openBatchSetStoreModal() {
    if (this.piggyback.selectedIds.length === 0) return;
    if (this.piggyback.store_id) {
      this.batchSetStoreTargetId = this.piggyback.store_id;
    } else if (this.activeStores.length > 0 && !this.batchSetStoreTargetId) {
      this.batchSetStoreTargetId = this.activeStores[0].id;
    }
    this.showBatchSetStoreModal = true;
  },


  async executeBatchSetStore() {
    if (this.piggyback.selectedIds.length === 0 || !this.batchSetStoreTargetId || this.isBatchSettingStore) return;
    this.isBatchSettingStore = true;
    try {
      const res = await fetch('/api/piggyback/batch-set-store', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: this.piggyback.selectedIds, store_id: this.batchSetStoreTargetId })
      });
      let data = {};
      try {
        data = await res.json();
      } catch (jsonErr) {
        const rawText = await res.text().catch(() => '');
        data = { detail: rawText || `HTTP ${res.status}` };
      }
      if (!res.ok) throw new Error(data.detail || '修改店铺失败');
      this.showBatchSetStoreModal = false;
      this.showToast(`✅ 成功将 ${data.updated_count} 件商品归属转移至店铺「${data.store_name}」`, 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量改店铺异常: ' + e.message, 'error');
    } finally {
      this.isBatchSettingStore = false;
    }
  },


  async handleBatchPublishPiggyback() {
    this.openBatchPublishPiggybackModal();
  },


  async handleAbandonPiggyback(item) {
    const isPublished = item.status === 'ACTIVE' || item.status === 'PUBLISHED';
    const alertNotice = isPublished 
      ? '⚠️ 提示：该商品当前在线在售，弃用后系统将自动在 Makro 店铺后台清零库存并下架！\n\n' 
      : '';
    const reason = prompt(`${alertNotice}确认弃用商品「${item.title.substring(0, 25)}...」并加入防重采黑名单吗？\n请输入弃用原因 (如: 侵权风险/款式过季/无货):`, '侵权风险拦截/手工弃用');
    if (reason === null) return;
    try {
      const res = await fetch(`/api/piggyback/items/${item.id}/abandon`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason || '侵权风险拦截/手工弃用' })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '弃用失败');
      this.showToast(isPublished ? '🚫 商品已移入弃用黑名单，且已同步在 Makro 店铺下架清零库存' : '🚫 商品已移入弃用黑名单，后续采集将自动阻断防重', 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('弃用失败: ' + e.message, 'error');
    }
  },


  async handleBatchAbandonPiggyback() {
    if (this.piggyback.selectedIds.length === 0) return;
    const count = this.piggyback.selectedIds.length;
    const reason = prompt(`⚠️ 提示：选中的商品若有在线在售商品，弃用时系统将自动在 Makro 店铺后台清零库存下架！\n\n确认将选中的 ${count} 件商品批量弃用并加入黑名单吗？\n请输入弃用原因:`, '批量弃用/侵权风险拦截');
    if (reason === null) return;
    try {
      const res = await fetch('/api/piggyback/batch-abandon', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: this.piggyback.selectedIds, reason: reason || '批量弃用/侵权风险拦截' })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '批量弃用失败');
      const delistNote = data.delisted_count > 0 ? `（其中 ${data.delisted_count} 件在线在售商品已自动同步下架清零库存）` : '';
      this.showToast(`🚫 成功将 ${data.abandoned_count} 件商品移入弃用黑名单${delistNote}`, 'success');
      this.clearPiggybackSelection();
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量弃用失败: ' + e.message, 'error');
    }
  },


  async handleRestorePiggyback(item) {
    if (!confirm(`确认恢复「${item.title.substring(0, 25)}...」回到正常跟品待处理池吗？`)) return;
    try {
      const res = await fetch(`/api/piggyback/items/${item.id}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '恢复失败');
      this.showToast('🔄 商品已成功恢复至待处理池', 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('恢复失败: ' + e.message, 'error');
    }
  },


  async handleBatchRestorePiggyback() {
    if (this.piggyback.selectedIds.length === 0) return;
    const count = this.piggyback.selectedIds.length;
    if (!confirm(`确认将选中的 ${count} 件弃用商品批量恢复至正常池吗？`)) return;
    try {
      const res = await fetch('/api/piggyback/batch-restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: this.piggyback.selectedIds })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '批量恢复失败');
      this.showToast(`🔄 成功恢复 ${data.restored_count} 件商品至正常池`, 'success');
      this.piggyback.selectedIds = [];
      this.piggyback.selectAll = false;
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量恢复失败: ' + e.message, 'error');
    }
  },


  async handleDeletePiggyback(item) {
    if (!confirm(`确认从跟品池移除「${item.title.substring(0, 30)}...」吗？`)) return;
    try {
      const res = await fetch(`/api/piggyback/items/${item.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('删除失败');
      this.showToast('已移除跟品商品', 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast(e.message, 'error');
    }
  },


  async handleBatchDeletePiggyback() {
    if (this.piggyback.selectedIds.length === 0) return;
    const count = this.piggyback.selectedIds.length;
    if (!confirm(`⚠️ 警告：该操作将彻底从跟品库中删除选中的 ${count} 件商品！\n（注：若希望保留记录并阻断后续重复采集，建议使用「批量弃用」）\n\n确认彻底删除这 ${count} 件商品吗？`)) return;
    try {
      const res = await fetch('/api/piggyback/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: this.piggyback.selectedIds })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '批量删除失败');
      this.showToast(`🗑️ 成功彻底删除 ${data.deleted_count} 件跟品商品`, 'success');
      this.clearPiggybackSelection();
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量删除失败: ' + e.message, 'error');
    }
  },


  openEditPiggyback(item) {
    this.editingPiggybackItem = JSON.parse(JSON.stringify(item));
    this.showEditPiggybackModal = true;
  },


  async saveEditPiggyback() {
    if (!this.editingPiggybackItem) return;
    try {
      const it = this.editingPiggybackItem;
      const res = await fetch(`/api/piggyback/items/${it.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          seller_sku: it.seller_sku,
          target_price: it.target_price,
          target_mrp: it.target_mrp,
          min_price_floor: it.min_price_floor,
          max_price_ceiling: it.max_price_ceiling,
          auto_reprice: it.auto_reprice,
          variant_name: it.variant_name,
          price_strategy: it.price_strategy,
          inventory: it.inventory,
          lead_time_days: it.lead_time_days,
          store_id: it.store_id,
          weight: it.weight,
          length: it.length,
          breadth: it.breadth,
          height: it.height
        })
      });
      if (!res.ok) throw new Error('保存失败');
      this.showToast('已更新跟品参数', 'success');
      this.showEditPiggybackModal = false;
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast(e.message, 'error');
    }
  },


  openComplianceDetails(item) {
    this.currentComplianceItem = item;
    this.piggybackArbitrationNotes = (item.compliance_details && item.compliance_details.human_arbitration && item.compliance_details.human_arbitration.notes) || '';
    this.showComplianceDetailModal = true;
  },


  async submitPiggybackArbitration(item, verdict) {
    if (!item) return;
    this.isArbitratingPiggyback = true;
    try {
      const res = await fetch(`/api/piggyback/arbitrate/${item.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          human_verdict: verdict,
          human_notes: this.piggybackArbitrationNotes || ''
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '仲裁提交失败');
      item.compliance_status = data.compliance_status;
      item.compliance_details = data.compliance_details;
      this.showToast(`终审仲裁完成: 已裁定为 [${verdict}]`, 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('终审失败: ' + e.message, 'error');
    } finally {
      this.isArbitratingPiggyback = false;
    }
  },


  async triggerSingleItemReprice(item) {
    this.showToast(`正在探测竞对价格并为 [${item.seller_sku}] 执行跟价...`, 'info');
    try {
      const res = await fetch(`/api/reprice/trigger-item/${item.id}`, { method: 'POST' });
      const data = await res.json();
      if (data.success && data.result) {
        const r = data.result;
        const actionMap = {
          'UNDER_CUT': '📉 已下调抢占黄金购物车',
          'WINNING_HOLD': '🏆 当前己方已占位，保持原价稳定获利',
          'REACHED_FLOOR': '🛡️ 竞对过低，已触发保本底线锁定',
          'NO_CHANGE': '⚪ 计算价格与现售价一致，保持不变'
        };
        const actionLabel = actionMap[r.action] || r.action;
        this.showToast(`${actionLabel} (原R${r.old_price} ➔ 现R${r.new_price})`, 'success');
        this.loadPiggybackItems(this.piggyback.page);
      } else {
        throw new Error(data.result?.reason || '跟价执行未成功');
      }
    } catch (e) {
      this.showToast('执行跟价失败: ' + e.message, 'error');
    }
  },


  async triggerFullCruiseReprice() {
    const storeId = this.piggyback.store_id || null;
    let storeName = '全部店铺';
    if (storeId && this.stores && this.stores.length > 0) {
      const foundStore = this.stores.find(s => s.id == storeId);
      if (foundStore) storeName = foundStore.name;
    }

    // 确保系统设置已加载就绪
    if (!this.settings || !this.settings.piggyback_cruise_concurrency) {
      try {
        await this.loadSettings();
      } catch (e) {
        console.warn('加载系统设置失败:', e);
      }
    }

    const currentConcurrency = parseInt(this.settings?.piggyback_cruise_concurrency, 10) || 3;
    const promptText = `确定立即对【${storeName}】的所有在售跟品发起全量巡检巡航吗？\n\n系统将按照系统设置中管理员设定的【${currentConcurrency} 线程并发】启动受控巡检，抓取买家前台最新竞对报价、重新评定 Buybox 黄金购物车赢车归属，并对已开启自动跟价的商品执行智能调价。`;
    if (!confirm(promptText)) return;

    this.runningFullCruise = true;
    this.showToast(`🚀 正在发起【${storeName}】全量巡检巡航 (${currentConcurrency} 线程)...`, 'info');
    try {
      const res = await fetch('/api/reprice/trigger-full-cruise', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: storeId, concurrency: currentConcurrency })
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || data.message || '启动全量巡检失败');
      }
      this.showToast(data.message || '全量巡检巡航任务已在后台启动！', 'success');
      this.ensureBgTasksPolling();
      setTimeout(() => this.pollActiveTasks(), 300);
    } catch (e) {
      this.runningFullCruise = false;
      this.showToast('启动全量巡检异常: ' + e.message, 'error');
    }
  },


  async triggerBatchReprice() {
    const isSelected = this.piggyback.selectedIds.length > 0;
    if (!isSelected) {
      return this.triggerFullCruiseReprice();
    }
    const promptText = `确定立即对选中的 ${this.piggyback.selectedIds.length} 件在售跟品执行自动跟价调价吗？`;
    if (!confirm(promptText)) return;

    this.runningBatchReprice = true;
    this.showToast('🚀 正在批量探测竞对并执行自动跟价...', 'info');
    try {
      const payload = { ids: this.piggyback.selectedIds };
      const res = await fetch('/api/reprice/trigger-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      const blockedPart = data.blocked_count ? `, 🛡️阻断: ${data.blocked_count}` : '';
      const toastType = data.blocked_count > 0 ? 'warning' : 'success';
      this.showToast(`🤖 批量跟价完成！处理 ${data.total || data.total_items} 件 (降价抢流: ${data.undercut_count || 0}, 胜出保持: ${data.winning_hold_count || 0}, 触底保本: ${data.floor_count || 0}${blockedPart})`, toastType);
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量跟价异常: ' + e.message, 'error');
    } finally {
      this.runningBatchReprice = false;
    }
  },


  async toggleItemAutoReprice(item) {
    const nextState = !item.auto_reprice;
    try {
      const res = await fetch(`/api/reprice/config/${item.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_reprice: nextState })
      });
      if (!res.ok) throw new Error('修改失败');
      item.auto_reprice = nextState;
      this.showToast(nextState ? `已开启 [${item.seller_sku}] 自动跟价` : `已暂停 [${item.seller_sku}] 自动跟价`, 'info');
    } catch (e) {
      this.showToast('切换自动跟价开关失败: ' + e.message, 'error');
    }
  },


  openRepriceLogsModal() {
    this.showRepriceLogsModal = true;
    this.loadRepriceLogs(1);
  },


  async loadRepriceLogs(page = 1) {
    this.loadingRepriceLogs = true;
    this.repriceLogs.page = page;
    try {
      const params = new URLSearchParams({
        page: page,
        page_size: this.repriceLogs.pageSize
      });
      if (this.repriceLogs.actionFilter) {
        params.append('action', this.repriceLogs.actionFilter);
      }
      if (this.piggyback.store_id) {
        params.append('store_id', this.piggyback.store_id);
      }
      const res = await fetch(`/api/reprice/logs?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.repriceLogs.items = data.items || [];
      this.repriceLogs.total = data.total || 0;
    } catch (e) {
      this.showToast('加载调价日志失败: ' + e.message, 'error');
    } finally {
      this.loadingRepriceLogs = false;
    }
  },

  async handleRetryFetchItem(item) {
    if (!item || !item.id) return;
    try {
      item.status = 'FETCHING';
      const res = await fetch(`/api/piggyback/${item.id}/retry-fetch`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🔄 [${item.makro_product_id}] 已重新推入后台静默拉取队列`, 'info');
      } else {
        throw new Error(data.detail || data.message || '重试失败');
      }
    } catch (e) {
      this.showToast('重试拉取失败: ' + e.message, 'error');
      item.status = 'FAILED';
    }
  },

  async handleBatchRetryFetch() {
    const storeId = this.piggyback.store_id;
    try {
      const url = storeId ? `/api/piggyback/batch-retry-fetch?store_id=${storeId}` : '/api/piggyback/batch-retry-fetch';
      const res = await fetch(url, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🔄 已将 ${data.count || 0} 件失败商品重新加入静默拉取队列`, 'success');
        this.loadPiggybackItems(this.piggyback.page);
      } else {
        throw new Error(data.detail || data.message || '批量重试失败');
      }
    } catch (e) {
      this.showToast('批量重试失败: ' + e.message, 'error');
    }
  },

  sortPiggybackVerticals(list = null) {
    const target = list || (this.piggyback && this.piggyback.verticalsList);
    if (!Array.isArray(target) || target.length === 0) return;
    target.sort((a, b) => {
      const labelA = this.getVerticalZh(a) || a;
      const labelB = this.getVerticalZh(b) || b;
      const lenDiff = labelA.length - labelB.length;
      if (lenDiff !== 0) return lenDiff;
      return labelA.localeCompare(labelB, 'zh-CN');
    });
  },

  async loadPiggybackVerticals() {
    try {
      const params = new URLSearchParams();
      if (this.piggyback.store_id) params.append('store_id', this.piggyback.store_id);
      const res = await fetch(`/api/piggyback/verticals?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        const list = data.verticals || [];
        this.sortPiggybackVerticals(list);
        this.piggyback.verticalsList = list;
      }
    } catch (e) {
      console.warn('获取类目列表异常:', e);
    }
  },

  resetPiggybackFilters() {
    this.clearPiggybackSelection();
    this.piggyback.search = '';
    this.piggyback.vertical = 'ALL';
    this.piggyback.inventory_status = 'ALL';
    this.piggyback.auto_reprice = 'ALL';
    this.piggyback.has_floor_price = 'ALL';
    this.piggyback.min_price = '';
    this.piggyback.max_price = '';
    this.piggyback.date_range = 'ALL';
    this.piggyback.sort_by = 'ID_DESC';
    this.piggyback.compliance_status = 'ALL';
    this.piggyback.brand_nature = 'ALL';
    this.piggyback.is_white_label = 'ALL';
    this.piggyback.has_image_logo = 'ALL';
    this.piggyback.image_prohibited = 'ALL';
    this.piggyback.violation_type = 'ALL';
    this.piggyback.stage = 'ALL';
    this.piggyback.buybox_status = 'ALL';
    this.piggyback.isAllFilteredSelected = false;
    this.loadPiggybackItems(1);
    this.showToast('已重置所有过滤条件', 'info');
  },

  changePiggybackPageSize(size) {
    this.piggyback.pageSize = parseInt(size);
    this.loadPiggybackItems(1);
  },

  openBatchPriceAdjustModal() {
    if (this.piggyback.selectedIds.length === 0) {
      this.showToast('请先勾选需要改价的商品', 'warning');
      return;
    }
    this.batchPriceAdjustForm = {
      mode: 'DELTA',
      value: 0.0,
      sync_to_makro: false,
      enforce_floor: true
    };
    this.showBatchPriceAdjustModal = true;
  },

  async handleBatchAdjustPrice() {
    if (this.piggyback.selectedIds.length === 0) return;

    const mode = (this.batchPriceAdjustForm.mode || 'DELTA').toUpperCase();
    const rawVal = this.batchPriceAdjustForm.value;
    const val = parseFloat(rawVal);

    if (isNaN(val)) {
      this.showToast('请输入有效的调价数值', 'warning');
      return;
    }
    if (mode === 'FIXED' && val <= 0) {
      this.showToast('固定售价必须大于 0 兰特', 'warning');
      return;
    }
    if ((mode === 'DELTA' || mode === 'PERCENT') && val === 0) {
      this.showToast('调整数值不能为 0（如需降价请输入负数，如 -5）', 'warning');
      return;
    }

    this.isBatchAdjustingPrice = true;
    try {
      const res = await fetch('/api/piggyback/batch-adjust-price', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ids: this.piggyback.selectedIds,
          mode: mode,
          value: val,
          sync_to_makro: Boolean(this.batchPriceAdjustForm.sync_to_makro),
          enforce_floor: Boolean(this.batchPriceAdjustForm.enforce_floor)
        })
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {
        data = { detail: await res.text().catch(() => '网络响应异常') };
      }

      if (!res.ok) {
        let errMsg = data.detail || data.message || '批量改价失败';
        if (Array.isArray(data.detail)) {
          errMsg = data.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
        } else if (typeof data.detail === 'object' && data.detail !== null) {
          errMsg = JSON.stringify(data.detail);
        }
        throw new Error(errMsg);
      }

      this.showToast(data.message || `已成功批量调整 ${data.updated_count} 件商品价格`, 'success');
      this.showBatchPriceAdjustModal = false;
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量改价失败: ' + e.message, 'error');
    } finally {
      this.isBatchAdjustingPrice = false;
    }
  },

  async handleBatchToggleAutoReprice(enable) {
    if (this.piggyback.selectedIds.length === 0) {
      this.showToast('请先勾选需要操作的商品', 'warning');
      return;
    }
    try {
      const res = await fetch('/api/piggyback/batch-toggle-auto-reprice', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ids: this.piggyback.selectedIds,
          auto_reprice: Boolean(enable)
        })
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {
        data = { detail: await res.text().catch(() => '网络响应异常') };
      }

      if (!res.ok) {
        let errMsg = data.detail || data.message || '批量修改跟价状态失败';
        if (Array.isArray(data.detail)) {
          errMsg = data.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
        }
        throw new Error(errMsg);
      }

      this.showToast(data.message || '操作成功！', 'success');
      this.loadPiggybackItems(this.piggyback.page);
    } catch (e) {
      this.showToast('批量操作失败: ' + e.message, 'error');
    }
  },

  exportPiggybackData(mode = 'current') {
    const params = new URLSearchParams();
    if (mode === 'selected') {
      if (this.piggyback.selectedIds.length === 0) {
        this.showToast('请先勾选需要导出的商品', 'warning');
        return;
      }
      params.append('ids', this.piggyback.selectedIds.join(','));
    } else {
      if (this.piggyback.status && this.piggyback.status !== 'ALL') params.append('status', this.piggyback.status);
      if (this.piggyback.stage && this.piggyback.stage !== 'ALL') params.append('stage', this.piggyback.stage);
      if (this.piggyback.buybox_status && this.piggyback.buybox_status !== 'ALL') params.append('buybox_status', this.piggyback.buybox_status);
      if (this.piggyback.compliance_status && this.piggyback.compliance_status !== 'ALL') params.append('compliance_status', this.piggyback.compliance_status);
      if (this.piggyback.store_id) params.append('store_id', this.piggyback.store_id);
      if (this.piggyback.user_id) params.append('user_id', this.piggyback.user_id);
      if (this.piggyback.search) params.append('search', this.piggyback.search.trim());
      if (this.piggyback.vertical && this.piggyback.vertical !== 'ALL') params.append('vertical', this.piggyback.vertical);
      if (this.piggyback.inventory_status && this.piggyback.inventory_status !== 'ALL') params.append('inventory_status', this.piggyback.inventory_status);
      if (this.piggyback.auto_reprice && this.piggyback.auto_reprice !== 'ALL') params.append('auto_reprice', this.piggyback.auto_reprice);
      if (this.piggyback.has_floor_price && this.piggyback.has_floor_price !== 'ALL') params.append('has_floor_price', this.piggyback.has_floor_price);
      if (this.piggyback.min_price !== '' && !isNaN(this.piggyback.min_price)) params.append('min_price', this.piggyback.min_price);
      if (this.piggyback.max_price !== '' && !isNaN(this.piggyback.max_price)) params.append('max_price', this.piggyback.max_price);
      if (this.piggyback.date_range && this.piggyback.date_range !== 'ALL') params.append('date_range', this.piggyback.date_range);
      if (this.piggyback.sort_by) params.append('sort_by', this.piggyback.sort_by);
      if (this.piggyback.brand_nature && this.piggyback.brand_nature !== 'ALL') params.append('brand_nature', this.piggyback.brand_nature);
      if (this.piggyback.is_white_label && this.piggyback.is_white_label !== 'ALL') params.append('is_white_label', this.piggyback.is_white_label);
      if (this.piggyback.has_image_logo && this.piggyback.has_image_logo !== 'ALL') params.append('has_image_logo', this.piggyback.has_image_logo);
      if (this.piggyback.image_prohibited && this.piggyback.image_prohibited !== 'ALL') params.append('image_prohibited', this.piggyback.image_prohibited);
      if (this.piggyback.violation_type && this.piggyback.violation_type !== 'ALL') params.append('violation_type', this.piggyback.violation_type);
    }

    this.showToast('正在生成并下载 Excel/CSV 报表...', 'info');
    window.location.href = `/api/piggyback/export?${params.toString()}`;
  },

  copyToClipboard(text, label = '内容') {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        this.showToast(`已复制 ${label}: ${text}`, 'success');
      }).catch(() => {
        this._fallbackCopy(text, label);
      });
    } else {
      this._fallbackCopy(text, label);
    }
  },

  _fallbackCopy(text, label) {
    const el = document.createElement('textarea');
    el.value = text;
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
    this.showToast(`已复制 ${label}: ${text}`, 'success');
  },

  batchToggleAutoReprice(enable) {
    return this.handleBatchToggleAutoReprice(enable);
  },

  getStoreName(storeId) {
    if (!storeId) return '';
    const st = (this.stores || []).find(s => s.id === storeId);
    return st ? st.name : `店铺#${storeId}`;
  },
};

export default piggybackMethods;
