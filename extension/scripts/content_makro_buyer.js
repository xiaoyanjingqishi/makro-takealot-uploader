/**
 * Makro 官网买家端 (makro.co.za) 全场景智能跟品助手
 * 功能涵盖：
 * 1. 详情页单品及多变体 (Color / Size / Packaging) 深度解析与一键批量挂靠
 * 2. 搜索列表页 (Search Result) 与类目页卡片级快速跟品注入
 * 3. 搜索页底部悬浮批量跟品工具栏 (全选/勾选批量/一键全采)
 */
(function () {
  console.log("[Makro-Piggyback] Makro 买家端全场景跟品助手已加载");

  // -------------------------------------------------------------
  // 工具函数：安全解析价格文本 (如 "R 199 00" -> 199.00)
  // -------------------------------------------------------------
  function parsePrice(text) {
    if (!text) return 0.0;
    const clean = text.replace(/[^0-9.]/g, " ").trim();
    const parts = clean.split(/\s+/).filter(Boolean);
    if (parts.length >= 2 && parts[1].length === 2) {
      return parseFloat(`${parts[0]}.${parts[1]}`) || 0.0;
    }
    const m = text.match(/R\s*([0-9]+(?:\.[0-9]{2})?)/i) || text.match(/([0-9]+(?:\.[0-9]{2})?)/);
    return m ? parseFloat(m[1]) : 0.0;
  }

  // =============================================================
  // 模块一：详情页多变体与单品采集 (PDP Collector)
  // =============================================================

  // 尝试从 Redux Store 或 DOM 提取变体列表
  function extractProductVariants() {
    const variants = [];

    // 1. 优先尝试从 React Redux Store 提取 COMPOSED_SWATCH (最精准)
    try {
      const container = document.getElementById("container");
      let reduxStore = null;
      function walk(fiber, depth = 0) {
        if (!fiber || depth > 30 || reduxStore) return;
        if (fiber.memoizedProps && fiber.memoizedProps.store && fiber.memoizedProps.store.getState) {
          reduxStore = fiber.memoizedProps.store;
          return;
        }
        walk(fiber.child, depth + 1);
        walk(fiber.sibling, depth);
      }
      if (container && container._reactRootContainer && container._reactRootContainer._internalRoot) {
        walk(container._reactRootContainer._internalRoot.current);
      }
      if (reduxStore) {
        const state = reduxStore.getState();
        const pageData = state?.pageDataV4?.page?.data;
        for (const slotKey in pageData) {
          for (const slot of pageData[slotKey]) {
            if (slot.widget?.type === "COMPOSED_SWATCH") {
              const swatchComp = slot.widget.data?.swatchComponent?.value;
              if (swatchComp && swatchComp.products) {
                const attrs = swatchComp.attributes || [];
                const options = swatchComp.attributeOptions || [];
                for (const pid in swatchComp.products) {
                  const pData = swatchComp.products[pid];
                  const attrIndexes = pData.attributeIndexes || [];
                  const labelParts = [];
                  attrIndexes.forEach((optIdx, dimIdx) => {
                    const dimName = attrs[dimIdx]?.text || "";
                    const optVal = options[dimIdx] && options[dimIdx][optIdx] ? options[dimIdx][optIdx].value : "";
                    if (optVal) labelParts.push(optVal);
                  });
                  const varName = labelParts.join(" / ") || pData.titles?.subtitle || pid;
                  const finalP = pData.pricing?.finalPrice?.value || 0.0;
                  let mrp = 0.0;
                  (pData.pricing?.prices || []).forEach(pr => {
                    if (pr.priceType === "MRP" || pr.name?.includes("Maximum")) mrp = pr.value;
                  });

                  let imgUrl = "";
                  if (pData.images && pData.images.length > 0) {
                    imgUrl = pData.images[0].url ? pData.images[0].url.replace(/\{@width\}/g, "400").replace(/\{@height\}/g, "400").replace(/\{@quality\}/g, "80") : "";
                  }

                  variants.push({
                    fsn: pid.toUpperCase(),
                    variant_name: varName,
                    price: finalP,
                    mrp: mrp || (finalP * 1.5),
                    image_url: imgUrl,
                    url: pData.productUrl ? `https://www.makro.co.za${pData.productUrl}` : window.location.href,
                    title: pData.titles?.title || document.title
                  });
                }
              }
            }
          }
        }
      }
    } catch (e) {
      console.debug("[Makro-Piggyback] Redux 变体提取跳过:", e);
    }

    // 2. DOM 兜底解析 (若 Redux 未拿到)
    if (variants.length <= 1) {
      const swatchLinks = document.querySelectorAll("ul._1q8vHb li._3V2wfe a._1fGeJ5");
      if (swatchLinks.length > 1) {
        swatchLinks.forEach((a) => {
          const href = a.getAttribute("href") || "";
          const qPid = href.match(/[?&]pid=([a-zA-Z0-9]+)/i);
          if (qPid) {
            const fsn = qPid[1].toUpperCase();
            if (!variants.some(v => v.fsn === fsn)) {
              variants.push({
                fsn: fsn,
                variant_name: a.textContent.trim(),
                price: 0.0,
                mrp: 0.0,
                image_url: "",
                url: href.startsWith("http") ? href : `https://www.makro.co.za${href}`,
                title: document.title
              });
            }
          }
        });
      }
    }

    return variants;
  }

  // 解析当前页面主商品
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

    const urlObj = new URL(currentUrl);
    const qPid = urlObj.searchParams.get("pid") || urlObj.searchParams.get("fsn") || urlObj.searchParams.get("fsnSearch") || urlObj.searchParams.get("productId");
    if (qPid && qPid.length >= 12) {
      fsn = qPid.toUpperCase();
    }

    const pathMatch = window.location.pathname.match(/\/p\/([a-zA-Z0-9]+)/i);
    if (pathMatch) {
      const seg = pathMatch[1];
      if (seg.toLowerCase().startsWith("itm")) {
        itemId = seg;
      } else if (!fsn && seg.length === 16) {
        fsn = seg.toUpperCase();
      }
    }

    // 提取 __INITIAL_STATE__
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

              if (ctx.imageUrl) {
                imageUrl = ctx.imageUrl
                  .replace(/\{@width\}/g, "400")
                  .replace(/\{@height\}/g, "400")
                  .replace(/\{@quality\}/g, "80");
              }

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

    // DOM 兜底
    if (!title) {
      const h1 = document.querySelector("h1");
      if (h1) title = h1.textContent.trim();
    }
    if (price <= 0) {
      const pEl = document.querySelector('[data-testid="selling-price"], .price, [class*="Price"], div._30jeq3');
      if (pEl) price = parsePrice(pEl.textContent);
    }
    if (mrp <= 0 && price > 0) {
      const mEl = document.querySelector('div._3I9_wc');
      mrp = mEl ? parsePrice(mEl.textContent) : Math.round(price * 1.5 * 100) / 100;
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

  // 注入详情页跟品悬浮窗 (支持变体选择)
  function injectDetailPiggybackWidget() {
    if (document.getElementById("makro-piggyback-helper-widget")) return;

    const data = extractProductPageData();
    const isProductPage = data.fsn || data.itemId || window.location.pathname.includes("/p/");
    if (!isProductPage) return;

    const variants = extractProductVariants();
    const hasVariants = variants && variants.length > 1;

    const widget = document.createElement("div");
    widget.id = "makro-piggyback-helper-widget";
    widget.style.cssText = `
      position: fixed;
      right: 20px;
      bottom: 75px;
      z-index: 999999;
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      color: #f8fafc;
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.12);
      display: flex;
      flex-direction: column;
      gap: 10px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      min-width: 260px;
      max-width: 320px;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    `;

    const priceText = data.price > 0 ? `R ${data.price}` : "暂未抓到售价";
    const sellerText = data.sellerName ? `${data.sellerName} (${data.sellerCount}家在售)` : `共${data.sellerCount}家在售`;

    let variantsHtml = "";
    if (hasVariants) {
      const varRows = variants.map((v, idx) => `
        <label style="display:flex; align-items:center; gap:6px; font-size:11px; padding:3px 0; cursor:pointer;">
          <input type="checkbox" class="makro-variant-cb" data-idx="${idx}" ${v.fsn === data.fsn ? "checked" : "checked"} style="cursor:pointer;" />
          <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${v.variant_name}">
            <strong>${v.variant_name}</strong>
          </span>
          <span style="color:#fbbf24; font-weight:600;">${v.price > 0 ? 'R' + v.price : ''}</span>
        </label>
      `).join("");

      variantsHtml = `
        <div style="background:rgba(255,255,255,0.06); padding:8px; border-radius:6px; border:1px solid rgba(255,255,255,0.08);">
          <div style="display:flex; justify-content:space-between; align-items:center; font-weight:600; color:#38bdf8; font-size:11px; margin-bottom:4px;">
            <span>📦 检测到 ${variants.length} 个规格变体</span>
            <span style="font-size:10px; color:#94a3b8; cursor:pointer;" id="makro-toggle-all-vars">全选/反选</span>
          </div>
          <div style="max-height:120px; overflow-y:auto; display:flex; flex-direction:column;">
            ${varRows}
          </div>
        </div>
      `;
    }

    widget.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:8px;">
        <span style="display:flex; align-items:center; gap:6px; font-weight:700; color:#38bdf8;">
          <span>🎯</span> Makro 智能跟品
        </span>
        <span style="font-size:10px; background:#0284c7; color:#fff; padding:2px 6px; border-radius:4px; font-family:monospace;">
          ${data.fsn || data.itemId || '已识别'}
        </span>
      </div>
      <div style="font-size:11px; color:#cbd5e1; display:flex; flex-direction:column; gap:3px;">
        <div>💰 当前在售: <strong style="color:#fbbf24;">${priceText}</strong></div>
        <div>🏪 黄金买家: <span style="color:#94a3b8;">${sellerText}</span></div>
      </div>
      ${variantsHtml}
      <div style="display:flex; flex-direction:column; gap:6px;">
        ${hasVariants ? `
          <button id="makro-collect-vars-btn" style="
            background: linear-gradient(135deg, #0284c7 0%, #2563eb 100%);
            color: white; border: none; padding: 7px 12px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 12px;
            display: flex; align-items: center; justify-content: center; gap: 6px; box-shadow: 0 4px 10px rgba(37, 99, 235, 0.3);
          ">
            <span>⚡</span> 批量跟品选中变体
          </button>
        ` : ""}
        <button id="makro-piggyback-btn" style="
          background: ${hasVariants ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #0284c7 0%, #2563eb 100%)'};
          color: white; border: 1px solid rgba(255,255,255,0.15); padding: 7px 12px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 12px;
          display: flex; align-items: center; justify-content: center; gap: 6px;
        ">
          <span>🚀</span> ${hasVariants ? '仅采集当前单品' : '采集跟品到本地系统'}
        </button>
      </div>
      <div id="makro-piggyback-msg" style="display:none; font-size:11px; text-align:center; padding:4px; border-radius:4px;"></div>
    `;

    document.body.appendChild(widget);

    // 变体全选/反选事件
    const toggleVarsBtn = document.getElementById("makro-toggle-all-vars");
    if (toggleVarsBtn) {
      toggleVarsBtn.addEventListener("click", () => {
        const cbs = document.querySelectorAll(".makro-variant-cb");
        const allChecked = Array.from(cbs).every(cb => cb.checked);
        cbs.forEach(cb => cb.checked = !allChecked);
      });
    }

    // 批量采集变体按钮事件
    const collectVarsBtn = document.getElementById("makro-collect-vars-btn");
    if (collectVarsBtn) {
      collectVarsBtn.addEventListener("click", () => {
        const cbs = document.querySelectorAll(".makro-variant-cb:checked");
        if (cbs.length === 0) {
          alert("请至少勾选一个规格变体！");
          return;
        }
        collectVarsBtn.disabled = true;
        collectVarsBtn.innerText = "⏳ 正在批量入库...";

        const richItems = [];
        cbs.forEach(cb => {
          const idx = parseInt(cb.getAttribute("data-idx"));
          const v = variants[idx];
          if (v) {
            richItems.push({
              fsn: v.fsn,
              item_id: data.itemId,
              url: v.url,
              title: data.title,
              price: v.price > 0 ? v.price : data.price,
              mrp: v.mrp > 0 ? v.mrp : data.mrp,
              image_url: v.image_url || data.imageUrl,
              seller_name: data.sellerName,
              seller_count: data.sellerCount,
              variant_name: v.variant_name,
              variant_attributes: { "variant": v.variant_name }
            });
          }
        });

        chrome.runtime.sendMessage({
          action: "COLLECT_MAKRO_PIGGYBACK_BATCH",
          rich_items: richItems
        }, (res) => {
          collectVarsBtn.disabled = false;
          collectVarsBtn.innerHTML = "<span>⚡</span> 批量跟品选中变体";
          const msg = document.getElementById("makro-piggyback-msg");
          if (res && res.success) {
            msg.style.display = "block";
            msg.style.background = "rgba(16, 185, 129, 0.2)";
            msg.style.color = "#34d399";
            msg.style.border = "1px solid #059669";
            msg.innerHTML = `✅ 成功入库 ${res.data?.success_count || richItems.length} 个规格变体！`;
            setTimeout(() => { msg.style.display = "none"; }, 4000);
          } else {
            msg.style.display = "block";
            msg.style.background = "rgba(239, 68, 68, 0.2)";
            msg.style.color = "#f87171";
            msg.style.border = "1px solid #dc2626";
            msg.innerHTML = "❌ 批量采集失败: " + ((res && res.error) || "未知错误");
          }
        });
      });
    }

    // 单品采集按钮事件
    document.getElementById("makro-piggyback-btn").addEventListener("click", () => {
      const btn = document.getElementById("makro-piggyback-btn");
      const msg = document.getElementById("makro-piggyback-msg");
      btn.innerText = "⏳ 正在解析入库...";
      btn.disabled = true;
      msg.style.display = "none";

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
        btn.innerHTML = hasVariants ? "<span>🚀</span> 仅采集当前单品" : "<span>🚀</span> 采集跟品到本地系统";

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


  // =============================================================
  // 模块二：搜索列表页卡片注入与批量工具栏 (Search Page Collector)
  // =============================================================

  // 从搜索卡片 DOM 解析单品信息
  function extractCardData(cardEl) {
    const fsn = (cardEl.getAttribute("data-id") || "").trim().toUpperCase();
    if (!fsn) return null;

    const titleLink = cardEl.querySelector("a.s1Q9rs") || cardEl.querySelector("a._2rpwqI") || cardEl.querySelector("a[href*='/p/']");
    const title = titleLink ? (titleLink.getAttribute("title") || titleLink.textContent.trim()) : fsn;
    const href = titleLink ? titleLink.href : "";

    let itemId = null;
    const itmMatch = href.match(/\/p\/([a-zA-Z0-9]+)/i);
    if (itmMatch && itmMatch[1].toLowerCase().startsWith("itm")) {
      itemId = itmMatch[1];
    }

    const imgEl = cardEl.querySelector("img._396cs4") || cardEl.querySelector("img");
    const imageUrl = imgEl ? (imgEl.src || imgEl.getAttribute("src") || "") : "";

    const priceEl = cardEl.querySelector("div._30jeq3");
    const mrpEl = cardEl.querySelector("div._3I9_wc");
    const price = priceEl ? parsePrice(priceEl.textContent) : 0.0;
    const mrp = mrpEl ? parsePrice(mrpEl.textContent) : (price * 1.5);

    return {
      fsn,
      item_id: itemId,
      title,
      price,
      mrp,
      image_url: imageUrl,
      url: href || `https://www.makro.co.za/-/p/${itemId || fsn}?pid=${fsn}`,
      seller_name: "",
      seller_count: 1
    };
  }

  // 为每个商品卡片注入轻量操作按钮与复选框
  function injectSearchCardButtons() {
    const cards = document.querySelectorAll("div[data-id]");
    if (cards.length === 0) return;

    cards.forEach((card) => {
      if (card.querySelector(".makro-card-piggyback-container")) return;

      // 设为相对定位便于徽章固定
      if (getComputedStyle(card).position === "static") {
        card.style.position = "relative";
      }

      const fsn = card.getAttribute("data-id");

      const badge = document.createElement("div");
      badge.className = "makro-card-piggyback-container";
      badge.style.cssText = `
        position: absolute;
        top: 8px;
        left: 8px;
        z-index: 100;
        display: flex;
        align-items: center;
        gap: 6px;
        background: rgba(15, 23, 42, 0.85);
        backdrop-filter: blur(4px);
        padding: 4px 8px;
        border-radius: 6px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.3);
        border: 1px solid rgba(255,255,255,0.15);
      `;

      badge.innerHTML = `
        <input type="checkbox" class="makro-search-cb" data-fsn="${fsn}" style="cursor:pointer; width:14px; height:14px; margin:0;" />
        <button class="makro-search-quick-btn" style="
          background: #0284c7; color: white; border: none; padding: 2px 6px; border-radius: 4px;
          font-size: 11px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 2px;
        ">
          🎯 跟品
        </button>
      `;

      card.appendChild(badge);

      // 单卡片快速采集
      const qBtn = badge.querySelector(".makro-search-quick-btn");
      qBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();

        const cardData = extractCardData(card);
        if (!cardData) return;

        qBtn.innerText = "⏳ 提交中...";
        qBtn.disabled = true;

        chrome.runtime.sendMessage({
          action: "COLLECT_MAKRO_PIGGYBACK",
          url: cardData.url,
          fsn: cardData.fsn,
          item_id: cardData.item_id,
          title: cardData.title,
          price: cardData.price,
          mrp: cardData.mrp,
          image_url: cardData.image_url,
          seller_name: cardData.seller_name,
          seller_count: cardData.seller_count
        }, (res) => {
          if (res && res.success) {
            qBtn.style.background = "#059669";
            qBtn.innerText = "✅ 已入库";
          } else {
            qBtn.disabled = false;
            qBtn.style.background = "#dc2626";
            qBtn.innerText = "❌ 重试";
          }
        });
      });

      // 复选框变化更新底部计数
      badge.querySelector(".makro-search-cb").addEventListener("change", updateToolbarCount);
    });

    updateToolbarCount();
  }

  // 更新搜索页底部工具栏勾选计数
  function updateToolbarCount() {
    const totalCards = document.querySelectorAll("div[data-id]").length;
    const selectedCbs = document.querySelectorAll(".makro-search-cb:checked");
    const countEl = document.getElementById("makro-bar-selected-count");
    const totalEl = document.getElementById("makro-bar-total-count");
    if (countEl) countEl.innerText = selectedCbs.length;
    if (totalEl) totalEl.innerText = totalCards;
  }

  // 注入搜索页底部吸附批量工具栏
  function injectSearchBatchToolbar() {
    const isSearchPage = window.location.pathname.includes("/search") || document.querySelectorAll("div[data-id]").length > 3;
    if (!isSearchPage || document.getElementById("makro-search-batch-toolbar")) return;

    const bar = document.createElement("div");
    bar.id = "makro-search-batch-toolbar";
    bar.style.cssText = `
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 999998;
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      color: #f8fafc;
      border-radius: 30px;
      padding: 10px 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.15);
      display: flex;
      align-items: center;
      gap: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      animation: makroSlideUp 0.3s ease-out;
    `;

    bar.innerHTML = `
      <div style="display:flex; align-items:center; gap:6px; font-weight:600; color:#38bdf8;">
        <span>🎯</span> Makro 搜索采集助手: 
        <span style="color:#cbd5e1; font-weight:normal; font-size:12px;">
          本页 <strong id="makro-bar-total-count" style="color:#38bdf8;">0</strong> 件，已选 <strong id="makro-bar-selected-count" style="color:#fbbf24;">0</strong> 件
        </span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <button id="makro-select-all-btn" style="
          background: rgba(255,255,255,0.1); color: #e2e8f0; border: 1px solid rgba(255,255,255,0.2);
          padding: 6px 12px; border-radius: 20px; font-size: 12px; cursor: pointer; font-weight: 500;
        ">
          ☑️ 全选/反选
        </button>
        <button id="makro-collect-selected-btn" style="
          background: linear-gradient(135deg, #0284c7 0%, #2563eb 100%); color: white; border: none;
          padding: 6px 16px; border-radius: 20px; font-size: 12px; cursor: pointer; font-weight: 600;
          box-shadow: 0 4px 10px rgba(37, 99, 235, 0.3); display: flex; align-items: center; gap: 4px;
        ">
          <span>🚀</span> 采集勾选
        </button>
        <button id="makro-collect-all-page-btn" style="
          background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: white; border: none;
          padding: 6px 16px; border-radius: 20px; font-size: 12px; cursor: pointer; font-weight: 600;
          box-shadow: 0 4px 10px rgba(16, 185, 129, 0.3); display: flex; align-items: center; gap: 4px;
        ">
          <span>⚡</span> 采集整页
        </button>
      </div>
      <div id="makro-bar-msg" style="display:none; font-size:12px; font-weight:600;"></div>
    `;

    document.body.appendChild(bar);

    // 全选/反选
    document.getElementById("makro-select-all-btn").addEventListener("click", () => {
      const cbs = document.querySelectorAll(".makro-search-cb");
      const allChecked = Array.from(cbs).every(cb => cb.checked);
      cbs.forEach(cb => cb.checked = !allChecked);
      updateToolbarCount();
    });

    // 采集勾选
    document.getElementById("makro-collect-selected-btn").addEventListener("click", () => {
      const selectedCards = [];
      document.querySelectorAll(".makro-search-cb:checked").forEach(cb => {
        const card = cb.closest("div[data-id]");
        if (card) {
          const d = extractCardData(card);
          if (d) selectedCards.push(d);
        }
      });

      if (selectedCards.length === 0) {
        alert("请先勾选需要跟品的商品！");
        return;
      }
      runBatchCollect(selectedCards, document.getElementById("makro-collect-selected-btn"));
    });

    // 采集整页
    document.getElementById("makro-collect-all-page-btn").addEventListener("click", () => {
      const allCards = [];
      document.querySelectorAll("div[data-id]").forEach(card => {
        const d = extractCardData(card);
        if (d) allCards.push(d);
      });

      if (allCards.length === 0) {
        alert("本页未找到可采集的商品卡片！");
        return;
      }
      runBatchCollect(allCards, document.getElementById("makro-collect-all-page-btn"));
    });

    function runBatchCollect(itemsList, actionBtn) {
      actionBtn.disabled = true;
      const originalText = actionBtn.innerHTML;
      actionBtn.innerHTML = `<span>⏳</span> 正在入库 (${itemsList.length} 件)...`;
      const msgEl = document.getElementById("makro-bar-msg");
      msgEl.style.display = "none";

      chrome.runtime.sendMessage({
        action: "COLLECT_MAKRO_PIGGYBACK_BATCH",
        rich_items: itemsList
      }, (res) => {
        actionBtn.disabled = false;
        actionBtn.innerHTML = originalText;
        msgEl.style.display = "block";

        if (res && res.success) {
          msgEl.style.color = "#34d399";
          msgEl.innerHTML = `✅ 成功入库 ${res.data?.success_count || itemsList.length} 件商品！`;
          // 将已采集卡片按钮置为已入库
          itemsList.forEach(it => {
            const card = document.querySelector(`div[data-id="${it.fsn}"]`);
            if (card) {
              const b = card.querySelector(".makro-search-quick-btn");
              if (b) {
                b.style.background = "#059669";
                b.innerText = "✅ 已入库";
              }
            }
          });
          setTimeout(() => { msgEl.style.display = "none"; }, 5000);
        } else {
          msgEl.style.color = "#f87171";
          msgEl.innerHTML = "❌ 采集失败: " + ((res && res.error) || "未知错误");
        }
      });
    }

    updateToolbarCount();
  }

  // -------------------------------------------------------------
  // 主入口调度：根据当前路由分发注入
  // -------------------------------------------------------------
  function dispatchInjection() {
    const path = window.location.pathname;
    if (path.includes("/p/")) {
      injectDetailPiggybackWidget();
    }
    if (path.includes("/search") || document.querySelectorAll("div[data-id]").length > 0) {
      injectSearchCardButtons();
      injectSearchBatchToolbar();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", dispatchInjection);
  } else {
    dispatchInjection();
  }

  // 定时轮询补刀，应对 SPA 路由切换与瀑布流懒加载
  setInterval(dispatchInjection, 2500);
})();
