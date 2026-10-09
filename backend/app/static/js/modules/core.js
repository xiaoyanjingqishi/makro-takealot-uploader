/**
 * 业务模块: core
 * 导出该领域的业务方法集
 */
export const coreMethods = {
  async loadVerticalTranslations() {
    try {
      const res = await fetch('/api/products/vertical-translations', {
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.translations) {
          this.verticalZhMap = Object.assign({}, this.verticalZhMap, data.translations);
        }
      }
    } catch (e) {
      console.warn('Failed to load vertical translations:', e);
    }
  },


  getVerticalZh(vert, item = null) {
    if (!vert) return '';
    const clean = String(vert).toLowerCase().trim().replace(/-/g, '_');
    if (this.verticalZhMap && this.verticalZhMap[clean]) {
      return this.verticalZhMap[clean];
    }
    if (item && item.makro_vertical_zh && (item.makro_vertical === vert)) return item.makro_vertical_zh;
    for (const [k, v] of Object.entries(this.verticalZhMap || {})) {
      if (clean.includes(k) || k.includes(clean)) return v;
    }
    const tokens = clean.split('_');
    const common = {
      'lock': '锁', 'security': '安全防盗', 'lockset': '车锁套件', 'latch': '锁扣',
      'case': '保护壳', 'cover': '保护套', 'cable': '数据线', 'holder': '支架'
    };
    const parts = tokens.map(t => common[t] || (t.charAt(0).toUpperCase() + t.slice(1)));
    return parts.join(' / ');
  },


  async translateActiveItemTitles() {
    if (!this.activeItem || !this.activeItem.id) return;
    this.translatingTitles = true;
    try {
      const res = await fetch(`/api/products/${this.activeItem.id}/translate`, {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        if (data.makro_title_zh) {
          this.activeItem.makro_title_zh = data.makro_title_zh;
        }
        if (data.takealot_title_zh) {
          this.activeItem.takealot_title_zh = data.takealot_title_zh;
        }
        this.showToast('✅ 中文翻译已更新', 'success');
        if (this.products && this.products.items) {
          const found = this.products.items.find(x => x.id === this.activeItem.id);
          if (found) {
            found.makro_title_zh = data.makro_title_zh;
            found.takealot_title_zh = data.takealot_title_zh;
          }
        }
      } else {
        this.showToast('❌ 翻译请求未成功', 'error');
      }
    } catch (e) {
      this.showToast('❌ 翻译网络异常: ' + e, 'error');
    } finally {
      this.translatingTitles = false;
    }
  },


  toggleSidebar() {
    this.sidebarCollapsed = !this.sidebarCollapsed;
    try {
      localStorage.setItem('makro_sidebar_collapsed', this.sidebarCollapsed ? 'true' : 'false');
    } catch (_) {}
  },


  getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
  },


  getAuthHeaders(extra = {}) {
    const headers = Object.assign({}, extra);
    const token = localStorage.getItem('makro_auth_token') || this.getCookie('auth_token');
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
  },


  async checkAuth() {
    let token = localStorage.getItem('makro_auth_token');
    if (!token) {
      token = this.getCookie('auth_token');
      if (token) {
        localStorage.setItem('makro_auth_token', token);
      }
    }
    if (!token) {
      this.currentUser = null;
      this.showLoginModal = true;
      return false;
    }
    try {
      const res = await fetch('/api/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        this.currentUser = data.user;
        document.cookie = 'auth_token=' + encodeURIComponent(token) + '; path=/; max-age=604800; SameSite=Lax';
        this.authorizedStores = data.authorized_stores || [];
        if (this.authorizedStores.length > 0) {
          const savedId = Number(localStorage.getItem('makro_selected_store_id'));
          if (savedId && this.authorizedStores.some(s => s.id === savedId)) {
            this.selectedStoreId = savedId;
          } else {
            this.selectedStoreId = this.authorizedStores[0].id;
            localStorage.setItem('makro_selected_store_id', this.selectedStoreId);
          }
        }
        return true;
      } else {
        this.currentUser = null;
        localStorage.removeItem('makro_auth_token');
        document.cookie = 'auth_token=; path=/; max-age=0; SameSite=Lax';
        this.showLoginModal = true;
        return false;
      }
    } catch (e) {
      console.warn('检查认证状态失败:', e);
      return false;
    }
  },


  async doLogin() {
    this.loginForm.loading = true;
    this.loginForm.error = '';
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: this.loginForm.username,
          password: this.loginForm.password
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        localStorage.setItem('makro_auth_token', data.token);
        document.cookie = 'auth_token=' + encodeURIComponent(data.token) + '; path=/; max-age=604800; SameSite=Lax';
        this.currentUser = data.user;
        this.authorizedStores = data.authorized_stores || [];
        if (this.authorizedStores.length > 0) {
          this.selectedStoreId = this.authorizedStores[0].id;
          localStorage.setItem('makro_selected_store_id', this.selectedStoreId);
        }
        this.showLoginModal = false;
        this.loginForm.password = '';
        this.showToast(`🎉 登录成功，欢迎回来 ${this.currentUser.nickname || this.currentUser.username}！`, 'success');
        // 刷新当前用户视角的选品与店铺数据
        this.loadProducts(1);
        this.loadPiggybackItems(1);
        this.loadStores();
        this.loadSettings();
        if (this.currentTab === 'piggyback') this.loadPiggybackItems(1);
        if (this.currentTab === 'store_products') this.loadStoreProducts(1);
        if (this.currentTab === 'orders') this.loadStoreOrders(1);
        if (this.currentTab === 'users' && this.currentUser.role === 'ADMIN') this.loadUsers();
      } else {
        this.loginForm.error = data.detail || data.message || '用户名或密码错误';
      }
    } catch (e) {
      this.loginForm.error = '网络请求异常: ' + e;
    } finally {
      this.loginForm.loading = false;
    }
  },


  logout() {
    localStorage.removeItem('makro_auth_token');
    document.cookie = 'auth_token=; path=/; max-age=0; SameSite=Lax';
    this.currentUser = null;
    this.authorizedStores = [];
    this.showUserMenu = false;
    this.showLoginModal = true;
    this.showToast('已安全退出登录', 'info');
  },


  openChangePasswordModal() {
    this.showUserMenu = false;
    this.changePwdForm = { old_password: '', new_password: '', confirm_password: '', loading: false, error: '' };
    this.showChangePwdModal = true;
  },


  async doChangePassword() {
    if (this.changePwdForm.new_password !== this.changePwdForm.confirm_password) {
      this.changePwdForm.error = '两次输入的新密码不一致';
      return;
    }
    if (this.changePwdForm.new_password.length < 6) {
      this.changePwdForm.error = '新密码长度不能少于 6 位';
      return;
    }
    this.changePwdForm.loading = true;
    this.changePwdForm.error = '';
    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          old_password: this.changePwdForm.old_password,
          new_password: this.changePwdForm.new_password
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast('✅ 密码修改成功！', 'success');
        this.showChangePwdModal = false;
      } else {
        this.changePwdForm.error = data.detail || data.message || '密码修改失败';
      }
    } catch (e) {
      this.changePwdForm.error = '网络请求异常: ' + e;
    } finally {
      this.changePwdForm.loading = false;
    }
  },


  async loadNetworkInfo() {
    try {
      const res = await fetch('/api/settings/network-info');
      if (res.ok) {
        this.networkInfo = await res.json();
      }
    } catch (e) {
      console.warn('获取局域网信息失败:', e);
    }
  },


  copyLanUrl() {
    if (!this.networkInfo || !this.networkInfo.lan_url) return;
    const urlToCopy = this.networkInfo.lan_url;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(urlToCopy).then(() => {
        this.copiedLan = true;
        setTimeout(() => { this.copiedLan = false; }, 2500);
      }).catch(() => {
        prompt('请手动复制局域网访问地址:', urlToCopy);
      });
    } else {
      prompt('请手动复制局域网访问地址:', urlToCopy);
    }
  },


  cleanImageUrl(url) {
    if (!url || typeof url !== 'string') return '';
    let u = url.trim();
    if (u.includes('{size}')) {
      u = u.replace(/\{size\}/g, 'pdpxl');
    }
    return u;
  },


  getImageProxyUrl(url) {
    const clean = this.cleanImageUrl(url);
    if (!clean || clean.startsWith('data:') || clean.startsWith('/api/products/image-proxy')) return clean;
    return `/api/products/image-proxy?url=${encodeURIComponent(clean)}`;
  },


  getItemImage(item) {
    if (!item) return this.placeholderImg;
    let url = null;
    if (Array.isArray(item.raw_images) && item.raw_images.length > 0 && item.raw_images[0]) {
      url = item.raw_images[0];
    } else if (Array.isArray(item.images) && item.images.length > 0 && item.images[0]) {
      url = item.images[0];
    } else if (item.variants && item.variants.length > 0 && item.variants[0].images && item.variants[0].images.length > 0) {
      url = item.variants[0].images[0];
    }
    if (!url) return this.placeholderImg;
    return this.cleanImageUrl(url);
  },


  handleImageError(event, fallbackUrl) {
    const img = event.target;
    if (!img) return;
    const currentSrc = img.getAttribute('src') || img.src || '';
    if (currentSrc && !currentSrc.includes('/api/products/image-proxy') && !currentSrc.startsWith('data:image/svg')) {
      const targetUrl = fallbackUrl || currentSrc;
      img.src = this.getImageProxyUrl(targetUrl);
    } else {
      img.src = this.placeholderImg;
    }
  },


  openImagePreview(url) {
    if (!url || (typeof url === 'string' && url.startsWith('data:image/svg'))) return;
    const clean = this.cleanImageUrl(url);
    this.previewLoading = true;
    this.previewImageFailed = false;
    this.previewImageUrl = clean;
  },


  handlePreviewImageError() {
    if (!this.previewImageUrl) return;
    if (!this.previewImageUrl.includes('/api/products/image-proxy')) {
      // 自动重试：切换为本地反向代理加速拉取
      this.previewImageUrl = this.getImageProxyUrl(this.previewImageUrl);
    } else {
      this.previewLoading = false;
      this.previewImageFailed = true;
    }
  },


  retryPreviewWithProxy() {
    if (!this.previewImageUrl) return;
    this.previewLoading = true;
    this.previewImageFailed = false;
    this.previewImageUrl = this.getImageProxyUrl(this.previewImageUrl);
  },


  onPreviewImageLoad() {
    this.previewLoading = false;
    this.previewImageFailed = false;
  },


  closeImagePreview() {
    this.previewImageUrl = null;
    this.previewLoading = false;
    this.previewImageFailed = false;
  },


  setViewMode(mode) {
    this.viewMode = mode;
    try {
      localStorage.setItem('makro_view_mode', mode);
    } catch (_) {}
  },


  toggleGroupExpand(code) {
    this.expandedGroups[code] = !this.expandedGroups[code];
  },


  isGroupExpanded(code) {
    return !!this.expandedGroups[code];
  },


  isGroupSelected(grp) {
    if (!grp.items || grp.items.length === 0) return false;
    const s = this.selectedIdSet;
    return grp.items.every(x => s.has(x.id));
  },


  toggleGroupSelect(grp, e) {
    const ids = grp.items.map(x => x.id);
    if (e.target.checked) {
      this.selectedIds = Array.from(new Set([...this.selectedIds, ...ids]));
    } else {
      this.selectedIds = this.selectedIds.filter(id => !ids.includes(id));
    }
  },


  showToast(message, type = 'info', duration = 3000) {
    const id = Date.now() + Math.random();
    this.toasts.push({ id, message, type });
    setTimeout(() => {
      this.toasts = this.toasts.filter(t => t.id !== id);
    }, duration);
  },


  openConfirm(options) {
    this.confirmDialog = {
      show: true,
      title: options.title || '操作确认',
      message: options.message || '确定要执行此操作吗？',
      confirmText: options.confirmText || '确定',
      cancelText: options.cancelText || '取消',
      type: options.type || 'primary',
      action: options.onConfirm || null
    };
  },


  async handleConfirm() {
    const act = this.confirmDialog.action;
    this.confirmDialog.show = false;
    if (act && typeof act === 'function') {
      await act();
    }
  },


  onSelectVertical(val) {
    if (val && this.activeItem) {
      this.activeItem.makro_vertical = val;
    }
  },


  expandAllGroups(expand = true) {
    const map = {};
    if (expand) {
      for (const g of this.groupedProducts) {
        map[g.group_code] = true;
      }
    }
    this.expandedGroups = map;
  },


  switchTab(tabId) {
    if ((tabId === 'settings' || tabId === 'users' || tabId === 'management') && (!this.currentUser || this.currentUser.role !== 'ADMIN')) {
      this.showToast('⛔ 无权访问：该功能仅限系统管理员使用', 'error');
      this.currentTab = 'products';
      return;
    }
    const prevTab = this.currentTab;
    this.currentTab = tabId;
    try { localStorage.setItem('makro_current_tab', tabId); } catch (_) {}
    if (tabId === 'inspector' && this.activeItem && this.activeItem.id && (!this.activeItem.takealot_specs || !this.activeItem.makro_catalog_attributes)) {
      this.fetchFullProductDetails(this.activeItem.id);
    }
    if (tabId === 'products' && prevTab === 'inspector' && this.lastInspectedId) {
      this.scrollToProduct(this.lastInspectedId);
    }
    if (tabId === 'piggyback') {
      this.loadPiggybackItems(1);
    }
    if (tabId === 'store_products') {
      this.loadStoreProducts(1);
    }
    if (tabId === 'store_audits') {
      this.loadStoreAudits(1);
    }
    if (tabId === 'orders') {
      this.loadStoreOrders(1);
    }
    if (tabId === 'users' && this.currentUser && this.currentUser.role === 'ADMIN') {
      this.loadUsers();
    }
    if (tabId === 'management' && this.currentUser && this.currentUser.role === 'ADMIN') {
      this.loadManagementData();
    }
  },


  returnToProducts() {
    this.currentTab = 'products';
    if (this.lastInspectedId) {
      this.scrollToProduct(this.lastInspectedId);
    }
  },


  scrollToProduct(productId) {
    if (!productId) return;
    this.highlightedProductId = productId;
    this.$nextTick(() => {
      if (this.viewMode === 'grouped' && this.groupedProducts) {
        const grp = this.groupedProducts.find(g => g.items && g.items.some(x => x.id === productId));
        if (grp) {
          this.expandedGroups[grp.group_code] = true;
        }
      }
      setTimeout(() => {
        const el = document.getElementById(`product-row-${productId}`) || document.getElementById(`product-group-${productId}`);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 60);
      setTimeout(() => {
        if (this.highlightedProductId === productId) {
          this.highlightedProductId = null;
        }
      }, 3000);
    });
  },


  isGroupHighlighted(grp) {
    return this.highlightedProductId && grp && grp.items && grp.items.some(x => x.id === this.highlightedProductId);
  },


  formatDateTime(dtStr) {
    if (!dtStr) return '-';
    try {
      if (typeof dtStr === 'string' && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/.test(dtStr) && !dtStr.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(dtStr)) {
        return dtStr.replace('T', ' ').slice(0, 19);
      }
      const d = new Date(dtStr);
      if (isNaN(d.getTime())) {
        return String(dtStr).replace('T', ' ').slice(0, 19);
      }
      const pad = n => String(n).padStart(2, '0');
      const Y = d.getFullYear();
      const M = pad(d.getMonth() + 1);
      const D = pad(d.getDate());
      const h = pad(d.getHours());
      const m = pad(d.getMinutes());
      const s = pad(d.getSeconds());
      return `${Y}-${M}-${D} ${h}:${m}:${s}`;
    } catch (e) {
      return String(dtStr).replace('T', ' ').slice(0, 19);
    }
  },
};

export default coreMethods;
