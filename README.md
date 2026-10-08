# Makro-Takealot 智能搬品与多店铺自动化运营系统 (v2.1.3)

[![System Version](https://img.shields.io/badge/System-v2.1.3-blue.svg)](backend/app/config.py)
[![Extension Version](https://img.shields.io/badge/Extension-v1.1.3-green.svg)](extension/manifest.json)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python)](requirements.txt)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688.svg?logo=fastapi)](backend/requirements.txt)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)](#)

基于南非电商平台 **Takealot** 官方原生 API 采集、**AI（DeepSeek / 通义千问）** 规范化数据清洗与合规风控、逆向破解的 **Makro（基于 Flipkart SaaS 引擎）** 底层发布协议，并深度集成了 **智能跟卖 (Piggyback)**、**BuyBox 动态改价巡航**、**多店铺凭据自动保活 (IMAP OTP)** 及 **订单静默同步** 的全流程跨境电商自动化运营系统。

---

## 📑 目录

- [一、核心功能全景图](#一核心功能全景图)
- [二、主要系统模块与特性](#二主要系统模块与特性)
  - [1. 轻量 Chrome 采集与同步插件 (`extension/`)](#1-轻量-chrome-采集与同步插件-extension)
  - [2. Takealot 官方原生 API 并发采集 (`takealot_service.py`)](#2-takealot-官方原生-api-并发采集-takealot_servicepy)
  - [3. AI 规范清洗与合规风控流水线 (`ai_cleaner_service.py`)](#3-ai-规范清洗与合规风控流水线-ai_cleaner_servicepy)
  - [4. Makro 逆向协议刊登上架引擎 (`makro_client.py` & `makro.py`)](#4-makro-逆向协议刊登上架引擎-makro_clientpy--makropy)
  - [5. 智能跟卖池与 BuyBox 自动改价巡航 (`auto_reprice_service.py`)](#5-智能跟卖池与-buybox-自动改价巡航-auto_reprice_servicepy)
  - [6. 多店铺矩阵与凭据无感保活 (`auto_login_scheduler.py` & `email_otp_service.py`)](#6-多店铺矩阵与凭据无感保活-auto_login_schedulerpy--email_otp_servicepy)
  - [7. RBAC 权限与员工工作量人效审计 (`management.py`)](#7-rbac-权限与员工工作量人效审计-managementpy)
- [三、系统架构与目录结构](#三系统架构与目录结构)
- [四、快速上手与部署指引](#四快速上手与部署指引)
  - [1. 环境准备与依赖安装](#1-环境准备与依赖安装)
  - [2. 配置环境变量 (`.env`)](#2-配置环境变量-env)
  - [3. 一键启动系统 (自带局域网反向代理)](#3-一键启动系统-自带局域网反向代理)
  - [4. 安装并连接 Chrome 浏览器插件](#4-安装并连接-chrome-浏览器插件)
- [五、版本迭代与打包维护工具](#五版本迭代与打包维护工具)
- [六、维护者交接与参考文档](#六维护者交接与参考文档)

---

## 一、核心功能全景图

```mermaid
flowchart TD
    subgraph S1["1. 选品与多渠道采集"]
        A1[Takealot 商品详情页 / 列表页] -->|Chrome 插件抓取 PLID| B(POST /api/products/collect-by-plid)
        A2[控制台手动输入 PLID / 批量 CSV] --> B
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
        F -->|侵权与合规检测| H[AI 违禁品与侵权质检]
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

    subgraph S4["4. 智能跟卖与 BuyBox 巡航改价"]
        L[Makro 前台 / 店铺现有商品] -->|一键跟卖采集| M[(跟卖池: makro_piggyback_items)]
        M --> N[自动改价巡航引擎 (Chrome 124 TLS 拟真)]
        N --> O{BuyBox 竞价判定}
        O -->|高于竞品| P[自动下调 1 兰特抢占 BuyBox]
        O -->|触碰保护底价| Q[触发 FLOOR_HIT 保护，终止降价]
        O -->|已赢车 WINNING| R[保持当前最优价格优势]
        P --> S[写入改价审计日志 makro_reprice_logs]
    end

    subgraph S5["5. 自动化运维与后台守护"]
        T[21小时自动登录保活] -->|IMAP 收取邮箱 OTP 验证码| U[刷新店铺 Cookie & CSRF]
        V[30分钟订单同步引擎] -->|静默同步| W[拉取买家订单与履约状态]
    end
```

---

## 二、主要系统模块与特性

### 1. 轻量 Chrome 采集与同步插件 (`extension/`)
- **Manifest V3 极简架构**：仅保留轻量 DOM 监听与网络委托，彻底摒弃前端重逻辑与反爬风险。
- **PLID 自动提取**：在 Takealot 商品详情页和列表页自动提取 `PLID`，实时调用后端接口完成采集。
- **Makro 凭据一键上报**：在 `seller.makro.co.za` 页面一键捕获当前登录 Session Cookie、`fk-csrf-token` 与 `sellerId`，无缝同步至系统后台指定店铺。
- **前台跟卖抓取浮窗**：在 Makro 电脑前台商品页注入浮窗，一键抓取竞品 Seller、售价、MRP 与 ItemId 入库跟卖池。

### 2. Takealot 官方原生 API 并发采集 (`takealot_service.py`)
- **绕过 DOM 反爬**：直连 Takealot 移动端/Web 原生 REST 接口，毫秒级响应。
- **异构多变体无损解析**：精准提取容量（1TB/2TB/512GB）、包装件数（60Pack/120Pack）、尺码、颜色、独立条形码（Barcode）及各变体专属高清图组，杜绝默认属性退化。
- **CSV 批量导入**：支持批量上传包含 URL 或 PLID 的表格，后台并发自动化采集。

### 3. AI 规范清洗与合规风控流水线 (`ai_cleaner_service.py`)
- **解耦式处理**：采集与清洗完全解耦，支持用户批量勾选按需清洗。
- **双大模型无缝切换**：原生支持 **DeepSeek**（`deepseek-chat`）与 **阿里云通义千问**（`qwen-plus` / `qwen-vl-plus` 图文多模态）。
- **品牌防侵权重塑**：知名大牌（Apple、Samsung、Dyson 等）保护配件自动重塑为符合电商平台规范的第三方兼容词（`Third-Party ... Compatible with ...`）。
- **Model Number 严格校验**：自动从型号中清洗剥离品牌名，满足 Makro CMS 的强校验规则。
- **类目与单位自适应**：智能将 Takealot 分类映射到 Makro 的 400+ 个底层 Vertical，并为属性自动匹配 `allowedValues` 与 `qualifier`（单位限定符）。

### 4. Makro 逆向协议刊登上架引擎 (`makro_client.py` & `makro.py`)
- **Flipkart SaaS 协议层破解**：完整逆向 Makro 卖家平台草稿创建、CDN 图像转存、属性映射提交全流程。
- **全量多变体矩阵刊登**：支持父商品下一键批量发布所有变体，或针对特定失败变体单独发布与重试。
- **多店铺隔离刊登**：支持任意商品自由指定发布到已配置的任一 Makro 店铺。

### 5. 智能跟卖池与 BuyBox 自动改价巡航 (`auto_reprice_service.py`)
- **Chrome 124 TLS 指纹拟真**：集成 `curl_cffi`，完整模拟现代 Chrome 浏览器的 TLS/JA3/HTTP2 指纹，绕过 PerimeterX 防护拦截。
- **5 并发 + 随机抖动保护**：改价巡航采用 5 线程并发加 0.3~0.8 秒随机请求抖动，平滑请求波峰，杜绝触发风控 403。
- **智能改价策略**：
  - `MINUS_1`（默认）：低于当前 BuyBox 最低竞品 1 兰特抢车；
  - `MATCH`：平价跟卖；
  - `PERCENT_OFF`：比竞品低固定百分比。
- **底价保护机制 (Floor Price)**：设定最低保底价格，一旦竞品击穿底价自动触发 `FLOOR_HIT`，保护利润不被恶意价格战消耗。
- **完整改价审计日志**：记录每一次竞品报价、改价前后差额及改价原因。

### 6. 多店铺矩阵与凭据无感保活 (`auto_login_scheduler.py` & `email_otp_service.py`)
- **21 小时周期保活**：Makro 卖家 Session 有效期约为 24 小时，后台调度器默认每 21 小时自动触发保活刷新。
- **IMAP 邮箱验证码自动提取**：支持主流海外及企业邮箱（Gmail、Outlook、163、QQ、自定义 IMAP），自动连接邮箱接收并正则提取 6 位登录 OTP 验证码，实现真正的无人值守多店铺自动化。
- **30 分钟订单与在售商品静默同步**：自动拉取各店铺的新增订单、履约状态及在售 Listing 状态。

### 7. RBAC 权限与员工工作量人效审计 (`management.py`)
- **角色权限控制**：支持 `ADMIN`（超级管理员）与 `OPERATOR`（普通操作员），可分配操作员名下的独立店铺管辖权。
- **员工绩效人效大盘**：详细记录每位操作员的选品量、AI清洗量、成功刊登量与操作日志。

---

## 三、系统架构与目录结构

```tree
makro-takealot-uploader/
├── backend/                         # FastAPI 后端核心工程
│   ├── app/
│   │   ├── api/                     # RESTful 接口层 (商品、清洗、刊登、跟卖、改价、店铺、用户等)
│   │   │   ├── auth.py              # JWT 认证与登录登出
│   │   │   ├── cleaner.py           # AI 规范化清洗与合规检测接口
│   │   │   ├── makro.py             # Makro 协议刊登与草稿处理接口
│   │   │   ├── piggyback.py         # 智能跟卖池增删改查与批量操作
│   │   │   ├── reprice.py           # BuyBox 巡航改价与改价日志接口
│   │   │   ├── products.py          # 选品库管理与 CSV 采集
│   │   │   ├── stores.py            # 多店铺配置与凭据绑定
│   │   │   ├── store_orders.py      # 店铺订单同步与履约接口
│   │   │   ├── users.py             # RBAC 用户与员工管理
│   │   │   └── management.py        # 人效统计大盘与系统审计
│   │   ├── core/                    # 核心配置与工具
│   │   ├── models/                  # SQLAlchemy 数据库实体模型 (11 张核心表)
│   │   ├── schemas/                 # Pydantic 输入输出校验模型
│   │   ├── services/                # 业务逻辑与算法引擎
│   │   │   ├── ai_cleaner_service.py      # AI 清洗与 16+ 项属性推导
│   │   │   ├── compliance_service.py      # 侵权品牌与违禁品合规检测
│   │   │   ├── makro_client.py            # Makro 底层 HTTP 通信协议封装
│   │   │   ├── makro_piggyback_service.py # 跟卖价格核算与上架服务
│   │   │   ├── auto_reprice_service.py    # BuyBox 巡航比价与自动推价
│   │   │   ├── makro_scraper_service.py   # curl_cffi Chrome 124 拟真反爬采集器
│   │   │   ├── auto_login_scheduler.py    # 21小时店铺凭据自动登录保活调度器
│   │   │   ├── email_otp_service.py       # IMAP 邮箱 OTP 验证码自动提取器
│   │   │   ├── order_sync_scheduler.py    # 30分钟订单/Listing 定时同步
│   │   │   ├── takealot_service.py        # Takealot 原生 API 变体采集引擎
│   │   │   └── vertical_service.py        # 类目自适应映射与 Qualifier 适配
│   │   ├── static/                  # 静态资源与打包扩展 (.zip)
│   │   ├── templates/               # 前端单页应用 (Vue 3 + Tailwind CSS)
│   │   ├── config.py                # Pydantic 全局配置管理
│   │   ├── database.py              # 数据库连接池与会话管理
│   │   └── init_db.py               # 数据库初始化与迁移补丁脚本
│   ├── tests/                       # 单元测试与集成测试脚本集
│   ├── tools/                       # 实用运维脚本 (bump_version.py, backfill.py)
│   ├── requirements.txt             # 后端 Python 依赖清册
│   └── main.py                      # FastAPI 启动主入口
│
├── extension/                       # Chrome 辅助扩展程序 (Manifest V3)
│   ├── manifest.json                # 扩展程序清单
│   ├── popup/                       # 扩展弹窗控制台
│   ├── scripts/                     # 注入内容脚本 (Takealot 采集、Makro 凭据同步、跟卖浮窗)
│   └── styles/                      # 注入样式文件
│
├── tools/                           # 辅助工具包
│   └── makro_pricing_calculator.html# 1688 采购与全链路成本核算计算器
├── docs/                            # 业务文档与交付指南
│   ├── 项目维护与交接指南.md          # 详细系统维护手册、表结构、接口对照与排错 FAQ
│   ├── 1688采购与全链路成本定价核算手册.md
│   └── MAKRO选品与防侵权合规实操手册.md
│
├── .env.example                     # 环境变量配置文件模板
├── requirements.txt                 # 项目根目录依赖清单
├── start_system.py                  # 双端口与局域网反代智能拉起脚本
├── 启动系统.bat                      # Windows 双击一键启动脚本
├── extension.zip                    # 浏览器插件最新编译打包文件
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

> **特别提示**：若需要使用动态改价巡航的 PerimeterX 绕过特性，确保 `curl_cffi` 正确安装（已包含在 `requirements.txt` 中）。

### 2. 配置环境变量 (`.env`)

复制环境配置模板：
```bash
cp .env.example .env
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
   - 局域网其他电脑使用：在插件配置面板选择【局域网反代 (:80)】预设或输入中台服务器 IP 即可。

---

## 五、版本迭代与打包维护工具

项目配备了自动化版本同步与插件打包工具：[`backend/tools/bump_version.py`](file:///backend/tools/bump_version.py)。

```bash
# 同时自增系统与插件补丁小版本号并自动重新打包 extension.zip
python backend/tools/bump_version.py --patch-all

# 或显式指定版本号
python backend/tools/bump_version.py --sys 2.1.4 --ext 1.1.4
```

该工具会自动更新：
- `backend/app/config.py` 中的版本号
- `extension/manifest.json` 与 `extension/popup/popup.html`
- `backend/app/templates/index.html` 中的前台版本徽标与下载链接
- `frontend/package.json` 中的工程版本号
- 自动生成最新的 `extension.zip` 与 `backend/app/static/makro-extension.zip`

---

## 六、维护者交接与参考文档

交接给新维护人员时，请重点查阅以下文档：

- 📘 **[项目维护与交接指南 (必读)](file:///docs/%E9%A1%B9%E7%9B%AE%E7%BB%B4%E6%8A%A4%E4%B8%8E%E4%BA%A4%E6%8E%A5%E6%8C%87%E5%8D%97.md)**：包含全数据表字段释义、接口调用链、逆向发包协议解析、PerimeterX 应对方案、常见报错排查 FAQ。
- 📗 **[MAKRO选品与防侵权合规实操手册](file:///docs/MAKRO%E9%80%89%E5%93%81%E4%88%8E%E9%98%B2%E4%BE%B5%E6%9D%83%E5%90%88%E8%A7%84%E5%AE%9E%E6%93%8D%E6%89%8B%E5%86%8C.md)**：选品避坑与品牌侵权识别规范。
- 📙 **[1688采购与全链路成本定价核算手册](file:///docs/1688%E9%87%87%E8%B4%AD%E4%B8%8E%E5%85%A8%E9%93%BE%E8%B7%AF%E6%88%90%E6%9C%AC%E5%AE%9A%E4%BB%B7%E6%A0%B8%E7%AE%97%E6%89%8B%E5%86%8C.md)**：跨境运费、关税与汇率测算模型。
