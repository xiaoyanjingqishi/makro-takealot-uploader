/**
 * Makro 官网买家端 (makro.co.za) 一键跟品助手脚本
 * 自动识别商品详情页、真实 FSN、官方 Item ID、实时售价与竞争卖家情报
 * 支持在页面右下角一键采集跟品并回传中台
 */
(function () {
  console.log("[Makro-Piggyback] Makro 买家端跟品采集助手已注入");

  // 1. 全量解析当前商品页面数据 (URL + __INITIAL_STATE__ + DOM)
  function extractProductPageData() {
    let fsn = null;
    let itemId = null;
    let title = "";
    let price = 0.0;
    let mrp = 0.0;
    let imageUrl = "";
    let sellerName = "";
    let sellerCount = 1;
    const currentUrl = window.location.href;

    // A. 优先从 URL query 参数提取真实 FSN (如 pid=GSPHPVTNMFHDAWV4)
    const urlObj = new URL(currentUrl);
    const qPid = urlObj.searchParams.get("pid") || urlObj.searchParams.get("fsn") || urlObj.searchParams.get("fsnSearch") || urlObj.searchParams.get("productId");
    if (qPid && qPid.length >= 12) {
      fsn = qPid.toUpperCase();
    }

    // B. 从 URL 路径提取 Item ID (如 /p/itmdda5c11c09523)
    const pathMatch = window.location.pathname.match(/\/p\/([a-zA-Z0-9]+)/i);
    if (pathMatch) {
      const seg = pathMatch[1];
      if (seg.toLowerCase().startsWith("itm")) {
        itemId = seg;
      } else if (!fsn && seg.length === 16) {
        fsn = seg.toUpperCase();
      }
    }

    // C. 深度解析页面注入的全局状态 window.__INITIAL_STATE__
    try {
      const scripts = document.querySelectorAll("script");
      for (const s of scripts) {
        const text = s.textContent || "";
        if (text.includes("window.__INITIAL_STATE__")) {
          const m = text.match(/window\.__INITIAL_STATE__\s*=\s*(\{.*?\});/s);
          if (m) {
            const state = JSON.parse(m[1]);
            const ctx = state?.pageDataV4?.page?.pageData?.pageContext;
            if (ctx) {
              if (!fsn && ctx.productId) fsn = String(ctx.productId).toUpperCase();
              if (!itemId && ctx.itemId) itemId = String(ctx.itemId);
              if (ctx.titles && ctx.titles.title) title = ctx.titles.title.trim();
              
              // 提取价格
              if (ctx.pricing) {
                if (ctx.pricing.finalPrice && typeof ctx.pricing.finalPrice.value === "number") {
                  price = ctx.pricing.finalPrice.value;
                } else if (ctx.pricing.fsp) {
                  price = ctx.pricing.fsp > 5000 ? ctx.pricing.fsp / 100 : ctx.pricing.fsp;
                }
                const pricesList = ctx.pricing.prices || [];
                const mrpItem = pricesList.find(p => p.priceType === "MRP");
                if (mrpItem && typeof mrpItem.value === "number") {
                  mrp = mrpItem.value;
                }
              }

              // 提取主图并替换模板占位符
              if (ctx.imageUrl) {
                imageUrl = ctx.imageUrl
                  .replace(/\{@width\}/g, "400")
                  .replace(/\{@height\}/g, "400")
                  .replace(/\{@quality\}/g, "80");
              }

              // 提取跟卖与商家情报
              if (ctx.trackingDataV2) {
                sellerName = ctx.trackingDataV2.sellerName || "";
                sellerCount = ctx.trackingDataV2.sellerCount || 1;
              }
            }
          }
          break;
        }
      }
    } catch (e) {
      console.warn("[Makro-Piggyback] 解析 __INITIAL_STATE__ 异常:", e);
    }

    // D. DOM 与 ld+json 兜底补充
    if (!title) {
      const h1 = document.querySelector("h1");
      if (h1) title = h1.textContent.trim();
    }
    if (price <= 0) {
      const priceEl = document.querySelector('[data-testid="selling-price"], .price, [class*="Price"]');
      if (priceEl) {
        const pm = priceEl.textContent.match(/R\s*([0-9]+(?:\.[0-9]{2})?)/);
        if (pm) price = parseFloat(pm[1]);
      }
    }
    if (mrp <= 0 && price > 0) {
      mrp = Math.round(price * 1.5 * 100) / 100;
    }
    if (!imageUrl) {
      const img = document.querySelector('img[src*="/asset/rukmini/"], img[src*="cms/"]');
      if (img && img.src) imageUrl = img.src;
    }

    return {
      fsn: fsn || "",
      itemId: itemId || "",
      title: title || "",
      price: price || 0.0,
      mrp: mrp || 0.0,
      imageUrl: imageUrl || "",
      sellerName: sellerName || "",
      sellerCount: sellerCount || 1,
      url: currentUrl
    };
  }

  // 2. 注入跟品悬浮采集按钮
  function injectPiggybackWidget() {
    if (document.getElementById("makro-piggyback-helper-widget")) return;

    const data = extractProductPageData();
    const isProductPage = data.fsn || data.itemId || window.location.pathname.includes("/p/");
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
      min-width: 240px;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    `;

    const priceText = data.price > 0 ? `R ${data.price}` : "暂未抓到售价";
    const sellerText = data.sellerName ? `${data.sellerName} (${data.sellerCount}家在售)` : `共${data.sellerCount}家在售`;

    widget.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:8px;">
        <span style="display:flex; align-items:center; gap:6px; font-weight:700; color:#38bdf8;">
          <span>🎯</span> Makro 智能跟品
        </span>
        <span style="font-size:11px; background:#0284c7; color:#fff; padding:2px 6px; border-radius:4px; font-family:monospace;">
          ${data.fsn || data.itemId || '商品已识别'}
        </span>
      </div>
      <div style="font-size:11px; color:#cbd5e1; display:flex; flex-direction:column; gap:3px;">
        <div>💰 当前在售: <strong style="color:#fbbf24;">${priceText}</strong></div>
        <div>🏪 黄金买家: <span style="color:#94a3b8;">${sellerText}</span></div>
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
      btn.innerText = "⏳ 正在解析入库...";
      btn.disabled = true;
      msg.style.display = "none";

      // 实时重新获取最新数据
      const latestData = extractProductPageData();

      chrome.runtime.sendMessage({
        action: "COLLECT_MAKRO_PIGGYBACK",
        url: latestData.url,
        fsn: latestData.fsn || latestData.url,
        item_id: latestData.itemId,
        title: latestData.title,
        price: latestData.price,
        mrp: latestData.mrp,
        image_url: latestData.imageUrl,
        seller_name: latestData.sellerName,
        seller_count: latestData.sellerCount
      }, (response) => {
        btn.disabled = false;
        btn.innerHTML = "<span>🚀</span> 采集跟品到本地系统";

        if (response && response.success) {
          msg.style.display = "block";
          msg.style.background = "rgba(16, 185, 129, 0.2)";
          msg.style.color = "#34d399";
          msg.style.border = "1px solid #059669";
          msg.innerHTML = `✅ 采集成功！已入库 (R${latestData.price || '0'})`;
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
