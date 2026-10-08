/**
 * 业务模块: orders
 * 导出该领域的业务方法集
 */
export const ordersMethods = {
  changeOrderStatus(stKey) {
    this.orders.status = stKey;
    this.loadStoreOrders(1);
  },,

  async loadStoreOrders(page = 1) {
    if (!this.selectedStoreId) return;
    this.orders.loading = true;
    this.orders.page = page;
    try {
      let url = `/api/store-orders/list?store_id=${this.selectedStoreId}&page=${page}&page_size=${this.orders.pageSize}`;
      if (this.orders.status) {
        url += `&status=${encodeURIComponent(this.orders.status)}`;
      }
      if (this.orders.search) {
        url += `&search=${encodeURIComponent(this.orders.search)}`;
      }
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.orders.items = data.items || [];
        this.orders.total = data.total || 0;
        this.orders.counts = data.counts || {};
      } else if (res.status === 401) {
        this.checkAuth();
      } else {
        const err = await res.json();
        this.showToast(`❌ 加载订单失败: ${err.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      console.error('加载店铺订单失败:', e);
    } finally {
      this.orders.loading = false;
    }
  },,

  async syncStoreOrders() {
    if (!this.selectedStoreId) {
      alert('请先选择要同步的店铺');
      return;
    }
    this.syncingOrders = true;
    try {
      const res = await fetch(`/api/store-orders/sync?store_id=${this.selectedStoreId}`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        await this.loadStoreOrders(1);
      } else {
        alert('同步订单失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('同步官方订单网络异常: ' + e);
    } finally {
      this.syncingOrders = false;
    }
  },,

  formatOrderState(st) {
    const map = {
      'all': '全部订单',
      'pending_rtd': '待发货 (Pending RTD)',
      'in_transit': '运输中 (In Transit)',
      'completed': '已完成 (Delivered)',
      'sla_breached': '🚨 SLA 超期预警',
      'approved': '待确认',
      'packed': '已打包',
      'rtd': '准备发货',
      'pending_labels': '待打单',
      'shipments_to_pack': '待打单',
      'pending_handover': '待交运',
      'shipments_to_handover': '待交运',
      'shipped': '已发货',
      'delivered': '已签收送达',
      'shipments_delivered': '已签收送达',
      'cancelled': '已取消'
    };
    return map[st] || st;
  },
};

export default ordersMethods;
