/**
 * Takealot 前台商品选品与采集注入脚本 (极简轻量版)
 * 职责：仅负责从当前详情页精准提取商品 PLID 并投递给后端，后端统一执行官方 API 高速爬取、变体独立图组隔离与全参数入库。
 */
(function () {
  console.log("[Takealot-Makro] 选品助手已注入 (轻量 PLID 转发模式)");

  // 判断是否为详情页
  const isDetailPage = () => {
    return /PLID\d+/i.test(window.location.href) || 
           (document.querySelector("h1") !== null && (document.querySelector("[class*='buybox']") || document.querySelector("[class*='price']")));
  };

  // 从当前页面多重维度精准提取 PLID
  function extractPlid() {
    // 1. 从 URL 路径、搜索参数或哈希中提取
    const urlMatch = window.location.pathname.match(/PLID(\d+)/i) || 
                     window.location.href.match(/PLID(\d+)/i) ||
                     window.location.search.match(/PLID(\d+)/i);
    if (urlMatch) return `PLID${urlMatch[1]}`;

    // 2. 从页面 Canonical 标签提取
    const canonical = document.querySelector("link[rel='canonical']");
    if (canonical && canonical.href) {
      const cMatch = canonical.href.match(/PLID(\d+)/i);
      if (cMatch) return `PLID${cMatch[1]}`;
    }

    // 3. 从 LD+JSON 结构化数据提取
    const jsonLdScripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of jsonLdScripts) {
      try {
        const item = JSON.parse(script.textContent);
        if (item && item.sku && /PLID\d+/i.test(item.sku)) {
          return item.sku;
        }
        if (item && item["@id"] && /PLID(\d+)/i.test(item["@id"])) {
          return `PLID${item["@id"].match(/PLID(\d+)/i)[1]}`;
        }
      } catch (e) {}
    }

    // 4. 从页面任意包含 PLID 的 DOM 属性中提取
    const plidElem = document.querySelector("[data-plid], [data-product-id], a[href*='PLID']");
    if (plidElem) {
      const plidAttr = plidElem.getAttribute("data-plid") || plidElem.getAttribute("data-product-id") || plidElem.href;
      const m = plidAttr.match(/PLID(\d+)/i);
      if (m) return `PLID${m[1]}`;
    }

    return null;
  }

  // 提示 Toast 消息
  function showToast(msg, isSuccess = true) {
    let toast = document.getElementById("makro-collector-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "makro-collector-toast";
      document.body.appendChild(toast);
    }
    toast.style.cssText = `
      position: fixed;
      top: 24px;
      right: 24px;
      z-index: 9999999;
      background: ${isSuccess ? "#10b981" : "#ef4444"};
      color: white;
      padding: 14px 20px;
      border-radius: 8px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.2);
      font-size: 14px;
      font-weight: 600;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      transition: all 0.3s ease;
      display: flex;
      align-items: center;
      gap: 10px;
    `;
    toast.innerText = msg;
    setTimeout(() => {
      if (toast) toast.remove();
    }, 4500);
  }

  // 执行采集核心逻辑
  function triggerCollect(callback) {
    const plid = extractPlid();
    if (!plid) {
      showToast("❌ 未能识别当前页面的 PLID，请确认处于 Takealot 商品详情页", false);
      if (callback) callback({ success: false, error: "未能识别 PLID" });
      return;
    }

    const btn = document.getElementById("makro-collect-btn");
    if (btn) {
      btn.innerText = `⏳ 后端正在采集 (${plid})...`;
      btn.disabled = true;
    }

    // 仅将 PLID 和当前页面 URL 传递给后端，由后端执行 Takealot 官方 API 爬取与变体下钻
    chrome.runtime.sendMessage({
      action: "COLLECT_PLID",
      data: {
        plid: plid,
        url: window.location.href
      }
    }, (response) => {
      if (btn) {
        btn.innerText = "📦 一键采集到 Makro";
        btn.disabled = false;
      }

      if (response && response.success) {
        const p = response.data;
        const varCount = p.variants ? p.variants.length : 1;
        const shortTitle = (p.takealot_title || plid).slice(0, 32);
        showToast(`✅ 采集成功！已由后端完整入库【${shortTitle}...】(变体: ${varCount}个, 售价: R${p.makro_selling_price})`, true);
      } else {
        const err = response ? (response.error || response.message) : "无法连接本地后端服务 (请确保 http://localhost:8001 已启动)";
        showToast(`❌ 采集失败: ${err}`, false);
      }
      if (callback) callback(response);
    });
  }

  // 监听来自 Popup 弹窗的消息
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "DO_COLLECT") {
      triggerCollect(sendResponse);
      return true;
    }
  });

  // 注入详情页悬浮采集卡片
  function injectDetailButton() {
    if (document.getElementById("makro-collect-floating-card")) return;

    const card = document.createElement("div");
    card.id = "makro-collect-floating-card";
    card.style.cssText = `
      position: fixed;
      right: 24px;
      bottom: 60px;
      z-index: 999999;
      background: #ffffff;
      border-radius: 12px;
      padding: 14px 18px;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.15);
      border: 1px solid #e2e8f0;
      display: flex;
      flex-direction: column;
      gap: 8px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    `;

    card.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <span style="font-weight:700;color:#1e293b;font-size:14px;display:flex;align-items:center;gap:6px;">
          <span style="display:inline-block;width:10px;height:10px;background:#2563eb;border-radius:50%;"></span>
          Makro 搬品助手
        </span>
        <a href="http://localhost:8001" target="_blank" style="color:#2563eb;font-size:12px;text-decoration:none;">打开中台 &rarr;</a>
      </div>
      <button id="makro-collect-btn" style="
        background: linear-gradient(135deg, #2563eb, #1d4ed8);
        color: white;
        border: none;
        padding: 10px 18px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 14px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        transition: transform 0.1s;
      ">
        <span>📦 一键采集到 Makro</span>
      </button>
    `;

    document.body.appendChild(card);

    const btn = document.getElementById("makro-collect-btn");
    btn.addEventListener("click", () => {
      triggerCollect();
    });
  }

  // 页面加载完成后注入
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (isDetailPage()) injectDetailButton();
    });
  } else {
    if (isDetailPage()) injectDetailButton();
  }

  // 针对 SPA 路由变化的监听
  let lastUrl = location.href;
  new MutationObserver(() => {
    const url = location.href;
    if (url !== lastUrl) {
      lastUrl = url;
      setTimeout(() => {
        if (isDetailPage()) injectDetailButton();
      }, 1000);
    }
  }).observe(document, { subtree: true, childList: true });

})();
