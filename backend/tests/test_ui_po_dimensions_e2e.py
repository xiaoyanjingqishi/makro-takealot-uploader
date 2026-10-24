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

    # 2. 切换至订单管理 Tab
    driver.execute_script("""
        if (window.vm) {
            window.vm.switchTab('orders');
        }
    """)
    time.sleep(2)

    # 3. 切换至 PO 视角
    driver.execute_script("""
        if (window.vm) {
            window.vm.switchOrdersPerspective('po');
        }
    """)
    time.sleep(2)

    # 4. 检查 PO 视角中是否有批次卡片及其箱规字段
    po_check = driver.execute_script("""
        if (!window.vm) return { error: 'vm not found' };
        var list = window.vm.poListModal.list || [];
        if (list.length === 0) return { empty: true, list_len: 0 };
        var p0 = list[0];
        return {
            id: p0.id,
            po_number: p0.po_number,
            box_length_cm: p0.box_length_cm,
            box_width_cm: p0.box_width_cm,
            box_height_cm: p0.box_height_cm,
            package_weight_kg: p0.package_weight_kg,
            box_dimensions_str: p0.box_dimensions_str
        };
    """)
    print("PO check in UI:", po_check)

    # 5. 测试打开新建 PO 弹窗 (poCreateForm 初始结构)
    create_form_check = driver.execute_script("""
        window.vm.orders.selectedIds = [window.vm.orders.items[0]?.id || 24];
        window.vm.openCreatePoModal();
        return {
            open: window.vm.showCreatePoModal,
            box_length_cm: window.vm.poCreateForm.box_length_cm,
            box_width_cm: window.vm.poCreateForm.box_width_cm,
            box_height_cm: window.vm.poCreateForm.box_height_cm,
            package_weight_kg: window.vm.poCreateForm.package_weight_kg,
            selected_weight: window.vm.poCreateForm.selected_orders_weight_kg
        };
    """)
    print("Create PO Modal check:", create_form_check)
    assert create_form_check.get("open") is True, "Create PO modal should open"

    # 关闭创建弹窗
    driver.execute_script("window.vm.showCreatePoModal = false;")

    # 6. 如果有 PO，模拟行内修改箱规并自动保存
    if po_check and not po_check.get("empty"):
        update_result = driver.execute_async_script("""
            var callback = arguments[arguments.length - 1];
            var po = window.vm.poListModal.list[0];
            po.box_length_cm = 42.5;
            po.box_width_cm = 32.0;
            po.box_height_cm = 22.0;
            po.package_weight_kg = 7.35;
            window.vm.updatePoDetails(po).then(function() {
                callback({ success: true, updated_id: po.id });
            }).catch(function(e) {
                callback({ success: false, error: String(e) });
            });
        """)
        print("Inline update result:", update_result)
        assert update_result.get("success") is True, "Inline update should succeed"

    # 7. 检查浏览器 Console 是否有 SEVERE 错误
    logs = driver.get_log("browser")
    severe = [l for l in logs if l.get("level") == "SEVERE"]
    print("Browser Console severe errors count:", len(severe))
    if severe:
        for s in severe:
            print("  -", s)
    assert len(severe) == 0, f"Expected 0 severe errors, found {len(severe)}"

    print(">>> All PO Dimensions and Weight UI tests passed with 0 errors! <<<")
finally:
    driver.quit()
