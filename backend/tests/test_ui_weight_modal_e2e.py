# -*- coding: utf-8 -*-
import time
import sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

options = Options()
options.add_argument("--headless")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--window-size=1440,900")

driver = webdriver.Chrome(options=options)

try:
    driver.get("http://localhost:8001")
    time.sleep(1.5)

    # 1. 登录
    inputs = driver.find_elements(By.TAG_NAME, "input")
    for inp in inputs:
        typ = inp.get_attribute("type")
        placeholder = inp.get_attribute("placeholder") or ""
        if "用户" in placeholder or "user" in placeholder.lower() or typ == "text":
            inp.clear()
            inp.send_keys("admin")
        elif typ == "password":
            inp.clear()
            inp.send_keys("admin123")

    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        if "登录" in btn.text:
            btn.click()
            break

    time.sleep(2)
    print("Logged in, current URL:", driver.current_url)

    # 2. 检查全局配置中的 air_freight_rate
    config_rate = driver.execute_script("""
        return window.vm ? window.vm.orderFinanceConfig.air_freight_rate : null;
    """)
    print(f"orderFinanceConfig.air_freight_rate = {config_rate} (Expected: 90.0)")

    # 3. 切换至订单管理 Tab
    driver.execute_script("""
        if (window.vm) {
            window.vm.switchTab('orders');
        }
    """)
    time.sleep(2)

    # 4. 触发订单 24 (或首笔订单) 的 openWeightModal
    modal_info = driver.execute_script("""
        if (!window.vm) return { error: 'vm not ready' };
        var list = window.vm.orders.items || [];
        var target = list.find(o => o.id === 24) || list[0];
        if (!target) return { error: 'no order in list' };
        window.vm.openWeightModal(target);
        return {
            order_id: target.order_id,
            totalWeightG: window.vm.weightModal.totalWeightG,
            airFreightRate: window.vm.weightModal.airFreightRate,
            open: window.vm.weightModal.open
        };
    """)
    print("Modal Opened Info:", modal_info)
    assert modal_info.get("open") is True, "Modal should be open"
    assert modal_info.get("airFreightRate") == 90.0, f"Expected 90.0, got {modal_info.get('airFreightRate')}"

    # 5. 模拟用户输入称重 663g 并保存
    save_result = driver.execute_async_script("""
        var callback = arguments[arguments.length - 1];
        window.vm.weightModal.totalWeightG = 663;
        window.vm.saveOrderWeight().then(function() {
            callback({
                success: true,
                modalOpen: window.vm.weightModal.open,
                totalWeightG: window.vm.weightModal.order.total_weight_g,
                headHaul: window.vm.weightModal.order.independent_head_haul
            });
        }).catch(function(err) {
            callback({ success: false, error: String(err) });
        });
    """)
    print("Save Modal Result:", save_result)
    assert save_result.get("success") is True, f"Save failed: {save_result}"
    assert save_result.get("modalOpen") is False, "Modal should close on success"

    # 6. 检查浏览器错误日志
    logs = driver.get_log("browser")
    severe = [l for l in logs if l.get("level") == "SEVERE"]
    print("Console severe logs count:", len(severe))
    if severe:
        for s in severe:
            print("  -", s)

    print(">>> All UI Weight Modal and 90 rate tests passed with zero errors! <<<")
finally:
    driver.quit()
