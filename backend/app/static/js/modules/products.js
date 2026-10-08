/**
 * 业务模块: products
 * 导出该领域的业务方法集
 */
export const productsMethods = {
  async loadProducts(page = null) {
    if (page !== null && page >= 1) {
      this.currentPage = page;
    }
    if (this.productAbortController) {
      try { this.productAbortController.abort(); } catch (_) {}
    }
    this.productAbortController = new AbortController();
    this.loadingProducts = true;
    try {
      let url = `/api/products?page=${this.currentPage}&page_size=${this.pageSize}`;
      if (this.searchQuery) url += `&search=${encodeURIComponent(this.searchQuery)}`;
      if (this.statusFilter) url += `&status=${this.statusFilter}`;
      if (this.complianceFilter) url += `&compliance_status=${this.complianceFilter}`;
      if (this.userFilter) url += `&user_id=${encodeURIComponent(this.userFilter)}`;
      if (this.storePublishFilterStoreId) url += `&store_id=${this.storePublishFilterStoreId}`;
      if (this.storePublishStatus && this.storePublishStatus !== 'ALL') url += `&publish_status=${this.storePublishStatus}`;
      if (this.minPriceFilter !== null && this.minPriceFilter !== undefined && this.minPriceFilter !== '') {
        url += `&min_price=${this.minPriceFilter}`;
      }
      if (this.maxPriceFilter !== null && this.maxPriceFilter !== undefined && this.maxPriceFilter !== '') {
        url += `&max_price=${this.maxPriceFilter}`;
      }
      const res = await fetch(url, { signal: this.productAbortController.signal, headers: this.getAuthHeaders() });
      const data = await res.json();
      
      // 一次性高性能数据预处理挂载 (Zero-Cost Template Access)
      if (data && Array.isArray(data.items)) {
        for (let i = 0; i < data.items.length; i++) {
          const it = data.items[i];
          it._displayImage = this.getItemImage(it);
          it._createdTime = this.formatDateTime(it.created_at);
          it._updatedTime = (it.updated_at && it.updated_at !== it.created_at) ? this.formatDateTime(it.updated_at) : '';
          it._statusLabel = this.formatStatus(it.status);
          it._statusBadgeClass = this.getStatusBadgeClass(it.status);
          it._topKeywords = (it.seo_keywords && it.seo_keywords.length > 3) ? it.seo_keywords.slice(0, 3) : (it.seo_keywords || []);
          it._cleanGroupTitle = (it.takealot_title || '').replace(/\s*-\s*[^-]+-[^-]+$/, '').replace(/\s*-\s*[^-]+$/, '').trim();
        }
      }

      this.products = data;
      if (data.status_counts) {
        this.statusCounts = data.status_counts;
      }
      if (data.compliance_counts) {
        this.complianceCounts = data.compliance_counts;
      }
      if (!this.activeItem && this.products.items && this.products.items.length > 0) {
        this.activeItem = JSON.parse(JSON.stringify(this.products.items[0]));
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      console.error(e);
    } finally {
      if (!this.productAbortController || !this.productAbortController.signal.aborted) {
        this.loadingProducts = false;
      }
    }
  },,

  async batchClean() {
    if (this.selectedIds.length === 0 || this.isCleaning) return;
    this.isCleaning = true;
    const targetIds = [...this.selectedIds];
    const mode = this.batchCleanMode || this.settings.cleaner_mode || 'text';
    const modeName = mode === 'vision' ? '图文多模态' : '纯文本';
    try {
      const res = await fetch('/api/cleaner/batch-clean', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ product_ids: targetIds, clean_mode: mode })
      });
      const resData = await res.json();
      if (resData.task_id) {
        this.addOrUpdateBgTask({
          id: resData.task_id,
          task_type: 'BATCH_CLEAN',
          name: `批量AI数据清洗 (${modeName})`,
          status: 'RUNNING',
          total: targetIds.length,
          current: 0,
          progress: 0,
          success_count: 0,
          fail_count: 0,
          current_title: '正在启动并发清洗线程...',
          message: resData.message
        });
        this.ensureBgTasksPolling();
        this.selectedIds = [];
      } else {
        alert(`🎉 批量AI清洗完成: 成功 ${resData.success} 件, 失败 ${resData.failed} 件`);
        this.selectedIds = [];
        await this.loadProducts();
      }
    } catch (e) {
      alert('批量清洗操作失败: ' + e);
    } finally {
      this.isCleaning = false;
    }
  },,

  async batchPublish() {
    this.openBatchPublishModal();
  },,

  async batchCheckCompliance() {
    if (this.selectedIds.length === 0 || this.isBatchCheckingCompliance) return;
    this.isBatchCheckingCompliance = true;
    const targetIds = [...this.selectedIds];
    try {
      const res = await fetch('/api/cleaner/batch-compliance', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ product_ids: targetIds, concurrency: 20 })
      });
      const resData = await res.json();
      if (resData.task_id) {
        this.addOrUpdateBgTask({
          id: resData.task_id,
          task_type: 'BATCH_COMPLIANCE',
          name: '批量合规与侵权排查 (20线程并发)',
          status: 'RUNNING',
          total: targetIds.length,
          current: 0,
          progress: 0,
          success_count: 0,
          fail_count: 0,
          current_title: '正在以 20 线程并发排查品牌侵权与违禁属性...',
          message: resData.message
        });
        this.ensureBgTasksPolling();
        this.selectedIds = [];
      } else {
        alert(`批量合规排查完成: 成功检测 ${resData.success} 件, 失败 ${resData.failed} 件`);
        this.selectedIds = [];
        await this.loadProducts();
      }
    } catch (e) {
      alert('批量合规检测失败: ' + e);
    } finally {
      this.isBatchCheckingCompliance = false;
    }
  },,

  async submitQuickCollect() {
    const info = this.parsedPlidInfo;
    if (!info || info.status !== 'valid' || this.isQuickCollecting) return;
    this.isQuickCollecting = true;
    try {
      // 与浏览器插件传递完全相同的规范化 payload: { plid, url }
      const res = await fetch('/api/products/collect-by-plid', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          plid: info.plid,
          url: info.url
        })
      });
      if (res.ok) {
        const p = await res.json();
        const varCount = p.total_variants || (p.variants ? p.variants.length : 1);
        const title = p.takealot_title || info.plid;
        const price = p.makro_selling_price ? `售价: R${p.makro_selling_price}, ` : '';
        alert(`✅ 采集成功！已通过官方 API 采集【${title}】\n${price}变体数: ${varCount}个`);
        this.showQuickCollectModal = false;
        this.quickCollectInput = '';
        await this.loadProducts();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`❌ 采集失败: ${err.detail || res.statusText}`);
      }
    } catch (e) {
      alert(`❌ 采集请求失败: ${e}`);
    } finally {
      this.isQuickCollecting = false;
    }
  },,

  handleCsvFileSelect(e) {
    const files = e.target.files || (e.dataTransfer && e.dataTransfer.files);
    if (!files || files.length === 0) return;
    const file = files[0];
    this.csvFile = file;
    this.csvFileName = file.name;
    const reader = new FileReader();
    reader.onload = (evt) => {
      try {
        const text = evt.target.result;
        const matches = text.match(/\b\d{7,10}\b/g) || [];
        const unique = new Set(matches);
        this.csvPlidCount = unique.size;
      } catch (err) {
        this.csvPlidCount = 0;
      }
    };
    reader.readAsText(file.slice(0, 204800));
  },,

  handleCsvFileDrop(e) {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      this.handleCsvFileSelect(e);
    }
  },,

  async submitCsvCollect() {
    if (!this.csvFile || this.isCsvCollecting) return;
    this.isCsvCollecting = true;
    try {
      const formData = new FormData();
      formData.append('file', this.csvFile);
      formData.append('skip_existing', this.csvSkipExisting ? 'true' : 'false');
      const res = await fetch('/api/products/import-csv', {
        method: 'POST',
        headers: this.getAuthHeaders(),
        body: formData
      });
      if (res.ok) {
        const data = await res.json();
        if (data.task_id) {
          this.activeBgTasks.unshift({
            task_id: data.task_id,
            type: 'batch_csv_import',
            task_name: 'CSV 商品批量采集',
            status: 'RUNNING',
            total: data.total || 0,
            current: 0,
            progress: 0,
            success_count: 0,
            fail_count: 0,
            current_title: `已启动 6 线程并发采集 ${data.total} 件商品...`,
            message: data.message
          });
          this.ensureBgTasksPolling();
        }
        alert(`🚀 ${data.message || '批量采集任务已在后台启动！'}\n共计 ${data.total} 件商品，您可随时在顶部任务栏查看采集进度。`);
        this.showQuickCollectModal = false;
        this.csvFile = null;
        this.csvFileName = '';
        this.csvPlidCount = 0;
        if (this.$refs.csvFileInput) {
          this.$refs.csvFileInput.value = '';
        }
        await this.loadProducts();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`❌ 启动批量采集失败: ${err.detail || res.statusText}`);
      }
    } catch (e) {
      alert(`❌ 请求失败: ${e}`);
    } finally {
      this.isCsvCollecting = false;
    }
  },,

  async showComplianceModal(item) {
    this.complianceModalItem = item;
    // 若列表中数据因精简缺少双 AI 会审明细，异步自动拉取完整详情补全
    if (item && item.compliance_status && item.compliance_status !== 'PENDING_CHECK' && (!item.compliance_details || !item.compliance_details.qwen_verdict)) {
      try {
        const res = await fetch(`/api/products/${item.id}`);
        if (res.ok) {
          const fullItem = await res.json();
          if (fullItem.compliance_details) {
            item.compliance_details = fullItem.compliance_details;
            if (this.complianceModalItem && this.complianceModalItem.id === item.id) {
              this.complianceModalItem.compliance_details = fullItem.compliance_details;
            }
          }
        }
      } catch (e) {
        console.warn('自动获取完整合规报告异常', e);
      }
    }
  },,

  getProhibitedSummary(item) {
    if (!item.compliance_details) return '违禁';
    const p = item.compliance_details.prohibited_items;
    return p && p.length > 0 ? p.join('、') : '违禁';
  },,

  adoptRecommendedTitle(item) {
    if (!item.compliance_details || !item.compliance_details.brand_info || !item.compliance_details.brand_info.recommended_title) return;
    const rec = item.compliance_details.brand_info.recommended_title;
    item.makro_title = rec;
    if (item.makro_catalog_attributes) {
      if (typeof item.makro_catalog_attributes === 'string') {
        try {
          item.makro_catalog_attributes = JSON.parse(item.makro_catalog_attributes);
        } catch(e) {}
      }
      if (item.makro_catalog_attributes.model_number && item.makro_catalog_attributes.model_number[0]) {
        item.makro_catalog_attributes.model_number[0].value = rec;
      }
    }
    this.showToast('✅ 已采纳合规建议标题！', 'success');
  },,

  async checkCompliance(item) {
    this.checkingComplianceId = item.id;
    try {
      const res = await fetch(`/api/cleaner/check-compliance/${item.id}`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        item.compliance_status = data.compliance_status;
        item.compliance_details = data.compliance_details;
        if (this.activeItem && this.activeItem.id === item.id) {
          this.activeItem.compliance_status = data.compliance_status;
          this.activeItem.compliance_details = data.compliance_details;
        }
        if (this.complianceModalItem && this.complianceModalItem.id === item.id) {
          this.complianceModalItem.compliance_status = data.compliance_status;
          this.complianceModalItem.compliance_details = data.compliance_details;
        }
        if (!this.complianceModalItem && this.currentTab !== 'inspector') {
          this.complianceModalItem = item;
        }
      } else {
        alert('合规检测失败: ' + res.statusText);
      }
    } catch (e) {
      alert('合规检测网络异常: ' + e);
    } finally {
      this.checkingComplianceId = null;
    }
  },,

  async submitArbitration(item, humanVerdict) {
    if (!item) return;
    this.isArbitrating = true;
    try {
      const res = await fetch(`/api/cleaner/arbitrate-compliance/${item.id}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          human_verdict: humanVerdict,
          human_notes: this.arbitrationNotes || ''
        })
      });
      if (res.ok) {
        const data = await res.json();
        item.compliance_status = data.compliance_status;
        item.compliance_details = data.compliance_details;
        if (this.activeItem && this.activeItem.id === item.id) {
          this.activeItem.compliance_status = data.compliance_status;
          this.activeItem.compliance_details = data.compliance_details;
        }
        if (this.complianceModalItem && this.complianceModalItem.id === item.id) {
          this.complianceModalItem.compliance_status = data.compliance_status;
          this.complianceModalItem.compliance_details = data.compliance_details;
        }
        this.showToast(`✅ 人工终审完成！已裁定为【${humanVerdict}】，归因: ${data.error_attribution}`, 'success');
        this.arbitrationNotes = '';
      } else {
        const err = await res.json().catch(() => ({}));
        alert('人工仲裁提交失败: ' + (err.detail || res.statusText));
      }
    } catch (e) {
      alert('人工仲裁提交异常: ' + e);
    } finally {
      this.isArbitrating = false;
    }
  },,

  getRiskBadgeClass(level) {
    if (level === 'PROHIBITED') return 'bg-rose-600 text-white';
    if (level === 'RISK') return 'bg-amber-500 text-white';
    if (level === 'SAFE') return 'bg-emerald-600 text-white';
    return 'bg-slate-400 text-white';
  },,

  getTextRiskColor(level) {
    if (level === 'PROHIBITED') return 'text-rose-600';
    if (level === 'RISK') return 'text-amber-600';
    return 'text-emerald-600';
  },,

  async deleteItem(item) {
    const isAbandoned = item.status === 'ABANDONED';
    const title = item.makro_title || item.takealot_title;
    const confirmMsg = isAbandoned
      ? `【⚠️ 彻底删除警告】\n确定要永久彻底删除商品【${title}】吗？\n删除后将从数据库彻底抹除，不可恢复！`
      : `确定要将商品【${title}】移入弃用箱吗？\n移入后可在【🗑️ 弃用商品】分页中随时恢复或彻底删除。`;

    if (!confirm(confirmMsg)) return;

    try {
      const res = await fetch(`/api/products/${item.id}`, { 
        method: 'DELETE',
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        if (isAbandoned) {
          this.showToast('💥 商品已永久彻底删除！', 'info');
        } else {
          this.showToast('🗑️ 商品已移入弃用箱！', 'warning');
        }
        if (this.activeItem && this.activeItem.id === item.id) {
          this.currentTab = 'products';
          this.activeItem = null;
        }
        this.loadProducts();
      } else {
        alert('操作失败: ' + res.statusText);
      }
    } catch (e) {
      alert('操作出错: ' + e);
    }
  },,

  async batchDelete() {
    if (this.selectedIds.length === 0) return;
    const isAbandonedTab = this.statusFilter === 'ABANDONED';
    const confirmMsg = isAbandonedTab
      ? `【⚠️ 批量彻底删除警告】\n确定要永久彻底删除选中的 ${this.selectedIds.length} 件弃用商品吗？\n删除后数据不可恢复！`
      : `确定要将选中的 ${this.selectedIds.length} 件商品移入弃用箱吗？\n移入后可在【🗑️ 弃用商品】分页中随时恢复或彻底删除。`;

    if (!confirm(confirmMsg)) return;

    try {
      const res = await fetch('/api/products/batch-delete', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ product_ids: this.selectedIds })
      });
      const data = await res.json();
      this.showToast(`✅ ${data.message || '操作成功'}`, isAbandonedTab ? 'info' : 'warning');
      this.selectedIds = [];
      this.loadProducts();
    } catch (e) {
      alert('批量删除出错: ' + e);
    }
  },,

  async restoreItem(item) {
    try {
      const res = await fetch(`/api/products/${item.id}/restore`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast('✅ 商品已成功恢复至选品箱！', 'success');
        if (this.activeItem && this.activeItem.id === item.id) {
          this.activeItem.status = data.status;
        }
        this.loadProducts();
      } else {
        alert('恢复失败: ' + (data.detail || data.message));
      }
    } catch (e) {
      alert('恢复网络异常: ' + e);
    }
  },,

  async batchRestore() {
    if (this.selectedIds.length === 0) return;
    try {
      const res = await fetch('/api/products/batch-restore', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ product_ids: this.selectedIds })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`✅ ${data.message || '批量恢复成功！'}`, 'success');
        this.selectedIds = [];
        this.loadProducts();
      } else {
        alert('批量恢复失败: ' + (data.detail || data.message));
      }
    } catch (e) {
      alert('批量恢复网络异常: ' + e);
    }
  },,

  clearSelection() {
    this.selectedIds = [];
  },,

  toggleSelectAll(e) {
    const pageIds = (this.products.items || []).map(x => x.id);
    if (e.target.checked) {
      this.selectedIds = Array.from(new Set([...this.selectedIds, ...pageIds]));
    } else {
      const pageIdSet = new Set(pageIds);
      this.selectedIds = this.selectedIds.filter(id => !pageIdSet.has(id));
    }
  },,

  async selectAllFiltered() {
    if (this.loadingAllFiltered) return;
    const total = (this.products && this.products.total) || 0;
    if (total === 0) {
      this.showToast('⚠️ 当前过滤条件下暂无商品', 'warning');
      return;
    }
    this.loadingAllFiltered = true;
    try {
      let filterParams = '';
      if (this.searchQuery) filterParams += `&search=${encodeURIComponent(this.searchQuery)}`;
      if (this.statusFilter) filterParams += `&status=${this.statusFilter}`;
      if (this.complianceFilter) filterParams += `&compliance_status=${this.complianceFilter}`;
      if (this.userFilter) filterParams += `&user_id=${encodeURIComponent(this.userFilter)}`;
      if (this.minPriceFilter !== null && this.minPriceFilter !== undefined && this.minPriceFilter !== '') {
        filterParams += `&min_price=${this.minPriceFilter}`;
      }
      if (this.maxPriceFilter !== null && this.maxPriceFilter !== undefined && this.maxPriceFilter !== '') {
        filterParams += `&max_price=${this.maxPriceFilter}`;
      }

      // 优先尝试 /api/products/ids 极速端点
      let ids = [];
      try {
        const res = await fetch(`/api/products/ids?${filterParams.replace(/^&/, '')}`, {
          headers: this.getAuthHeaders()
        });
        if (res.ok) {
          const data = await res.json();
          if (data && Array.isArray(data.ids)) {
            ids = data.ids;
          }
        }
      } catch (_) {}

      // 若后端新路由未热重载则自适应回退到分页并发聚合兜底 (500条/页高速聚合)
      if (!ids || ids.length === 0) {
        const pageSize = 500;
        const totalPages = Math.ceil(total / pageSize);
        const fetchPage = async (p) => {
          const res = await fetch(`/api/products?page=${p}&page_size=${pageSize}${filterParams}`, {
            headers: this.getAuthHeaders()
          });
          if (res.ok) {
            const d = await res.json();
            return (d.items || []).map(x => x.id);
          }
          return [];
        };

        const promises = [];
        for (let p = 1; p <= totalPages; p++) {
          promises.push(fetchPage(p));
        }
        const results = await Promise.all(promises);
        const aggregated = [];
        for (const list of results) {
          aggregated.push(...list);
        }
        ids = Array.from(new Set(aggregated));
      }

      if (ids.length > 0) {
        this.selectedIds = ids;
        this.showToast(`✅ 已全选当前过滤项下的全部 ${ids.length} 件商品！`, 'success');
      } else {
        this.showToast('⚠️ 未能获取到过滤商品', 'warning');
      }
    } catch (err) {
      console.error(err);
      this.showToast('获取全量过滤项失败: ' + (err.message || err), 'error');
    } finally {
      this.loadingAllFiltered = false;
    }
  },,

  formatStatus(st) {
    const map = {
      'PENDING_CLEAN': '待AI清洗',
      'CLEANED': '已清洗待上',
      'SUBMITTED': '已提交审核',
      'QC_PENDING': '平台质检中',
      'ACTIVE': '销售中',
      'FAILED': '上品失败',
      'ABANDONED': '已弃用'
    };
    return map[st] || st;
  },,

  getStatusBadgeClass(st) {
    if (st === 'CLEANED') return 'bg-blue-100 text-blue-800';
    if (st === 'SUBMITTED' || st === 'QC_PENDING' || st === 'SUCCESS') return 'bg-emerald-100 text-emerald-800';
    if (st === 'ACTIVE') return 'bg-green-100 text-green-900 font-bold';
    if (st === 'FAILED') return 'bg-red-100 text-red-800';
    if (st === 'ABANDONED') return 'bg-slate-200 text-slate-700 font-medium';
    return 'bg-slate-100 text-slate-700';
  },,

  getListingForStore(item, storeId) {
    if (!item || !item.store_listings) return null;
    return item.store_listings.find(l => l.store_id === storeId);
  },,

  getDisplayMakroSku(item) {
    if (!item) return '';
    if (this.selectedStoreId && item.store_listings && item.store_listings.length) {
      const matched = item.store_listings.find(sl => sl.store_id === this.selectedStoreId);
      if (matched && matched.makro_sku_id) return matched.makro_sku_id;
    }
    if (item.makro_sku_id) return item.makro_sku_id;
    if (item.store_listings && item.store_listings.length) {
      const anyWithSku = item.store_listings.find(sl => sl.makro_sku_id);
      if (anyWithSku) return anyWithSku.makro_sku_id;
    }
    return '';
  },,

  async quickUpdatePrice(item) {
    const ratio = Number(this.settings.mrp_ratio) || 1.5;
    const price = Number(item.makro_selling_price) || 0;
    item.makro_mrp = Math.round(price * ratio);
    try {
      const res = await fetch(`/api/products/${item.id}`, {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          makro_selling_price: item.makro_selling_price,
          makro_mrp: item.makro_mrp
        })
      });
      if (res.ok) {
        this.showToast(`✅ [${item.sku_id || item.id}] 售价已更新为 R${item.makro_selling_price}，MRP 自动同步为 R${item.makro_mrp}`, 'success');
        if (this.activeItem && this.activeItem.id === item.id) {
          this.activeItem.makro_selling_price = item.makro_selling_price;
          this.activeItem.makro_mrp = item.makro_mrp;
        }
      } else {
        this.showToast(`❌ 价格更新失败: ${res.statusText}`, 'error');
      }
    } catch (e) {
      this.showToast(`❌ 价格更新网络异常: ${e}`, 'error');
    }
  },,

  changeStatusTab(st) {
    if (this.statusFilter === st && !this.loadingProducts) return;
    this.statusFilter = st;
    this.selectedIds = [];
    this.loadProducts(1);
  },,

  async fetchFullProductDetails(productId) {
    if (!productId) return;
    try {
      const res = await fetch(`/api/products/${productId}`, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const full = await res.json();
        if (this.activeItem && this.activeItem.id === productId) {
          this.activeItem = full;
          if (!this.activeItem.makro_title_zh || !this.activeItem.takealot_title_zh) {
            this.translateActiveItemTitles();
          }
        }
      }
    } catch (e) {
      console.error('获取完整商品详情失败:', e);
    }
  },,

  openBatchPriceModal() {
    if (this.selectedIds.length === 0) return;
    this.batchPriceForm = {
      mode: 'formula',
      multiplier: 1.0,
      fixed_offset: 0,
      fixed_price: null
    };
    this.showBatchPriceModal = true;
  },,

  async submitBatchPriceUpdate() {
    if (this.selectedIds.length === 0) return;
    if (this.batchPriceForm.mode === 'fixed') {
      if (!this.batchPriceForm.fixed_price || this.batchPriceForm.fixed_price <= 0) {
        alert('请输入有效的统一固定售价！');
        return;
      }
    }
    this.isUpdatingBatchPrice = true;
    try {
      const res = await fetch('/api/products/batch-update-price', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          product_ids: this.selectedIds,
          mode: this.batchPriceForm.mode,
          fixed_price: this.batchPriceForm.fixed_price,
          multiplier: this.batchPriceForm.multiplier,
          fixed_offset: this.batchPriceForm.fixed_offset,
          mrp_ratio: this.settings.mrp_ratio || 1.5
        })
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {
        const text = await res.text();
        data = { detail: text || res.statusText || '服务器响应异常' };
      }
      if (res.ok) {
        this.showToast(`✅ ${data.message || '批量改价成功！'}`, 'success');
        this.showBatchPriceModal = false;
        this.loadProducts(this.currentPage);
      } else {
        alert('批量改价失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('批量改价网络异常: ' + e);
    } finally {
      this.isUpdatingBatchPrice = false;
    }
  },
};

export default productsMethods;
