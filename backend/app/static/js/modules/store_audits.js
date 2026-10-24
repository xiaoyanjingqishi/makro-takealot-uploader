/**
 * 业务模块: store_audits
 * 导出该领域的业务方法集
 */
export const store_auditsMethods = {
  changeStoreAuditStatus(stKey) {
    this.storeAudits.status = stKey;
    this.loadStoreAudits(1);
  },


  async loadStoreAudits(page = 1) {
    if (!this.selectedStoreId) return;
    this.storeAudits.loading = true;
    this.storeAudits.page = page;
    try {
      let url = `/api/store-audits/list?store_id=${this.selectedStoreId}&page=${page}&page_size=${this.storeAudits.pageSize}`;
      if (this.storeAudits.status && this.storeAudits.status !== 'ALL') {
        url += `&status=${encodeURIComponent(this.storeAudits.status)}`;
      }
      if (this.storeAudits.search) {
        url += `&search=${encodeURIComponent(this.storeAudits.search)}`;
      }
      if (this.storeAudits.vertical) {
        url += `&vertical=${encodeURIComponent(this.storeAudits.vertical)}`;
      }
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.storeAudits.items = data.items || [];
        this.storeAudits.total = data.total || 0;
        this.storeAudits.counts = data.state_counts || {};
        this.storeAudits.verticals = data.verticals || [];
        this.storeAudits.draftCount = (data.state_counts && data.state_counts.DRAFT) || 0;
      } else if (res.status === 401) {
        this.checkAuth();
      } else {
        const err = await res.json();
        this.showToast(`❌ 加载审核列表失败: ${err.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      console.error('加载审核列表失败:', e);
    } finally {
      this.storeAudits.loading = false;
    }
  },


  async syncStoreAudits() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择店铺', 'warning');
      return;
    }
    this.syncingStoreAudits = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 45000);
    try {
      const res = await fetch(`/api/store-audits/sync?store_id=${this.selectedStoreId}&max_pages=30&async_task=true`, {
        method: 'POST',
        headers: this.getAuthHeaders(),
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      const data = await res.json();
      if (res.ok && data.success) {
        if (data.task_id) {
          const storeObj = (this.stores || []).find(s => s.id === this.selectedStoreId);
          const storeName = storeObj ? storeObj.name : `店铺 ${this.selectedStoreId}`;
          this.addOrUpdateBgTask({
            id: data.task_id,
            task_type: 'SYNC_STORE_AUDITS',
            name: `同步在途审核流转 [${storeName}]`,
            status: 'RUNNING',
            total: 0,
            current: 0,
            progress: 0,
            current_title: '正在拉取 Makro 官方在途审核记录...',
            message: data.message
          });
          this.showToast(`🚀 ${data.message}`, 'info');
        } else {
          this.showToast(`🎉 ${data.message}`, 'success');
          if (data.state_counts) {
            this.storeAudits.counts = data.state_counts;
          }
          await this.loadStoreAudits(1);
        }
      } else {
        this.showToast('同步审核列表失败: ' + (data.detail || data.message || '未知错误'), 'error');
      }
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') {
        this.showToast('同步启动请求超时，请稍后刷新查看', 'error');
      } else {
        this.showToast('同步官方审核流转网络异常: ' + (e.message || e), 'error');
      }
    } finally {
      this.syncingStoreAudits = false;
    }
  },


  openAuditDetail(it) {
    this.activeAuditDetail = it;
    this.showAuditDetailModal = true;
  },


  openAuditLocalProduct(localP) {
    this.activeAuditLocalProduct = localP;
    this.showAuditLocalProductModal = true;
  },


  jumpToLocalProduct(target) {
    this.showAuditDetailModal = false;
    this.showAuditLocalProductModal = false;

    let targetSku = '';
    if (typeof target === 'string') {
      targetSku = target;
    } else if (target && typeof target === 'object') {
      // 优先提取 SKU: audit item 的 sku_id，或关联 local_product 的 sku_id / makro_sku_id
      targetSku = target.sku_id || (target.local_product && (target.local_product.sku_id || target.local_product.makro_sku_id)) || target.makro_sku_id || '';
      // 若没有特定 SKU，才降级使用商品 ID 或数字
      if (!targetSku && target.id && isNaN(target.id) === false) {
        targetSku = String(target.id);
      }
    } else if (target) {
      targetSku = String(target);
    }

    this.switchTab('products');
    if (targetSku) {
      this.searchQuery = targetSku.trim();
      this.statusFilter = ''; // 清除选品箱状态限制 (保证能跨状态搜到该商品)
      this.complianceFilter = ''; // 清除合规状态限制
      this.userFilter = ''; // 清除人员过滤限制
    }
    this.loadProducts(1);
  },
};

export default store_auditsMethods;
