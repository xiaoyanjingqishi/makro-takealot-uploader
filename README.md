# Makro-Takealot 智能搬品与自动化刊登系统

基于南非电商平台 **Takealot** 前台选品、**AI（通义千问 / DeepSeek）** 规范化数据清洗、以及逆向破解的 **Makro（基于 Flipkart SaaS 引擎）** 底层协议所构建的一体化智能搬品刊登系统。

---

## 一、系统架构与核心特性

1. **Chrome 选品与辅助插件 (`extension/`)**：
   - **Takealot 一键采集**：注入 Takealot 商品详情与搜索页，页面右下角悬浮“📦 一键采集到 Makro”卡片，自动提取原图、原标题、原价、参数规格、变体（尺码/颜色）。
   - **Makro 凭据一键同步**：注入 `seller.makro.co.za`，点击左下角悬浮助手一键同步当前浏览器的 `fk-csrf-token`、Session Cookie 与 `sellerId` 到本地中台，彻底免除 Cookie 手动复制与过期问题。
2. **AI 规范化清洗引擎 (`backend/app/services/ai_cleaner_service.py`)**：
   - 支持 **通义千问 (Qwen)** 与 **DeepSeek**（统一 OpenAI 兼容接口，后台一键切换）。
   - 自动按照 Makro 官方类目规范，提取 16+ 项严苛必填 Catalog 属性（材质、尺寸、类型、包装等），并生成合规的 SEO 英文标题。
   - 具备本地智能规则兜底（未填 API Key 时也能正常体验）。
3. **动态定价与利润引擎 (`backend/app/services/pricing_service.py`)**：
   - 预设公式：`Makro 售价 = Takealot 原价 * 1.35 + 20`，`划线原价 MRP = 售价 * 1.5`。
   - 加价比例、固定附加运费、MRP 上浮倍率均可在 Web 控制台随时灵活调整。
4. **Makro 逆向协议驱动 (`backend/app/services/makro_client.py`)**：
   - 自动将 Takealot 高清原图流式转存上传至 Makro 官方静态 CDN（`/napi/scf/uploadImage`）。
   - 自动生成草稿并分配事务 `requestId` / `txnId` / `reqId`（`/napi/createProductV2/create`）。
   - 组装多变体（Group Listings）与完整 Catalog + Listing 数据包提交审核（`/napi/createProductV2/submit`）。
5. **现代化 Web 管理控制台 (`http://localhost:8000`)**：
   - **选品箱**：表格化浏览已采集的 Takealot 商品，支持批量 AI 清洗与批量刊登。
   - **双栏审品比对**：左侧展示 Takealot 原图原参数，右侧展示 AI 转换后的 Makro 待上架表单，支持人工快速微调。
   - **上品任务看板**：实时追踪上传日志、Makro 审核状态与错误回执。
   - **系统配置**：可视化配置 AI Key、加价公式与店铺参数。

---

## 二、快速上手指南 (1 分钟极速运行)

### 第一步：启动本地中台系统
在项目根目录下，直接**双击运行 `启动系统.bat`**（或在终端运行）：
```bash
python start_system.py
```
终端将启动 FastAPI 服务，并**自动在默认浏览器中打开 Web 控制台**：
- 控制台地址：`http://localhost:8000`
- Swagger API 接口文档：`http://localhost:8000/api/docs`

---

### 第二步：安装 Chrome 浏览器插件
1. 打开 Chrome 浏览器，在地址栏输入：`chrome://extensions/`
2. 开启右上角的 **【开发者模式】(Developer mode)**。
3. 点击左上角 **【加载已解压的扩展程序】(Load unpacked)**。
4. 选择本项目中的 **`extension`** 文件夹（即 `e:\Python Project\makro上品\extension`）。
5. 安装完成！Chrome 右上角扩展栏将出现 **Makro 搬品助手** 图标。

---

### 第三步：在 Makro 卖家后台同步登录凭据 (只需点一次)
1. 在 Chrome 中打开并登录 [Makro 卖家后台](https://seller.makro.co.za/)。
2. 页面左下角将出现 **【搬品系统已就绪】** 浮动卡片。
3. 点击 **【🔄 同步登录态至后台】** 按钮，系统自动读取当前会话的 Cookie 与 `fk-csrf-token` 并存入后端！

---

### 第四步：在 Takealot 选品与搬品
1. 在 Chrome 中访问 [Takealot.com](https://www.takealot.com/) 并打开任意商品详情页（如毛巾、浴室用品等）。
2. 页面右下角将出现 **【📦 一键采集到 Makro】** 浮动按钮。
3. 点击按钮，插件将在 1 秒内将商品标题、价格、高清图片、规格参数推送到本地中台，并自动完成 AI 清洗与加价计算！
4. 打开控制台 `http://localhost:8000`：
   - 在 **【选品箱】** 勾选商品，点击 **【审品比对】** 查看左右两侧数据对齐情况。
   - 点击 **【🚀 立即上品到 Makro】**，系统自动转存图片并提交到 Makro 卖家中心审核！

---

## 三、系统目录说明

```tree
makro上品/
├── backend/                  # FastAPI 后端服务
│   ├── app/
│   │   ├── api/              # 商品、清洗、上品、配置、任务路由
│   │   ├── core/
│   │   ├── models/           # SQLAlchemy 数据模型 (Product, Variant, Task, Setting)
│   │   ├── schemas/          # Pydantic 校验模型
│   │   ├── services/         # 核心业务 (makro_client, ai_cleaner, pricing, takealot)
│   │   └── templates/        # Web 控制台 SPA 界面
│   ├── tests/                # 自动化测试用例
│   └── main.py               # FastAPI 主入口
│
├── extension/                # Chrome 选品与辅助插件 (Manifest V3)
│   ├── manifest.json
│   ├── icons/                # 插件图标
│   ├── popup/                # 插件弹窗面板
│   └── scripts/
│       ├── content_takealot.js # Takealot 采集注入脚本
│       ├── content_makro.js    # Makro 凭据同步注入脚本
│       └── background.js       # 跨域与通信后台脚本
│
├── frontend/                 # 前端独立模块源码
├── start_system.py           # 一键启动脚本
├── 启动系统.bat               # Windows 双击启动入口
├── makro1.har / makro2.har   # 抓包源数据
└── README.md
```
