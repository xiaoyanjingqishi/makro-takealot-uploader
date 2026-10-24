/**
 * 业务模块: orders
 * Makro 订单履约追踪、7 阶流程流转、全链路单号追溯与 PO 批次财务核算
 */
export const ordersMethods = {
  changeOrderStatus(stKey) {
    this.orders.status = stKey;
    this.loadStoreOrders(1);
  },

  changeFulfillmentStatus(stKey) {
    this.orders.fulfillment_status = stKey;
    this.loadStoreOrders(1);
  },

  async loadStoreOrders(page = 1) {
    if (!this.selectedStoreId) return;
    this.orders.loading = true;
    this.orders.page = page;
    try {
      let url = `/api/store-orders/list?store_id=${this.selectedStoreId}&page=${page}&page_size=${this.orders.pageSize}`;
      if (this.orders.status && this.orders.status !== 'all') {
        url += `&status=${encodeURIComponent(this.orders.status)}`;
      }
      if (this.orders.fulfillment_status && this.orders.fulfillment_status !== 'all') {
        url += `&fulfillment_status=${encodeURIComponent(this.orders.fulfillment_status)}`;
      }
      if (this.orders.search) {
        url += `&search=${encodeURIComponent(this.orders.search)}`;
      }
      if (this.orders.order_date_start) {
        url += `&order_date_start=${encodeURIComponent(this.orders.order_date_start)}`;
      }
      if (this.orders.order_date_end) {
        url += `&order_date_end=${encodeURIComponent(this.orders.order_date_end)}`;
      }
      if (this.orders.label_printed && this.orders.label_printed !== 'all') {
        url += `&label_printed=${encodeURIComponent(this.orders.label_printed)}`;
      }

      // 附加可配置结算参数
      const cfg = this.orderFinanceConfig || {};
      url += `&commission_rate=${cfg.commission_rate ?? 17.5}`;
      url += `&shipping_fee_zar=${cfg.shipping_fee_zar ?? 70.0}`;
      url += `&exchange_rate=${cfg.exchange_rate ?? 0.39}`;

      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.orders.items = data.items || [];
        this.orders.total = data.total || 0;
        this.orders.counts = data.counts || {};
        this.orders.fulfillment_counts = data.fulfillment_counts || {};
        this.orders.financial_summary = data.financial_summary || {};
        // 清理已不存在的选中项
        this.orders.selectedIds = (this.orders.selectedIds || []).filter(id => 
          (this.orders.items || []).some(o => o.id === id)
        );
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
  },

  // 勾选管理
  toggleSelectAllOrders() {
    const allIds = (this.orders.items || []).map(o => o.id);
    if (!this.orders.selectedIds) this.orders.selectedIds = [];
    if (this.orders.selectedIds.length === allIds.length && allIds.length > 0) {
      this.orders.selectedIds = [];
    } else {
      this.orders.selectedIds = [...allIds];
    }
  },

  toggleSelectOrder(orderId) {
    if (!this.orders.selectedIds) this.orders.selectedIds = [];
    const idx = this.orders.selectedIds.indexOf(orderId);
    if (idx > -1) {
      this.orders.selectedIds.splice(idx, 1);
    } else {
      this.orders.selectedIds.push(orderId);
    }
  },

  // 单笔流转履约状态
  async updateOrderFulfillment(order, newStatus) {
    if (order.fulfillment_status === newStatus) return;
    try {
      const res = await fetch('/api/store-orders/update-fulfillment', {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          order_ids: [order.id],
          fulfillment_status: newStatus
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        order.fulfillment_status = newStatus;
        this.showToast(`✅ 订单 #${order.order_id} 状态已流转至: ${this.formatFulfillmentState(newStatus)}`, 'success');
        // 重新核算此单利润
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 状态更新失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('状态更新网络异常: ' + e.message, 'error');
    }
  },

  // 批量流转履约状态
  async batchUpdateFulfillment(newStatus) {
    if (!this.orders.selectedIds || this.orders.selectedIds.length === 0) {
      this.showToast('请先勾选需要流转状态的订单', 'warning');
      return;
    }
    try {
      const res = await fetch('/api/store-orders/update-fulfillment', {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          order_ids: this.orders.selectedIds,
          fulfillment_status: newStatus
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 成功批量流转 ${data.updated_count} 笔订单至: ${this.formatFulfillmentState(newStatus)}`, 'success');
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 批量流转失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('批量流转网络异常: ' + e.message, 'error');
    }
  },

  // 行内保存单笔订单采购成本与快递单号
  async saveOrderProcurementCost(order) {
    try {
      const res = await fetch('/api/store-orders/update-procurement-cost', {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          order_id: order.id,
          purchase_cost: parseFloat(order.purchase_cost || 0),
          domestic_logistics_fee: parseFloat(order.domestic_logistics_fee || 0),
          purchase_order_sn: (order.purchase_order_sn || '').trim(),
          purchase_notes: (order.purchase_notes || '').trim(),
          domestic_tracking_no: (order.domestic_tracking_no || '').trim()
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`✅ 订单 #${order.order_id} 采购成本与单号已保存`, 'success');
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 保存失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('保存采购信息网络异常: ' + e.message, 'error');
    }
  },

  // 打开创建 PO 批次弹窗
  openCreatePoModal() {
    if (!this.orders.selectedIds || this.orders.selectedIds.length === 0) {
      this.showToast('请先勾选要合并为 PO 批次的订单', 'warning');
      return;
    }
    const dateStr = new Date().toISOString().slice(2, 10).replace(/-/g, '');
    const randSuffix = Math.floor(Math.random() * 900 + 100);

    // 计算勾选订单的累计估算重量 (kg)
    let selectedWeightKg = 0;
    if (this.orders.items && this.orders.selectedIds) {
      const selectedOrders = this.orders.items.filter(o => this.orders.selectedIds.includes(o.id));
      const totalG = selectedOrders.reduce((acc, o) => acc + (Number(o.total_weight_g) || 0), 0);
      selectedWeightKg = Number((totalG / 1000.0).toFixed(2));
    }

    this.poCreateForm = {
      po_number: `PO${dateStr}-${randSuffix}`,
      name: `${new Date().getMonth() + 1}月${new Date().getDate()}日南非空运拼单批次`,
      forwarder_name: '易达货代',
      air_waybill_no: '',
      domestic_logistics_fee: 0.0,
      head_haul_fee: 300.0,
      logistics_loss_fee: 30.0,
      box_length_cm: null,
      box_width_cm: null,
      box_height_cm: null,
      package_weight_kg: selectedWeightKg > 0 ? selectedWeightKg : null,
      selected_orders_weight_kg: selectedWeightKg,
      notes: '',
      submitting: false
    };
    this.showCreatePoModal = true;
  },

  // 提交创建 PO 并合并归档勾选订单
  async submitCreatePo() {
    this.poCreateForm.submitting = true;
    try {
      const res = await fetch('/api/store-orders/po/create-and-assign', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          store_id: this.selectedStoreId,
          po_number: this.poCreateForm.po_number.trim(),
          name: this.poCreateForm.name.trim(),
          forwarder_name: this.poCreateForm.forwarder_name.trim(),
          air_waybill_no: this.poCreateForm.air_waybill_no.trim(),
          domestic_logistics_fee: parseFloat(this.poCreateForm.domestic_logistics_fee || 0),
          head_haul_fee: parseFloat(this.poCreateForm.head_haul_fee || 0),
          logistics_loss_fee: parseFloat(this.poCreateForm.logistics_loss_fee || 0),
          box_length_cm: parseFloat(this.poCreateForm.box_length_cm || 0),
          box_width_cm: parseFloat(this.poCreateForm.box_width_cm || 0),
          box_height_cm: parseFloat(this.poCreateForm.box_height_cm || 0),
          package_weight_kg: parseFloat(this.poCreateForm.package_weight_kg || 0),
          notes: this.poCreateForm.notes.trim(),
          order_ids: this.orders.selectedIds
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        this.showCreatePoModal = false;
        this.orders.selectedIds = [];
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 创建 PO 失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('创建 PO 网络异常: ' + e.message, 'error');
    } finally {
      this.poCreateForm.submitting = false;
    }
  },

  // 打开归档至已有 PO 弹窗
  async openAssignPoModal() {
    if (!this.orders.selectedIds || this.orders.selectedIds.length === 0) {
      this.showToast('请先勾选需要归档或移出的订单', 'warning');
      return;
    }
    await this.loadPoList();
    this.assignPoTargetPoId = null;
    this.showAssignPoModal = true;
  },

  // 提交加入已有 PO 或移出 PO
  async submitAssignPo() {
    this.assignPoSubmitting = true;
    try {
      const res = await fetch('/api/store-orders/po/assign-existing', {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          po_id: this.assignPoTargetPoId ? parseInt(this.assignPoTargetPoId) : null,
          order_ids: this.orders.selectedIds
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        this.showAssignPoModal = false;
        this.orders.selectedIds = [];
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 归档失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('归档网络异常: ' + e.message, 'error');
    } finally {
      this.assignPoSubmitting = false;
    }
  },

  // 加载 PO 列表及财务账目
  async loadPoList() {
    this.poListModal.loading = true;
    try {
      const cfg = this.orderFinanceConfig || {};
      const url = `/api/store-orders/po/list?store_id=${this.selectedStoreId}&commission_rate=${cfg.commission_rate ?? 17.5}&shipping_fee_zar=${cfg.shipping_fee_zar ?? 70.0}&exchange_rate=${cfg.exchange_rate ?? 0.39}`;
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.poListModal.list = data.items || [];
      }
    } catch (e) {
      console.error('加载 PO 列表失败:', e);
    } finally {
      this.poListModal.loading = false;
    }
  },

  // 打开 PO 批次财务总账管理弹窗
  async openPoListModal() {
    await this.loadPoList();
    this.poListModal.open = true;
  },

  // 行内快速更新 PO 信息 (国内快递费、头程费、损耗费、提单号等)
  async updatePoDetails(po) {
    try {
      const res = await fetch(`/api/store-orders/po/update/${po.id}`, {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          name: po.name,
          forwarder_name: po.forwarder_name,
          air_waybill_no: po.air_waybill_no,
          domestic_logistics_fee: parseFloat(po.domestic_logistics_fee || 0),
          head_haul_fee: parseFloat(po.head_haul_fee || 0),
          logistics_loss_fee: parseFloat(po.logistics_loss_fee || 0),
          box_length_cm: parseFloat(po.box_length_cm || 0),
          box_width_cm: parseFloat(po.box_width_cm || 0),
          box_height_cm: parseFloat(po.box_height_cm || 0),
          package_weight_kg: parseFloat(po.package_weight_kg || 0),
          notes: po.notes
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`✅ PO [${po.po_number}] 箱规、实重与费用已更新`, 'success');
        await this.loadPoList();
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 更新 PO 失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('更新 PO 网络异常: ' + e.message, 'error');
    }
  },

  // PO 内所有订单一键批量流转
  async batchFulfillPoOrders(po, targetStatus) {
    if (!confirm(`确定将 PO [${po.po_number}] 名下所有 ${po.orders_count} 笔订单统一流转至「${this.formatFulfillmentState(targetStatus)}」吗？`)) {
      return;
    }
    try {
      const res = await fetch(`/api/store-orders/po/batch-update-fulfillment/${po.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ target_status: targetStatus })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        await this.loadPoList();
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 一键流转失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('一键流转网络异常: ' + e.message, 'error');
    }
  },

  // 解散/删除 PO
  async disbandPo(po) {
    if (!confirm(`确定解散 PO [${po.po_number}] 吗？关联订单将自动恢复为未归档状态。`)) {
      return;
    }
    try {
      const res = await fetch(`/api/store-orders/po/delete/${po.id}`, {
        method: 'DELETE',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`✅ ${data.message}`, 'success');
        await this.loadPoList();
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 解散 PO 失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('解散 PO 网络异常: ' + e.message, 'error');
    }
  },

  // 打开结算参数配置弹窗
  // 一键将指定订单从 PO 批次中剔除
  async removeOrderFromPo(po, subO) {
    const orderSn = subO.order_id || subO.id;
    if (!confirm(`确定将订单 #${orderSn} 从批次 [${po.po_number}] 中剔除吗？\n剔除后该订单将恢复为未归档状态。`)) {
      return;
    }
    try {
      const res = await fetch('/api/store-orders/po/remove-order', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...this.getAuthHeaders()
        },
        body: JSON.stringify({
          po_id: po.id,
          order_id: subO.id
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`✅ ${data.message || '订单已成功移出 PO 批次'}`, 'success');
        await this.loadPoList();
        await this.loadStoreOrders(this.orders.page);
      } else {
        this.showToast(`❌ 剔除订单失败: ${data.detail || data.message || '未知错误'}`, 'error');
      }
    } catch (e) {
      this.showToast('剔除订单网络异常: ' + e.message, 'error');
    }
  },

  // 预览大图容错代理方法
  previewImage(url) {
    if (typeof this.openImagePreview === 'function') {
      this.openImagePreview(url);
    }
  },

  openOrderFinanceConfigModal() {
    this.showOrderFinanceConfigModal = true;
  },

  // 保存结算参数并实时重算
  saveOrderFinanceConfig() {
    try {
      localStorage.setItem('makro_order_finance_cfg', JSON.stringify(this.orderFinanceConfig));
    } catch (e) {}
    this.showOrderFinanceConfigModal = false;
    this.showToast('⚙️ 结算参数已更新，正在按新费率重新核算...', 'info');
    this.loadStoreOrders(1);
  },

  // 同步 Makro 官方订单
  async syncStoreOrders() {
    if (!this.selectedStoreId) {
      this.showToast('请先选择要同步的店铺', 'warning');
      return;
    }
    this.syncingOrders = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 45000);
    try {
      const res = await fetch(`/api/store-orders/sync?store_id=${this.selectedStoreId}&async_task=true`, {
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
            task_type: 'SYNC_STORE_ORDERS',
            name: `同步官方订单 [${storeName}]`,
            status: 'RUNNING',
            total: 0,
            current: 0,
            progress: 0,
            current_title: '正在连接 Makro 获取各状态订单...',
            message: data.message
          });
          this.showToast(`🚀 ${data.message}`, 'info');
        } else {
          this.showToast(`🎉 ${data.message}`, 'success');
          await this.loadStoreOrders(1);
        }
      } else {
        this.showToast('同步订单失败: ' + (data.detail || data.message || '未知错误'), 'error');
      }
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') {
        this.showToast('同步启动请求超时，请稍后刷新查看', 'error');
      } else {
        this.showToast('同步官方订单网络异常: ' + (e.message || e), 'error');
      }
    } finally {
      this.syncingOrders = false;
    }
  },

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

  formatFulfillmentState(st) {
    const map = {
      'all': '全部阶段',
      'NEW': '新订单',
      'PURCHASED': '已采购',
      'SENT_TO_FORWARDER': '发送货代',
      'AIR_FREIGHT': '货代空运',
      'ARRIVED_SA': '到达南非',
      'SA_PICKUP': '南非取件',
      'COMPLETED': '订单完结',
      'CANCELLED_RTO': '异常/退回'
    };
    return map[st] || st;
  },

  getFulfillmentBadgeClass(st) {
    const map = {
      'NEW': 'bg-blue-50 text-blue-700 border-blue-200',
      'PURCHASED': 'bg-indigo-50 text-indigo-700 border-indigo-200',
      'SENT_TO_FORWARDER': 'bg-purple-50 text-purple-700 border-purple-200',
      'AIR_FREIGHT': 'bg-sky-50 text-sky-700 border-sky-200',
      'ARRIVED_SA': 'bg-amber-50 text-amber-800 border-amber-200',
      'SA_PICKUP': 'bg-orange-50 text-orange-800 border-orange-200',
      'COMPLETED': 'bg-emerald-50 text-emerald-700 border-emerald-200',
      'CANCELLED_RTO': 'bg-rose-50 text-rose-700 border-rose-200 font-bold'
    };
    return map[st] || 'bg-slate-100 text-slate-700 border-slate-200';
  },

  // 视角切换: 订单明细视角 vs PO 批次核算视角
  switchOrdersPerspective(persp) {
    this.orders.activePerspective = persp;
    if (persp === 'po') {
      this.loadPoList();
    } else {
      this.loadStoreOrders(this.orders.page);
    }
  },

  // PO 订单明细折叠/展开
  togglePoExpand(poId) {
    if (!this.poExpandedIds) this.poExpandedIds = [];
    const idx = this.poExpandedIds.indexOf(poId);
    if (idx > -1) {
      this.poExpandedIds.splice(idx, 1);
    } else {
      this.poExpandedIds.push(poId);
    }
  },

  // 打开单笔订单称重录入弹窗
  openWeightModal(order) {
    const rawItems = order.order_items || [];
    const items = rawItems.map(oi => ({
      sku_id: oi.sku_id,
      title: oi.title,
      image_url: oi.image_url,
      quantity: oi.quantity || 1,
      price: oi.price,
      weight_g: oi.weight_g !== undefined && oi.weight_g !== null ? oi.weight_g : 0
    }));

    // 计算初始总克重 (支持录入为 0 克)
    let totalG = items.reduce((acc, it) => acc + (Number(it.weight_g) || 0) * (Number(it.quantity) || 1), 0);
    if (order.total_weight_g !== null && order.total_weight_g !== undefined) {
      totalG = Number(order.total_weight_g);
      if (items.length === 1) {
        items[0].weight_g = Math.round(totalG / (items[0].quantity || 1));
      }
    }

    const defaultRate = order.air_freight_rate || this.orderFinanceConfig?.air_freight_rate || this.settings?.default_air_freight_rate || 90.0;

    this.weightModal = {
      open: true,
      order: order,
      items: items,
      totalWeightG: totalG,
      airFreightRate: defaultRate,
      syncToCatalog: true,
      propagateToSameSku: true,
      submitting: false
    };
  },

  // 单品重量变动时，实时自动汇总订单总重量
  onWeightItemChange() {
    if (!this.weightModal.items) return;
    this.weightModal.totalWeightG = this.weightModal.items.reduce((acc, it) => {
      return acc + (Number(it.weight_g) || 0) * (Number(it.quantity) || 1);
    }, 0);
  },

  // 修改订单总克重时，若只有单件商品则反向均摊单品重量
  onTotalWeightChange() {
    if (!this.weightModal.items || this.weightModal.items.length === 0) return;
    if (this.weightModal.items.length === 1) {
      const qty = Number(this.weightModal.items[0].quantity) || 1;
      this.weightModal.items[0].weight_g = Math.round((Number(this.weightModal.totalWeightG) || 0) / qty);
    }
  },

  // 保存称重信息并重新核算头程与利润
  async saveOrderWeight() {
    if (!this.weightModal.order) return;
    this.weightModal.submitting = true;
    try {
      const itemsList = (this.weightModal.items || []).map(it => ({
        sku_id: it.sku_id,
        weight_g: Number(it.weight_g) || 0
      }));
      const itemsWeightMap = {};
      itemsList.forEach(it => {
        if (it.sku_id) itemsWeightMap[it.sku_id] = it.weight_g;
      });

      const res = await fetch('/api/store-orders/update-weight-and-freight', {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          order_id: this.weightModal.order.id,
          total_weight_g: (this.weightModal.totalWeightG !== null && this.weightModal.totalWeightG !== '' && !isNaN(this.weightModal.totalWeightG)) ? Number(this.weightModal.totalWeightG) : 0,
          air_freight_rate: Number(this.weightModal.airFreightRate) || 90.0,
          items_weight: itemsWeightMap,
          items: itemsList,
          sync_to_catalog: !!this.weightModal.syncToCatalog,
          sync_same_sku_orders: !!this.weightModal.propagateToSameSku,
          propagate_to_same_sku: !!this.weightModal.propagateToSameSku
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message || '订单称重与头程成本核算已保存'}`, 'success');
        this.weightModal.open = false;
        await this.loadStoreOrders(this.orders.page);
        if (this.orders.activePerspective === 'po') {
          await this.loadPoList();
        }
      } else {
        let errMsg = '未知错误';
        if (typeof data.detail === 'string') {
          errMsg = data.detail;
        } else if (Array.isArray(data.detail)) {
          errMsg = data.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
        } else if (data.message) {
          errMsg = data.message;
        } else if (data.detail && typeof data.detail === 'object') {
          errMsg = JSON.stringify(data.detail);
        }
        this.showToast(`❌ 保存称重失败: ${errMsg}`, 'error');
      }
    } catch (e) {
      this.showToast('保存称重网络异常: ' + e.message, 'error');
    } finally {
      this.weightModal.submitting = false;
    }
  },

  // 单张 40×30mm 标签 PDF 下载
  async downloadOrderLabel(orderId, orderSn) {
    try {
      this.showToast(`⏳ 正在生成订单 #${orderSn || orderId} 40×30mm 热敏防混标签 PDF...`, 'info');
      const res = await fetch(`/api/store-orders/labels/${orderId}/pdf`, {
        headers: this.getAuthHeaders()
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        this.showToast(`❌ 下载标签失败: ${err.detail || res.statusText}`, 'error');
        return;
      }
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Label_${orderSn || orderId}_40x30.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      // 更新本地订单状态为已打单
      const targetOrder = (this.orders.items || []).find(o => o.id === orderId);
      if (targetOrder) {
        targetOrder.label_printed = true;
      }
      this.showToast(`🏷️ 标签已生成并下载 (40×30mm)`, 'success');
    } catch (e) {
      this.showToast(`下载标签异常: ${e.message}`, 'error');
    }
  },

  // 批量合并多页 40×30mm 标签 PDF 连打下载
  async downloadBatchLabels(orderIds = null) {
    const ids = orderIds || this.orders.selectedIds;
    if (!ids || ids.length === 0) {
      this.showToast('请先勾选需要批量打印标签的订单', 'warning');
      return;
    }
    try {
      this.showToast(`⏳ 正在合并生成 ${ids.length} 张 40×30mm 标签 PDF...`, 'info');
      const res = await fetch('/api/store-orders/batch-labels/pdf', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ order_ids: ids })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        this.showToast(`❌ 批量生成标签失败: ${err.detail || res.statusText}`, 'error');
        return;
      }
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Batch_Labels_${ids.length}_Orders_40x30.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      // 批量更新本地订单状态为已打单
      (this.orders.items || []).forEach(o => {
        if (ids.includes(o.id)) {
          o.label_printed = true;
        }
      });
      this.showToast(`🎉 成功导出 ${ids.length} 笔订单合并连打标签 PDF (40×30mm)`, 'success');
    } catch (e) {
      this.showToast(`导出标签异常: ${e.message}`, 'error');
    }
  },

  // PO 批次名下所有订单标签一键批量打印
  downloadPoLabels(po) {
    if (!po.orders || po.orders.length === 0) {
      this.showToast('该 PO 批次下暂无关联订单', 'warning');
      return;
    }
    const ids = po.orders.map(o => o.id);
    this.downloadBatchLabels(ids);
  },

  // 手动切换标签打印标记
  async toggleOrderLabelPrinted(order) {
    if (!order) return;
    try {
      const res = await fetch(`/api/store-orders/${order.id}/toggle-label-printed`, {
        method: 'PUT',
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        order.label_printed = data.label_printed;
        order.label_printed_at = data.label_printed_at;
        this.showToast(data.label_printed ? '🏷️ 已标记为已打单' : '⏳ 已重置为未打单', 'success');
      } else {
        const err = await res.json().catch(() => ({}));
        this.showToast(`切换打单状态失败: ${err.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      this.showToast('切换打单状态网络异常: ' + e.message, 'error');
    }
  },

  // 快捷设置出单日期范围过滤
  setDateFilter(range) {
    const today = new Date();
    const formatDate = (d) => {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${day}`;
    };
    if (range === 'today') {
      this.orders.order_date_start = formatDate(today);
      this.orders.order_date_end = formatDate(today);
    } else if (range === '7days') {
      const past = new Date(today.getTime() - 6 * 24 * 3600 * 1000);
      this.orders.order_date_start = formatDate(past);
      this.orders.order_date_end = formatDate(today);
    } else if (range === '30days') {
      const past = new Date(today.getTime() - 29 * 24 * 3600 * 1000);
      this.orders.order_date_start = formatDate(past);
      this.orders.order_date_end = formatDate(today);
    } else if (range === 'clear') {
      this.orders.order_date_start = '';
      this.orders.order_date_end = '';
    }
    this.loadStoreOrders(1);
  },

  // 查看订单全生命周期状态轨迹弹窗
  async openStatusHistoryModal(order) {
    this.orderStatusHistoryModal = {
      open: true,
      loading: true,
      order: order,
      logs: []
    };
    try {
      const res = await fetch(`/api/store-orders/${order.id}/status-history`, {
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        this.orderStatusHistoryModal.logs = data.history || [];
      } else {
        const err = await res.json().catch(() => ({}));
        this.showToast(`加载状态流转记录失败: ${err.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      this.showToast(`加载轨迹网络异常: ${e.message}`, 'error');
    } finally {
      this.orderStatusHistoryModal.loading = false;
    }
  },

  closeStatusHistoryModal() {
    this.orderStatusHistoryModal.open = false;
    this.orderStatusHistoryModal.order = null;
    this.orderStatusHistoryModal.logs = [];
  }
};

export default ordersMethods;
