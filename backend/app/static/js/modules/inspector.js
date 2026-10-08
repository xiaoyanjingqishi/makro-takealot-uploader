/**
 * 业务模块: inspector
 * 导出该领域的业务方法集
 */
export const inspectorMethods = {
  openInspector(item) {
    this.lastInspectedId = item.id;
    this.activeItem = JSON.parse(JSON.stringify(item));
    this.currentTab = 'inspector';
    this.fetchFullProductDetails(item.id);
  },


  async saveActiveItem() {
    try {
      const res = await fetch(`/api/products/${this.activeItem.id}`, {
        method: 'PUT',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          makro_title: this.activeItem.makro_title,
          makro_vertical: this.activeItem.makro_vertical,
          makro_brand: this.activeItem.makro_brand,
          makro_selling_price: this.activeItem.makro_selling_price,
          makro_mrp: this.activeItem.makro_mrp,
          makro_description: this.activeItem.makro_description,
          makro_catalog_attributes: this.activeItem.makro_catalog_attributes,
          sku_id: this.activeItem.sku_id,
          barcode: this.activeItem.barcode,
          colour: this.activeItem.colour,
          size: this.activeItem.size,
          brand_colour: this.activeItem.brand_colour,
          pack_of: this.activeItem.pack_of,
          variant_attributes: this.activeItem.variant_attributes
        })
      });
      const updated = await res.json();
      this.activeItem = updated;

      // 内存就地同步更新选品箱列表中的该条记录，避免重新请求与重置滚动条
      if (this.products && this.products.items) {
        const idx = this.products.items.findIndex(p => p.id === this.activeItem.id);
        if (idx !== -1) {
          const cur = this.products.items[idx];
          cur.makro_title = updated.makro_title;
          cur.makro_vertical = updated.makro_vertical;
          cur.makro_brand = updated.makro_brand;
          cur.makro_selling_price = updated.makro_selling_price;
          cur.makro_mrp = updated.makro_mrp;
          cur.sku_id = updated.sku_id;
          cur.barcode = updated.barcode;
          cur.colour = updated.colour;
          cur.size = updated.size;
          cur.brand_colour = updated.brand_colour;
          cur.pack_of = updated.pack_of;
          cur.variant_attributes = updated.variant_attributes;
          cur.updated_at = updated.updated_at;
        }
      }
      alert('修改已保存！');
    } catch (e) {
      alert('保存失败: ' + e);
    }
  },


  async reCleanActiveItem(mode = null) {
    this.isCleaning = true;
    const targetMode = mode || this.settings.cleaner_mode || 'text';
    const modeName = targetMode === 'vision' ? '图文多模态' : '纯文本';
    try {
      const res = await fetch(`/api/cleaner/clean/${this.activeItem.id}?mode=${targetMode}`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '清洗接口请求失败');
      }
      this.activeItem = await res.json();

      // 内存就地同步更新选品箱列表中的清洗状态与结果，避免全表刷新
      if (this.products && this.products.items) {
        const idx = this.products.items.findIndex(p => p.id === this.activeItem.id);
        if (idx !== -1) {
          const cur = this.products.items[idx];
          cur.makro_title = this.activeItem.makro_title;
          cur.makro_vertical = this.activeItem.makro_vertical;
          cur.makro_brand = this.activeItem.makro_brand;
          cur.makro_selling_price = this.activeItem.makro_selling_price;
          cur.makro_mrp = this.activeItem.makro_mrp;
          cur.status = this.activeItem.status;
          cur.clean_mode = this.activeItem.clean_mode;
          cur.updated_at = this.activeItem.updated_at;
        }
      }
      alert(`🎉 AI ${modeName}清洗完成！已更新 Makro 属性与建议标题。`);
    } catch (e) {
      alert(`清洗失败: ${e.message || e}`);
    } finally {
      this.isCleaning = false;
    }
  },


  onInspectorSellingPriceInput() {
    if (this.activeItem) {
      const ratio = Number(this.settings.mrp_ratio) || 1.5;
      const price = Number(this.activeItem.makro_selling_price) || 0;
      this.activeItem.makro_mrp = Math.round(price * ratio);
    }
  },
};

export default inspectorMethods;
