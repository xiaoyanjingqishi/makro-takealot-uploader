/**
 * Makro 搬品系统 - 前端应用主入口 (ES Modules)
 * 聚合所有业务域模块、装配 Vue 3 实例并挂载
 */

const { createApp } = Vue;

// 导入各业务域方法集
import { coreMethods } from './modules/core.js';
import { productsMethods } from './modules/products.js';
import { piggybackMethods } from './modules/piggyback.js';
import { makro_publisherMethods } from './modules/makro_publisher.js';
import { storesMethods } from './modules/stores.js';
import { store_productsMethods } from './modules/store_products.js';
import { store_auditsMethods } from './modules/store_audits.js';
import { ordersMethods } from './modules/orders.js';
import { managementMethods } from './modules/management.js';
import { tasksMethods } from './modules/tasks.js';
import { settingsMethods } from './modules/settings.js';
import { usersMethods } from './modules/users.js';
import { inspectorMethods } from './modules/inspector.js';

// 初始化 Vue 应用实例
const app = createApp({
  data() {
        return {
          currentTab: 'products',
          // Makro 智能跟品管理状态
          piggyback: {
            total: 0,
            page: 1,
            pageSize: 20,
            status: 'ALL',
            stage: 'ALL',
            buybox_status: 'ALL',
            compliance_status: 'ALL',
            store_id: null,
            user_id: null,
            search: '',
            stats: {
              total_all: 0,
              pending_count: 0,
              active_count: 0,
              failed_count: 0,
              pending_check_count: 0,
              safe_count: 0,
              risk_count: 0,
              prohibited_count: 0
            },
            kpi: {
              total_count: 0,
              active_count: 0,
              winning_count: 0,
              losing_count: 0,
              floor_hit_count: 0,
              missing_floor_count: 0,
              staging_count: 0,
              blocked_count: 0,
              abandoned_count: 0
            },
            items: [],
            selectedIds: [],
            selectAll: false,
            isAllFilteredSelected: false,
            autoCompliance: localStorage.getItem('makro_piggyback_auto_compliance') === 'true'
          },
          // 管理驾驶舱状态 (团队人效与员工工作量看板)
          management: {
            dateRange: 'today', // 'today', 'yesterday', '7d', '30d', 'custom'
            customStart: '',
            customEnd: '',
            filterUserId: null,
            filterStoreId: null,
            sortBy: 'total_actions',
            loading: false,
            overview: null,
            leaderboard: [],
            hourly: { hours: [], counts: [], peak_hour: '--', total_actions: 0, max_hourly_count: 0 },
            logs: { items: [], total: 0, page: 1, pageSize: 15, taskType: '', status: '', keyword: '', loading: false },
            selectedLog: null,
            showLogModal: false
          },
          // 员工个人今日战报小组件
          myDailySummary: {
            today_sourcing: 0,
            today_cleaning: 0,
            today_publishing: 0,
            today_piggyback: 0,
            today_actions: 0,
            team_rank: 1,
            total_operators: 1,
            encouragement: '新的一天开始了，加油冲榜！'
          },
          showMyDailySummaryModal: false,
          inlineEditing: {
            id: null,
            field: null,
            tempValue: null,
            saving: false
          },
          showBatchFloorModal: false,
          batchFloorForm: {
            mode: 'PERCENT',
            value: 75,
            auto_enable_reprice: true
          },
          submittingBatchFloor: false,
          showBatchPublishPiggybackModal: false,
          batchPublishPiggybackTargetStoreId: null,
          batchPublishPiggybackStoreMode: 'target', // 'target' | 'original'
          isBatchPublishingPiggyback: false,
          showBatchSetStoreModal: false,
          batchSetStoreTargetId: null,
          isBatchSettingStore: false,
          loadingPiggyback: false,
          showCollectPiggybackModal: false,
          collectPiggybackForm: {
            url_or_fsn: '',
            store_id: null,
            price_strategy: 'MINUS_15',
            min_price_floor: 0.0
          },
          collectingPiggyback: false,
          showBatchCollectPiggybackModal: false,
          batchCollectPiggybackForm: {
            raw_text: '',
            store_id: null,
            price_strategy: 'MINUS_15',
            min_price_floor: 0.0
          },
          batchCollectingPiggyback: false,
          showBatchPricingPiggybackModal: false,
          batchPricingForm: {
            price_strategy: 'MINUS_15',
            min_price_floor: null,
            custom_delta: 0.0,
            sync_to_makro: true
          },
          activeStrategyDropdownId: null,
          strategyQuickOptions: [
            { value: 'MINUS_15', label: '🔥 比竞对低 R15.00 (推荐抢流)', short: '-R15.00' },
            { value: 'MINUS_1', label: '⚡ 比竞对低 R1.00 (微降夺车)', short: '-R1.00' },
            { value: 'MINUS_0.5', label: '⚡ 比竞对低 R0.50 (微降保利)', short: '-R0.50' },
            { value: 'MINUS_2', label: '🚀 比竞对低 R2.00 (强力夺车)', short: '-R2.00' },
            { value: 'PERCENT_2', label: '📊 比竞对低 2% (按比例让利)', short: '-2%' },
            { value: 'PERCENT_5', label: '📊 比竞对低 5% (按比例让利)', short: '-5%' },
            { value: 'MANUAL', label: '🤝 平价跟卖 (与竞对持平)', short: '平价' },
          ],
          applyingBatchPricing: false,
          showEditPiggybackModal: false,
          editingPiggybackItem: null,
          showComplianceDetailModal: false,
          currentComplianceItem: null,
          piggybackArbitrationNotes: '',
          isArbitratingPiggyback: false,
          showRepriceLogsModal: false,
          loadingRepriceLogs: false,
          repriceLogs: {
            items: [],
            total: 0,
            page: 1,
            pageSize: 15,
            actionFilter: ''
          },
          runningBatchReprice: false,
          runningFullCruise: false,
          translatingTitles: false,
          verticalZhMap: {
            "cases_covers": "手机保护套 / 保护壳",
            "massager": "个人护理按摩器 / 按摩仪",
            "health_beauty_massager": "美体个护按摩器",
            "card_holder": "卡包 / 名片夹",
            "support": "防护支架 / 医用护具",
            "complete_vest": "背心 / 战术防护背心",
            "headphone": "耳机 / 头戴式耳麦",
            "mould": "模具 / 烘焙模具",
            "plier": "钳子 / 五金工具钳",
            "kitchen_rack": "厨房置物架 / 收纳架",
            "tool_kit": "五金工具套装",
            "brush_applicator": "刷子 / 涂抹刷",
            "pet_collar_harness": "宠物项圈 / 胸背带",
            "vehicle_pipe_hose_components": "汽车软管与管道配件",
            "protective_glasses": "护目镜 / 防护眼镜",
            "vehicle_light_bulb": "汽车车灯灯泡",
            "costume_wear": "演出服 / 角色扮演服饰",
            "data_cable": "数据线 / 充电线",
            "shower_grab_bar": "浴室安全扶手",
            "skin_treatment": "护肤用品 / 皮肤护理",
            "watch": "手表 / 腕表",
            "wrench_set": "扳手套装",
            "cap": "帽子 / 鸭舌帽",
            "drill_bit_set": "钻头套装",
            "soap_case": "皂盒 / 肥皂架",
            "learning_toy": "早教益智玩具",
            "bath_towel": "浴巾 / 毛巾",
            "bed": "床 / 卧具",
            "bed_mattress": "床垫",
            "side_table": "床头柜 / 边几",
            "wardrobe_closet": "衣柜 / 组合衣橱",
            "headboard": "床头板",
            "kid_seating": "儿童座椅",
            "kid_table": "儿童学习桌",
            "dining_chair": "餐椅",
            "kitchen_cabinet": "橱柜 / 厨房储物柜",
            "kitchen_trolley": "厨房小推车",
            "dining_table": "餐桌",
            "dining_set": "餐桌椅套装",
            "furniture_accessories": "家具五金配件",
            "outdoor_chair": "户外休闲椅",
            "outdoor_table": "户外桌",
            "sofa_sectional": "沙发 / 组合沙发",
            "coffee_table": "咖啡茶几",
            "book_shelf": "书架 / 置物架",
            "computer_table": "电脑桌 / 书桌",
            "pillow": "枕头 / 靠垫",
            "blanket_quilt": "毛毯 / 被子",
            "curtain": "窗帘 / 遮阳帘",
            "carpet_rug": "地毯 / 地垫",
            "wall_clock": "挂钟 / 钟表",
            "water_bottle": "运动水壶 / 水杯",
            "lunch_box": "便当盒 / 饭盒",
            "cookware_set": "锅具套装",
            "knife_sharpener": "磨刀器",
            "vacuum_cleaner_filter": "吸尘器滤网 / 耗材配件",
            "mobile_holder": "手机支架 / 车载支架",
            "screen_guard": "屏幕保护膜 / 钢化膜",
            "stylus": "手写笔 / 触控笔",
            "usb_flash_drive": "U盘 / 移动闪存盘",
            "smart_watch_strap": "智能手表表带",
            "backpack": "双肩背包 / 电脑包",
            "handbag": "手提包 / 单肩包",
            "wallet": "男士钱包 / 钱夹",
            "belt": "皮带 / 腰带",
            "sunglasses": "太阳眼镜 / 墨镜",
            "stethoscope": "听诊器 / 医疗听诊配件",
            "stethoscope_accessory": "听诊器替换导管与配件",
            "blood_pressure_monitor": "血压计",
            "thermometer": "体温计 / 测温仪",
            "pulse_oximeter": "指夹式血氧仪",
            "nebulizer": "雾化器 / 雾化吸入仪",
            "first_aid_kit": "急救包 / 应急医疗箱",
            "dental_care": "口腔护理 / 牙齿模型",
            "hair_dryer": "电吹风 / 吹风机",
            "hair_straightener": "直发器 / 卷发棒",
            "shaver_trimmer": "剃须刀 / 理发推剪",
            "nail_care": "美甲工具 / 美甲仪",
            "garden_sprayer": "园艺喷枪 / 洒水器",
            "garden_hose": "园艺水管 / 伸缩水管",
            "plant_pot": "花盆 / 园艺花架",
            "smart_switch_plug": "智能插座 / 智能开关",
            "led_bulb": "LED 灯泡",
            "table_lamp": "台灯 / 护眼灯",
            "strip_light": "LED 氛围灯带",
            "cctv_camera": "安防监控摄像头",
            "mouse": "鼠标",
            "keyboard": "键盘",
            "laptop_stand": "笔记本电脑支架",
            "power_bank": "移动电源 / 充电宝",
            "wireless_charger": "无线充电器",
            "adhesive": "工业强力胶水 / 双面胶带",
            "cleaning_glove": "清洁手套",
            "mop_bucket": "拖把桶套装",
            "trash_can": "垃圾桶",
            "storage_box": "收纳箱 / 整理盒",
            "shoe_rack": "鞋架 / 鞋柜",
            "hanger": "衣架 / 晒衣夹"
          },
          sidebarCollapsed: (function() {
            try { return localStorage.getItem('makro_sidebar_collapsed') === 'true'; } catch (_) { return false; }
          })(),
          showFullTasksBanner: false,
          highlightedProductId: null,
          lastInspectedId: null,
          networkInfo: null,
          copiedLan: false,
          viewMode: (function() {
            try { return localStorage.getItem('makro_view_mode') || 'flat'; } catch (_) { return 'flat'; }
          })(),
          expandedGroups: {},
          previewImageUrl: null,
          previewLoading: false,
          previewImageFailed: false,
          placeholderImg: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='80' height='80' viewBox='0 0 80 80'><rect width='100%' height='100%' fill='%23f1f5f9'/><text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' fill='%2394a3b8' font-size='12' font-family='sans-serif'>暂无图片</text></svg>",
          toasts: [],
          confirmDialog: {
            show: false,
            title: '',
            message: '',
            confirmText: '确定',
            cancelText: '取消',
            type: 'primary',
            action: null
          },
          activeBgTasks: [],
          bgTasksPollTimer: null,
          logFilterType: '',
          logFilterStatus: '',
          logSearchInput: '',
          showLogDetailModal: false,
          activeLogDetail: null,
          currentUser: null,
          authToken: (function() { try { return localStorage.getItem('makro_auth_token') || ''; } catch(_) { return ''; } })(),
          authorizedStores: [],
          selectedStoreId: (function() { try { const v = localStorage.getItem('makro_selected_store_id'); return v ? Number(v) : null; } catch(_) { return null; } })(),
          showUserMenu: false,
          showLoginModal: false,
          showExtensionModal: false,
          loginForm: { username: '', password: '', loading: false, error: '' },
          showChangePwdModal: false,
          changePwdForm: { old_password: '', new_password: '', confirm_password: '', loading: false, error: '' },
          storeProducts: { total: 0, items: [], page: 1, pageSize: 20, loading: false, internal_state: 'ACTIVE', search: '', counts: {} },
          syncingStoreProducts: false,
          editingInventoryId: null,
          editingInventoryVal: 0,
          selectedStoreProductSkus: [],
          isAllStoreProductsSelected: false,
          showBatchInventoryModal: false,
          batchInventoryValue: 999,
          isSubmittingBatchInventory: false,
          storeAudits: { total: 0, items: [], page: 1, pageSize: 20, loading: false, status: 'ALL', search: '', vertical: '', only_errors: false, counts: {}, verticals: [], draftCount: 0 },
          syncingStoreAudits: false,
          showAuditDetailModal: false,
          activeAuditDetail: null,
          showAuditLocalProductModal: false,
          activeAuditLocalProduct: null,
          orders: { total: 0, items: [], page: 1, pageSize: 20, loading: false, status: 'all', search: '', counts: {} },
          syncingOrders: false,
          userList: [],
          loadingUsers: false,
          showUserModal: false,
          userModalTitle: '新建员工账号',
          userForm: { id: null, username: '', password: '', nickname: '', role: 'OPERATOR', store_ids: [] },
          isSavingUser: false,
          showAuthStoresModal: false,
          targetAuthUser: null,
          targetAuthStoreIds: [],
          tabs: [
            { id: 'products', name: '选品箱', icon: '📦' },
            { id: 'inspector', name: '审品比对', icon: '🔍' },
            { id: 'piggyback', name: 'Makro跟品', icon: '🎯' },
            { id: 'store_products', name: '店铺商品', icon: '🏬' },
            { id: 'store_audits', name: '审核列表', icon: '📋' },
            { id: 'orders', name: '订单管理', icon: '📑' },
            { id: 'stores', name: '多店铺管理', icon: '🏪' },
            { id: 'tasks', name: '操作与任务日志', icon: '📋' },
            { id: 'settings', name: '系统设置', icon: '⚙️' },
          ],
          products: { total: 0, items: [] },
          tasks: [],
          stores: [],
          loadingStores: false,
          showStoreModal: false,
          storeModalTitle: '添加新店铺',
          storeForm: {
            id: null,
            name: '',
            seller_id: '',
            fk_csrf_token: '',
            cookie: '',
            default_brand: 'Beishi',
            is_active: true,
            is_default: false,
            notes: '',
            login_email: '',
            login_password: '',
            imap_provider: 'auto',
            imap_server: '',
            imap_port: 993,
            imap_user: '',
            imap_password: ''
          },
          testingStoreId: null,
          showStoreLoginPassword: false,
          showStoreImapPassword: false,
          showAutoLoginPassword: false,
          showAutoLoginImapPassword: false,
          triggeringAutoLoginAll: false,
          // Makro 自动登录与多邮箱验证码状态
          showAutoLoginModal: false,
          autoLoginStore: null,
          autoLoginForm: {
            store_id: null,
            email: '',
            password: '',
            imap_provider: 'auto',
            imap_server: '',
            imap_port: 993,
            imap_user: '',
            imap_password: '',
            otp: ''
          },
          autoLoginStage: 'ready', // 'ready' | 'running_auto' | 'waiting_otp' | 'success' | 'failed'
          autoLoginStatusMsg: '',
          autoLoginErrorMsg: '',
          autoLoginLogs: [],
          autoLoginSessionId: null,
          autoLoginCountdown: 60,
          autoLoginTimer: null,
          testingEmail: false,
          testingEmailResult: null,
          showBatchPublishModal: false,
          batchPublishTarget: 'all', // 'all' | 'single'
          batchPublishTargetStoreId: null,
          batchPublishForce: false,
          showSinglePublishModal: false,
          singlePublishItem: null,
          testingJev: false,
          jevTestResult: null,
          settings: {
            markup_ratio: 1.35,
            fixed_markup: 20,
            mrp_ratio: 1.5,
            publish_concurrency: 2,
            ai_provider: 'deepseek',
            qwen_api_key: '',
            qwen_model: 'qwen-plus',
            deepseek_api_key: '',
            deepseek_model: 'deepseek-chat',
            jev_api_key: '',
            jev_base_url: 'https://api.typesafe.ai',
            jev_model: 'jev-latest',
            jev_enabled: true,
            seller_id: 'cb80491bf0a34dc5',
            fk_csrf_token: 'FbvXzXEP-45o5eUtkeX8Wo6LCq9GBWDg9Rcg',
            cookie: '',
            default_brand: 'Beishi',
            seo_title_enabled: true,
            seo_title_max_len: 120,
            cleaner_mode: 'text',
            qwen_vision_model: 'qwen-vl-plus',
            custom_category_synonyms: '{}',
            piggyback_default_strategy: 'MINUS_15',
            piggyback_default_floor_mode: 'PERCENT',
            piggyback_default_floor_value: 70.0,
            piggyback_default_inventory: 500
          },
          batchCleanMode: 'text',
          searchQuery: '',
          statusFilter: '',
          complianceFilter: '',
          userFilter: '',
          storePublishFilterStoreId: null,
          storePublishStatus: 'ALL',
          operatorsList: [],
          showBatchAssignModal: false,
          batchAssignTargetUserId: '',
          isAssigningUser: false,
          minPriceFilter: null,
          maxPriceFilter: null,
          currentPage: 1,
          pageSize: 50,
          selectedIds: [],
          loadingAllFiltered: false,
          statusCounts: { ALL: 0, PENDING_CLEAN: 0, CLEANED: 0, SUBMITTED: 0, FAILED: 0, ABANDONED: 0 },
          complianceCounts: { ALL: 0, DISPUTED: 0, SAFE: 0, RISK: 0, PROHIBITED: 0, PENDING_CHECK: 0 },
          showBatchPriceModal: false,
          isUpdatingBatchPrice: false,
          batchPriceForm: {
            mode: 'formula',
            multiplier: 1.0,
            fixed_offset: 0,
            fixed_price: null
          },
          activeItem: null,
          complianceModalItem: null,
          checkingComplianceId: null,
          arbitrationNotes: '',
          isArbitrating: false,
          loadingProducts: false,
          isCleaning: false,
          isBatchCheckingCompliance: false,
          isBatchPublishing: false,
          publishingId: null,
          publishingVariantId: null,
          showQuickCollectModal: false,
          quickCollectInput: '',
          isQuickCollecting: false,
          quickCollectTab: 'single', // 'single' | 'csv'
          csvFile: null,
          csvFileName: '',
          csvPlidCount: 0,
          csvSkipExisting: true,
          isCsvCollecting: false,
          productAbortController: null
        };
      },,

  computed: {
        businessTabs() {
          return [
            { id: 'products', name: '选品箱', icon: '📦', badge: (this.statusCounts && this.statusCounts.PENDING_CLEAN) || null, badgeType: 'warning', desc: 'Takealot 采集商品池 · AI 清洗 · 合规检测与批量刊登' },
            { id: 'piggyback', name: 'Makro跟品', icon: '🎯', badge: (this.piggyback && this.piggyback.stats && this.piggyback.stats.pending_count > 0 ? this.piggyback.stats.pending_count : null), badgeType: 'warning', desc: 'Makro 站内同款跟品 · 一键跟卖 · 双AI合规拦截 · 智能定价破价' },
            { id: 'inspector', name: '审品比对', icon: '🔍', desc: '双端字段深度对齐 · 标题/主图/规格实时比对与编辑' },
            { id: 'store_products', name: '店铺商品', icon: '🏬', badge: (this.storeProducts && this.storeProducts.total) || null, desc: '多店铺已刊登商品 · 在线库存同步与价格调节' },
            { id: 'store_audits', name: '审核列表', icon: '📋', badge: (this.storeAudits && this.storeAudits.counts && this.storeAudits.counts.DRAFT > 0 ? this.storeAudits.counts.DRAFT : null), badgeType: 'warning', desc: 'Makro 官方商品在途流转 · 平台质检 · 驳回诊断与错误修复' },
            { id: 'orders', name: '订单管理', icon: '📑', badge: (this.orders && this.orders.total) || null, desc: 'Makro 店铺全量订单同步 · 履约发货与状态追踪' }
          ];
        },
        isAllCurrentPageStoreProductsSelected() {
          if (!this.storeProducts.items || this.storeProducts.items.length === 0) return false;
          return this.storeProducts.items.every(it => this.selectedStoreProductSkus.includes(it.sku_id));
        },
        systemTabs() {
          const list = [
            { id: 'stores', name: '多店铺管理', icon: '🏪', badge: this.stores && this.stores.length ? `${this.stores.length}店` : null, desc: '多店铺授权凭据 · Cookie 与 CSRF 令牌巡检' },
            { id: 'tasks', name: '任务日志', icon: '📋', hasActiveTask: (this.activeBgTasks || []).some(t => t.status === 'RUNNING'), desc: '后台多模态图文异步调度引擎与执行日志' }
          ];
          if (this.currentUser && this.currentUser.role === 'ADMIN') {
            list.push({ id: 'management', name: '人效驾驶舱', icon: '📊', badge: '管理', badgeType: 'admin', desc: '团队人效看板 · 员工工作量矩阵 · 24h时段工时与操作审计' });
            list.push({ id: 'settings', name: '系统设置', icon: '⚙️', badge: '管理', badgeType: 'admin', desc: '系统全局参数 · 凭据保活与模型配置' });
            list.push({ id: 'users', name: '用户管理', icon: '👥', badge: '管理', badgeType: 'admin', desc: '团队子账号管理 · 店铺访问控制与 RBAC 权限分配' });
          }

          return list;
        },
        visibleTabs() {
          return [...this.businessTabs, ...this.systemTabs];
        },
        currentTabMeta() {
          const all = [...this.businessTabs, ...this.systemTabs];
          const found = all.find(t => t.id === this.currentTab);
          if (found) return found;
          return { id: this.currentTab, name: '工作台', icon: '📁', desc: 'Makro 智能搬品系统' };
        },
        runningTask() {
          return (this.activeBgTasks || []).find(t => t.status === 'RUNNING') || null;
        },
        selectedIdSet() {
          return new Set(this.selectedIds);
        },
        activeStores() {
          return (this.stores || []).filter(s => s.is_active);
        },
        selectedPiggybackItems() {
          if (!this.piggyback.items || !this.piggyback.selectedIds.length) return [];
          const s = new Set(this.piggyback.selectedIds);
          return this.piggyback.items.filter(it => s.has(it.id));
        },
        selectedPiggybackSafeCount() {
          return this.selectedPiggybackItems.filter(it => it.compliance_status === 'SAFE').length;
        },
        selectedPiggybackPendingCount() {
          return this.selectedPiggybackItems.filter(it => it.compliance_status === 'PENDING_CHECK').length;
        },
        selectedPiggybackProhibitedCount() {
          return this.selectedPiggybackItems.filter(it => it.compliance_status === 'PROHIBITED').length;
        },
        selectedPiggybackValidCount() {
          return this.selectedPiggybackItems.filter(it => it.compliance_status !== 'PROHIBITED').length;
        },
        isAllSelected() {
          if (!this.products.items || this.products.items.length === 0) return false;
          return this.products.items.every(it => this.selectedIdSet.has(it.id));
        },
        isAllFilteredSelected() {
          const total = (this.products && this.products.total) || 0;
          return total > 0 && this.selectedIds.length >= total;
        },
        totalPages() {
          return Math.max(1, Math.ceil((this.products.total || 0) / this.pageSize));
        },
        groupedProducts() {
          if (!this.products.items || this.products.items.length === 0) return [];
          const items = this.products.items;
          const map = {};
          for (let i = 0; i < items.length; i++) {
            const item = items[i];
            const grpKey = item.group_code || item.takealot_id || `GRP_${item.id}`;
            if (!map[grpKey]) {
              const cover = item._displayImage || this.getItemImage(item);
              const cleanTitle = item._cleanGroupTitle || (item.takealot_title || '').replace(/\s*-\s*[^-]+-[^-]+$/, '').replace(/\s*-\s*[^-]+$/, '').trim();
              map[grpKey] = {
                group_code: grpKey,
                title: cleanTitle,
                coverImage: cover,
                takealot_url: item.takealot_url,
                items: [],
                colors: [],
                sizes: [],
                minPrice: item.makro_selling_price || 0,
                maxPrice: item.makro_selling_price || 0,
              };
            }
            const g = map[grpKey];
            g.items.push(item);
            if (item.colour && item.colour !== '多色' && !g.colors.includes(item.colour)) {
              g.colors.push(item.colour);
            }
            if (item.size && item.size !== '均码' && !g.sizes.includes(item.size)) {
              g.sizes.push(item.size);
            }
            if (item.makro_selling_price < g.minPrice) g.minPrice = item.makro_selling_price;
            if (item.makro_selling_price > g.maxPrice) g.maxPrice = item.makro_selling_price;
          }
          return Object.values(map);
        },
        parsedPlidInfo() {
          const raw = (this.quickCollectInput || '').trim();
          if (!raw) return { status: 'empty', plid: null, url: null, label: '' };

          // 1. 匹配带 PLID 的特征 (URL 或 纯 PLID 字符串)
          let m = raw.match(/PLID(\d+)/i);
          if (m) {
            const plid = `PLID${m[1]}`;
            const isUrl = /^https?:\/\//i.test(raw);
            return {
              status: 'valid',
              plid: plid,
              url: isUrl ? raw : `https://www.takealot.com/x/${plid}`,
              label: isUrl ? `🔗 已识别 Takealot 网址 -> ${plid}` : `🎯 已识别 PLID 商品编号: ${plid}`
            };
          }

          // 1.5 匹配带 TSIN 的特征 (URL query 或 纯 TSIN 字符串)
          let tsinM = raw.match(/TSIN(\d+)/i) || raw.match(/[?&]tsin=(\d+)/i);
          if (tsinM) {
            const tsin = `TSIN${tsinM[1]}`;
            const isUrl = /^https?:\/\//i.test(raw);
            return {
              status: 'valid',
              plid: tsin,
              url: isUrl ? raw : `https://www.takealot.com/x/${tsin}`,
              label: isUrl ? `🔗 已从网址参数识别 TSIN -> ${tsin}` : `🎯 已识别 TSIN 变体编号: ${tsin}`
            };
          }

          // 2. 匹配纯数字 (7~10位数字，支持后端智能自动匹配 PLID 与 TSIN)
          let numMatch = raw.match(/^\d{7,10}$/);
          if (numMatch) {
            const num = numMatch[0];
            return {
              status: 'valid',
              plid: num,
              url: `https://www.takealot.com/x/PLID${num}`,
              label: `🎯 已识别纯数字编号 (自动兼容 PLID/TSIN) -> ${num}`
            };
          }

          // 3. 匹配从网址 query 中包含 plid=
          let queryMatch = raw.match(/[?&]plid=(\d+)/i);
          if (queryMatch) {
            const plid = `PLID${queryMatch[1]}`;
            return {
              status: 'valid',
              plid: plid,
              url: raw,
              label: `🔗 已从网址参数提取 -> ${plid}`
            };
          }

          // 4. 输入尚未构成有效编号
          return {
            status: 'invalid',
            plid: null,
            url: null,
            label: '⚠️ 尚未识别到有效编号（支持粘贴 Takealot 商品链接、PLID、TSIN 或 7-10位纯数字编号）'
          };
        },
        samplePreviewFloor() {
          if (!this.piggyback.selectedIds || this.piggyback.selectedIds.length === 0) return null;
          const firstItem = this.piggyback.items.find(it => this.piggyback.selectedIds.includes(it.id));
          if (!firstItem) return null;
          const orig = Number(firstItem.original_price || firstItem.target_price || 100);
          const val = Number(this.batchFloorForm.value || 0);
          let newFloor = 0;
          if (this.batchFloorForm.mode === 'PERCENT') {
            newFloor = Math.round(orig * (val / 100) * 100) / 100;
          } else if (this.batchFloorForm.mode === 'OFFSET') {
            newFloor = Math.max(Math.round((orig - val) * 100) / 100, 1);
          } else {
            newFloor = Math.max(val, 1);
          }
          return { orig, newFloor };
        }
      },,

  async mounted() {
        const authed = await this.checkAuth();
        if (authed) {
          this.loadProducts(1);
          this.loadPiggybackItems(1);
          this.loadOperators();
          this.loadSettings();
          this.loadStores();
          this.loadTasks();
          this.checkActiveTasks();
          this.loadNetworkInfo();
          this.loadVerticalTranslations();
          this.loadMyDailySummary();
          if (this.currentTab === 'users' && this.currentUser && this.currentUser.role === 'ADMIN') {
            this.loadUsers();
          } else if (this.currentTab === 'management' && this.currentUser && this.currentUser.role === 'ADMIN') {
            this.loadManagementData();
          } else if (this.currentTab === 'store_products') {
            this.loadStoreProducts(1);
          } else if (this.currentTab === 'store_audits') {
            this.loadStoreAudits(1);
          } else if (this.currentTab === 'orders') {
            this.loadStoreOrders(1);
          }
        }

        window.addEventListener('keydown', (e) => {
          if (e.key === 'Escape' && this.previewImageUrl) {
            this.closeImagePreview();
          }
        });
      },,

  methods: {
    ...coreMethods,
    ...productsMethods,
    ...piggybackMethods,
    ...makro_publisherMethods,
    ...storesMethods,
    ...store_productsMethods,
    ...store_auditsMethods,
    ...ordersMethods,
    ...managementMethods,
    ...tasksMethods,
    ...settingsMethods,
    ...usersMethods,
    ...inspectorMethods
  }
});

// 挂载应用到 DOM
window.vm = app.mount('#app');
console.log('Makro App Vue 3 successfully initialized and mounted via ES Modules.');
