/**
 * 业务模块: settings
 * 导出该领域的业务方法集
 */
export const settingsMethods = {
  async loadSettings() {
    try {
      const res = await fetch('/api/settings', { headers: this.getAuthHeaders() });
      this.settings = await res.json();
      if (this.settings.cleaner_mode) {
        this.batchCleanMode = this.settings.cleaner_mode;
      }
    } catch (e) {
      console.error(e);
    }
  },


  async saveSettings() {
    try {
      if (this.settings) {
        let conc = parseInt(this.settings.piggyback_cruise_concurrency, 10);
        if (isNaN(conc) || conc < 1) conc = 3;
        if (conc > 100) conc = 100;
        this.settings.piggyback_cruise_concurrency = conc;

        let colConc = parseInt(this.settings.piggyback_collect_concurrency, 10);
        if (isNaN(colConc) || colConc < 1) colConc = 5;
        if (colConc > 50) colConc = 50;
        this.settings.piggyback_collect_concurrency = colConc;
      }
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(this.settings)
      });
      if (res.status === 401) {
        this.showToast('⚠️ 登录已过期或未登录，请重新登录系统', 'warning');
        this.showLoginModal = true;
        return;
      }
      if (res.ok) {
        const conc = this.settings?.piggyback_cruise_concurrency || 3;
        const colConc = this.settings?.piggyback_collect_concurrency || 5;
        this.showToast(`✅ 全局配置已成功保存！(巡检并发: ${conc} 线程 · 采集并发: ${colConc} 线程)`, 'success');
      } else {
        const errData = await res.json().catch(() => ({}));
        this.showToast(`保存失败: ${errData.detail || res.statusText}`, 'error');
      }
    } catch (e) {
      this.showToast('保存失败: ' + e, 'error');
    }
  },


  async testJevConnectivity() {
    this.testingJev = true;
    this.jevTestResult = null;
    try {
      const res = await fetch('/api/settings/test-jev', {
        method: 'POST',
        headers: this.getAuthHeaders()
      });
      const data = await res.json();
      this.jevTestResult = data;
    } catch (e) {
      this.jevTestResult = { success: false, error: String(e) };
    } finally {
      this.testingJev = false;
    }
  },
};

export default settingsMethods;
