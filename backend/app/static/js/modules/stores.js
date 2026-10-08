/**
 * 业务模块: stores
 * 导出该领域的业务方法集
 */
export const storesMethods = {
  onStoreChange() {
    if (this.selectedStoreId) {
      localStorage.setItem('makro_selected_store_id', this.selectedStoreId);
      const curStore = this.authorizedStores.find(s => s.id === this.selectedStoreId);
      this.showToast(`已切换至店铺: ${curStore?.name || this.selectedStoreId}`, 'info');
      if (this.currentTab === 'piggyback') {
        this.loadPiggybackItems(1);
      } else if (this.currentTab === 'store_products') {
        this.loadStoreProducts(1);
      } else if (this.currentTab === 'store_audits') {
        this.loadStoreAudits(1);
      } else if (this.currentTab === 'orders') {
        this.loadStoreOrders(1);
      }
    }
  },


  getStoreNameById(sid) {
    const s = (this.stores || []).find(x => x.id === sid);
    return s ? s.name : `店铺#${sid}`;
  },


  async loadStores() {
    this.loadingStores = true;
    try {
      const res = await fetch('/api/stores', { headers: this.getAuthHeaders() });
      if (res.ok) {
        this.stores = await res.json();
      }
    } catch (e) {
      console.error('加载店铺失败:', e);
    } finally {
      this.loadingStores = false;
    }
  },


  openAddStoreModal() {
    this.storeForm = {
      id: null,
      name: '',
      seller_id: '',
      fk_csrf_token: '',
      cookie: '',
      default_brand: 'Beishi',
      is_active: true,
      is_default: this.stores.length === 0,
      notes: '',
      login_email: '',
      login_password: '',
      imap_provider: 'auto',
      imap_server: '',
      imap_port: 993,
      imap_user: '',
      imap_password: '',
      has_login_password: false,
      has_imap_password: false
    };
    this.storeModalTitle = '➕ 添加新店铺';
    this.showStoreModal = true;
  },


  openEditStoreModal(s) {
    this.storeForm = {
      id: s.id,
      name: s.name,
      seller_id: s.seller_id,
      fk_csrf_token: s.fk_csrf_token || '',
      cookie: '', // 留空表示保持原 Cookie
      default_brand: s.default_brand || 'Beishi',
      is_active: s.is_active,
      is_default: s.is_default,
      notes: s.notes || '',
      login_email: s.login_email || '',
      login_password: s.login_password || '',
      imap_provider: this.detectEmailProvider(s.login_email || s.imap_user),
      imap_server: s.imap_server || '',
      imap_port: s.imap_port || 993,
      imap_user: s.imap_user || '',
      imap_password: s.imap_password || '',
      has_login_password: !!s.has_login_password,
      has_imap_password: !!s.has_imap_password
    };
    this.storeModalTitle = `✏️ 编辑店铺: ${s.name}`;
    this.showStoreModal = true;
  },


  detectEmailProvider(emailAddr) {
    if (!emailAddr || !emailAddr.includes('@')) return 'custom';
    const domain = emailAddr.split('@')[1].toLowerCase();
    if (domain.includes('163') || domain.includes('126') || domain.includes('yeah')) return '163';
    if (domain.includes('gmail')) return 'gmail';
    if (domain.includes('qq')) return 'qq';
    if (domain.includes('outlook') || domain.includes('hotmail') || domain.includes('live')) return 'outlook';
    return 'custom';
  },


  resolveImapDefaults(emailAddr) {
    if (!emailAddr || !emailAddr.includes('@')) return { server: 'imap.gmail.com', port: 993 };
    const domain = emailAddr.split('@')[1].toLowerCase();
    if (domain === '163.com' || domain === 'vip.163.com') return { server: 'imap.163.com', port: 993 };
    if (domain === '126.com' || domain === 'vip.126.com') return { server: 'imap.126.com', port: 993 };
    if (domain === 'yeah.net') return { server: 'imap.yeah.net', port: 993 };
    if (domain === 'gmail.com' || domain === 'googlemail.com') return { server: 'imap.gmail.com', port: 993 };
    if (domain === 'qq.com' || domain === 'vip.qq.com') return { server: 'imap.qq.com', port: 993 };
    if (domain === 'exmail.qq.com') return { server: 'imap.exmail.qq.com', port: 993 };
    if (domain === 'outlook.com' || domain === 'hotmail.com' || domain === 'live.com' || domain === 'office365.com') return { server: 'outlook.office365.com', port: 993 };
    return { server: 'imap.' + domain, port: 993 };
  },


  onStoreEmailInput() {
    const em = (this.storeForm.login_email || '').trim();
    if (em.includes('@')) {
      const def = this.resolveImapDefaults(em);
      this.storeForm.imap_server = def.server;
      this.storeForm.imap_port = def.port;
      this.storeForm.imap_provider = this.detectEmailProvider(em);
    }
  },


  setStoreFormMailProvider(prov) {
    this.storeForm.imap_provider = prov.id;
    if (prov.srv) {
      this.storeForm.imap_server = prov.srv;
      this.storeForm.imap_port = 993;
    }
  },


  async testEmailConnection(emailAddr, password, server, port) {
    if (!emailAddr || !password) {
      alert('请先填写邮箱地址和授权码/应用专用密码！');
      return;
    }
    this.testingEmail = true;
    this.testingEmailResult = null;
    try {
      const res = await fetch('/api/stores/test-email', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          email: emailAddr.trim(),
          password: password.trim(),
          imap_server: server ? server.trim() : null,
          imap_port: port ? parseInt(port) : 993
        })
      });
      const data = await res.json();
      if (data.success) {
        this.showToast(`✅ ${data.message}`, 'success');
        alert(`🎉 邮箱连接成功！\n服务器: ${data.server}:${data.port}\n收件箱共: ${data.total_messages} 封邮件\n该邮箱已完全具备 Makro 验证码自动提取能力！`);
      } else {
        this.showToast(`❌ ${data.message}`, 'error');
        alert(`❌ 邮箱连接失败:\n${data.message}`);
      }
    } catch (e) {
      alert('测试邮箱连接网络异常: ' + e);
    } finally {
      this.testingEmail = false;
    }
  },


  testEmailFromStoreForm() {
    const emailAddr = this.storeForm.imap_user || this.storeForm.login_email;
    const pwd = this.storeForm.imap_password;
    if (!pwd) {
      alert('请先输入“邮箱授权码 / 应用专用密码”！');
      return;
    }
    this.testEmailConnection(emailAddr, pwd, this.storeForm.imap_server, this.storeForm.imap_port);
  },


  openAutoLoginFromStoreForm() {
    this.openAutoLoginModal({
      id: this.storeForm.id,
      name: this.storeForm.name || '当前店铺',
      seller_id: this.storeForm.seller_id,
      login_email: this.storeForm.login_email,
      imap_server: this.storeForm.imap_server,
      imap_port: this.storeForm.imap_port,
      imap_user: this.storeForm.imap_user
    });
  },


  openAutoLoginModal(store = null) {
    this.autoLoginStore = store;
    const targetStore = store || (this.stores.length > 0 ? this.stores[0] : null);
    const email = (targetStore && targetStore.login_email) || (this.storeForm && this.storeForm.login_email) || 'xiaoyanjingqishi@gmail.com';
    const def = this.resolveImapDefaults(email);
    this.autoLoginForm = {
      store_id: targetStore ? targetStore.id : null,
      email: email,
      password: (targetStore && targetStore.login_password) || '',
      imap_provider: this.detectEmailProvider(email),
      imap_server: (targetStore && targetStore.imap_server) || def.server,
      imap_port: (targetStore && targetStore.imap_port) || def.port,
      imap_user: (targetStore && (targetStore.imap_user || targetStore.login_email)) || email,
      imap_password: (targetStore && targetStore.imap_password) || '',
      otp: ''
    };
    this.showAutoLoginPassword = false;
    this.showAutoLoginImapPassword = false;
    this.autoLoginStage = 'ready';
    this.autoLoginStatusMsg = '';
    this.autoLoginErrorMsg = '';
    this.autoLoginSessionId = null;
    this.autoLoginCountdown = 60;
    if (this.autoLoginTimer) clearInterval(this.autoLoginTimer);
    this.showAutoLoginModal = true;
  },


  closeAutoLoginModal() {
    if (this.autoLoginTimer) clearInterval(this.autoLoginTimer);
    this.showAutoLoginModal = false;
  },


  onAutoLoginStoreChange() {
    const s = this.stores.find(x => x.id === this.autoLoginForm.store_id);
    if (s) {
      this.autoLoginStore = s;
      if (s.login_email) {
        this.autoLoginForm.email = s.login_email;
        this.onAutoLoginEmailInput();
      }
      if (s.login_password) {
        this.autoLoginForm.password = s.login_password;
      }
      if (s.imap_password) {
        this.autoLoginForm.imap_password = s.imap_password;
      }
      if (s.imap_server) {
        this.autoLoginForm.imap_server = s.imap_server;
      }
      if (s.imap_port) {
        this.autoLoginForm.imap_port = s.imap_port;
      }
    }
  },


  onAutoLoginEmailInput() {
    const em = (this.autoLoginForm.email || '').trim();
    if (em.includes('@')) {
      const def = this.resolveImapDefaults(em);
      this.autoLoginForm.imap_server = def.server;
      this.autoLoginForm.imap_port = def.port;
      this.autoLoginForm.imap_provider = this.detectEmailProvider(em);
    }
  },


  selectAutoLoginProvider(prov) {
    this.autoLoginForm.imap_provider = prov.id;
    if (prov.srv) {
      this.autoLoginForm.imap_server = prov.srv;
      this.autoLoginForm.imap_port = prov.port || 993;
    }
  },


  async startAutoLoginPipeline() {
    const hasCachedPwd = this.autoLoginStore && this.autoLoginStore.has_login_password;
    if (!this.autoLoginForm.email || (!this.autoLoginForm.password && !hasCachedPwd)) {
      alert('请填写 Makro 登录邮箱和密码！');
      return;
    }
    this.autoLoginErrorMsg = '';

    // 若配置了邮箱授权码，执行全自动流水线
    if (this.autoLoginForm.imap_password && this.autoLoginForm.store_id) {
      this.autoLoginStage = 'running_auto';
      this.autoLoginCountdown = 60;
      if (this.autoLoginTimer) clearInterval(this.autoLoginTimer);
      this.autoLoginTimer = setInterval(() => {
        if (this.autoLoginCountdown > 0) {
          this.autoLoginCountdown--;
        } else {
          clearInterval(this.autoLoginTimer);
        }
      }, 1000);

      try {
        const res = await fetch(`/api/stores/${this.autoLoginForm.store_id}/auto-login`, {
          method: 'POST',
          headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            username: this.autoLoginForm.email.trim(),
            password: this.autoLoginForm.password.trim(),
            imap_server: this.autoLoginForm.imap_server ? this.autoLoginForm.imap_server.trim() : null,
            imap_port: this.autoLoginForm.imap_port ? parseInt(this.autoLoginForm.imap_port) : 993,
            imap_user: this.autoLoginForm.email.trim(),
            imap_password: this.autoLoginForm.imap_password.trim(),
            max_wait_seconds: 60
          })
        });
        clearInterval(this.autoLoginTimer);
        const data = await res.json();
        if (res.ok && data.success) {
          this.autoLoginStage = 'success';
          this.autoLoginStatusMsg = data.message;
          this.showToast('🎉 Makro 全自动登录成功！凭据已更新', 'success');
          await this.loadStores();
        } else if (data.need_manual_otp && data.session_id) {
          // 自动提取超时或降级为手动输入
          this.autoLoginSessionId = data.session_id;
          this.autoLoginStage = 'waiting_otp';
          this.showToast('⏱️ 自动读取邮件超时，请在下方手动输入邮箱验证码', 'warning');
          this.$nextTick(() => {
            if (this.$refs.otpInput) this.$refs.otpInput.focus();
          });
        } else {
          this.autoLoginStage = 'failed';
          this.autoLoginErrorMsg = data.detail || data.message || '登录异常';
        }
      } catch (e) {
        clearInterval(this.autoLoginTimer);
        this.autoLoginStage = 'failed';
        this.autoLoginErrorMsg = '全自动登录网络异常: ' + e;
      }
    } else {
      // 未配置授权码，或无店铺ID：发送验证码，转为手动输入
      this.autoLoginStage = 'running_auto';
      try {
        let url = '/api/stores/quick-login-send-otp';
        if (this.autoLoginForm.store_id) {
          url = `/api/stores/${this.autoLoginForm.store_id}/send-login-otp`;
        }
        const res = await fetch(url, {
          method: 'POST',
          headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            username: this.autoLoginForm.email.trim(),
            password: this.autoLoginForm.password.trim()
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          this.autoLoginSessionId = data.session_id;
          this.autoLoginStage = 'waiting_otp';
          this.showToast(`📩 验证码已发送至 ${data.masked_email}，请输入`, 'info');
          this.$nextTick(() => {
            if (this.$refs.otpInput) this.$refs.otpInput.focus();
          });
        } else {
          this.autoLoginStage = 'failed';
          this.autoLoginErrorMsg = data.detail || data.message || '发送验证码失败';
        }
      } catch (e) {
        this.autoLoginStage = 'failed';
        this.autoLoginErrorMsg = '发起登录请求网络异常: ' + e;
      }
    }
  },


  switchToManualOtpMode() {
    if (this.autoLoginTimer) clearInterval(this.autoLoginTimer);
    this.autoLoginStage = 'waiting_otp';
    this.$nextTick(() => {
      if (this.$refs.otpInput) this.$refs.otpInput.focus();
    });
  },


  async submitManualOtp() {
    if (!this.autoLoginForm.otp || this.autoLoginForm.otp.trim().length !== 6) {
      alert('请输入完整的 6 位数字验证码！');
      return;
    }
    try {
      let url = '/api/stores/quick-login-verify-otp';
      if (this.autoLoginForm.store_id) {
        url = `/api/stores/${this.autoLoginForm.store_id}/verify-login-otp`;
      }
      const res = await fetch(url, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          session_id: this.autoLoginSessionId,
          otp: this.autoLoginForm.otp.trim()
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.autoLoginStage = 'success';
        this.autoLoginStatusMsg = data.message || '验证码核验成功，凭据已自动同步！';
        this.showToast('🎉 Makro 登录成功！凭据已更新生效', 'success');
        // 如果是在编辑/添加店铺窗口中触发的，直接回填
        if (this.showStoreModal && data.credentials) {
          this.storeForm.seller_id = data.credentials.seller_id || this.storeForm.seller_id;
          this.storeForm.fk_csrf_token = data.credentials.fk_csrf_token || this.storeForm.fk_csrf_token;
          this.storeForm.cookie = data.credentials.cookie || this.storeForm.cookie;
        }
        await this.loadStores();
      } else {
        this.autoLoginErrorMsg = data.detail || data.message || '验证码核验失败';
        alert('验证码核验失败: ' + this.autoLoginErrorMsg);
      }
    } catch (e) {
      alert('核验验证码网络异常: ' + e);
    }
  },


  async saveStore() {
    if (!this.storeForm.name || !this.storeForm.seller_id) {
      alert('请填写店铺名称和 Seller ID！');
      return;
    }
    try {
      const isEdit = !!this.storeForm.id;
      const url = isEdit ? `/api/stores/${this.storeForm.id}` : '/api/stores';
      const method = isEdit ? 'PUT' : 'POST';
      const body = { ...this.storeForm };
      if (isEdit && !body.cookie) {
        delete body.cookie;
      }
      if (isEdit && !body.login_password) {
        delete body.login_password;
      }
      if (isEdit && !body.imap_password) {
        delete body.imap_password;
      }
      const res = await fetch(url, {
        method,
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body)
      });
      if (res.status === 401) {
        this.showToast('⚠️ 登录已过期或未登录，请重新登录系统', 'warning');
        this.showLoginModal = true;
        return;
      }
      const data = await res.json();
      if (res.ok) {
        const savedStoreId = this.storeForm.id || data.id;
        this.showToast(isEdit ? '✅ 店铺信息修改成功！' : '🎉 新店铺创建成功！', 'success');
        this.showStoreModal = false;
        await this.loadStores();
        if (savedStoreId) {
          this.testStore(savedStoreId, true);
        }
      } else {
        alert('保存店铺失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('保存店铺网络异常: ' + e);
    }
  },


  async triggerAutoLoginAllStores() {
    if (!confirm('确定立即对所有已配置账密的店铺执行全自动保活检测与登录刷新吗？')) return;
    this.triggeringAutoLoginAll = true;
    try {
      const res = await fetch('/api/stores/trigger-auto-login-all', {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`🎉 ${data.message}`, 'success');
        await this.loadStores();
      } else {
        this.showToast(`⚠️ 执行提示: ${data.detail || data.message || '未知'}`, 'warning');
      }
    } catch (e) {
      this.showToast('触发全店铺保活网络异常: ' + e, 'error');
    } finally {
      this.triggeringAutoLoginAll = false;
    }
  },


  async deleteStore(s) {
    if (!confirm(`确定要删除店铺【${s.name}】吗？\n注意：如果该店铺已有关联的上架记录，删除将同时清理该店铺的数据！`)) return;
    try {
      const res = await fetch(`/api/stores/${s.id}`, { 
        method: 'DELETE',
        headers: this.getAuthHeaders()
      });
      if (res.status === 401) {
        this.showToast('⚠️ 登录已过期或未登录，请重新登录系统', 'warning');
        this.showLoginModal = true;
        return;
      }
      const data = await res.json();
      if (res.ok) {
        this.showToast('🗑️ 店铺已成功删除', 'info');
        await this.loadStores();
      } else {
        alert('删除失败: ' + (data.detail || data.message));
      }
    } catch (e) {
      alert('删除店铺异常: ' + e);
    }
  },


  async setDefaultStore(storeId) {
    try {
      const res = await fetch(`/api/stores/${storeId}/set-default`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      if (res.status === 401) {
        this.showToast('⚠️ 登录已过期或未登录，请重新登录系统', 'warning');
        this.showLoginModal = true;
        return;
      }
      const data = await res.json();
      if (res.ok) {
        this.showToast('⭐ 默认主力店切换成功！', 'success');
        await this.loadStores();
      } else {
        alert('设置默认店铺失败: ' + (data.detail || data.message));
      }
    } catch (e) {
      alert('网络异常: ' + e);
    }
  },


  async testStore(storeId, silent = false) {
    this.testingStoreId = storeId;
    try {
      const res = await fetch(`/api/stores/${storeId}/test`, { 
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      if (res.ok && data.success) {
        if (silent) {
          this.showToast(`✅ 店铺凭据验证通过！${data.message || ''}`, 'success');
        } else {
          alert(`✅ 店铺连通性测试通过！\n状态: ${data.message || '凭据有效，可正常通信'}`);
        }
      } else {
        if (silent) {
          this.showToast(`⚠️ 店铺连通异常: ${data.message || data.detail || 'Cookie 无效'}`, 'warning');
        } else {
          alert(`❌ 店铺连通失败: ${data.message || data.detail || 'Cookie 无效或请求超时'}`);
        }
      }
    } catch (e) {
      if (!silent) {
        alert('测试接口网络异常: ' + e);
      }
    } finally {
      this.testingStoreId = null;
    }
  },
};

export default storesMethods;
