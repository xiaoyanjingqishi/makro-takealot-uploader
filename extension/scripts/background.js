const DEFAULT_BACKEND_URL = "http://localhost:8001";

// 动态获取中台后端地址 (优先从本地存储读取用户自定义或局域网反代地址)
async function getBackendUrl() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["backend_url"], (res) => {
      let url = (res && res.backend_url) ? res.backend_url.trim() : DEFAULT_BACKEND_URL;
      if (!url) url = DEFAULT_BACKEND_URL;
      // 确保协议开头并移除末尾斜杠
      if (!/^https?:\/\//i.test(url)) {
        url = "http://" + url;
      }
      url = url.replace(/\/+$/, "");
      resolve(url);
    });
  });
}

// 监听内容脚本与弹窗的消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // 0. 读取当前配置的后端地址
  if (request.action === "GET_BACKEND_CONFIG") {
    getBackendUrl().then((backendUrl) => {
      sendResponse({ success: true, backend_url: backendUrl, default_url: DEFAULT_BACKEND_URL });
    });
    return true;
  }

  // 0.1 保存更新后端地址
  if (request.action === "SET_BACKEND_CONFIG") {
    let newUrl = (request.backend_url || "").trim();
    if (!newUrl) newUrl = DEFAULT_BACKEND_URL;
    if (!/^https?:\/\//i.test(newUrl)) {
      newUrl = "http://" + newUrl;
    }
    newUrl = newUrl.replace(/\/+$/, "");

    chrome.storage.local.set({ backend_url: newUrl }, () => {
      sendResponse({ success: true, backend_url: newUrl });
    });
    return true;
  }

  // 0.2 打开中台控制台
  if (request.action === "OPEN_DASHBOARD") {
    getBackendUrl().then((backendUrl) => {
      chrome.tabs.create({ url: backendUrl });
      sendResponse({ success: true, url: backendUrl });
    });
    return true;
  }

  // 0.3 测试指定地址连通性
  if (request.action === "TEST_BACKEND_CONNECTION") {
    const testUrl = (request.url || "").trim().replace(/\/+$/, "");
    fetch(`${testUrl}/api/settings/network-info`)
      .then(async (res) => {
        if (!res.ok) {
          // 尝试回退至普通 settings 接口
          const res2 = await fetch(`${testUrl}/api/settings`);
          if (!res2.ok) throw new Error(`HTTP ${res2.status}`);
          return res2.json();
        }
        return res.json();
      })
      .then((data) => {
        sendResponse({ success: true, data });
      })
      .catch((err) => {
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }

  // 1. PLID 极速采集到后端（后端直接爬取全量数据）
  if (request.action === "COLLECT_PLID" || request.action === "COLLECT_PRODUCT" || request.action === "SCRAPE_PRODUCT") {
    getBackendUrl().then((backendUrl) => {
      const isPlidRequest = request.action === "COLLECT_PLID" || request.action === "SCRAPE_PRODUCT" || (request.data && (request.data.plid || typeof request.data === "string"));
      const endpoint = isPlidRequest ? `${backendUrl}/api/products/collect-by-plid` : `${backendUrl}/api/products/collect`;
      const payload = isPlidRequest 
        ? (typeof request.data === "string" ? { plid: request.data } : request.data) 
        : request.data;

      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(async res => {
          if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || `HTTP ${res.status}`);
          }
          return res.json();
        })
        .then(data => {
          const count = data.total_variants || (Array.isArray(data.items) ? data.items.length : 1);
          sendResponse({ success: true, ok: true, count, data });
        })
        .catch(err => {
          console.error("采集推送到后端失败:", err);
          sendResponse({ success: false, ok: false, message: err.message, error: err.message });
        });
    });
    return true; // 保持异步通道
  }

  // 1.5 批量检查商品 PLID 是否已在选品箱中
  if (request.action === "CHECK_PLIDS_EXISTENCE") {
    getBackendUrl().then((backendUrl) => {
      const plids = Array.isArray(request.plids) ? request.plids : [request.plids];
      fetch(`${backendUrl}/api/products/check-existence`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plids })
      })
        .then(res => res.json())
        .then(data => sendResponse({ success: true, exists: data.exists || {} }))
        .catch(err => {
          console.warn("查询商品已采集状态失败:", err);
          sendResponse({ success: false, exists: {}, error: err.message });
        });
    });
    return true;
  }

  // 2. 跨域透传请求 Takealot 官方 API
  if (request.action === "FETCH_TAKEALOT_API") {
    const targetUrl = request.url || `https://api.takealot.com/rest/v-1-19-0/product-details/PLID${request.plid}?platform=desktop`;
    fetch(targetUrl, {
      headers: {
        "Accept": "application/json",
        "Origin": "https://www.takealot.com",
        "Referer": "https://www.takealot.com/"
      }
    })
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(data => sendResponse({ success: true, data }))
      .catch(err => {
        console.error("Takealot API 请求失败:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true; // 保持异步通道
  }

  // 3. 同步 Makro 登录凭据 Cookie
  if (request.action === "SYNC_MAKRO_CREDENTIALS") {
    getBackendUrl().then((backendUrl) => {
      chrome.cookies.getAll({ domain: "seller.makro.co.za" }, (cookies) => {
        const cookieStr = (cookies || []).map(c => `${c.name}=${c.value}`).join("; ");
        
        const payload = {
          seller_id: request.data.seller_id,
          fk_csrf_token: request.data.fk_csrf_token,
          cookie: cookieStr
        };

        fetch(`${backendUrl}/api/makro/sync-credentials`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        })
          .then(res => res.json())
          .then(resData => {
            sendResponse({ success: true, data: resData });
          })
          .catch(err => {
            console.error("凭据同步失败:", err);
            sendResponse({ success: false, error: err.message });
          });
      });
    });
    return true;
  }

  // 4. 检查后端服务连通状态
  if (request.action === "GET_BACKEND_STATUS") {
    getBackendUrl().then((backendUrl) => {
      fetch(`${backendUrl}/api/settings`)
        .then(res => res.json())
        .then(data => sendResponse({ success: true, backend_url: backendUrl, data }))
        .catch(err => sendResponse({ success: false, backend_url: backendUrl, error: err.message }));
    });
    return true;
  }
});
