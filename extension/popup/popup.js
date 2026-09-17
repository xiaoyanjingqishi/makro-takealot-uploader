document.addEventListener("DOMContentLoaded", () => {
  const backendStatus = document.getElementById("backend-status");
  const pageInfo = document.getElementById("page-info");
  const actionBtn = document.getElementById("action-btn");
  const statCount = document.getElementById("stat-count");
  const statMarkup = document.getElementById("stat-markup");

  const autoCollectorBtn = document.getElementById("auto-collector-btn");

  const BACKEND_URL = "http://localhost:8001";

  // 1. 检查后端连接与拉取基础数据
  fetch(`${BACKEND_URL}/api/settings`)
    .then(res => res.json())
    .then(settings => {
      backendStatus.innerText = "服务在线";
      backendStatus.className = "status-badge online";
      statMarkup.innerText = `+${Math.round((settings.markup_ratio - 1) * 100)}% +R${settings.fixed_markup}`;

      // 拉取商品数量
      return fetch(`${BACKEND_URL}/api/products?page=1&page_size=1`);
    })
    .then(res => res ? res.json() : null)
    .then(pData => {
      if (pData) statCount.innerText = `${pData.total} 件`;
    })
    .catch(() => {
      backendStatus.innerText = "离线 (请启动后端)";
      backendStatus.className = "status-badge offline";
      statCount.innerText = "-";
    });

  // 2. 获取当前活动标签页
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs || tabs.length === 0) return;
    const currentTab = tabs[0];
    const url = currentTab.url || "";

    if (url.includes("takealot.com")) {
      const isPlp = !/PLID\d+/i.test(url);
      pageInfo.innerText = currentTab.title || (isPlp ? "Takealot 列表搜索页" : "Takealot 商品详情页");
      
      if (autoCollectorBtn) {
        autoCollectorBtn.style.display = "block";
        autoCollectorBtn.onclick = () => {
          chrome.tabs.sendMessage(currentTab.id, { action: "OPEN_AUTO_COLLECTOR" });
          window.close();
        };
      }

      if (isPlp) {
        actionBtn.innerText = "🤖 启动自动筛选采集面板";
        actionBtn.disabled = false;
        actionBtn.onclick = () => {
          chrome.tabs.sendMessage(currentTab.id, { action: "OPEN_AUTO_COLLECTOR" });
          window.close();
        };
      } else {
        actionBtn.innerText = "📦 立即采集当前商品";
        actionBtn.disabled = false;
        actionBtn.onclick = () => {
        actionBtn.innerText = "⏳ 采集处理中...";
        actionBtn.disabled = true;

        // 优先向 content_takealot.js 发送 DO_COLLECT 触发提取
        chrome.tabs.sendMessage(currentTab.id, { action: "DO_COLLECT" }, (res) => {
          if (chrome.runtime.lastError || !res) {
            // 如果 content_script 未响应，尝试直接从 tab.url 解析 PLID 调 background
            const urlMatch = url.match(/PLID(\d+)/i);
            if (urlMatch) {
              const plid = `PLID${urlMatch[1]}`;
              chrome.runtime.sendMessage({
                action: "COLLECT_PLID",
                data: { plid, url }
              }, (bgRes) => {
                if (bgRes && bgRes.success) {
                  actionBtn.innerText = "✅ 采集入库成功！";
                  fetch(`${BACKEND_URL}/api/products?page=1&page_size=1`)
                    .then(r => r.json())
                    .then(p => { if (p) statCount.innerText = `${p.total} 件`; });
                } else {
                  actionBtn.innerText = "❌ 采集失败";
                }
                setTimeout(() => {
                  actionBtn.innerText = "📦 立即采集当前商品";
                  actionBtn.disabled = false;
                }, 2500);
              });
              return;
            }
          }

          if (res && res.success) {
            actionBtn.innerText = "✅ 采集入库成功！";
            fetch(`${BACKEND_URL}/api/products?page=1&page_size=1`)
              .then(r => r.json())
              .then(p => { if (p) statCount.innerText = `${p.total} 件`; });
          } else {
            actionBtn.innerText = res ? "❌ 采集失败" : "✅ 已发送采集指令";
          }
          setTimeout(() => {
            actionBtn.innerText = "📦 立即采集当前商品";
            actionBtn.disabled = false;
          }, 2500);
        });
      };
    } else if (url.includes("seller.makro.co.za")) {
      pageInfo.innerText = "Makro 卖家中心";
      actionBtn.innerText = "🔄 同步 Makro 店铺凭据";
      actionBtn.disabled = false;
      actionBtn.onclick = () => {
        actionBtn.innerText = "⏳ 正在同步...";
        actionBtn.disabled = true;

        chrome.runtime.sendMessage(
          {
            action: "SYNC_MAKRO_CREDENTIALS",
            data: {
              seller_id: "cb80491bf0a34dc5",
              fk_csrf_token: "FbvXzXEP-45o5eUtkeX8Wo6LCq9GBWDg9Rcg"
            }
          },
          (res) => {
            if (res && res.success) {
              actionBtn.innerText = "✅ 同步成功！";
            } else {
              actionBtn.innerText = "❌ 同步失败";
            }
            setTimeout(() => {
              actionBtn.innerText = "🔄 同步 Makro 店铺凭据";
              actionBtn.disabled = false;
            }, 2500);
          }
        );
      };
    } else {
      pageInfo.innerText = "未在 Takealot 或 Makro 页面";
      actionBtn.innerText = "访问 Takealot 选品";
      actionBtn.disabled = false;
      actionBtn.onclick = () => {
        chrome.tabs.create({ url: "https://www.takealot.com" });
      };
    }
  });
});
