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
      alert('配置已成功保存！');
    } catch (e) {
      alert('保存失败: ' + e);
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
