/**
 * Makro 卖家后台协同助手脚本 (seller.makro.co.za)
 * 自动提取 sellerId、fk-csrf-token 与登录状态，并提供一键同步到本地搬品系统的能力
 */
(function () {
  console.log("[Takealot-Makro] Makro 协同助手已注入");

  // 提取 sellerId
  function getSellerId() {
    // 1. URL 参数中寻找 sellerId
    const urlMatch = window.location.href.match(/sellerId=([a-f0-9]+)/i);
    if (urlMatch) return urlMatch[1];

    // 2. localStorage 或 sessionStorage 寻找
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      const v = localStorage.getItem(k);
      if (v && v.includes("cb80491bf0a34dc5")) return "cb80491bf0a34dc5";
      if (k.toLowerCase().includes("seller") && v.length === 16) return v;
    }

    return "cb80491bf0a34dc5"; // 抓包中的默认值
  }

  // 提取 fk-csrf-token
  function getCsrfToken() {
    // 1. 从 meta 标签查找
    const metaCsrf = document.querySelector('meta[name="csrf-token"], meta[name="fk-csrf-token"]');
    if (metaCsrf) return metaCsrf.getAttribute("content");

    // 2. 从 localStorage 或 sessionStorage 中查找
    for (let i = 0; i < sessionStorage.length; i++) {
      const v = sessionStorage.getItem(sessionStorage.key(i));
      if (v && v.includes("FbvXzXEP")) return v;
    }
    
    // 默认或从内存拦截
    return window.__FK_CSRF_TOKEN__ || "FbvXzXEP-45o5eUtkeX8Wo6LCq9GBWDg9Rcg";
  }

  // 拦截全局 fetch 和 XHR 自动更新 CSRF Token
  const originalFetch = window.fetch;
  window.fetch = function () {
    if (arguments[1] && arguments[1].headers) {
      const h = arguments[1].headers;
      const token = h["fk-csrf-token"] || h["Fk-Csrf-Token"];
      if (token) window.__FK_CSRF_TOKEN__ = token;
    }
    return originalFetch.apply(this, arguments);
  };

  // 注入悬浮同步按钮
  function injectSyncBar() {
    if (document.getElementById("makro-sync-helper-bar")) return;

    const bar = document.createElement("div");
    bar.id = "makro-sync-helper-bar";
    bar.style.cssText = `
      position: fixed;
      left: 20px;
      bottom: 24px;
      z-index: 999999;
      background: #1e293b;
      color: #f8fafc;
      border-radius: 10px;
      padding: 10px 16px;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
      display: flex;
      align-items: center;
      gap: 12px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
    `;

    bar.innerHTML = `
      <span style="display:flex;align-items:center;gap:6px;">
        <span style="display:inline-block;width:8px;height:8px;background:#10b981;border-radius:50%;"></span>
        <b>搬品系统已就绪</b>
      </span>
      <button id="makro-sync-btn" style="
        background: #2563eb;
        color: white;
        border: none;
        padding: 6px 14px;
        border-radius: 6px;
        font-weight: 600;
        cursor: pointer;
        font-size: 12px;
      ">🔄 同步登录态至后台</button>
    `;

    document.body.appendChild(bar);

    document.getElementById("makro-sync-btn").addEventListener("click", () => {
      const btn = document.getElementById("makro-sync-btn");
      btn.innerText = "⏳ 正在同步...";
      btn.disabled = true;

      const sellerId = getSellerId();
      const csrf = getCsrfToken();

      chrome.runtime.sendMessage(
        {
          action: "SYNC_MAKRO_CREDENTIALS",
          data: {
            seller_id: sellerId,
            fk_csrf_token: csrf
          }
        },
        (resp) => {
          btn.disabled = false;
          if (resp && resp.success) {
            btn.innerText = "✅ 同步成功！";
            setTimeout(() => { btn.innerText = "🔄 同步登录态至后台"; }, 3000);
          } else {
            btn.innerText = "❌ 失败，点此重试";
            alert("同步失败: " + (resp ? resp.error : "请检查中台服务是否在线，并在搬品插件弹窗中确认中台连接配置"));
          }
        }
      );
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectSyncBar);
  } else {
    injectSyncBar();
  }
})();
