"""
系统与浏览器插件版本号自动迭代与打包工具
使用方法:
    python backend/tools/bump_version.py --sys 2.1.0 --ext 1.1.0
    python backend/tools/bump_version.py --patch-sys
    python backend/tools/bump_version.py --patch-ext
    python backend/tools/bump_version.py --patch-all
"""
import os
import re
import json
import zipfile
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
EXTENSION_DIR = ROOT_DIR / "extension"

CONFIG_PY = BACKEND_DIR / "app" / "config.py"
INDEX_HTML = BACKEND_DIR / "app" / "templates" / "index.html"
MANIFEST_JSON = EXTENSION_DIR / "manifest.json"
POPUP_HTML = EXTENSION_DIR / "popup" / "popup.html"
FRONTEND_PKG = ROOT_DIR / "frontend" / "package.json"
ZIP_TARGETS = [
    ROOT_DIR / "extension.zip",
    BACKEND_DIR / "app" / "static" / "makro-extension.zip"
]

def get_current_versions():
    sys_ver = "2.1.0"
    ext_ver = "1.1.0"

    if CONFIG_PY.exists():
        content = CONFIG_PY.read_text(encoding="utf-8")
        m = re.search(r'SYSTEM_VERSION:\s*str\s*=\s*["\']([^"\']+)["\']', content)
        if m:
            sys_ver = m.group(1)

    if MANIFEST_JSON.exists():
        try:
            m_data = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
            if m_data.get("version"):
                ext_ver = m_data["version"]
        except Exception:
            pass

    return sys_ver, ext_ver

def bump_semver(ver_str: str) -> str:
    parts = ver_str.split(".")
    if len(parts) == 3 and parts[2].isdigit():
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)
    elif len(parts) == 2 and parts[1].isdigit():
        parts[1] = str(int(parts[1]) + 1)
        return ".".join(parts)
    return ver_str + ".1"

def update_versions(new_sys_ver: str, new_ext_ver: str):
    print(f"===> 开始迭代版本号:")
    print(f"     系统版本号 -> v{new_sys_ver}")
    print(f"     插件版本号 -> v{new_ext_ver}")

    # 1. 更新 backend/app/config.py
    if CONFIG_PY.exists():
        content = CONFIG_PY.read_text(encoding="utf-8")
        content = re.sub(r'SYSTEM_VERSION:\s*str\s*=\s*["\'][^"\']+["\']', f'SYSTEM_VERSION: str = "{new_sys_ver}"', content)
        content = re.sub(r'EXTENSION_VERSION:\s*str\s*=\s*["\'][^"\']+["\']', f'EXTENSION_VERSION: str = "{new_ext_ver}"', content)
        CONFIG_PY.write_text(content, encoding="utf-8")
        print(f"  [√] 更新 {CONFIG_PY.relative_to(ROOT_DIR)}")

    # 2. 更新 extension/manifest.json
    if MANIFEST_JSON.exists():
        m_data = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
        m_data["version"] = new_ext_ver
        MANIFEST_JSON.write_text(json.dumps(m_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  [√] 更新 {MANIFEST_JSON.relative_to(ROOT_DIR)}")

    # 3. 更新 extension/popup/popup.html
    if POPUP_HTML.exists():
        content = POPUP_HTML.read_text(encoding="utf-8")
        content = re.sub(r'<span class="ver">v[^<]+</span>', f'<span class="ver">v{new_ext_ver}</span>', content)
        POPUP_HTML.write_text(content, encoding="utf-8")
        print(f"  [√] 更新 {POPUP_HTML.relative_to(ROOT_DIR)}")

    # 4. 更新 backend/app/templates/index.html
    if INDEX_HTML.exists():
        content = INDEX_HTML.read_text(encoding="utf-8")
        # 系统版本徽标
        content = re.sub(r'(<span class="font-bold text-white text-xs tracking-tight">Makro 搬品</span>\s*<span class="[^"]*">)v[^<]+(</span>)', rf'\g<1>v{new_sys_ver}\g<2>', content)
        # 插件版本徽标
        content = re.sub(r'(<span class="text-sm font-bold text-slate-900">Makro 搬品插件安装包</span>\s*<span class="[^"]*">)v[^<]+(</span>)', rf'\g<1>v{new_ext_ver}\g<2>', content)
        # 下载文件名
        content = re.sub(r'download="makro-extension-v[^"]+\.zip"', f'download="makro-extension-v{new_ext_ver}.zip"', content)
        # 指引文本里的文件夹名
        content = re.sub(r'makro-extension-v[0-9.]+', f'makro-extension-v{new_ext_ver}', content)
        INDEX_HTML.write_text(content, encoding="utf-8")
        print(f"  [√] 更新 {INDEX_HTML.relative_to(ROOT_DIR)}")

    # 5. 更新 frontend/package.json
    if FRONTEND_PKG.exists():
        try:
            pkg_data = json.loads(FRONTEND_PKG.read_text(encoding="utf-8"))
            pkg_data["version"] = new_sys_ver
            FRONTEND_PKG.write_text(json.dumps(pkg_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"  [√] 更新 {FRONTEND_PKG.relative_to(ROOT_DIR)}")
        except Exception:
            pass

    # 6. 重新打包 extension.zip
    if EXTENSION_DIR.exists():
        for zip_p in ZIP_TARGETS:
            zip_p.parent.mkdir(parents=True, exist_ok=True)
            if zip_p.exists():
                zip_p.unlink()
            with zipfile.ZipFile(zip_p, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(EXTENSION_DIR):
                    for f in files:
                        full_p = Path(root) / f
                        arc_p = full_p.relative_to(EXTENSION_DIR)
                        z.write(full_p, arc_p)
            print(f"  [√] 编译打包 {zip_p.relative_to(ROOT_DIR)} ({zip_p.stat().st_size} bytes)")

    print(f"\n版本迭代与插件打包全部就绪！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="系统与插件版本号自动迭代工具")
    parser.add_argument("--sys", type=str, help="指定新的系统版本号 (如 2.1.0)")
    parser.add_argument("--ext", type=str, help="指定新的插件版本号 (如 1.1.0)")
    parser.add_argument("--patch-sys", action="store_true", help="自增系统小版本号 (+0.0.1)")
    parser.add_argument("--patch-ext", action="store_true", help="自增插件小版本号 (+0.0.1)")
    parser.add_argument("--patch-all", action="store_true", help="同时自增系统与插件小版本号")
    args = parser.parse_args()

    cur_sys, cur_ext = get_current_versions()

    target_sys = args.sys or (bump_semver(cur_sys) if (args.patch_sys or args.patch_all) else cur_sys)
    target_ext = args.ext or (bump_semver(cur_ext) if (args.patch_ext or args.patch_all) else cur_ext)

    update_versions(target_sys, target_ext)
