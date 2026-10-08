document.addEventListener("DOMContentLoaded", () => {
  const backendStatus = document.getElementById("backend-status");
  const pageInfo = document.getElementById("page-info");
  const actionBtn = document.getElementById("action-btn");
  const statCount = document.getElementById("stat-count");
  const statMarkup = document.getElementById("stat-markup");
  const autoCollectorBtn = document.getElementById("auto-collector-btn");
  const openDashboardBtn = document.getElementById("open-dashboard-btn");

  const backendUrlInput = document.getElementById("backend-url-input");
  const saveBackendBtn = document.getElementById("save-backend-btn");
  const presetCloud = document.getElementById("preset-cloud");
  const presetLocal = document.getElementById("preset-local");
  const presetLan = document.getElementById("preset-lan");
  const presetLan8001 = document.getElementById("preset-lan-8001");
  const connFeedback = document.getElementById("conn-feedback");
  const pingIndicator = document.getElementById("ping-indicator");
  const operatorSelect = document.getElementById("operator-select");
  const operatorIndicator = document.getElementById("operator-indicator");
  const operatorFeedback = document.getElementById("operator-feedback");

  let detectedLanIp = "192.168.110.145";
  let currentBackendUrl = "https://makro.superll.top";

  function showFeedback(msg, isSuccess = true) {
    if (!connFeedback) return;
    connFeedback.innerText = msg;
    connFeedback.className = isSuccess ? "conn-feedback success" : "conn-feedback error";
    setTimeout(() => {
      if (connFeedback.innerText === msg) {
        connFeedback.innerText = "";
      }
    }, 4000);
  }

  // 加载系统员工列表并同步选中状态 (方案 A)
  function loadOperatorsAndSync(targetUrl) {
    if (!operatorSelect) return;
    targetUrl = (targetUrl || currentBackendUrl).replace(/\/+$/, "");

    fetch(`${targetUrl}/api/users/operators`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (!data || !Array.isArray(data.operators)) return;
        chrome.storage.local.get(["makro_collector_user"], (sRes) => {
          const savedUser = sRes?.makro_collector_user || null;
          operatorSelect.innerHTML = '<option value="">-- 请选择当前操作员工 --</option>';

          let matched = false;
          data.operators.forEach(op => {
            const opt = document.createElement("option");
            opt.value = op.id;
            opt.setAttribute("data-username", op.username);
            opt.setAttribute("data-nickname", op.nickname || op.username);
            opt.innerText = `${op.nickname || op.username} (${op.username}) [${op.role === 'ADMIN' ? '管理员' : '员工'}]`;
            if (savedUser && String(savedUser.id) === String(op.id)) {
              opt.selected = true;
              matched = true;
            }
            operatorSelect.appendChild(opt);
          });

          if (matched && savedUser) {
            if (operatorIndicator) {
              operatorIndicator.innerText = `已绑定: ${savedUser.nickname || savedUser.username}`;
              operatorIndicator.style.color = "#10b981";
            }
          } else {
            if (operatorIndicator) {
              operatorIndicator.innerText = "未选择";
              operatorIndicator.style.color = "#f59e0b";
            }
          }
        });
      })
      .catch(err => {
        console.warn("加载员工列表失败:", err);
        if (operatorSelect) {
          operatorSelect.innerHTML = '<option value="">❌ 无法连接服务器获取员工</option>';
        }
      });
  }

  if (operatorSelect) {
    operatorSelect.onchange = () => {
      const val = operatorSelect.value;
      if (!val) {
        chrome.storage.local.remove(["makro_collector_user"]);
        if (operatorIndicator) {
          operatorIndicator.innerText = "未选择";
          operatorIndicator.style.color = "#f59e0b";
        }
        if (operatorFeedback) {
          operatorFeedback.innerText = "⚠️ 未指定采集员工，采集将记录为未分配";
          operatorFeedback.style.color = "#f59e0b";
        }
        return;
      }
      const selectedOption = operatorSelect.options[operatorSelect.selectedIndex];
      const username = selectedOption.getAttribute("data-username");
      const nickname = selectedOption.getAttribute("data-nickname");
      const userObj = { id: parseInt(val, 10), username, nickname };

      chrome.storage.local.set({ makro_collector_user: userObj }, () => {
        if (operatorIndicator) {
          operatorIndicator.innerText = `已绑定: ${nickname || username}`;
          operatorIndicator.style.color = "#10b981";
        }
        if (operatorFeedback) {
          operatorFeedback.innerText = `✅ 已绑定采集归属：${nickname || username}，后续采集自动归属此账号`;
          operatorFeedback.style.color = "#10b981";
        }
      });
    };
  }

  // 加载系统店铺列表并同步选中状态
  function loadStoresAndSync(targetUrl) {
    const storeSelect = document.getElementById("store-select");
    const storeIndicator = document.getElementById("store-indicator");
    if (!storeSelect) return;
    targetUrl = (targetUrl || currentBackendUrl).replace(/\/+$/, "");

    fetch(`${targetUrl}/api/stores`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (!Array.isArray(data)) return;
        chrome.storage.local.get(["makro_target_store_id"], (sRes) => {
          const savedStoreId = sRes?.makro_target_store_id || null;
          storeSelect.innerHTML = '<option value="">-- 系统默认主店 --</option>';

          let matched = false;
          let matchedName = "默认主店";
          data.forEach(s => {
            if (!s.is_active) return;
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.setAttribute("data-name", s.name);
            opt.innerText = `${s.name} (ID: ${s.id})${s.is_default ? ' [默认]' : ''}`;
            if (savedStoreId && String(savedStoreId) === String(s.id)) {
              opt.selected = true;
              matched = true;
              matchedName = s.name;
            }
            storeSelect.appendChild(opt);
          });

          if (matched && storeIndicator) {
            storeIndicator.innerText = `已选: ${matchedName}`;
            storeIndicator.style.color = "#10b981";
          } else if (storeIndicator) {
            storeIndicator.innerText = "默认主店";
            storeIndicator.style.color = "#38bdf8";
          }
        });
      })
      .catch(err => {
        console.warn("加载店铺列表失败:", err);
        if (storeSelect) {
          storeSelect.innerHTML = '<option value="">❌ 无法连接服务器获取店铺</option>';
        }
      });
  }

  const storeSelect = document.getElementById("store-select");
  if (storeSelect) {
    storeSelect.onchange = () => {
      const val = storeSelect.value;
      const storeIndicator = document.getElementById("store-indicator");
      const storeFeedback = document.getElementById("store-feedback");
      if (!val) {
        chrome.storage.local.remove(["makro_target_store_id"]);
        if (storeIndicator) {
          storeIndicator.innerText = "默认主店";
          storeIndicator.style.color = "#38bdf8";
        }
        if (storeFeedback) {
          storeFeedback.innerText = "💡 已恢复为系统默认主店";
          storeFeedback.style.color = "#64748b";
        }
        return;
      }
      const sId = parseInt(val, 10);
      const selectedOption = storeSelect.options[storeSelect.selectedIndex];
      const storeName = selectedOption.getAttribute("data-name") || `店铺#${sId}`;

      chrome.storage.local.set({ makro_target_store_id: sId }, () => {
        if (storeIndicator) {
          storeIndicator.innerText = `已选: ${storeName}`;
          storeIndicator.style.color = "#10b981";
        }
        if (storeFeedback) {
          storeFeedback.innerText = `✅ 已绑定跟品店铺：${storeName}，采集将直接录入此店并精准排重`;
          storeFeedback.style.color = "#10b981";
        }
      });
    };
  }

  // 1. 检查指定地址连通性并拉取核心指标
  function checkConnectionAndRefresh(targetUrl) {
    if (!targetUrl) targetUrl = currentBackendUrl;
    targetUrl = targetUrl.replace(/\/+$/, "");

    if (pingIndicator) pingIndicator.innerText = "正在探测...";

    // 同步拉取员工列表与店铺列表
    loadOperatorsAndSync(targetUrl);
    loadStoresAndSync(targetUrl);

    fetch(`${targetUrl}/api/settings`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(settings => {
        backendStatus.innerText = "服务在线";
        backendStatus.className = "status-badge online";
        if (pingIndicator) pingIndicator.innerText = "✅ 延迟良好";
        if (statMarkup) {
          statMarkup.innerText = `+${Math.round(((settings.markup_ratio || 1.35) - 1) * 100)}% +R${settings.fixed_markup || 20}`;
        }
        if (openDashboardBtn) {
          openDashboardBtn.href = targetUrl;
        }

        // 尝试探测更详细的网络信息
        fetch(`${targetUrl}/api/settings/network-info`)
          .then(r => r.ok ? r.json() : null)
          .then(net => {
            if (net && net.primary_ip) {
              detectedLanIp = net.primary_ip;
              if (presetLan) presetLan.title = `http://${detectedLanIp}`;
              if (presetLan8001) presetLan8001.title = `http://${detectedLanIp}:8001`;
            }
          })
          .catch(() => {});

        // 拉取商品箱总数
        return fetch(`${targetUrl}/api/products?page=1&page_size=1`);
      })
      .then(res => res ? res.json() : null)
      .then(pData => {
        if (pData && statCount) statCount.innerText = `${pData.total} 件`;
      })
      .catch((err) => {
        backendStatus.innerText = "离线 (请检查)";
        backendStatus.className = "status-badge offline";
        if (pingIndicator) pingIndicator.innerText = "❌ 无法连通";
        if (statCount) statCount.innerText = "-";
      });
  }

  // 2. 初始化加载存储的后端 URL 配置
  chrome.storage.local.get(["backend_url"], (res) => {
    let savedUrl = (res && res.backend_url) ? res.backend_url.trim() : "https://makro.superll.top";
    currentBackendUrl = savedUrl;
    if (backendUrlInput) backendUrlInput.value = savedUrl;
    if (openDashboardBtn) openDashboardBtn.href = savedUrl;
    checkConnectionAndRefresh(savedUrl);
  });

  // 3. 保存并测试后端地址配置
  function saveAndApplyUrl(newUrl) {
    newUrl = (newUrl || "").trim();
    if (!newUrl) newUrl = "https://makro.superll.top";
    if (!/^https?:\/\//i.test(newUrl)) {
      newUrl = "http://" + newUrl;
    }
    newUrl = newUrl.replace(/\/+$/, "");

    if (backendUrlInput) backendUrlInput.value = newUrl;
    currentBackendUrl = newUrl;

    chrome.runtime.sendMessage({ action: "SET_BACKEND_CONFIG", backend_url: newUrl }, (resp) => {
      showFeedback(`已保存中台地址: ${newUrl}`, true);
      checkConnectionAndRefresh(newUrl);
    });
  }

  if (saveBackendBtn) {
    saveBackendBtn.onclick = () => {
      saveAndApplyUrl(backendUrlInput ? backendUrlInput.value : "");
    };
  }

  if (backendUrlInput) {
    backendUrlInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        saveAndApplyUrl(backendUrlInput.value);
      }
    });
  }

  // 4. 预设快捷按钮绑定
  if (presetCloud) {
    presetCloud.onclick = () => {
      saveAndApplyUrl("https://makro.superll.top");
    };
  }

  if (presetLocal) {
    presetLocal.onclick = () => {
      saveAndApplyUrl("http://localhost:8001");
    };
  }

  if (presetLan) {
    presetLan.onclick = () => {
      saveAndApplyUrl(`http://${detectedLanIp}`);
    };
  }

  if (presetLan8001) {
    presetLan8001.onclick = () => {
      saveAndApplyUrl(`http://${detectedLanIp}:8001`);
    };
  }

  // 5. 获取当前活动标签页并设置页面快捷操作
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
          chrome.tabs.sendMessage(currentTab.id, { action: "OPEN_MAKRO_AUTO_COLLECTOR" });
          window.close();
        };
      }

      if (isPlp) {
        actionBtn.innerText = "📦 启动 Makro 自动采集面板";
        actionBtn.disabled = false;
        actionBtn.onclick = () => {
          chrome.tabs.sendMessage(currentTab.id, { action: "OPEN_MAKRO_AUTO_COLLECTOR" });
          window.close();
        };
      } else {
        actionBtn.innerText = "📦 立即采集当前商品";
        actionBtn.disabled = false;
        actionBtn.onclick = () => {
          actionBtn.innerText = "⏳ 采集处理中...";
          actionBtn.disabled = true;

          chrome.tabs.sendMessage(currentTab.id, { action: "DO_COLLECT" }, (res) => {
            if (chrome.runtime.lastError || !res) {
              const urlMatch = url.match(/PLID(\d+)/i);
              if (urlMatch) {
                const plid = `PLID${urlMatch[1]}`;
                chrome.runtime.sendMessage({
                  action: "COLLECT_PLID",
                  data: { plid, url }
                }, (bgRes) => {
                  if (bgRes && bgRes.success) {
                    actionBtn.innerText = "✅ 采集入库成功！";
                    checkConnectionAndRefresh(currentBackendUrl);
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
              checkConnectionAndRefresh(currentBackendUrl);
            } else {
              actionBtn.innerText = res ? "❌ 采集失败" : "✅ 已发送采集指令";
            }
            setTimeout(() => {
              actionBtn.innerText = "📦 立即采集当前商品";
              actionBtn.disabled = false;
            }, 2500);
          });
        };
      }
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
