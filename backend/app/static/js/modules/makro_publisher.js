/**
 * 业务模块: makro_publisher
 * 导出该领域的业务方法集
 */
export const makro_publisherMethods = {
  async publishItem(item) {
    this.triggerProductPublish(item);
  },


  async publishSingleVariant(v) {
    if (!confirm(`确认单独上传变体【${v.sku_id}】(${v.variant_title || v.colour || v.size}) 到 Makro 吗？`)) return;
    this.publishingVariantId = v.id;
    const taskId = `var_pub_${v.id}_${Date.now()}`;
    const varLabel = v.variant_title || v.colour || v.size || v.sku_id;

    this.addOrUpdateBgTask({
      id: taskId,
      task_type: 'BATCH_PUBLISH',
      name: `🚀 变体独立上品: ${v.sku_id}`,
      status: 'RUNNING',
      is_single: true,
      total: 1,
      current: 0,
      progress: 20,
      success_count: 0,
      fail_count: 0,
      current_title: `正在为变体【${varLabel}】初始化草稿并上传图组...`,
      message: `正在连接 Makro 上传变体: ${v.sku_id}`
    });

    let stepProgress = 20;
    const stepTimer = setInterval(() => {
      if (stepProgress < 85) {
        stepProgress += 15;
        this.addOrUpdateBgTask({
          id: taskId,
          task_type: 'BATCH_PUBLISH',
          name: `🚀 变体独立上品: ${v.sku_id}`,
          status: 'RUNNING',
          is_single: true,
          total: 1,
          current: 0,
          progress: stepProgress,
          success_count: 0,
          fail_count: 0,
          current_title: '正在中转图组至 Makro CDN 并提交平台审核...',
          message: `正在处理变体: ${v.sku_id}`
        });
      }
    }, 800);

    try {
      const res = await fetch(`/api/makro/publish-variant/${v.id}`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      clearInterval(stepTimer);
      let data = {};
      try {
        data = await res.json();
      } catch(jsonErr) {
        data = { message: res.statusText || '服务器响应异常' };
      }
      if (res.ok && data.success) {
        const reqId = data.result?.request_id || data.request_id || '已生成';
        this.addOrUpdateBgTask({
          id: taskId,
          task_type: 'BATCH_PUBLISH',
          name: `🚀 变体独立上品: ${v.sku_id}`,
          status: 'SUCCESS',
          is_single: true,
          total: 1,
          current: 1,
          progress: 100,
          success_count: 1,
          fail_count: 0,
          current_title: `🎉 变体 ${v.sku_id} 上品成功！已提交审核 (RequestId: ${reqId})`,
          message: `变体【${varLabel}】已成功提交平台审核！`
        });
        this.showToast(`🎉 变体 ${v.sku_id} 上品成功！`, 'success');
        setTimeout(() => this.dismissBgTask(taskId), 15000);
        v.status = 'SUBMITTED';
        v.makro_submit_error = null;
      } else {
        const errMsg = data.message || data.detail || '未知错误';
        this.addOrUpdateBgTask({
          id: taskId,
          task_type: 'BATCH_PUBLISH',
          name: `🚀 变体独立上品: ${v.sku_id}`,
          status: 'FAILED',
          is_single: true,
          total: 1,
          current: 1,
          progress: 100,
          success_count: 0,
          fail_count: 1,
          current_title: `❌ 变体上品失败: ${errMsg}`,
          message: `变体【${varLabel}】未能通过校验`
        });
        setTimeout(() => this.dismissBgTask(taskId), 15000);
        alert(`❌ 变体上品失败: ${errMsg}`);
        v.status = 'FAILED';
        v.makro_submit_error = errMsg;
      }
      await this.loadProducts();
      if (this.activeItem) {
        const updated = this.products.items.find(x => x.id === this.activeItem.id);
        if (updated) this.activeItem = updated;
      }
      this.loadTasks();
    } catch (e) {
      clearInterval(stepTimer);
      this.addOrUpdateBgTask({
        id: taskId,
        task_type: 'BATCH_PUBLISH',
        name: `🚀 变体独立上品: ${v.sku_id}`,
        status: 'FAILED',
        is_single: true,
        total: 1,
        current: 1,
        progress: 100,
        success_count: 0,
        fail_count: 1,
        current_title: `❌ 上传网络异常: ${e}`,
        message: `网络连接中断`
      });
      alert('上传过程发生网络异常: ' + e);
    } finally {
      this.publishingVariantId = null;
    }
  },


  openBatchPublishModal() {
    if (this.selectedIds.length === 0 || this.isBatchPublishing) {
      this.showToast('请先勾选需要上品的商品！', 'warning');
      return;
    }
    this.batchPublishTarget = 'all';
    if (this.activeStores.length > 0 && !this.batchPublishTargetStoreId) {
      this.batchPublishTargetStoreId = this.activeStores[0].id;
    }
    this.batchPublishForce = false;
    this.showBatchPublishModal = true;
  },


  async executeBatchPublish() {
    if (this.selectedIds.length === 0 || this.isBatchPublishing) return;
    this.showBatchPublishModal = false;
    this.isBatchPublishing = true;
    const targetIds = [...this.selectedIds];
    const isAllStores = this.batchPublishTarget === 'all';
    const targetStoreId = isAllStores ? null : this.batchPublishTargetStoreId;
    const storeLabel = isAllStores ? `全部已启用店铺 (共 ${this.activeStores.length} 家)` : (this.stores.find(s => s.id === targetStoreId)?.name || '指定店铺');

    // 立即创建本地占位任务，提供零延迟视觉反馈
    const tempTaskId = 'batch_pub_temp_' + Date.now();
    this.addOrUpdateBgTask({
      id: tempTaskId,
      task_type: 'BATCH_PUBLISH',
      name: `批量上品至 Makro (${storeLabel})`,
      status: 'RUNNING',
      total: targetIds.length,
      current: 0,
      progress: 0,
      success_count: 0,
      fail_count: 0,
      current_title: '正在连接 Makro 卖家中心并初始化后台任务...',
      message: `准备推送 ${targetIds.length} 个商品...`
    });
    this.ensureBgTasksPolling();

    try {
      const queryParam = this.batchPublishForce ? '?force=true' : '';
      const res = await fetch(`/api/makro/batch-publish${queryParam}`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          product_ids: targetIds,
          publish_all_stores: isAllStores,
          store_id: targetStoreId,
          force: this.batchPublishForce,
          concurrency: this.settings.publish_concurrency || 2
        })
      });
      const resData = await res.json();
      // 移除临时占位任务
      this.dismissBgTask(tempTaskId);

      if (resData.task_id) {
        this.addOrUpdateBgTask({
          id: resData.task_id,
          task_type: 'BATCH_PUBLISH',
          name: `批量上品至 Makro (${storeLabel})`,
          status: 'RUNNING',
          total: resData.total || targetIds.length,
          current: 0,
          progress: 0,
          success_count: 0,
          fail_count: 0,
          current_title: '正在连接 Makro 卖家中心并发上品...',
          message: resData.message
        });
        this.ensureBgTasksPolling();
        this.selectedIds = [];
        this.showToast(`🚀 已启动批量上品任务: ${storeLabel}`, 'info');
      } else {
        alert(`🚀 批量上品完成: 成功提交 ${resData.success} 件, 失败 ${resData.failed} 件`);
        this.selectedIds = [];
        await this.loadProducts();
        this.loadTasks();
      }
    } catch (e) {
      this.dismissBgTask(tempTaskId);
      alert('批量上品失败: ' + e);
    } finally {
      this.isBatchPublishing = false;
    }
  },


  triggerProductPublish(item) {
    if (!item) return;
    this.singlePublishItem = item;
    this.singlePublishForce = item.compliance_status === 'PROHIBITED';
    this.showSinglePublishModal = true;
  },


  async doPublishItem(item, storeId, publishAll, force) {
    if (!item) return;
    this.showSinglePublishModal = false;
    this.publishingId = item.id;

    const targetStoreObj = !publishAll ? this.stores.find(s => s.id === storeId) : null;
    const targetBrand = targetStoreObj ? (targetStoreObj.default_brand || 'Beishi') : '各店品牌';
    const targetStoreName = publishAll 
      ? `全部已启用店铺 (共 ${this.activeStores.length} 家 · 各店品牌)` 
      : `${targetStoreObj?.name || '指定店铺'} · 品牌: ${targetBrand}`;
    const pTitle = (item.makro_title || item.takealot_title || `商品 #${item.id}`).slice(0, 32);
    const taskId = `single_pub_${item.id}_${Date.now()}`;

    // 立即创建置顶后台任务进度条
    this.addOrUpdateBgTask({
      id: taskId,
      task_type: 'BATCH_PUBLISH',
      name: `🚀 商品上品至 Makro (${targetStoreName})`,
      status: 'RUNNING',
      is_single: true,
      total: 1,
      current: 0,
      progress: 15,
      success_count: 0,
      fail_count: 0,
      current_title: `正在为【${targetStoreName}】建立官方 Listing 草稿...`,
      message: `正在处理: ${pTitle}`
    });

    // 启动模拟阶段式步进动画，让用户感知到图组上传与提交审核的真实进度
    let stepProgress = 15;
    const stepTimer = setInterval(() => {
      if (stepProgress < 85) {
        stepProgress += Math.floor(Math.random() * 10) + 8;
        if (stepProgress > 85) stepProgress = 85;
        let stepDesc = '正在处理商品属性并准备提交平台审核...';
        if (stepProgress < 40) {
          stepDesc = `正在连接 Makro 并为【${targetStoreName}】初始化草稿...`;
        } else if (stepProgress < 75) {
          stepDesc = `正在将 Takealot 图组并发中转上传至 Makro CDN 静态加速源...`;
        }
        this.addOrUpdateBgTask({
          id: taskId,
          task_type: 'BATCH_PUBLISH',
          name: `🚀 商品上品至 Makro (${targetStoreName})`,
          status: 'RUNNING',
          is_single: true,
          total: 1,
          current: 0,
          progress: stepProgress,
          success_count: 0,
          fail_count: 0,
          current_title: stepDesc,
          message: `正在处理: ${pTitle}`
        });
      }
    }, 900);

    try {
      const queryParams = new URLSearchParams();
      if (publishAll) queryParams.set('publish_all_stores', 'true');
      if (storeId) queryParams.set('store_id', storeId);
      if (force) queryParams.set('force', 'true');

      const res = await fetch(`/api/makro/publish/${item.id}?${queryParams.toString()}`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      clearInterval(stepTimer);

      let data = {};
      try {
        data = await res.json();
      } catch(jsonErr) {
        data = { message: res.statusText || '服务器响应异常' };
      }

      if (res.ok && data.success) {
        const reqIdStr = data.request_id ? ` (RequestId: ${data.request_id})` : '';
        this.addOrUpdateBgTask({
          id: taskId,
          task_type: 'BATCH_PUBLISH',
          name: `🚀 商品上品至 Makro (${targetStoreName})`,
          status: 'SUCCESS',
          is_single: true,
          total: 1,
          current: 1,
          progress: 100,
          success_count: 1,
          fail_count: 0,
          current_title: `🎉 刊登成功，已提交平台审核${reqIdStr}`,
          message: `商品已成功提交至【${targetStoreName}】！`
        });
        this.showToast(`🎉 上品成功！已提交至 Makro${reqIdStr}`, 'success');
        setTimeout(() => {
          this.dismissBgTask(taskId);
        }, 15000);
      } else {
        const errMsg = data.message || data.detail || (typeof data === 'string' ? data : '未知校验错误');
        this.addOrUpdateBgTask({
          id: taskId,
          task_type: 'BATCH_PUBLISH',
          name: `🚀 商品上品至 Makro (${targetStoreName})`,
          status: 'FAILED',
          is_single: true,
          total: 1,
          current: 1,
          progress: 100,
          success_count: 0,
          fail_count: 1,
          current_title: `❌ 上品失败: ${errMsg}`,
          message: `提交至【${targetStoreName}】未通过平台校验`
        });
        setTimeout(() => {
          this.dismissBgTask(taskId);
        }, 15000);
        alert(`❌ 上品未能通过校验: ${errMsg}`);
      }
      await this.loadProducts();
      if (this.activeItem && this.activeItem.id === item.id) {
        const updated = this.products.items.find(x => x.id === item.id);
        if (updated) this.activeItem = updated;
      }
      this.loadTasks();
    } catch (e) {
      clearInterval(stepTimer);
      this.addOrUpdateBgTask({
        id: taskId,
        task_type: 'BATCH_PUBLISH',
        name: `🚀 商品上品至 Makro (${targetStoreName})`,
        status: 'FAILED',
        is_single: true,
        total: 1,
        current: 1,
        progress: 100,
        success_count: 0,
        fail_count: 1,
        current_title: `❌ 上传网络异常: ${e}`,
        message: `提交至【${targetStoreName}】发生网络中断`
      });
      alert('上传过程发生网络异常: ' + e);
    } finally {
      this.publishingId = null;
    }
  }
};

export default makro_publisherMethods;
