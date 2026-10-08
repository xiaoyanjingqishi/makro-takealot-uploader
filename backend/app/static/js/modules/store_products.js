/**
 * 业务模块: store_products
 * 导出该领域的业务方法集
 */
export const store_productsMethods = {
  changeStoreProductState(stKey) {
    this.storeProducts.internal_state = stKey;
    this.loadStoreProducts(1);
  },,

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
  },,

  async syncStoreProducts() {
    if (!this.selectedStoreId) {
      alert('请先选择要同步的店铺');
      return;
    }
    this.syncingStoreProducts = true;
    try {
      const res = await fetch(`/api/store-products/sync?store_id=${this.selectedStoreId}`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        if (data.state_counts) {
          this.storeProducts.counts = data.state_counts;
        }
        await this.loadStoreProducts(1);
      } else {
        alert('同步商品失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('同步官方商品网络异常: ' + e);
    } finally {
      this.syncingStoreProducts = false;
    }
  },,

  startEditInventory(item) {
    this.editingInventoryId = item.id;
    this.editingInventoryVal = item.inventory;
  },,

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
  },,

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
  },,

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
  },,

  toggleSelectAllStoreProducts() {
    this.isAllStoreProductsSelected = !this.isAllStoreProductsSelected;
    if (this.isAllStoreProductsSelected) {
      const pageSkus = (this.storeProducts.items || []).map(it => it.sku_id);
      const set = new Set([...this.selectedStoreProductSkus, ...pageSkus]);
      this.selectedStoreProductSkus = Array.from(set);
    }
  },,

  openBatchInventoryModal() {
    if (!this.isAllStoreProductsSelected && this.selectedStoreProductSkus.length === 0) {
      this.showToast('请先勾选需要修改库存的商品', 'warning');
      return;
    }
    this.showBatchInventoryModal = true;
  },,

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
    try {
      const payload = {
        store_id: this.selectedStoreId,
        new_inventory: qty,
        select_all: this.isAllStoreProductsSelected,
        status: this.storeProducts.internal_state,
        sku_ids: this.isAllStoreProductsSelected ? null : this.selectedStoreProductSkus
      };
      const res = await fetch('/api/store-products/batch-update-inventory', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        this.showBatchInventoryModal = false;
        this.selectedStoreProductSkus = [];
        this.isAllStoreProductsSelected = false;
        await this.loadStoreProducts(this.storeProducts.page);
      } else {
        alert('批量修改库存失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('批量修改库存网络异常: ' + e);
    } finally {
      this.isSubmittingBatchInventory = false;
    }
  },
};

export default store_productsMethods;
