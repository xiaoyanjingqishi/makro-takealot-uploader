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

                  variants.push({
                    fsn: pid.toUpperCase(),
                    variant_name: varName,
                    variant_attributes: { "variant": varName },
                    url: pData.productUrl ? (pData.productUrl.startsWith("http") ? pData.productUrl : `https://www.makro.co.za${pData.productUrl}`) : window.location.href
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
                variant_attributes: { "variant": a.textContent.trim() },
                url: href.startsWith("http") ? href : `https://www.makro.co.za${href}`
              });
            }
          }
        });
      }
    }

    return variants;
  }

  // 解析当前页面主商品（提取 PID、Item ID、标题、主图、实时售价、划线原价、品牌、类目、在售卖家等丰富元数据）
  function extractProductPageData() {
    let fsn = null;
    let itemId = null;
    let title = "";
    let price = 0.0;
    let mrp = 0.0;
    let imageUrl = "";
    let brand = "";
    let vertical = "";
    let sellerName = "";
    let sellerCount = 1;
    let modelNumber = "";
    let barcode = "";
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

    // 1. 从 window.__INITIAL_STATE__ 获取全量权威数据
    try {
      const scripts = document.querySelectorAll("script");
      for (const s of scripts) {
        const text = s.textContent || "";
        if (text.includes("window.__INITIAL_STATE__")) {
          const m = text.match(/window\.__INITIAL_STATE__\s*=\s*(\{.*?\});/s);
          if (m) {
            const state = JSON.parse(m[1]);
            const pageDataV4 = state?.pageDataV4 || {};
            const page = pageDataV4.page || {};
            const pageData = page.pageData || {};
            const ctx = pageData.pageContext || {};

            if (!fsn && ctx.productId) fsn = String(ctx.productId).toUpperCase();
            if (!itemId && ctx.itemId) itemId = String(ctx.itemId);

            if (ctx.titles) {
              title = ctx.titles.title || ctx.titles.subtitle || "";
            }
            if (ctx.imageUrl) {
              imageUrl = ctx.imageUrl.replace("{@width}", "400").replace("{@height}", "400").replace("{@quality}", "80");
            }

            const tracking = ctx.trackingDataV2 || {};
            if (tracking) {
              if (tracking.brand) brand = tracking.brand;
              if (tracking.vertical) vertical = tracking.vertical;
              if (tracking.sellerName) sellerName = tracking.sellerName;
              if (tracking.sellerCount !== undefined) sellerCount = parseInt(tracking.sellerCount) || 1;
            }

            const pricing = ctx.pricing;
            if (pricing && typeof pricing === "object") {
              const finalP = pricing.finalPrice || {};
              if (finalP.decimalValue) {
                price = parseFloat(String(finalP.decimalValue).replace(/[^0-9.]/g, "")) || 0.0;
              } else if (finalP.value) {
                const val = parseFloat(finalP.value);
                price = val >= 5000 && pricing.fsp === val ? val / 100.0 : val;
              } else if (pricing.fsp) {
                const fsp = parseFloat(pricing.fsp);
                price = fsp >= 5000 ? fsp / 100.0 : fsp;
              }

              const prices = pricing.prices || [];
              for (const pItem of prices) {
                if (pItem.priceType === "MRP") {
                  if (pItem.decimalValue) {
                    mrp = parseFloat(String(pItem.decimalValue).replace(/[^0-9.]/g, "")) || 0.0;
                  } else if (pItem.value) {
                    const mVal = parseFloat(pItem.value);
                    mrp = mVal >= 5000 && pricing.mrp === mVal ? mVal / 100.0 : mVal;
                  }
                }
              }
            }
          }
          break;
        }
      }
    } catch (e) {}

    // 2. DOM 提取补充
    if (!title) {
      const h1 = document.querySelector("h1._35KyD6") || document.querySelector("h1[class*='_35KyD6']") || document.querySelector("h1");
      if (h1) title = h1.innerText.trim();
    }
    if (!imageUrl) {
      const img = document.querySelector("img._396cs4") || document.querySelector("div._1tagpn img");
      if (img && img.src) imageUrl = img.src;
    }
    if (price <= 0) {
      const priceEl = document.querySelector("div._30jeq3") || document.querySelector("div[class*='_30jeq3']");
      if (priceEl) price = parsePrice(priceEl.innerText);
    }
    if (mrp <= 0) {
      const mrpEl = document.querySelector("div._27UcVY") || document.querySelector("div[class*='_27UcVY']");
      if (mrpEl) mrp = parsePrice(mrpEl.innerText);
    }
    if (mrp <= 0 && price > 0) {
      mrp = Math.round(price * 1.5 * 100) / 100;
    }

    return {
      fsn: fsn || "",
      itemId: itemId || "",
      url: currentUrl,
      title: title || "",
      price: price || 0.0,
      mrp: mrp || 0.0,
      imageUrl: imageUrl || "",
      brand: brand || "",
      vertical: vertical || "",
      sellerName: sellerName || "",
      sellerCount: sellerCount || 1,
      modelNumber: modelNumber || "",
      barcode: barcode || ""
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
        <div>📌 目标编号: <strong style="color:#fbbf24; font-family:monospace;">${data.fsn || data.itemId || '已识别'}</strong></div>
        <div style="color:#94a3b8; font-size:10px;">⚡ 点击后由中台后端直连 Makro 官方抓取真实售价与建档入库</div>
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
        collectVarsBtn.innerText = "⏳ 正在提交后端抓取入库...";

        const richItems = [];
        cbs.forEach(cb => {
          const idx = parseInt(cb.getAttribute("data-idx"));
          const v = variants[idx];
          if (v) {
            richItems.push({
              fsn: v.fsn,
              item_id: data.itemId,
              url: v.url,
              variant_name: v.variant_name,
              variant_attributes: { "variant": v.variant_name }
            });
          }
        });

        const startTime = Date.now();
        chrome.runtime.sendMessage({
          action: "COLLECT_MAKRO_PIGGYBACK_BATCH",
          rich_items: richItems
        }, (res) => {
          collectVarsBtn.disabled = false;
          collectVarsBtn.innerHTML = "<span>⚡</span> 批量跟品选中变体";
          const msg = document.getElementById("makro-piggyback-msg");
          const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
          if (res && res.success) {
            msg.style.display = "block";
            msg.style.background = "rgba(16, 185, 129, 0.2)";
            msg.style.color = "#34d399";
            msg.style.border = "1px solid #059669";
            const newAdded = res.data?.success_count || 0;
            const skipped = res.data?.skipped_existing_count || 0;
            let text = `⚡ 并发入库完成 (耗时 ${elapsedSec}s)：成功 ${newAdded} 件`;
            if (skipped > 0) text += `，跳过已在库 ${skipped} 件`;
            msg.innerHTML = `✅ ${text}！`;
            setTimeout(() => { msg.style.display = "none"; }, 4000);
          } else {
            msg.style.display = "block";
            msg.style.background = "rgba(239, 68, 68, 0.2)";
            msg.style.color = "#f87171";
            msg.style.border = "1px solid #dc2626";
            msg.innerHTML = `❌ 批量采集失败 (耗时 ${elapsedSec}s): ` + ((res && res.error) || "未知错误");
          }
        });
      });
    }

    // 单品采集按钮事件
    document.getElementById("makro-piggyback-btn").addEventListener("click", () => {
      const btn = document.getElementById("makro-piggyback-btn");
      const msg = document.getElementById("makro-piggyback-msg");

      if (btn.dataset.alreadyExists === "1") {
        msg.style.display = "block";
        msg.style.background = "rgba(16, 185, 129, 0.2)";
        msg.style.color = "#34d399";
        msg.style.border = "1px solid #059669";
        msg.innerHTML = "💡 该商品已在跟品库中，无需重复入库";
        setTimeout(() => { msg.style.display = "none"; }, 3500);
        return;
      }

      btn.innerText = "⏳ 正在由后端解析入库...";
      btn.disabled = true;
      msg.style.display = "none";

      const latestData = extractProductPageData();

      chrome.storage.local.get(["makro_target_store_id"], (sRes) => {
        const storeId = sRes?.makro_target_store_id || null;
        chrome.runtime.sendMessage({
          action: "COLLECT_MAKRO_PIGGYBACK",
          url: latestData.url,
          fsn: latestData.fsn || latestData.url,
          item_id: latestData.itemId,
          store_id: storeId,
          title: latestData.title || null,
          brand: latestData.brand || null,
          vertical: latestData.vertical || null,
          price: latestData.price || null,
          mrp: latestData.mrp || null,
          image_url: latestData.imageUrl || null,
          seller_name: latestData.sellerName || null,
          seller_count: latestData.sellerCount || 1,
          model_number: latestData.modelNumber || null,
          barcode: latestData.barcode || null
        }, (response) => {
          btn.disabled = false;
          btn.innerHTML = hasVariants ? "<span>🚀</span> 仅采集当前单品" : "<span>🚀</span> 采集跟品到本地系统";

          if (response && response.success) {
            btn.dataset.alreadyExists = "1";
            btn.style.background = "#059669";
            btn.innerHTML = "<span>✅</span> 该商品已在跟品库";
            msg.style.display = "block";
            msg.style.background = "rgba(16, 185, 129, 0.2)";
            msg.style.color = "#34d399";
            msg.style.border = "1px solid #059669";
            msg.innerHTML = response.already_exists 
              ? `💡 该商品已在跟品库中，已同步前台最新情报`
              : `✅ 采集成功！后端已获取权威数据并入库`;
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
    });

    // 异步排重校验：检查详情页主品与变体是否已入库
    try {
      const pdpFsns = [data.fsn];
      if (variants && variants.length > 0) {
        variants.forEach(v => { if (v.fsn) pdpFsns.push(v.fsn); });
      }
      chrome.storage.local.get(["makro_target_store_id"], (sRes) => {
        const storeId = sRes?.makro_target_store_id || null;
        chrome.runtime.sendMessage({
          action: "CHECK_PIGGYBACK_EXISTENCE",
          fsns: pdpFsns,
          store_id: storeId
        }, (res) => {
          if (res && res.success && res.exists) {
            const existsMap = res.exists;
            const mainInfo = existsMap[data.fsn];
            if (mainInfo) {
              const btn = document.getElementById("makro-piggyback-btn");
              if (btn) {
                btn.dataset.alreadyExists = "1";
                btn.style.background = "#059669";
                btn.innerHTML = mainInfo.status === "ACTIVE" 
                  ? `<span>🟢</span> 该商品已在售中 (R${mainInfo.target_price})`
                  : `<span>✅</span> 该商品已在跟品库`;
              }
            }
          if (variants && variants.length > 0) {
            variants.forEach((v, idx) => {
              if (existsMap[v.fsn]) {
                const rows = document.querySelectorAll(".makro-variant-row");
                if (rows && rows[idx]) {
                  const tag = document.createElement("span");
                  tag.style.cssText = "background:#059669; color:white; font-size:9.5px; padding:1px 4px; border-radius:3px; margin-left:4px;";
                  tag.innerText = "已在库";
                  const targetDiv = rows[idx].querySelector("div");
                  if (targetDiv) targetDiv.appendChild(tag);
                }
              }
            });
          }
        }
      });
    } catch (e) {
      console.debug("详情页排重核验跳过:", e);
    }
  }


  // =============================================================
  // 模块二：搜索列表页卡片注入与批量工具栏 (Search Page Collector)
  // =============================================================

  // 从搜索卡片 DOM 解析单品信息 (提取 FSN、Item ID、并解析卡片已渲染的标题、主图与前台价格)
  function extractCardData(cardEl) {
    const fsn = (cardEl.getAttribute("data-id") || "").trim().toUpperCase();
    if (!fsn) return null;

    const titleLink = cardEl.querySelector("a.s1Q9rs") || cardEl.querySelector("a._2rpwqI") || cardEl.querySelector("a[href*='/p/']");
    const href = titleLink ? titleLink.href : "";

    let itemId = null;
    if (href) {
      const itmMatch = href.match(/\/p\/([a-zA-Z0-9]+)/i);
      if (itmMatch && itmMatch[1].toLowerCase().startsWith("itm")) {
        itemId = itmMatch[1];
      }
    }

    // 提取卡片已渲染的标题
    let title = "";
    if (titleLink) {
      title = (titleLink.getAttribute("title") || titleLink.innerText || "").trim();
    }

    // 提取卡片主图
    const imgEl = cardEl.querySelector("img._396cs4") || cardEl.querySelector("img");
    const imageUrl = imgEl ? (imgEl.src || imgEl.getAttribute("data-src") || "") : "";

    // 提取卡片前台价格
    const priceEl = cardEl.querySelector("div._30jeq3") || cardEl.querySelector("div[class*='_30jeq3']");
    let price = 0;
    if (priceEl) {
      const pMatch = priceEl.innerText.replace(/,/g, "").match(/(\d+(?:\.\d+)?)/);
      if (pMatch) price = parseFloat(pMatch[1]);
    }

    // 提取划线原价
    const mrpEl = cardEl.querySelector("div._27UcVY") || cardEl.querySelector("div[class*='_27UcVY']");
    let mrp = 0;
    if (mrpEl) {
      const mMatch = mrpEl.innerText.replace(/,/g, "").match(/(\d+(?:\.\d+)?)/);
      if (mMatch) mrp = parseFloat(mMatch[1]);
    }

    return {
      fsn,
      item_id: itemId,
      url: href || `https://www.makro.co.za/-/p/${itemId || fsn}?pid=${fsn}`,
      title: title || undefined,
      image_url: imageUrl || undefined,
      price: price > 0 ? price : undefined,
      mrp: mrp > 0 ? mrp : undefined
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

        // 防重检查：如果卡片已在库中，拦截并提示用户，绝不重复发起入库请求
        if (card.dataset.checkedExistence === "exists") {
          const origText = qBtn.innerText;
          qBtn.innerText = "⚠️ 已在库中";
          setTimeout(() => { qBtn.innerText = origText; }, 2000);
          return;
        }

        const cardData = extractCardData(card);
        if (!cardData) return;

        qBtn.innerText = "⏳ 提交中...";
        qBtn.disabled = true;

        chrome.storage.local.get(["makro_target_store_id"], (sRes) => {
          const storeId = sRes?.makro_target_store_id || null;
          chrome.runtime.sendMessage({
            action: "COLLECT_MAKRO_PIGGYBACK",
            url: cardData.url,
            fsn: cardData.fsn,
            item_id: cardData.item_id,
            store_id: storeId,
            title: cardData.title || null,
            image_url: cardData.image_url || null,
            price: cardData.price || null,
            mrp: cardData.mrp || null
          }, (res) => {
            if (res && res.success) {
              card.dataset.checkedExistence = "exists";
              qBtn.style.background = "#059669";
              qBtn.innerText = res.already_exists ? "🟢 已在库" : "✅ 已入库";
              const cb = card.querySelector(".makro-search-cb");
              if (cb) {
                cb.checked = false;
                cb.title = "已在库商品 (无需重复采集)";
                cb.style.opacity = "0.5";
              }
              updateToolbarCount();
            } else {
              qBtn.disabled = false;
              qBtn.style.background = "#dc2626";
              qBtn.innerText = "❌ 重试";
            }
          });
        });
      });

      // 复选框变化更新底部计数
      badge.querySelector(".makro-search-cb").addEventListener("change", updateToolbarCount);
    });

    // 异步排重校验：检查当前页所有商品卡片是否已在跟品库中
    try {
      const fsnsToCheck = [];
      cards.forEach((card) => {
        const fsn = (card.getAttribute("data-id") || "").trim().toUpperCase();
        if (fsn && !card.dataset.checkedExistence) {
          card.dataset.checkedExistence = "pending";
          fsnsToCheck.push(fsn);
        }
      });

      if (fsnsToCheck.length > 0) {
        chrome.storage.local.get(["makro_target_store_id"], (sRes) => {
          const storeId = sRes?.makro_target_store_id || null;
          chrome.runtime.sendMessage({
            action: "CHECK_PIGGYBACK_EXISTENCE",
            fsns: fsnsToCheck,
            store_id: storeId
          }, (res) => {
            if (res && res.success && res.exists) {
              const existsMap = res.exists;
              cards.forEach((card) => {
                const fsn = (card.getAttribute("data-id") || "").trim().toUpperCase();
                if (existsMap[fsn]) {
                  card.dataset.checkedExistence = "exists";
                  const qBtn = card.querySelector(".makro-search-quick-btn");
                  const cb = card.querySelector(".makro-search-cb");
                  const info = existsMap[fsn];
                  if (qBtn) {
                    qBtn.style.background = "#059669";
                    qBtn.style.opacity = "0.9";
                    qBtn.innerText = info.status === "ACTIVE" ? "🟢 在售中" : "✅ 已在库";
                    qBtn.title = `已存在于跟品库 (SKU: ${info.seller_sku}，当前售价: R${info.target_price || 0}，店铺: ${info.store_name || ''})`;
                  }
                  if (cb) {
                    cb.checked = false; // 已在库商品默认不勾选！
                    cb.title = "已在库商品 (无需重复采集)";
                    cb.style.opacity = "0.5";
                  }
                }
              });
              updateToolbarCount();
            }
          });
        });
      }
    } catch (e) {
      console.debug("搜索页排重核验跳过:", e);
    }

    updateToolbarCount();
  }

  // 更新搜索页底部工具栏勾选计数
  function updateToolbarCount() {
    const totalCards = document.querySelectorAll("div[data-id]").length;
    const existsCards = document.querySelectorAll('div[data-id][data-checked-existence="exists"]').length;
    const selectedCbs = document.querySelectorAll(".makro-search-cb:checked");
    const countEl = document.getElementById("makro-bar-selected-count");
    const totalEl = document.getElementById("makro-bar-total-count");
    if (countEl) countEl.innerText = selectedCbs.length;
    if (totalEl) totalEl.innerText = existsCards > 0 ? `${totalCards} (已在库 ${existsCards})` : totalCards;
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
          ☑️ 全选未在库
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
          <span>⚡</span> 采集整页 (跳过在库)
        </button>
      </div>
      <div id="makro-bar-msg" style="display:none; font-size:12px; font-weight:600;"></div>
    `;

    document.body.appendChild(bar);

    // 全选/反选 (仅对未在库的新品生效)
    document.getElementById("makro-select-all-btn").addEventListener("click", () => {
      const cbs = document.querySelectorAll(".makro-search-cb");
      const availableCbs = Array.from(cbs).filter(cb => {
        const card = cb.closest("div[data-id]");
        return card && card.dataset.checkedExistence !== "exists";
      });
      if (availableCbs.length === 0) {
        alert("本页商品均已在跟品库中，无待采集新品！");
        return;
      }
      const allChecked = availableCbs.every(cb => cb.checked);
      availableCbs.forEach(cb => cb.checked = !allChecked);
      updateToolbarCount();
    });

    // 采集勾选 (自动跳过已在库商品)
    document.getElementById("makro-collect-selected-btn").addEventListener("click", () => {
      const selectedCards = [];
      let skippedCount = 0;
      document.querySelectorAll(".makro-search-cb:checked").forEach(cb => {
        const card = cb.closest("div[data-id]");
        if (card) {
          if (card.dataset.checkedExistence === "exists") {
            skippedCount++;
            return;
          }
          const d = extractCardData(card);
          if (d) selectedCards.push(d);
        }
      });

      if (selectedCards.length === 0) {
        if (skippedCount > 0) {
          alert(`所勾选的 ${skippedCount} 件商品均已在跟品库中，已全部跳过，无需重复采集！`);
        } else {
          alert("请先勾选需要跟品的商品！");
        }
        return;
      }

      if (skippedCount > 0) {
        const msgEl = document.getElementById("makro-bar-msg");
        if (msgEl) {
          msgEl.style.display = "block";
          msgEl.style.color = "#fbbf24";
          msgEl.innerHTML = `⚠️ 已自动跳过 ${skippedCount} 件已在库商品，准备入库剩余 ${selectedCards.length} 件...`;
        }
      }
      runBatchCollect(selectedCards, document.getElementById("makro-collect-selected-btn"));
    });

    // 采集整页 (自动跳过已在库商品)
    document.getElementById("makro-collect-all-page-btn").addEventListener("click", () => {
      const allCards = document.querySelectorAll("div[data-id]");
      if (allCards.length === 0) {
        alert("本页未找到可采集的商品卡片！");
        return;
      }

      const toCollectCards = [];
      let skippedCount = 0;
      allCards.forEach(card => {
        if (card.dataset.checkedExistence === "exists") {
          skippedCount++;
          return;
        }
        const d = extractCardData(card);
        if (d) toCollectCards.push(d);
      });

      if (toCollectCards.length === 0) {
        alert(`本页共 ${allCards.length} 件商品，全部已在跟品库中，已全部自动跳过！无需重复采集。`);
        return;
      }

      const btn = document.getElementById("makro-collect-all-page-btn");
      if (skippedCount > 0) {
        const msgEl = document.getElementById("makro-bar-msg");
        if (msgEl) {
          msgEl.style.display = "block";
          msgEl.style.color = "#fbbf24";
          msgEl.innerHTML = `⚡ 本页共 ${allCards.length} 件，已自动跳过 ${skippedCount} 件已在库商品，正在导入剩余 ${toCollectCards.length} 件新商品...`;
        }
      }
      runBatchCollect(toCollectCards, btn);
    });

    function runBatchCollect(itemsList, actionBtn) {
      actionBtn.disabled = true;
      const originalText = actionBtn.innerHTML;
      actionBtn.innerHTML = `<span>⚡</span> 正在极速并发入库 (${itemsList.length} 件)...`;
      const msgEl = document.getElementById("makro-bar-msg");
      if (msgEl) msgEl.style.display = "none";
      const startTime = Date.now();

      chrome.storage.local.get(["makro_target_store_id"], (sRes) => {
        const storeId = sRes?.makro_target_store_id || null;
        chrome.runtime.sendMessage({
          action: "COLLECT_MAKRO_PIGGYBACK_BATCH",
          rich_items: itemsList,
          store_id: storeId
        }, (res) => {
          actionBtn.disabled = false;
          actionBtn.innerHTML = originalText;
          if (msgEl) msgEl.style.display = "block";

          const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);

          if (res && res.success) {
            msgEl.style.color = "#34d399";
            const newAdded = res.data?.success_count || 0;
            const skipped = res.data?.skipped_existing_count || 0;
            const failed = res.data?.failed_count || 0;

            let tipText = `⚡ 并发采集完成 (耗时 ${elapsedSec}s)：成功入库 ${newAdded} 件`;
            if (skipped > 0) tipText += `，跳过已在库 ${skipped} 件`;
            if (failed > 0) tipText += `，失败 ${failed} 件`;
            msgEl.innerHTML = `✅ ${tipText}！`;

            // 将已采集卡片置为已入库
            itemsList.forEach(it => {
              const card = document.querySelector(`div[data-id="${it.fsn}"]`);
              if (card) {
                card.dataset.checkedExistence = "exists";
                const b = card.querySelector(".makro-search-quick-btn");
                if (b) {
                  b.style.background = "#059669";
                  b.innerText = "✅ 已入库";
                }
                const cb = card.querySelector(".makro-search-cb");
                if (cb) {
                  cb.checked = false;
                  cb.style.opacity = "0.5";
                }
              }
            });
            updateToolbarCount();
            setTimeout(() => { if (msgEl) msgEl.style.display = "none"; }, 6000);
          } else {
            msgEl.style.color = "#f87171";
            msgEl.innerHTML = `❌ 采集失败 (耗时 ${elapsedSec}s): ` + ((res && res.error) || "未知错误");
          }
        });
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
