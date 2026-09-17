// =======================================================
// Takealot 智能自动筛选与翻页采集引擎 (Auto Collector)
// =======================================================

(function () {
  'use strict';

  // 仅在 Takealot 列表页/类目页/搜索页中运行，详情页不注入
  function isPLP() {
    return !/PLID\d+/i.test(window.location.href);
  }

  if (!isPLP()) return;

  // 运行状态与配置模型
  const state = {
    running: false,
    paused: false,
    minPrice: '',
    maxPrice: '',
    minReviews: '',
    maxReviews: '',
    minRating: '4.0',
    maxCollectItems: 50,
    maxScanPages: 10,
    filterSponsored: true,
    filterRestricted: true,
    autoList: false,

    // 运行统计
    scannedCount: 0,
    collectedCount: 0,
    skippedCount: 0,
    currentPage: 1,
    processedPlids: new Set()
  };

  let panelEl = null;
  let capsuleEl = null;
  let logBoxEl = null;

  // 1. 初始化 DOM 界面
  function initUI() {
    if (document.getElementById('tk-auto-capsule')) return;

    // A. 悬浮胶囊入口按钮
    capsuleEl = document.createElement('div');
    capsuleEl.id = 'tk-auto-capsule';
    capsuleEl.innerHTML = `
      <span class="capsule-icon">🤖</span>
      <span>智能自动采集</span>
      <span class="capsule-badge" id="tkCapsuleCount" style="display:none;">0</span>
    `;
    capsuleEl.title = '打开 Takealot 自动筛选与批量翻页采集面板';
    capsuleEl.addEventListener('click', () => {
      togglePanel(true);
    });
    document.body.appendChild(capsuleEl);

    // B. 主控制面板
    panelEl = document.createElement('div');
    panelEl.id = 'tk-auto-panel';
    panelEl.style.display = 'none';
    panelEl.innerHTML = `
      <div class="tk-auto-header" id="tkAutoHeader">
        <div class="tk-auto-title">
          <span>🤖</span>
          <span>自动筛选与翻页采集</span>
        </div>
        <div class="tk-auto-controls">
          <button type="button" class="tk-auto-btn-icon" id="tkAutoMinBtn" title="最小化">−</button>
          <button type="button" class="tk-auto-btn-icon" id="tkAutoCloseBtn" title="关闭">✕</button>
        </div>
      </div>

      <div class="tk-auto-body">
        <!-- 筛选预设条件 -->
        <div class="tk-auto-section">
          <div class="tk-auto-sec-title">
            <span>🎯 过滤与筛选条件</span>
            <span style="font-size:10px; color:#a0aec0; font-weight:normal;">留空表示不限</span>
          </div>

          <!-- 价格区间 -->
          <div class="tk-auto-row">
            <span class="tk-auto-label">价格区间:</span>
            <div class="tk-auto-input-group">
              <input type="number" id="tkMinPrice" class="tk-auto-input tk-auto-input-short" placeholder="最低 R" min="0">
              <span class="tk-auto-sep">~</span>
              <input type="number" id="tkMaxPrice" class="tk-auto-input tk-auto-input-short" placeholder="最高 R" min="0">
            </div>
          </div>

          <!-- 评论数量 -->
          <div class="tk-auto-row">
            <span class="tk-auto-label">评论数量:</span>
            <div class="tk-auto-input-group">
              <input type="number" id="tkMinReviews" class="tk-auto-input tk-auto-input-short" placeholder="最少条数" min="0">
              <span class="tk-auto-sep">~</span>
              <input type="number" id="tkMaxReviews" class="tk-auto-input tk-auto-input-short" placeholder="最多条数" min="0">
            </div>
          </div>

          <!-- 星级要求 -->
          <div class="tk-auto-row">
            <span class="tk-auto-label">最低星级:</span>
            <div class="tk-auto-input-group">
              <input type="number" id="tkMinRating" class="tk-auto-input tk-auto-input-short" placeholder="如 4.0" step="0.1" min="0" max="5" value="4.0">
              <span style="font-size:11px; color:#718096; margin-left:6px;">⭐ 星以上</span>
            </div>
          </div>

          <!-- 采集上限 & 翻页上限 -->
          <div class="tk-auto-row">
            <span class="tk-auto-label">采集目标:</span>
            <div class="tk-auto-input-group">
              <input type="number" id="tkMaxCollect" class="tk-auto-input tk-auto-input-short" value="50" min="1" max="1000">
              <span style="font-size:11px; color:#718096; margin-left:4px;">件</span>
              <span class="tk-auto-label" style="min-width:45px; text-align:right; margin-left:6px;">最多翻:</span>
              <input type="number" id="tkMaxPages" class="tk-auto-input tk-auto-input-short" value="10" min="1" max="50">
              <span style="font-size:11px; color:#718096; margin-left:4px;">页</span>
            </div>
          </div>

          <!-- 安全过滤开关 -->
          <div class="tk-auto-checkbox-row">
            <input type="checkbox" id="tkFilterRestricted" checked>
            <label for="tkFilterRestricted">🛡️ 智能排除受限/独立品牌与选品黑名单 (假发/液体/3C等)</label>
          </div>

          <div class="tk-auto-checkbox-row">
            <input type="checkbox" id="tkFilterSponsored" checked>
            <label for="tkFilterSponsored">🚫 排除 Sponsored 赞助广告品</label>
          </div>

          <div class="tk-auto-checkbox-row">
            <input type="checkbox" id="tkAutoList">
            <label for="tkAutoList">⚡ 采集后立即直上到店铺 (直上跟卖模式)</label>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="tk-auto-actions">
          <button type="button" class="tk-auto-btn tk-auto-btn-primary" id="tkAutoStartBtn">
            <span>🚀</span> <span>开始自动采集</span>
          </button>
          <button type="button" class="tk-auto-btn tk-auto-btn-secondary" id="tkAutoPauseBtn" style="display:none;">
            <span>⏸</span> <span>暂停</span>
          </button>
          <button type="button" class="tk-auto-btn tk-auto-btn-danger" id="tkAutoStopBtn" style="display:none;">
            <span>⏹</span> <span>停止</span>
          </button>
        </div>

        <!-- 进度与统计 -->
        <div class="tk-auto-section" style="margin-top:10px;">
          <div class="tk-auto-sec-title">
            <span>📊 采集与过滤进度</span>
            <span id="tkRunStatusText" style="font-size:11px; font-weight:normal; color:#718096;">就绪</span>
          </div>

          <div class="tk-auto-progress-bar">
            <div class="tk-auto-progress-fill" id="tkProgressFill"></div>
          </div>

          <div class="tk-auto-stats-grid">
            <div class="tk-auto-stat-box success">
              <div class="tk-auto-stat-num" id="tkStatSuccess">0</div>
              <div class="tk-auto-stat-label">符合入库</div>
            </div>
            <div class="tk-auto-stat-box filter">
              <div class="tk-auto-stat-num" id="tkStatSkip">0</div>
              <div class="tk-auto-stat-label">条件过滤</div>
            </div>
            <div class="tk-auto-stat-box page">
              <div class="tk-auto-stat-num" id="tkStatPage">1</div>
              <div class="tk-auto-stat-label">当前页/批次</div>
            </div>
          </div>
        </div>

        <!-- 实时日志 -->
        <div class="tk-auto-section" style="margin-bottom:0;">
          <div class="tk-auto-sec-title">
            <span>📜 实时运行日志</span>
            <a href="javascript:void(0)" id="tkClearLogBtn" style="font-size:10px; color:#3182ce; text-decoration:none;">清空</a>
          </div>
          <div class="tk-auto-log-box" id="tkLogBox">
            <div class="tk-auto-log-line info">[系统就绪] 请在上方预设筛选条件，点击「开始自动采集」。</div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(panelEl);
    logBoxEl = document.getElementById('tkLogBox');

    // 绑定事件
    bindUIEvents();

    // 恢复历史配置
    loadSavedSettings();
  }

  function togglePanel(show) {
    if (!panelEl) return;
    if (show) {
      panelEl.style.display = 'flex';
      capsuleEl.style.display = 'none';
    } else {
      panelEl.style.display = 'none';
      capsuleEl.style.display = 'flex';
    }
  }

  // 绑定界面交互事件
  function bindUIEvents() {
    // 最小化 / 关闭
    document.getElementById('tkAutoCloseBtn').addEventListener('click', () => togglePanel(false));
    document.getElementById('tkAutoMinBtn').addEventListener('click', () => {
      panelEl.classList.toggle('minimized');
    });

    // 拖拽面板头部
    makeDraggable(document.getElementById('tkAutoHeader'), panelEl);

    // 清空日志
    document.getElementById('tkClearLogBtn').addEventListener('click', () => {
      if (logBoxEl) logBoxEl.innerHTML = '';
    });

    // 实时保存配置
    const inputs = ['tkMinPrice', 'tkMaxPrice', 'tkMinReviews', 'tkMaxReviews', 'tkMinRating', 'tkMaxCollect', 'tkMaxPages', 'tkFilterRestricted', 'tkFilterSponsored', 'tkAutoList'];
    inputs.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('change', saveSettingsFromUI);
      }
    });

    // 开始采集
    document.getElementById('tkAutoStartBtn').addEventListener('click', startAutoCollect);

    // 暂停/恢复
    document.getElementById('tkAutoPauseBtn').addEventListener('click', () => {
      if (!state.running) return;
      state.paused = !state.paused;
      const pauseBtn = document.getElementById('tkAutoPauseBtn');
      if (state.paused) {
        pauseBtn.innerHTML = '<span>▶</span> <span>继续</span>';
        updateStatusText('已暂停');
        addLog('⏸ 自动化采集已暂停', 'warn');
      } else {
        pauseBtn.innerHTML = '<span>⏸</span> <span>暂停</span>';
        updateStatusText('正在运行...');
        addLog('▶ 自动化采集已恢复执行', 'info');
      }
    });

    // 停止采集
    document.getElementById('tkAutoStopBtn').addEventListener('click', stopAutoCollect);
  }

  // 从 UI 读取配置并保存
  function saveSettingsFromUI() {
    state.minPrice = document.getElementById('tkMinPrice').value.trim();
    state.maxPrice = document.getElementById('tkMaxPrice').value.trim();
    state.minReviews = document.getElementById('tkMinReviews').value.trim();
    state.maxReviews = document.getElementById('tkMaxReviews').value.trim();
    state.minRating = document.getElementById('tkMinRating').value.trim();
    state.maxCollectItems = parseInt(document.getElementById('tkMaxCollect').value, 10) || 50;
    state.maxScanPages = parseInt(document.getElementById('tkMaxPages').value, 10) || 10;
    state.filterRestricted = document.getElementById('tkFilterRestricted').checked;
    state.filterSponsored = document.getElementById('tkFilterSponsored').checked;
    state.autoList = document.getElementById('tkAutoList').checked;

    const savedData = {
      autoCollector: {
        minPrice: state.minPrice,
        maxPrice: state.maxPrice,
        minReviews: state.minReviews,
        maxReviews: state.maxReviews,
        minRating: state.minRating,
        maxCollectItems: state.maxCollectItems,
        maxScanPages: state.maxScanPages,
        filterRestricted: state.filterRestricted,
        filterSponsored: state.filterSponsored,
        autoList: state.autoList
      }
    };
    chrome.storage.local.set(savedData);
  }

  // 加载已保存配置
  function loadSavedSettings() {
    chrome.storage.local.get(['autoCollector', 'autoList'], (res) => {
      const c = res?.autoCollector || {};
      if (c.minPrice !== undefined) {
        document.getElementById('tkMinPrice').value = c.minPrice;
        state.minPrice = c.minPrice;
      }
      if (c.maxPrice !== undefined) {
        document.getElementById('tkMaxPrice').value = c.maxPrice;
        state.maxPrice = c.maxPrice;
      }
      if (c.minReviews !== undefined) {
        document.getElementById('tkMinReviews').value = c.minReviews;
        state.minReviews = c.minReviews;
      }
      if (c.maxReviews !== undefined) {
        document.getElementById('tkMaxReviews').value = c.maxReviews;
        state.maxReviews = c.maxReviews;
      }
      if (c.minRating !== undefined) {
        document.getElementById('tkMinRating').value = c.minRating;
        state.minRating = c.minRating;
      }
      if (c.maxCollectItems !== undefined) {
        document.getElementById('tkMaxCollect').value = c.maxCollectItems;
        state.maxCollectItems = c.maxCollectItems;
      }
      if (c.maxScanPages !== undefined) {
        document.getElementById('tkMaxPages').value = c.maxScanPages;
        state.maxScanPages = c.maxScanPages;
      }
      if (c.filterRestricted !== undefined) {
        document.getElementById('tkFilterRestricted').checked = !!c.filterRestricted;
        state.filterRestricted = !!c.filterRestricted;
      }
      if (c.filterSponsored !== undefined) {
        document.getElementById('tkFilterSponsored').checked = !!c.filterSponsored;
        state.filterSponsored = !!c.filterSponsored;
      }
      if (c.autoList !== undefined || res?.autoList !== undefined) {
        const al = c.autoList !== undefined ? !!c.autoList : !!res?.autoList;
        document.getElementById('tkAutoList').checked = al;
        state.autoList = al;
      }
    });
  }

  // 日志记录与更新
  function addLog(msg, type = 'info') {
    if (!logBoxEl) return;
    const timeStr = new Date().toTimeString().slice(0, 8);
    const line = document.createElement('div');
    line.className = `tk-auto-log-line ${type}`;
    line.textContent = `[${timeStr}] ${msg}`;
    logBoxEl.appendChild(line);
    logBoxEl.scrollTop = logBoxEl.scrollHeight;
  }

  function updateStatusText(txt) {
    const el = document.getElementById('tkRunStatusText');
    if (el) el.textContent = txt;
  }

  function updateDashboard() {
    const sSucc = document.getElementById('tkStatSuccess');
    const sSkip = document.getElementById('tkStatSkip');
    const sPage = document.getElementById('tkStatPage');
    const pFill = document.getElementById('tkProgressFill');
    const capCount = document.getElementById('tkCapsuleCount');

    if (sSucc) sSucc.textContent = state.collectedCount;
    if (sSkip) sSkip.textContent = state.skippedCount;
    if (sPage) sPage.textContent = state.currentPage;

    const pct = Math.min(100, Math.round((state.collectedCount / Math.max(1, state.maxCollectItems)) * 100));
    if (pFill) pFill.style.width = `${pct}%`;

    if (capCount) {
      if (state.collectedCount > 0) {
        capCount.style.display = 'inline-block';
        capCount.textContent = state.collectedCount;
      } else {
        capCount.style.display = 'none';
      }
    }
  }

  // 2. 单个卡片深度数据提取器
  function parseProductCard(card) {
    // 提取 PLID
    let plid = card.id;
    if (!plid || !/^\d+$/.test(plid)) {
      const link = card.querySelector('a[href*="/PLID"], a[href*="PLID"]');
      const href = link ? (link.getAttribute('href') || '') : '';
      const m = href.match(/PLID(\d+)/i);
      if (m) plid = m[1];
    }
    if (!plid || !/^\d+$/.test(plid)) return null;

    // 提取标题 (避免 Takealot 的 "Go to product details" 占位链接干扰)
    let title = '';
    const h4 = card.querySelector('h4, [class*="product-title"], [class*="title-"]');
    if (h4) {
      title = h4.innerText.trim();
    } else {
      const titleLinks = card.querySelectorAll('a[title]');
      for (const tl of titleLinks) {
        const tVal = (tl.getAttribute('title') || '').trim();
        if (tVal && tVal.toLowerCase() !== 'go to product details') {
          title = tVal;
          break;
        }
      }
    }

    // 提取价格 (ZAR)
    let price = null;
    let priceText = '';
    const priceEls = card.querySelectorAll('[class*="price"], [class*="currency"]');
    for (const el of priceEls) {
      const txt = el.innerText || '';
      const m = txt.match(/R\s*([\d,]+(?:\.\d+)?)/i);
      if (m) {
        priceText = m[0];
        price = parseFloat(m[1].replace(/,/g, ''));
        break;
      }
    }

    // 提取评论星级与数量
    let rating = 0;
    let reviewCount = 0;
    const ratingEls = card.querySelectorAll('[class*="rating"], [class*="review"], [aria-label*="star"], [class*="star"]');
    for (const el of ratingEls) {
      const txt = el.innerText || el.getAttribute('aria-label') || '';
      const m = txt.match(/([\d\.]+)\s*\(\s*([\d,]+)\s*\)/);
      if (m) {
        rating = parseFloat(m[1]);
        reviewCount = parseInt(m[2].replace(/,/g, ''), 10);
        break;
      }
    }
    if (rating === 0) {
      const starEl = card.querySelector('[class*="rating-star"], [class*="rating-score"], [aria-label*="star"]');
      if (starEl) {
        const m = (starEl.innerText || starEl.getAttribute('aria-label') || '').match(/([\d\.]+)/);
        if (m) rating = parseFloat(m[1]);
      }
      const countEl = card.querySelector('[class*="rating-count"], [class*="reviews-count"]');
      if (countEl) {
        const m = countEl.innerText.match(/([\d,]+)/);
        if (m) reviewCount = parseInt(m[1].replace(/,/g, ''), 10);
      }
    }

    // 检测 Sponsored 赞助广告
    const isSponsored = /sponsored|赞助/i.test(card.innerText);

    // 检测受限品牌、独立品牌与选品黑名单
    let hitBrand = null;
    if (window.TkBrandChecker) {
      hitBrand = window.TkBrandChecker.detectInScope(card);
      if (!hitBrand && title && title.toLowerCase() !== 'go to product details') {
        hitBrand = window.TkBrandChecker.checkTitle(title);
      }
    }

    return {
      card,
      plid,
      title,
      price,
      priceText,
      rating,
      reviewCount,
      isSponsored,
      hitBrand
    };
  }

  // 3. 过滤规则核验
  function evaluateCard(data) {
    const { price, rating, reviewCount, isSponsored, hitBrand } = data;

    // A. 品牌与黑名单风控拦截
    if (state.filterRestricted && hitBrand) {
      return { pass: false, reason: hitBrand, type: 'danger' };
    }

    // B. 赞助广告拦截
    if (state.filterSponsored && isSponsored) {
      return { pass: false, reason: '赞助广告', type: 'skip' };
    }

    // C. 价格区间检验
    if (state.minPrice !== '' && !isNaN(parseFloat(state.minPrice))) {
      const minP = parseFloat(state.minPrice);
      if (price === null || price < minP) {
        return { pass: false, reason: `价格 R${price || 0} < R${minP}`, type: 'skip' };
      }
    }
    if (state.maxPrice !== '' && !isNaN(parseFloat(state.maxPrice))) {
      const maxP = parseFloat(state.maxPrice);
      if (price === null || price > maxP) {
        return { pass: false, reason: `价格 R${price || 0} > R${maxP}`, type: 'skip' };
      }
    }

    // D. 评论数量要求
    if (state.minReviews !== '' && !isNaN(parseInt(state.minReviews, 10))) {
      const minR = parseInt(state.minReviews, 10);
      if (reviewCount < minR) {
        return { pass: false, reason: `评论 ${reviewCount} < ${minR}`, type: 'skip' };
      }
    }
    if (state.maxReviews !== '' && !isNaN(parseInt(state.maxReviews, 10))) {
      const maxR = parseInt(state.maxReviews, 10);
      if (reviewCount > maxR) {
        return { pass: false, reason: `评论 ${reviewCount} > ${maxR}`, type: 'skip' };
      }
    }

    // E. 评论星级要求
    if (state.minRating !== '' && !isNaN(parseFloat(state.minRating))) {
      const minRat = parseFloat(state.minRating);
      if (rating < minRat) {
        return { pass: false, reason: `评分 ${rating}⭐ < ${minRat}⭐`, type: 'skip' };
      }
    }

    return { pass: true, reason: '符合条件', type: 'ok' };
  }

  // 4. 卡片状态打标装饰
  function markCardBadge(card, text, type) {
    if (!card) return;
    card.classList.remove('tk-card-matched', 'tk-card-skipped', 'tk-card-restricted');
    if (type === 'ok') card.classList.add('tk-card-matched');
    else if (type === 'danger') card.classList.add('tk-card-restricted');
    else card.classList.add('tk-card-skipped');

    let badge = card.querySelector('.tk-card-status-badge');
    if (!badge) {
      badge = document.createElement('div');
      badge.className = `tk-card-status-badge ${type}`;
      card.appendChild(badge);
    } else {
      badge.className = `tk-card-status-badge ${type}`;
    }
    badge.textContent = text;
  }

  // 5. 自动采集执行主调度器
  async function startAutoCollect() {
    saveSettingsFromUI();

    state.running = true;
    state.paused = false;
    state.scannedCount = 0;
    state.collectedCount = 0;
    state.skippedCount = 0;
    state.currentPage = 1;
    state.processedPlids.clear();

    document.getElementById('tkAutoStartBtn').style.display = 'none';
    document.getElementById('tkAutoPauseBtn').style.display = 'inline-flex';
    document.getElementById('tkAutoStopBtn').style.display = 'inline-flex';

    updateStatusText('正在运行...');
    updateDashboard();

    addLog(`🚀 启动自动采集: 价格[${state.minPrice || '不限'}~${state.maxPrice || '不限'}] 评论>=${state.minReviews || '不限'} 评分>=${state.minRating || '不限'}⭐`, 'info');
    addLog(`🎯 目标采集上限: ${state.maxCollectItems} 件, 最大翻页数: ${state.maxScanPages} 页`, 'info');

    try {
      await runCollectionLoop();
    } catch (err) {
      addLog(`❌ 采集执行异常: ${err.message}`, 'error');
    } finally {
      stopAutoCollect();
    }
  }

  // 采集主循环（逐批扫描 + 自动翻页/Load More）
  async function runCollectionLoop() {
    while (state.running) {
      // 暂停等待
      while (state.paused && state.running) {
        await sleep(500);
      }
      if (!state.running) break;

      addLog(`🔍 开始扫描第 ${state.currentPage} 页/批次商品...`, 'info');

      // 提取当前 DOM 中所有商品卡片
      const cards = Array.from(document.querySelectorAll('article.product-card, article[id], [data-ref="product-card"], .product-card'));
      const unscannedCards = [];

      for (const card of cards) {
        const parsed = parseProductCard(card);
        if (parsed && !state.processedPlids.has(parsed.plid)) {
          unscannedCards.push(parsed);
          state.processedPlids.add(parsed.plid);
        }
      }

      addLog(`本批次发现 ${unscannedCards.length} 个新商品 (总计加载 ${cards.length} 件)`, 'info');

      // 逐个比对并采集
      for (const item of unscannedCards) {
        if (!state.running) break;
        while (state.paused && state.running) {
          await sleep(500);
        }
        if (!state.running) break;

        state.scannedCount++;
        const check = evaluateCard(item);

        if (!check.pass) {
          state.skippedCount++;
          markCardBadge(item.card, `✗ ${check.reason}`, check.type);
          // 如果是受限品牌或重要广告，记录日志
          if (check.type === 'danger') {
            addLog(`[PLID ${item.plid}] 🛡️ 排除: ${check.reason}`, 'warn');
          }
          updateDashboard();
          continue;
        }

        // 符合条件 -> 采集入库
        markCardBadge(item.card, '⏳ 正在采集入库...', 'ok');
        addLog(`[PLID ${item.plid}] 🎯 命中目标 (R${item.price}, ${item.rating}⭐, ${item.reviewCount}评) -> 入库中...`, 'info');

        const success = await scrapeSingleProduct(item);
        if (success) {
          state.collectedCount++;
          markCardBadge(item.card, '✓ 符合已入库', 'ok');
          addLog(`[PLID ${item.plid}] ✓ 采集成功入库！(当前已达成 ${state.collectedCount}/${state.maxCollectItems})`, 'success');
        } else {
          markCardBadge(item.card, '❌ 入库失败', 'skip');
        }

        updateDashboard();

        // 检查是否已达到目标上限
        if (state.collectedCount >= state.maxCollectItems) {
          addLog(`🎉 已达目标采集数量 (${state.collectedCount} 件)，采集圆满完成！`, 'success');
          updateStatusText('采集已达成');
          return;
        }

        // 采集间隔防频控
        await sleep(700);
      }

      if (!state.running) break;

      // 检查是否已达最大翻页数
      if (state.currentPage >= state.maxScanPages) {
        addLog(`🏁 已达最大翻页批次 (${state.maxScanPages} 页)，采集结束。`, 'info');
        updateStatusText('已达最大页数');
        return;
      }

      // 尝试自动加载下一页 / Load More
      addLog(`⏳ 准备加载下一批商品 (Load More / 自动翻页)...`, 'info');
      const loadedMore = await triggerNextPage();
      if (!loadedMore) {
        addLog(`🏁 页面已无更多商品或翻页结束。`, 'info');
        updateStatusText('无更多商品');
        return;
      }

      state.currentPage++;
      updateDashboard();
      // 等待新商品在 DOM 中渲染
      await sleep(2000);
    }
  }

  // 触发翻页或点击 Load More
  async function triggerNextPage() {
    // 1. 滚动到页面底部以激活懒加载
    window.scrollTo({
      top: document.body.scrollHeight - 500,
      behavior: 'smooth'
    });
    await sleep(800);

    // 2. 优先查找 "Load More" 按钮
    const allBtns = Array.from(document.querySelectorAll('button, a'));
    const loadMoreBtn = allBtns.find(b => {
      const txt = b.innerText.trim().toLowerCase();
      return /load more|加载更多/i.test(txt);
    });

    if (loadMoreBtn && isElementVisible(loadMoreBtn)) {
      const beforeCount = document.querySelectorAll('article.product-card, article[id], [data-ref="product-card"], .product-card').length;
      loadMoreBtn.click();
      addLog('🖱️ 已点击「Load More」加载更多商品...', 'info');

      // 轮询等待新卡片渲染
      for (let i = 0; i < 12; i++) {
        await sleep(500);
        const curCount = document.querySelectorAll('article.product-card, article[id], [data-ref="product-card"], .product-card').length;
        if (curCount > beforeCount) {
          addLog(`✨ 成功加载新商品 (卡片总数增至 ${curCount})`, 'success');
          return true;
        }
      }
      return false;
    }

    // 3. 若无 Load More，检测常规下一页链接 (Next / › / Pagination)
    const nextLink = allBtns.find(b => {
      const txt = b.innerText.trim().toLowerCase();
      const aria = (b.getAttribute('aria-label') || '').toLowerCase();
      return /next|下一页|›|»/i.test(txt) || /next/i.test(aria);
    });

    if (nextLink && isElementVisible(nextLink)) {
      addLog('🖱️ 正在点击下一页按钮...', 'info');
      nextLink.click();
      await sleep(2500);
      return true;
    }

    // 4. 若为标准 page 参数 URL
    const url = new URL(window.location.href);
    const curPageParam = parseInt(url.searchParams.get('page') || '1', 10);
    url.searchParams.set('page', curPageParam + 1);
    addLog(`🌐 跳转至第 ${curPageParam + 1} 页: ${url.href}`, 'info');
    window.location.href = url.href;
    return false;
  }

  function isElementVisible(el) {
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  // 单品采集推送至后端
  function scrapeSingleProduct(item) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({
        action: 'SCRAPE_PRODUCT',
        data: {
          plid: item.plid,
          title: item.title,
          price: item.price,
          is_restricted: item.hitBrand ? 1 : 0,
          restricted_brand: item.hitBrand || '',
          auto_list: state.autoList
        }
      }, (res) => {
        if (res && res.ok) {
          resolve(true);
        } else {
          addLog(`[PLID ${item.plid}] 采集失败: ${res?.message || '网络异常'}`, 'error');
          resolve(false);
        }
      });
    });
  }

  // 停止采集
  function stopAutoCollect() {
    state.running = false;
    state.paused = false;

    const startBtn = document.getElementById('tkAutoStartBtn');
    const pauseBtn = document.getElementById('tkAutoPauseBtn');
    const stopBtn = document.getElementById('tkAutoStopBtn');

    if (startBtn) startBtn.style.display = 'inline-flex';
    if (pauseBtn) pauseBtn.style.display = 'none';
    if (stopBtn) stopBtn.style.display = 'none';

    updateStatusText('已停止');
    addLog(`⏹ 自动采集已终止。本次共扫描 ${state.scannedCount} 件，命中入库 ${state.collectedCount} 件，过滤跳过 ${state.skippedCount} 件。`, 'info');
  }

  function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  // 支持拖拽面板
  function makeDraggable(handle, target) {
    let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
    handle.onmousedown = dragMouseDown;

    function dragMouseDown(e) {
      if (e.target.tagName === 'BUTTON') return;
      e.preventDefault();
      pos3 = e.clientX;
      pos4 = e.clientY;
      document.onmouseup = closeDragElement;
      document.onmousemove = elementDrag;
    }

    function elementDrag(e) {
      e.preventDefault();
      pos1 = pos3 - e.clientX;
      pos2 = pos4 - e.clientY;
      pos3 = e.clientX;
      pos4 = e.clientY;
      target.style.top = (target.offsetTop - pos2) + "px";
      target.style.left = (target.offsetLeft - pos1) + "px";
      target.style.bottom = 'auto';
      target.style.right = 'auto';
    }

    function closeDragElement() {
      document.onmouseup = null;
      document.onmousemove = null;
    }
  }

  // 监听来自 Popup 的指令
  chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
    if (req.action === 'OPEN_AUTO_COLLECTOR') {
      initUI();
      togglePanel(true);
      sendResponse({ ok: true });
      return true;
    }
    if (req.action === 'START_AUTO_COLLECT') {
      initUI();
      togglePanel(true);
      if (!state.running) {
        startAutoCollect();
      }
      sendResponse({ ok: true });
      return true;
    }
    if (req.action === 'STOP_AUTO_COLLECT') {
      stopAutoCollect();
      sendResponse({ ok: true });
      return true;
    }
    if (req.action === 'GET_AUTO_COLLECT_STATE') {
      sendResponse({ ok: true, state });
      return true;
    }
  });

  // 页面加载完成后自动初始化入口胶囊
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
  } else {
    setTimeout(initUI, 400);
  }

})();
