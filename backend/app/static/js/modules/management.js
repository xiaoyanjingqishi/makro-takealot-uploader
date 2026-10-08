/**
 * 业务模块: management
 * 导出该领域的业务方法集
 */
export const managementMethods = {
  getManagementDateParams() {
    let s = '', e = '';
    const now = new Date();
    const formatDate = (d) => {
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    };
    const todayStr = formatDate(now);

    if (this.management.dateRange === 'today') {
      s = todayStr;
      e = todayStr;
    } else if (this.management.dateRange === 'yesterday') {
      const y = new Date(now);
      y.setDate(y.getDate() - 1);
      const yStr = formatDate(y);
      s = yStr;
      e = yStr;
    } else if (this.management.dateRange === '7d') {
      const past7 = new Date(now);
      past7.setDate(past7.getDate() - 6);
      s = formatDate(past7);
      e = todayStr;
    } else if (this.management.dateRange === '30d') {
      const past30 = new Date(now);
      past30.setDate(past30.getDate() - 29);
      s = formatDate(past30);
      e = todayStr;
    } else if (this.management.dateRange === 'custom') {
      s = this.management.customStart || todayStr;
      e = this.management.customEnd || todayStr;
    }
    return { start_date: s, end_date: e };
  },,

  setManagementDateRange(range) {
    this.management.dateRange = range;
    if (range !== 'custom') {
      this.loadManagementData();
    }
  },,

  async loadManagementData() {
    if (!this.currentUser || this.currentUser.role !== 'ADMIN') return;
    this.management.loading = true;
    try {
      await Promise.all([
        this.loadManagementOverview(),
        this.loadManagementLeaderboard(),
        this.loadManagementHourly(),
        this.loadManagementLogs(1)
      ]);
    } finally {
      this.management.loading = false;
    }
  },,

  async loadManagementOverview() {
    try {
      const { start_date, end_date } = this.getManagementDateParams();
      let url = `/api/management/overview?start_date=${start_date}&end_date=${end_date}`;
      if (this.management.filterUserId) url += `&user_id=${this.management.filterUserId}`;
      if (this.management.filterStoreId) url += `&store_id=${this.management.filterStoreId}`;
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        this.management.overview = await res.json();
      }
    } catch (e) {
      console.error('加载管理概览失败:', e);
    }
  },,

  async loadManagementLeaderboard() {
    try {
      const { start_date, end_date } = this.getManagementDateParams();
      let url = `/api/management/leaderboard?start_date=${start_date}&end_date=${end_date}&sort_by=${this.management.sortBy}`;
      if (this.management.filterStoreId) url += `&store_id=${this.management.filterStoreId}`;
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.management.leaderboard = data.leaderboard || [];
      }
    } catch (e) {
      console.error('加载人效排行榜失败:', e);
    }
  },,

  async loadManagementHourly() {
    try {
      const { start_date, end_date } = this.getManagementDateParams();
      let url = `/api/management/hourly-distribution?start_date=${start_date}&end_date=${end_date}`;
      if (this.management.filterUserId) url += `&user_id=${this.management.filterUserId}`;
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        this.management.hourly = await res.json();
      }
    } catch (e) {
      console.error('加载时段节奏失败:', e);
    }
  },,

  async loadManagementLogs(page = 1) {
    this.management.logs.loading = true;
    try {
      const { start_date, end_date } = this.getManagementDateParams();
      let url = `/api/management/activity-logs?page=${page}&page_size=${this.management.logs.pageSize}&start_date=${start_date}&end_date=${end_date}`;
      if (this.management.filterUserId) url += `&user_id=${this.management.filterUserId}`;
      if (this.management.logs.taskType) url += `&task_type=${this.management.logs.taskType}`;
      if (this.management.logs.status) url += `&status=${this.management.logs.status}`;
      if (this.management.logs.keyword) url += `&keyword=${encodeURIComponent(this.management.logs.keyword.trim())}`;
      const res = await fetch(url, { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.management.logs.items = data.items || [];
        this.management.logs.total = data.total || 0;
        this.management.logs.page = page;
      }
    } catch (e) {
      console.error('加载审计日志失败:', e);
    } finally {
      this.management.logs.loading = false;
    }
  },,

  drillDownUser(userId) {
    this.management.filterUserId = userId;
    this.loadManagementOverview();
    this.loadManagementHourly();
    this.loadManagementLogs(1);
    this.$nextTick(() => {
      const el = document.getElementById('management-stream-section');
      if (el) el.scrollIntoView({ behavior: 'smooth' });
    });
  },,

  clearUserDrillDown() {
    this.management.filterUserId = null;
    this.loadManagementOverview();
    this.loadManagementHourly();
    this.loadManagementLogs(1);
  },,

  exportManagementWorkload() {
    const { start_date, end_date } = this.getManagementDateParams();
    const token = localStorage.getItem('makro_auth_token') || '';
    window.open(`/api/management/export-workload?start_date=${start_date}&end_date=${end_date}&token=${token}`);
  },,

  async loadMyDailySummary() {
    if (!this.currentUser) return;
    try {
      const res = await fetch('/api/management/my-summary', { headers: this.getAuthHeaders() });
      if (res.ok) {
        this.myDailySummary = await res.json();
      }
    } catch (e) {
      console.error('加载个人今日战报失败:', e);
    }
  },,

  getUserDisplayName(userId) {
    if (!userId) return '系统/未分配';
    const u = (this.userList || []).find(it => it.id === userId);
    return u ? (u.nickname || u.username) : `用户#${userId}`;
  },
};

export default managementMethods;
