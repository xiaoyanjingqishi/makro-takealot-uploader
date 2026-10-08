/**
 * 统一 API 通信客户端
 * 封装鉴权 Token、请求拦截与统一错误处理
 */
export class ApiClient {
  static getAuthToken() {
    try {
      return localStorage.getItem('makro_auth_token') || '';
    } catch (_) {
      return '';
    }
  }

  static async request(url, options = {}) {
    const token = this.getAuthToken();
    const headers = {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      ...(options.headers || {})
    };

    const res = await fetch(url, {
      ...options,
      headers
    });

    if (res.status === 401) {
      // 鉴权失效
      console.warn('API Unauthorized (401), redirecting to login...');
    }

    return res;
  }

  static async getJson(url, options = {}) {
    const res = await this.request(url, { ...options, method: 'GET' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || err.message || `HTTP ${res.status}`);
    }
    return res.json();
  }

  static async postJson(url, data, options = {}) {
    const res = await this.request(url, {
      ...options,
      method: 'POST',
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || err.message || `HTTP ${res.status}`);
    }
    return res.json();
  }
}

export default ApiClient;
