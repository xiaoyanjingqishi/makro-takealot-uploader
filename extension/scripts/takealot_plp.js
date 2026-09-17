// Takealot PLP (Product Listing Page) 搜索页/类目页/列表页 智能采集脚本
(function () {
  'use strict';

  function isPLP() {
    return !/PLID\d+/i.test(window.location.href);
  }

  function injectCardButtons() {
    if (!isPLP()) {
      document.querySelectorAll('.tk-plp-action-btn, .tk-plp-badge-btn').forEach(el => el.remove());
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

      // 1. 底部主操作区：全宽醒目采集按钮 (置于 Add to Cart 上方)
      const actionsContainer = card.querySelector('[class*="product-actions-container"], .product-actions');
      if (actionsContainer && !actionsContainer.querySelector(`.tk-plp-action-btn[data-plid="${plid}"]`)) {
        actionsContainer.querySelectorAll('.tk-plp-action-btn').forEach(b => b.remove());

        const btn = document.createElement('button');
        btn.className = 'tk-plp-action-btn notranslate';
        btn.type = 'button';
        btn.dataset.plid = plid;
        btn.innerHTML = '⚡ 采集到 Makro';
        btn.title = `采集商品 (PLID ${plid}) 及其所有变体到 Makro`;

        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          doScrape(plid, btn, card);
        });

        actionsContainer.insertBefore(btn, actionsContainer.firstChild);
      }

      // 2. 主图左上角快捷悬浮角标 (方便快速扫品)
      const imgContainer = card.querySelector('[class*="product-image-container"], .product-card-image, [class*="aspect-ratio-container"]');
      if (imgContainer && !imgContainer.querySelector(`.tk-plp-badge-btn[data-plid="${plid}"]`)) {
        imgContainer.querySelectorAll('.tk-plp-badge-btn').forEach(b => b.remove());
        if (window.getComputedStyle(imgContainer).position === 'static') {
          imgContainer.style.position = 'relative';
        }

        const badge = document.createElement('button');
        badge.className = 'tk-plp-badge-btn notranslate';
        badge.type = 'button';
        badge.dataset.plid = plid;
        badge.innerHTML = '🚀 采集';
        badge.title = `采集商品 PLID ${plid} 到 Makro`;

        badge.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const mainBtn = card.querySelector(`.tk-plp-action-btn[data-plid="${plid}"]`);
          doScrape(plid, mainBtn || badge, card, badge);
        });

        imgContainer.appendChild(badge);
      }
    });
  }

  function doScrape(plid, btn, card, badge) {
    const triggerBtn = btn || badge;
    const defaultText = triggerBtn?.classList.contains('tk-plp-badge-btn') ? '🚀 采集' : '⚡ 采集到 Makro';
    
    // 品牌侵权风控检测与二次确认守卫
    if (window.TkBrandChecker && window.TkBrandChecker.guard(triggerBtn, card, defaultText)) {
      return;
    }

    const hitBrand = window.TkBrandChecker ? window.TkBrandChecker.detectInScope(card) : null;

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
        if (btn) {
          btn.innerHTML = `✓ 已入库 (${count} 个变体)`;
          btn.classList.remove('loading');
          btn.classList.add('done');
        }
        if (badge) {
          badge.innerHTML = `✓ ${count}变体`;
          badge.classList.add('done');
        }
      } else {
        if (btn) {
          btn.innerHTML = '❌ 采集失败';
          btn.classList.remove('loading');
          btn.classList.add('error');
          setTimeout(() => {
            btn.innerHTML = '⚡ 采集到 Makro';
            btn.classList.remove('error');
          }, 3000);
        }
        if (badge) {
          badge.innerHTML = '❌';
          setTimeout(() => {
            badge.innerHTML = '🚀 采集';
          }, 3000);
        }
        const errMsg = res ? (res.error || res.message || res.detail) : '采集失败，请确保本地中台服务 (http://localhost:8001) 已启动';
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
