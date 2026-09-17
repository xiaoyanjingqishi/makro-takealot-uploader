const BACKEND_URL = "http://localhost:8001";

// 监听内容脚本与弹窗的消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // 1. PLID 极速采集到后端（后端直接爬取全量数据）
  if (request.action === "COLLECT_PLID" || request.action === "COLLECT_PRODUCT" || request.action === "SCRAPE_PRODUCT") {
    const isPlidRequest = request.action === "COLLECT_PLID" || request.action === "SCRAPE_PRODUCT" || (request.data && (request.data.plid || typeof request.data === "string"));
    const endpoint = isPlidRequest ? `${BACKEND_URL}/api/products/collect-by-plid` : `${BACKEND_URL}/api/products/collect`;
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
    return true; // 保持异步消息通道
  }

  // 1.5 批量检查商品 PLID 是否已在选品箱中
  if (request.action === "CHECK_PLIDS_EXISTENCE") {
    const plids = Array.isArray(request.plids) ? request.plids : [request.plids];
    fetch(`${BACKEND_URL}/api/products/check-existence`, {
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
    chrome.cookies.getAll({ domain: "seller.makro.co.za" }, (cookies) => {
      const cookieStr = (cookies || []).map(c => `${c.name}=${c.value}`).join("; ");
      
      const payload = {
        seller_id: request.data.seller_id,
        fk_csrf_token: request.data.fk_csrf_token,
        cookie: cookieStr
      };

      fetch(`${BACKEND_URL}/api/makro/sync-credentials`, {
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
    return true;
  }

  // 4. 检查后端服务连通状态
  if (request.action === "GET_BACKEND_STATUS") {
    fetch(`${BACKEND_URL}/api/settings`)
      .then(res => res.json())
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
});
