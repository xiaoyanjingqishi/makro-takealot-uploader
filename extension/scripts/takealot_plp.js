// Takealot PLP (Product Listing Page) 搜索页/类目页/列表页 Makro 智能采集脚本 (独立命名空间 & 自动跟卖插件无冲突兼容版)
(function () {
  'use strict';

  function isPLP() {
    return !/PLID\d+/i.test(window.location.href);
  }

  // 已采集缓存 Map: plid -> { collected: true, count: N, status: 'PENDING_CLEAN' }
  const collectedPlidsMap = new Map();
  const pendingPlids = new Set();
  let queryTimer = null;

  // 调度批量存在性检查
  function scheduleExistenceCheck(plid) {
    if (!plid || collectedPlidsMap.has(plid)) return;
    pendingPlids.add(plid);
    if (queryTimer) clearTimeout(queryTimer);
    queryTimer = setTimeout(flushExistenceCheck, 200);
  }

  function flushExistenceCheck() {
    if (pendingPlids.size === 0) return;
    const plidsToQuery = Array.from(pendingPlids);
    pendingPlids.clear();

    chrome.runtime.sendMessage({
      action: 'CHECK_PLIDS_EXISTENCE',
      plids: plidsToQuery
    }, (res) => {
      if (res && res.success && res.exists) {
        for (const [k, v] of Object.entries(res.exists)) {
          collectedPlidsMap.set(k, v);
          const clean = k.replace(/[^0-9]/g, '');
          if (clean) collectedPlidsMap.set(clean, v);
        }
        applyCollectedStatusToDOM();
      }
    });
  }

  // 将已采集状态应用到 DOM 元素中 (仅作用于 Makro 专属类名)
  function applyCollectedStatusToDOM() {
    document.querySelectorAll('.makro-plp-action-btn').forEach(btn => {
      const plid = btn.dataset.makroPlid || btn.dataset.plid;
      const info = collectedPlidsMap.get(plid);
      if (info && info.collected && btn.dataset.makroReconfirmArmed !== '1' && !btn.classList.contains('loading')) {
        btn.classList.add('collected', 'makro-collected');
        btn.innerHTML = `✓ 已在选品箱 (${info.count}变体) · 点击重采`;
        btn.title = `该商品已在选品箱中（包含 ${info.count} 个变体）。点击可重新抓取覆盖。`;
      }
    });

    document.querySelectorAll('.makro-plp-badge-btn').forEach(badge => {
      const plid = badge.dataset.makroPlid || badge.dataset.plid;
      const info = collectedPlidsMap.get(plid);
      if (info && info.collected && badge.dataset.makroReconfirmArmed !== '1' && !badge.disabled) {
        badge.classList.add('collected', 'makro-collected');
        badge.innerHTML = `✓已采(${info.count})`;
        badge.title = `已在选品箱 (${info.count}变体)`;
      }
    });
  }

  // 已采集悬浮二次确认气泡 (专属 makro-reconfirm-bubble)
  function showReconfirmBubble(targetBtn, msg) {
    document.querySelectorAll('.makro-reconfirm-bubble').forEach(el => el.remove());

    const bubble = document.createElement('div');
    bubble.className = 'makro-reconfirm-bubble';
    bubble.innerHTML = msg;
    document.body.appendChild(bubble);

    const rect = targetBtn.getBoundingClientRect();
    const bw = bubble.offsetWidth || 340;
    const bh = bubble.offsetHeight || 38;
    let left = rect.left + rect.width / 2 - bw / 2;
    left = Math.max(10, Math.min(left, window.innerWidth - bw - 10));
    const top = Math.max(10, rect.top - bh - 10);
    bubble.style.left = `${left}px`;
    bubble.style.top = `${top}px`;

    requestAnimationFrame(() => bubble.classList.add('show'));
    setTimeout(() => {
      bubble.classList.remove('show');
      setTimeout(() => bubble.remove(), 250);
    }, 6000);
  }

  // 二次点击确认守卫机制 (使用专属属性 makroReconfirmArmed)
  function handleCollectedGuard(triggerBtn, plid, info, defaultText, badge) {
    if (!info || !info.collected) {
      delete triggerBtn.dataset.makroReconfirmArmed;
      return false;
    }

    // 第二次点击：已处于二次确认期，放行执行重新采集
    if (triggerBtn.dataset.makroReconfirmArmed === '1') {
      delete triggerBtn.dataset.makroReconfirmArmed;
      if (triggerBtn._makroReconfirmTimer) {
        clearTimeout(triggerBtn._makroReconfirmTimer);
        triggerBtn._makroReconfirmTimer = null;
      }
      triggerBtn.classList.remove('reconfirm-warning', 'makro-reconfirm-warning');
      if (badge) badge.classList.remove('reconfirm-warning', 'makro-reconfirm-warning');
      return false; // 放行执行
    }

    // 第一次点击：拦截并激活二次确认状态
    triggerBtn.dataset.makroReconfirmArmed = '1';
    triggerBtn.classList.add('reconfirm-warning', 'makro-reconfirm-warning');
    triggerBtn.innerHTML = '⚠️ 再次点击确认重新采集 (覆盖)';
    if (badge) {
      badge.classList.add('reconfirm-warning', 'makro-reconfirm-warning');
      badge.innerHTML = '⚠️确认重采';
    }

    showReconfirmBubble(triggerBtn, `💡 该商品已在选品箱中 (${info.count || 1} 个变体)，再次点击将重新抓取并覆盖更新！`);

    if (triggerBtn._makroReconfirmTimer) clearTimeout(triggerBtn._makroReconfirmTimer);
    triggerBtn._makroReconfirmTimer = setTimeout(() => {
      if (triggerBtn.dataset.makroReconfirmArmed === '1') {
        delete triggerBtn.dataset.makroReconfirmArmed;
        triggerBtn.classList.remove('reconfirm-warning', 'makro-reconfirm-warning');
        triggerBtn.innerHTML = defaultText;
        if (badge) {
          badge.classList.remove('reconfirm-warning', 'makro-reconfirm-warning');
          badge.innerHTML = `✓已采(${info.count || 1})`;
        }
      }
      triggerBtn._makroReconfirmTimer = null;
    }, 10000);

    return true; // 拦截执行
  }

  function injectCardButtons() {
    if (!isPLP()) {
      document.querySelectorAll('.makro-plp-action-btn, .makro-plp-badge-btn').forEach(el => el.remove());
      return;
    }

    // 匹配商品卡片外层容器
    const cards = document.querySelectorAll('article.product-card, article[id], [data-ref="product-card"], .product-card');
    cards.forEach((card) => {
      // 提取纯数字 PLID：优先从 article.id，其次从 a[href*="PLID"]
      let plid = card.id;
      if (!plid || !/^\d+$/.test(plid)) {
        const link = card.querySelector('a[href*="/PLID"], a[href*="PLID"]');
        const href = link ? (link.getAttribute('href') || '') : '';
        const m = href.match(/PLID(\d+)/i);
        if (m) plid = m[1];
      }
      if (!plid || !/^\d+$/.test(plid)) return;

      // 注册批量存在性检查
      scheduleExistenceCheck(plid);

      const collectedInfo = collectedPlidsMap.get(plid);
      const isCollected = collectedInfo && collectedInfo.collected;

      // 1. 底部主操作区：全宽醒目 Makro 采集按钮
      // 【无冲突设计】：绝不删除 .tk-plp-action-btn，仅维护专属的 .makro-plp-action-btn
      const actionsContainer = card.querySelector('[class*="product-actions-container"], .product-actions');
      if (actionsContainer && !actionsContainer.querySelector(`.makro-plp-action-btn[data-makro-plid="${plid}"]`)) {
        actionsContainer.querySelectorAll('.makro-plp-action-btn').forEach(b => b.remove());

        const btn = document.createElement('button');
        btn.className = `makro-plp-action-btn notranslate ${isCollected ? 'collected makro-collected' : ''}`;
        btn.type = 'button';
        btn.dataset.plid = plid;
        btn.dataset.makroPlid = plid;
        btn.innerHTML = isCollected 
          ? `✓ 已在选品箱 (${collectedInfo.count}变体) · 点击重采`
          : '⚡ 采集到 Makro';
        btn.title = isCollected
          ? `商品已在选品箱中（包含 ${collectedInfo.count} 个变体）。点击可重新抓取覆盖。`
          : `采集商品 (PLID ${plid}) 及其所有变体到 Makro`;

        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const badge = card.querySelector(`.makro-plp-badge-btn[data-makro-plid="${plid}"]`);
          doScrape(plid, btn, card, badge);
        });

        // 插入在操作区首部，与已有按钮（如 ERP 跟卖按钮、Add to Cart）垂直协同并列
        actionsContainer.insertBefore(btn, actionsContainer.firstChild);
      }

      // 2. 主图快捷悬浮小标
      // 【无冲突设计】：若检测到已有跟卖角标 (.tk-plp-badge-btn)，自动向下偏移 (makro-badge-stacked)
      const imgContainer = card.querySelector('[class*="product-image-container"], .product-card-image, [class*="aspect-ratio-container"]');
      if (imgContainer) {
        let badge = imgContainer.querySelector(`.makro-plp-badge-btn[data-makro-plid="${plid}"]`);
        const hasRepricerBadge = !!imgContainer.querySelector('.tk-plp-badge-btn');

        if (!badge) {
          imgContainer.querySelectorAll('.makro-plp-badge-btn').forEach(b => b.remove());
          if (window.getComputedStyle(imgContainer).position === 'static') {
            imgContainer.style.position = 'relative';
          }

          badge = document.createElement('button');
          badge.className = `makro-plp-badge-btn notranslate ${isCollected ? 'collected makro-collected' : ''} ${hasRepricerBadge ? 'makro-badge-stacked' : ''}`;
          badge.type = 'button';
          badge.dataset.plid = plid;
          badge.dataset.makroPlid = plid;
          badge.innerHTML = isCollected ? `✓已采(${collectedInfo.count})` : '📦 Makro采';
          badge.title = isCollected ? `已在选品箱 (${collectedInfo.count}变体)` : `采集商品 PLID ${plid} 到 Makro`;

          badge.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const mainBtn = card.querySelector(`.makro-plp-action-btn[data-makro-plid="${plid}"]`);
            doScrape(plid, mainBtn || badge, card, badge);
          });

          imgContainer.appendChild(badge);
        } else {
          // 动态响应跟卖插件的角标出现状态
          if (hasRepricerBadge && !badge.classList.contains('makro-badge-stacked')) {
            badge.classList.add('makro-badge-stacked');
          } else if (!hasRepricerBadge && badge.classList.contains('makro-badge-stacked')) {
            badge.classList.remove('makro-badge-stacked');
          }
        }
      }
    });
  }

  function doScrape(plid, btn, card, badge) {
    const triggerBtn = btn || badge;
    const collectedInfo = collectedPlidsMap.get(plid);
    const isCollected = collectedInfo && collectedInfo.collected;

    const defaultText = triggerBtn?.classList.contains('makro-plp-badge-btn')
      ? (isCollected ? `✓已采(${collectedInfo.count})` : '📦 Makro采')
      : (isCollected ? `✓ 已在选品箱 (${collectedInfo.count}变体) · 点击重采` : '⚡ 采集到 Makro');
    
    // 1. 已采集商品二次确认守卫
    if (handleCollectedGuard(triggerBtn, plid, collectedInfo, defaultText, badge)) {
      return;
    }

    // 2. 品牌侵权风控检测与二次确认守卫 (优先使用专属 MakroBrandChecker)
    const brandChecker = window.MakroBrandChecker || window.TkBrandChecker;
    if (brandChecker && brandChecker.guard(triggerBtn, card, defaultText)) {
      return;
    }

    const hitBrand = brandChecker ? brandChecker.detectInScope(card) : null;

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '⏳ 正在解析入库...';
      btn.classList.add('loading');
    }
    if (badge) {
      badge.disabled = true;
      badge.innerHTML = '⏳';
    }

    chrome.runtime.sendMessage({
      action: 'COLLECT_PLID',
      data: {
        plid: plid,
        is_restricted: hitBrand ? 1 : 0,
        restricted_brand: hitBrand || ''
      }
    }, (res) => {
      if (btn) btn.disabled = false;
      if (badge) badge.disabled = false;

      const isOk = res && (res.ok || res.success);
      if (isOk) {
        const count = (typeof res.count === 'number') ? res.count : (res.data?.total_variants || 1);
        collectedPlidsMap.set(plid, { collected: true, count: count });
        collectedPlidsMap.set(`PLID${plid}`, { collected: true, count: count });

        if (btn) {
          btn.innerHTML = `✓ 已入库 (${count} 个变体)`;
          btn.classList.remove('loading', 'reconfirm-warning', 'makro-reconfirm-warning');
          btn.classList.add('collected', 'makro-collected', 'done');
          setTimeout(() => {
            btn.innerHTML = `✓ 已在选品箱 (${count}变体) · 点击重采`;
            btn.classList.remove('done');
          }, 3000);
        }
        if (badge) {
          badge.innerHTML = `✓ ${count}变体`;
          badge.classList.remove('reconfirm-warning', 'makro-reconfirm-warning');
          badge.classList.add('collected', 'makro-collected', 'done');
          setTimeout(() => {
            badge.innerHTML = `✓已采(${count})`;
            badge.classList.remove('done');
          }, 3000);
        }
      } else {
        if (btn) {
          btn.innerHTML = '❌ 采集失败';
          btn.classList.remove('loading', 'reconfirm-warning', 'makro-reconfirm-warning');
          btn.classList.add('error');
          setTimeout(() => {
            btn.innerHTML = defaultText;
            btn.classList.remove('error');
          }, 3000);
        }
        if (badge) {
          badge.innerHTML = '❌';
          badge.classList.remove('reconfirm-warning', 'makro-reconfirm-warning');
          setTimeout(() => {
            badge.innerHTML = defaultText;
          }, 3000);
        }
        let errMsg = res ? (res.error || res.message || res.detail) : '采集失败，请检查中台服务是否已启动或在插件弹窗中确认中台连接地址';
        if (typeof errMsg === "string" && errMsg.includes("Failed to fetch")) {
          errMsg = "无法连接中台服务 (若在局域网电脑，请点击右上角插件图标配置服务器 IP 如 http://192.168.110.145:8001)";
        }
        alert(errMsg);
      }
    });
  }

  // 页面加载、无限滚动与 DOM 动态渲染监听
  setTimeout(injectCardButtons, 500);
  const observer = new MutationObserver(() => injectCardButtons());
  observer.observe(document.body, { childList: true, subtree: true });

  // 监听 SPA 路由切换 (Next.js 客户端导航)
  let lastUrl = window.location.href;
  setInterval(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      injectCardButtons();
    }
  }, 600);
})();
