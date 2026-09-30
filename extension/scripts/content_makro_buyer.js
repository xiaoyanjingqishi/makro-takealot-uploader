/**
 * Makro 官网买家端 (makro.co.za) 一键跟品助手脚本
 * 自动识别商品详情页与 FSN 编号，支持在页面右下角一键采集跟品并回传中台
 */
(function () {
  console.log("[Makro-Piggyback] Makro 买家端跟品采集助手已注入");

  // 1. 尝试从 URL 或页面 DOM 提取 16 位 FSN / Product ID
  function extractFsn() {
    // A. 从 URL /p/{FSN} 中提取
    const urlMatch = window.location.href.match(/\/p\/([A-Za-z0-9]{16})/i);
    if (urlMatch) return urlMatch[1].toUpperCase();

    // B. 从 query 参数提取
    const qMatch = window.location.href.match(/[?&](?:pid|fsn|productId)=([A-Za-z0-9]{16})/i);
    if (qMatch) return qMatch[1].toUpperCase();

    // C. 从 DOM 属性查找 (data-fsn, data-product-id)
    const elWithFsn = document.querySelector('[data-fsn], [data-product-id]');
    if (elWithFsn) {
      const f = elWithFsn.getAttribute('data-fsn') || elWithFsn.getAttribute('data-product-id');
      if (f && f.length === 16) return f.toUpperCase();
    }

    return null;
  }

  // 2. 注入跟品悬浮采集按钮
  function injectPiggybackWidget() {
    if (document.getElementById("makro-piggyback-helper-widget")) return;

    const fsn = extractFsn();
    // 只在包含商品详情特征或识别到 FSN 的页面展现
    const isProductPage = fsn !== null || window.location.pathname.includes("/p/");
    if (!isProductPage) return;

    const widget = document.createElement("div");
    widget.id = "makro-piggyback-helper-widget";
    widget.style.cssText = `
      position: fixed;
      right: 24px;
      bottom: 80px;
      z-index: 999999;
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      color: #f8fafc;
      border-radius: 12px;
      padding: 12px 18px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1);
      display: flex;
      flex-direction: column;
      gap: 10px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      min-width: 220px;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    `;

    widget.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:8px;">
        <span style="display:flex; align-items:center; gap:6px; font-weight:700; color:#38bdf8;">
          <span>🎯</span> Makro 智能跟品
        </span>
        <span style="font-size:11px; background:#0284c7; color:#fff; padding:2px 6px; border-radius:4px; font-family:monospace;">
          ${fsn || 'FSN识别中'}
        </span>
      </div>
      <div style="font-size:11px; color:#94a3b8; line-height:1.4;">
        一键提取官方类目与参数，自动进入系统跟品池并执行 AI 侵权检测
      </div>
      <button id="makro-piggyback-btn" style="
        background: linear-gradient(135deg, #0284c7 0%, #2563eb 100%);
        color: white;
        border: none;
        padding: 8px 14px;
        border-radius: 8px;
        font-weight: 600;
        cursor: pointer;
        font-size: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
      ">
        <span>🚀</span> 采集跟品到本地系统
      </button>
      <div id="makro-piggyback-msg" style="display:none; font-size:11px; text-align:center; padding:4px; border-radius:4px;"></div>
    `;

    document.body.appendChild(widget);

    document.getElementById("makro-piggyback-btn").addEventListener("click", () => {
      const btn = document.getElementById("makro-piggyback-btn");
      const msg = document.getElementById("makro-piggyback-msg");
      btn.innerText = "⏳ 正在解析采集...";
      btn.disabled = true;
      msg.style.display = "none";

      const currentUrl = window.location.href;
      const targetFsn = extractFsn() || currentUrl;

      chrome.runtime.sendMessage({
        action: "COLLECT_MAKRO_PIGGYBACK",
        url: currentUrl,
        fsn: targetFsn
      }, (response) => {
        btn.disabled = false;
        btn.innerHTML = "<span>🚀</span> 采集跟品到本地系统";

        if (response && response.success) {
          msg.style.display = "block";
          msg.style.background = "rgba(16, 185, 129, 0.2)";
          msg.style.color = "#34d399";
          msg.style.border = "1px solid #059669";
          msg.innerHTML = "✅ 采集成功！已加入跟品池";
          setTimeout(() => { msg.style.display = "none"; }, 4000);
        } else {
          msg.style.display = "block";
          msg.style.background = "rgba(239, 68, 68, 0.2)";
          msg.style.color = "#f87171";
          msg.style.border = "1px solid #dc2626";
          msg.innerHTML = "❌ 采集失败: " + ((response && response.error) || "无法连接中台服务");
        }
      });
    });
  }

  // 页面加载完成后尝试注入，并监听 URL 变化 (SPA 路由)
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectPiggybackWidget);
  } else {
    injectPiggybackWidget();
  }

  // 定时器补刀确保动态加载页面也能挂载
  setInterval(injectPiggybackWidget, 2500);
})();
