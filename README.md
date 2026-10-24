# Makro-Takealot 智能搬品与多店铺自动化运营系统 (v2.3.0)

[![System Version](https://img.shields.io/badge/System-v2.3.0-blue.svg)](backend/app/config.py)
[![Extension Version](https://img.shields.io/badge/Extension-v1.1.8-green.svg)](extension/manifest.json)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python)](requirements.txt)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688.svg?logo=fastapi)](backend/requirements.txt)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)](#)

基于南非电商平台 **Takealot** 官方原生 API 采集、**AI（DeepSeek / 通义千问）** 规范化数据清洗与双模态视觉风控、逆向破解的 **Makro（基于 Flipkart SaaS 引擎）** 底层发布协议，并深度集成了 **全生命周期订单与PO拼单履约中台**、**24小时全盘跟价监控与巡航改价**、**智能跟卖 (Piggyback)**、**40×30mm热敏防混打包标签生成**、**多店铺凭据自动保活 (IMAP OTP)** 及 **订单静默同步** 的全流程跨境电商自动化运营系统。

---

## 📑 目录

- [一、核心功能全景图](#一核心功能全景图)
- [二、主要系统模块与特性](#二主要系统模块与特性)
  - [1. 全生命周期订单管理与 PO 批次拼单履约中台 (`store_orders.py`)](#1-全生命周期订单管理与-po-批次拼单履约中台-store_orderspy)
  - [2. 24 小时全盘跟价监控与巡航改价引擎 (`reprice.py` & `store_product_reprice.py`)](#2-24-小时全盘跟价监控与巡航改价引擎-repricepy--store_product_repricepy)
  - [3. 40×30mm 热敏打包防混标签连打系统 (`label_service.py`)](#3-4030mm-热敏打包防混标签连打系统-label_servicepy)
  - [4. 轻量 Chrome 浏览器插件 v1.1.8 (`extension/`)](#4-轻量-chrome-浏览器插件-v118-extension)
  - [5. Takealot 官方原生 API 并发采集 (`takealot_service.py`)](#5-takealot-官方原生-api-并发采集-takealot_servicepy)
  - [6. AI 规范清洗与合规风控流水线 (`ai_cleaner_service.py`)](#6-ai-规范清洗与合规风控流水线-ai_cleaner_servicepy)
  - [7. Makro 逆向协议刊登上架引擎 (`makro_client.py` & `makro.py`)](#7-makro-逆向协议刊登上架引擎-makro_clientpy--makropy)
  - [8. 智能跟卖池与 BuyBox 自动改价巡航 (`auto_reprice_service.py`)](#8-智能跟卖池与-buybox-自动改价巡航-auto_reprice_servicepy)
  - [9. 多店铺矩阵与凭据无感保活 (`auto_login_scheduler.py` & `email_otp_service.py`)](#9-多店铺矩阵与凭据无感保活-auto_login_schedulerpy--email_otp_servicepy)
  - [10. RBAC 权限与员工工作量人效审计 (`management.py`)](#10-rbac-权限与员工工作量人效审计-managementpy)
- [三、系统架构与目录结构](#三系统架构与目录结构)
- [四、快速上手与部署指引](#四快速上手与部署指引)
  - [1. 环境准备与依赖安装](#1-环境准备与依赖安装)
  - [2. 配置环境变量 (`.env`)](#2-配置环境变量-env)
  - [3. 一键启动系统 (自带局域网反向代理)](#3-一键启动系统-自带局域网反向代理)
  - [4. 安装并连接 Chrome 浏览器插件](#4-安装并连接-chrome-浏览器插件)
- [五、维护者交接与参考文档](#五维护者交接与参考文档)

---

## 一、核心功能全景图

```mermaid
flowchart TD
    subgraph S1["1. 选品与多渠道采集"]
        A1[Takealot 商品详情页 / 列表页] -->|Chrome 插件抓取 PLID| B(POST /api/products/collect-by-plid)
        A2[控制台手动输入 PLID / 批量 CSV] --> B
        A3[局域网内网网站 8899 端口采集] -->|插件 v1.1.8 无缝识别| B
        B -->|Takealot 官方 API 并发抓取| C[原生主接口 + 变体 sub_data]
        C --> D[深度解析异构变体: 容量/尺码/颜色/条码/专属图]
        D --> E[(数据库入库: PENDING 待处理)]
    end

    subgraph S2["2. 双 AI 清洗与合规风控"]
        E --> F{选品箱手动/批量操作}
        F -->|AI 规范清洗| G[DeepSeek / Qwen 清洗引擎]
        G --> G1[1. 生成 SEO 规范英文标题]
        G --> G2[2. 16+ 项严苛必填 Catalog 属性提取]
        G --> G3[3. Model Number 去品牌化过滤]
        G --> G4[4. 智能类目 Vertical 自适应映射]
        F -->|多模态双 Vision 质检| H[AI 违禁品与侵权质检]
        H --> H1[知名大牌防侵权: 自动转换第三方兼容格式]
        H --> H2[违禁品拦截: 蓝牙/WiFi/红外/液体安全阻断]
    end

    subgraph S3["3. 审品微调与多店铺发布"]
        G4 --> I[双栏审品比对看板]
        H2 --> I
        I --> J[选择发布店铺 & 调整定价公式]
        J --> K[Makro 底层协议上架引擎]
        K --> K1[1. 创建独立草稿 create_draft]
        K --> K2[2. 专属高清图上传 Makro 官方 CDN]
        K --> K3[3. Qualifier 单位限定符自适应匹配]
        K --> K4[4. 提交 submit_product 平台审核]
    end

    subgraph S4["4. 全盘跟价监控与 BuyBox 巡航"]
        L[全店在售商品 / 跟卖池] -->|24小时全盘巡检| M[(跟价监控大盘)]
        M --> N[动态比价引擎 (Chrome 124 TLS 拟真)]
        N --> O{BuyBox 竞价判定}
        O -->|高于竞品| P[自动下调 1 兰特抢占 BuyBox]
        O -->|触碰保护底价| Q[触发 FLOOR_HIT 保护，坚守底线]
        O -->|已赢车 WINNING| R[保持最优价格优势]
        P --> S[写入改价审计日志 makro_reprice_logs]
    end

    subgraph S5["5. 全生命周期订单履约与 PO 拼单总账"]
        T[30分钟订单定时同步] -->|印度时区UTC+5:30 -> 北京时间UTC+8| U[(订单库: makro_orders)]
        U --> V[7阶履约状态流转机]
        V --> W1[单单称重核算 & 商品库双向同步]
        V --> W2[合并生成 PO 批次 / 空运拼单对账]
        V --> W3[40×30mm 热敏打包防混标签 PDF 批量连打]
        W2 --> W4[PO 展开明细: 主图预览、单品预计回款、一键剔除重算]
    end
```

---

## 二、主要系统模块与特性

### 1. 全生命周期订单管理与 PO 批次拼单履约中台 (`store_orders.py`)
- **7 阶履约状态流转机**：从 `NEW`（新订单）→ `PURCHASED`（已采购）→ `SENT_TO_FORWARDER`（发运货代）→ `AIR_FREIGHT`（货代空运）→ `ARRIVED_SA`（到达南非）→ `SA_PICKUP`（南非取件）→ `COMPLETED`（订单完结），以及 `CANCELLED_RTO`（异常退回）。
- **PO 拼单发货视角与对账总账**：
  - 自由合并订单生成 PO 批次 (`MakroPurchaseOrder`)；
  - 维护外箱规长宽高尺寸（自动计算体积重）与整箱打包实测毛重 (`package_weight_kg`)；
  - 国内发货快递运费总额与物流损耗费按名下有效订单数**自动平摊**；
  - **理论头程 vs 货代账单对账**：自动对比订单累计理论头程与货代实付费用，精确呈现**「头程结余」**或**「头程超支」**；
  - **PO 展开明细增强**：显示商品高清主图（支持点击预览放大）、单单预计回款（异常退回订单回款为 0 元）、**「🗑️ 一键剔除」**（移出当前批次并即刻重算）；
  - **一键全流转防污**：PO 批量流转状态时，自动保持异常退回订单不变。
- **称重核算规范化**：
  - 显式布尔字段 `is_weight_measured`，未录入订单呈现黄底徽章 `⚖️ 未填写`，已录入订单呈现绿底徽章 `⚖️ 已填写: Xg`；
  - 严格支持实测填写为 **0 克**；
  - 录入克重时支持一键同步更新至商品资料库（`MakroListing` 与 `MakroPiggybackItem`），并智能扩散更新名下相同 SKU 的其他订单。
- **时区规整标准化**：从 Makro 官方抓取的印度标准时间（`UTC+5:30`）入库前全部精确计算归正为中国**北京时间（`UTC+8`）**。
- **回款核算解耦防膨胀**：彻底解耦单件售价 `unit_price` 与行小计 `total_price`，订单总额严格以全单为基准，杜绝多件商品由于重复乘以数量导致的虚高计算。
- **出单日期过滤联动**：支持「今日」、「近7天」、「近30天」及自定义日期范围，顶部 7 阶状态徽章数量与财务看板即时动态联动刷新。

### 2. 24 小时全盘跟价监控与巡航改价引擎 (`reprice.py` & `store_product_reprice.py`)
- **全盘跟价看板**：专门针对在售 Listing 构建的全盘调价与 BuyBox 监控视图。
- **24 小时全盘巡检进度条**：
  - 断点续巡与分布式进度看板 (`cruise_progress_banner.html`)；
  - 支持暂停、恢复、终止巡航。
- **批量操作与全盘跨页勾选**：支持每页显示数量切换（20/50/100/200/500/全盘），支持一键全盘勾选批量设置保本底价、切换跟价策略。
- **行内改价防抖与光标保护**：解决输入框刚输入一位数自动跳回原值的痛点，输入失焦即刻平滑更新。
- **多维度筛选**：支持按「我店售价区间」、「跟卖状态（独家/被跟卖）」、「赢车状态（已赢车/丢车需调价/底价坚守/缺底价高危）」过滤。

### 3. 40×30mm 热敏打包防混标签连打系统 (`label_service.py`)
- **ReportLab + PyMuPDF 矢量生成引擎**：单单或批量生成标准 40×30mm 热敏标签 PDF。
- **防混装防错件标识**：条码集成标准 Code128、清晰打印订单号、出单北京时间、商品主标题缩写与件数，便于南非空运货代规范扫码出库。
- **打单状态追踪**：自动记录 `label_printed` 与 `label_printed_at`，操作列提供打单标记与快速重打。

### 4. 轻量 Chrome 浏览器插件 v1.1.8 (`extension/`)
- **全网及局域网内网兼容采集**：全面兼容 `http://127.0.0.1:8899/`、`http://192.168.110.145:8899/` 等内网选品采集站。
- **免鉴权脱敏店铺列表**：插件弹出框直连 `/api/stores`，未登录时自动剥离账号密码与 Cookie，无缝秒级加载所有可用店铺。
- **秒级入库与多线程静默拉取**：插件端仅负责抓取最轻量核心 ID，全量数据由中台多线程并发静默拉取，防爬更安全。

### 5. Takealot 官方原生 API 并发采集 (`takealot_service.py`)
- 直连 Takealot 移动端/Web 原生 REST 接口，绕过 DOM 反爬，毫秒级响应。
- 精准解析异构多变体（容量、包装件数、尺码、颜色、独立条形码 Barcode 及专属图组）。

### 6. AI 规范清洗与合规风控流水线 (`ai_cleaner_service.py`)
- 原生支持 **DeepSeek**（`deepseek-chat`）与 **阿里云通义千问**（`qwen-plus`）。
- **多模态双 Vision 质检**：对商品首图与变体图进行高精度视觉质检，拦截管制违禁品与侵权。
- **Token 极致压缩优化**：结构化提示词压缩 60%+，大幅降低调用成本并提升清洗吞吐率。

### 7. Makro 逆向协议刊登上架引擎 (`makro_client.py` & `makro.py`)
- 完整逆向 Makro 卖家平台草稿创建、CDN 图像转存、Qualifier 单位限定符自适应匹配、提审全流程。

### 8. 智能跟卖池与 BuyBox 自动改价巡航 (`auto_reprice_service.py`)
- 集成 `curl_cffi`，完整模拟现代 Chrome 浏览器的 TLS/JA3/HTTP2 指纹，穿透 PerimeterX 防护拦截。
- 5 并发加随机抖动，支持低于竞品 1 兰特抢车、平价跟卖与保本底价坚守（`FLOOR_HIT`）。

### 9. 多店铺矩阵与凭据无感保活 (`auto_login_scheduler.py` & `email_otp_service.py`)
- 21 小时后台调度器自动连接绑定邮箱（IMAP）接收登录 OTP 验证码，自动刷新 Session Cookie。
- 30 分钟静默拉取官方最新订单、Listing 在售状态。

### 10. RBAC 权限与员工工作量人效审计 (`management.py`)
- 支持 `ADMIN`（超级管理员）与 `OPERATOR`（普通操作员），可分配操作员名下的独立店铺管辖权。
- 状态流转操作审计记录与员工绩效大盘。

---

## 三、系统架构与目录结构

```tree
makro-takealot-uploader/
├── backend/                         # FastAPI 后端核心工程
│   ├── app/
│   │   ├── api/                     # RESTful 接口层
│   │   │   ├── auth.py              # JWT 认证与登录登出
│   │   │   ├── cleaner.py           # AI 规范化清洗与合规检测接口
│   │   │   ├── makro.py             # Makro 协议刊登与草稿处理接口
│   │   │   ├── piggyback.py         # 智能跟卖池增删改查与批量操作
│   │   │   ├── reprice.py           # 全盘跟价监控与巡航改价接口
│   │   │   ├── products.py          # 选品库管理与 CSV 采集
│   │   │   ├── stores.py            # 多店铺配置与凭据绑定
│   │   │   ├── store_orders.py      # 订单履约中台、PO拼单对账、称重与打单
│   │   │   ├── store_products.py    # 在售 Listing 状态管理
│   │   │   ├── users.py             # RBAC 用户与员工管理
│   │   │   └── management.py        # 人效统计大盘与系统审计
│   │   ├── core/                    # 数据库迁移补丁与自愈巡检器
│   │   ├── models/                  # SQLAlchemy 数据库实体模型 (12 张核心表)
│   │   │   ├── makro_order.py             # 订单全生命周期履约模型
│   │   │   ├── makro_purchase_order.py    # PO 批次拼单对账总账模型
│   │   │   ├── makro_order_status_log.py  # 订单流转审计记录模型
│   │   │   ├── store_product_reprice.py   # 全盘跟价监控数据模型
│   │   │   ├── makro_listing.py           # Makro 官方在售商品快照
│   │   │   ├── makro_piggyback_item.py    # 智能跟卖池实体
│   │   │   ├── product.py                 # Takealot 采集主表
│   │   │   └── store.py                   # 店铺矩阵与凭据配置
│   │   ├── schemas/                 # Pydantic 输入输出校验模型
│   │   ├── services/                # 业务逻辑与算法引擎
│   │   │   ├── label_service.py           # 40×30mm 热敏打包标签 PDF 连打服务
│   │   │   ├── unified_pricing_service.py # 统一成本利润核算引擎
│   │   │   ├── ai_cleaner_service.py      # AI 清洗与 16+ 项属性推导
│   │   │   ├── compliance_service.py      # 双模态品牌侵权与违禁品合规检测
│   │   │   ├── makro_client.py            # Makro 底层 HTTP 通信协议封装
│   │   │   ├── makro_piggyback_service.py # 跟卖价格核算与上架服务
│   │   │   ├── auto_reprice_service.py    # BuyBox 巡航比价与自动推价
│   │   │   ├── auto_reprice_scheduler.py  # 24小时全盘跟价巡航调度器
│   │   │   ├── makro_scraper_service.py   # curl_cffi Chrome 124 拟真反爬采集器
│   │   │   ├── auto_login_scheduler.py    # 21小时店铺凭据自动登录保活调度器
│   │   │   ├── email_otp_service.py       # IMAP 邮箱 OTP 验证码自动提取器
│   │   │   ├── order_sync_scheduler.py    # 30分钟订单/Listing 定时同步
│   │   │   ├── takealot_service.py        # Takealot 原生 API 变体采集引擎
│   │   │   └── vertical_service.py        # 类目自适应映射与 Qualifier 适配
│   │   ├── static/                  # 静态资源与打包扩展
│   │   │   ├── makro-extension.zip        # 浏览器插件最新编译打包包
│   │   │   └── js/modules/                # 前端模块化业务逻辑 (orders, reprice, etc.)
│   │   ├── templates/               # 前端单页应用 (Vue 3 + Tailwind CSS)
│   │   │   ├── views/                     # 核心业务视图 (tab_orders, tab_reprice, etc.)
│   │   │   └── components/                # 业务组件 (巡航进度条、弹窗、横幅)
│   │   ├── utils/                   # 认证、日期与时区规整工具集
│   │   ├── config.py                # Pydantic 全局配置管理
│   │   ├── database.py              # 数据库连接池与会话管理
│   │   └── init_db.py               # 数据库初始化与迁移补丁脚本
│   ├── tests/                       # 全量自动化测试套件
│   ├── tools/                       # 实用运维脚本集 (clean_non_active_reprice, etc.)
│   ├── requirements.txt             # 后端 Python 依赖清册
│   └── main.py                      # FastAPI 启动主入口
│
├── extension/                       # Chrome 辅助扩展程序 (Manifest V3 - v1.1.8)
│   ├── manifest.json                # 扩展程序清单
│   ├── popup/                       # 扩展弹窗控制台
│   └── scripts/                     # 注入内容脚本 (Takealot 采集、Makro 凭据同步、跟卖浮窗)
│
├── tools/                           # 辅助计算器与迁移工具
├── docs/                            # 业务文档与技术交接指南 (全套核心手册)
│   ├── 运维与排障速查手册.md          # 5分钟极速上手部署、日常高频运维与故障急救手册 (必读)
│   ├── 项目维护与交接指南.md          # 详细系统维护手册、表结构、接口对照与排错 FAQ
│   ├── 数据库模型与核心业务字典.md    # 全表 DDL、字段定义与核心业务状态机字典 (新维护者必读)
│   ├── 1688采购与全链路成本定价核算手册.md
│   └── MAKRO选品与防侵权合规实操手册.md
│
├── .env.example                     # 环境变量配置文件模板
├── requirements.txt                 # 项目根目录依赖清单
├── start_system.py                  # 双端口与局域网反代智能拉起脚本
├── 启动系统.bat                      # Windows 双击一键启动脚本
└── README.md                        # 本系统总体说明文档
```

---

## 四、快速上手与部署指引

### 1. 环境准备与依赖安装

系统要求：**Python 3.9 及以上**（Windows / macOS / Linux 均支持）。

在项目根目录下安装依赖：
```bash
pip install -r requirements.txt
```

> **特别提示**：若需要使用动态改价巡航的 PerimeterX 绕过特性，确保 `curl_cffi` 正确安装；若使用 40×30mm 热敏标签连打，确保 `pymupdf` 和 `python-barcode` 正确安装（均已包含在 `requirements.txt` 中）。

### 2. 配置环境变量 (`.env`)

复制环境配置模板：
```bash
copy .env.example .env
```
根据需求修改 `.env` 中的 AI API Key（推荐使用 DeepSeek，成本极低且效果优异）：
```ini
AI_PROVIDER="deepseek"
DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
```

### 3. 一键启动系统 (自带局域网反向代理)

在 Windows 环境下直接双击 **`启动系统.bat`**，或在命令行执行：
```bash
python start_system.py
```

启动脚本特性：
- **本机访问**：自动打开 `http://localhost:8001` 或 `http://localhost`。
- **免端口局域网反向代理**：自动检测本机 80 端口，开启反代服务。同局域网同事可直接通过 `http://<服务器IP>/` 访问系统，无需记忆端口。
- **API 交互式文档 (Swagger UI)**：`http://localhost:8001/api/docs`。
- **进程自愈保活**：主后端如果遇到非人为崩溃，启动器会在 3 秒内自动拉起重启。

### 4. 安装并连接 Chrome 浏览器插件

1. 打开 Chrome 浏览器，访问 `chrome://extensions/`；
2. 开启右上角 **【开发者模式】**；
3. 点击 **【加载已解压的扩展程序】**，选择项目根目录下的 **`extension`** 文件夹；
4. 插件安装完成后：
   - 本机使用：默认连接 `http://localhost:8001`，状态显示绿色「服务在线」；
   - 局域网其他电脑使用：在插件配置面板选择【局域网反代 (:80)】预设或输入中台服务器 IP 即可；
   - 目标店铺列表下拉框自动秒级加载，选择要绑定的目标店铺即可一键采集。

---

## 五、维护者交接与参考文档

交接给新维护人员时，请重点查阅 `docs/` 目录下的核心技术手册：

- 📕 **[运维与排障速查手册 (急救必读)](file:///docs/%E8%BF%90%E7%BB%B4%E4%B8%8E%E6%8E%92%E9%9A%9C%E9%80%9F%E6%9F%A5%E6%89%8B%E5%86%8C.md)**：3步极速部署、7大KPI实操、订单履约应急、打单排障、三轨重试防踩坑、官方动态代理池、白屏自愈、数据库备份恢复。
- 📘 **[项目维护与交接指南 (系统全貌)](file:///docs/%E9%A1%B9%E7%9B%AE%E7%BB%B4%E6%8A%A4%E4%B8%8E%E4%BA%A4%E6%8E%A5%E6%8C%87%E5%8D%97.md)**：包含全生命周期订单履约流程、PO拼单对账模型、防止重复乘数量回款算法、时区标准化规范、接口调用链、逆向发包协议解析。
- 📗 **[数据库模型与核心业务字典 (架构必读)](file:///docs/%E6%95%B0%E6%8D%AE%E5%BA%93%E6%A8%A1%E5%9E%8B%E4%B8%8E%E6%A0%B8%E5%BF%83%E4%B8%9A%E5%8A%A1%E5%AD%97%E5%85%B8.md)**：全库 12 张核心表完整 DDL 与字段释义、7 阶履约状态机、PO 状态机与改价状态机枚举对照图。
- 📙 **[1688采购与全链路成本定价核算手册](file:///docs/1688%E9%87%87%E8%B4%AD%E4%B8%8E%E5%85%A8%E9%93%BE%E8%B7%AF%E6%88%90%E6%9C%AC%E5%AE%9A%E4%BB%B7%E6%A0%B8%E7%AE%97%E6%89%8B%E5%86%8C.md)**：跨境运费、关税与汇率测算模型。
- 📓 **[MAKRO选品与防侵权合规实操手册](file:///docs/MAKRO%E9%80%89%E5%93%81%E4%88%8E%E9%98%B2%E4%BE%B5%E6%9D%83%E5%90%88%E8%A7%84%E5%AE%9E%E6%93%8D%E6%89%8B%E5%86%8C.md)**：选品避坑与品牌侵权识别规范。
