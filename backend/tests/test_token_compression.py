# -*- coding: utf-8 -*-
import sys
import os
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import SessionLocal
from app.services.compliance_service import ComplianceService

db = SessionLocal()
cs = ComplianceService.from_db(db)
title = 'Chengxixi 1 m USB-C to DisplayPort Cable 6.6ft (16K@60Hz 8K@120Hz), 80Gbps USB C to DP 2.1 Cord Thunderbolt 3/4/5 Compatible with MacBook Pro/Air, for iPhone 17 Pro Max, for iPad Pro, for iMac, for XPS.'

compressed_prompt = f'''跨境电商商品标题合规与知识产权风控审查。仅返回合法JSON，严禁输出非JSON内容。
【审查铁律】:
1. 知名大牌零容忍: 严查受保护大牌(Apple,Sony,Chevy,GMC,Ford,Toyota,Husqvarna,Nike,Dyson等)及动漫影视IP(Disney,Sanrio,Pokemon等)。
2. 兼容配件规范:
   - 必须白纸黑字出现英文兼容词: 'Compatible with', 'For', 'Fits', 'Suitable for'，严禁脑补隐含语境；无字面兼容词一律判定 RISK (MISSING_COMPATIBILITY)；
   - 第三方大牌必须且仅出现在兼容连接词之后；严禁第三方大牌出现在连接词之前修饰商品（如大牌前置裸露修饰属于侵权 RISK）；
   - 严禁第三方大牌直接紧跟自有品牌冒充联名；严禁连续堆砌>=3个大牌。
3. 卖家白牌豁免: Listing前缀品牌若为普通卖家商号/白牌(如Chengxixi)，Makro跟品官方规则100%允许跟卖，此类判定 SAFE。
4. 违禁品排查: 严禁蓝牙/WiFi无线发射设备；严禁液体/香水/精油/易燃化学品跨境禁运品；区分实体转接线/模具与灌装液体。
5. 评级: SAFE(合规通用品/白牌/规范配件), RISK(缺少字面兼容词/大牌前置修饰/过度堆砌), PROHIBITED(假冒大牌/受保护整机/违禁品)。

待审数据:
- 标题: {title}
- 授权自有品牌: Chengxixi
- 类目: Computer Cables

返回JSON模板:
{{
  "has_risk": false,
  "risk_level": "SAFE|RISK|PROHIBITED",
  "detected_brands_or_ips": ["识别到的品牌或IP"],
  "violation_type": "NONE|TRADEMARK|COPYRIGHT_IP|BRAND_SPAMMING|MISSING_COMPATIBILITY|PROHIBITED_TECH|PROHIBITED_LIQUID",
  "reasons": ["1.品牌属性说明", "2.兼容连接词语法与大牌位置说明", "3.平台跟品合规最终结论"],
  "summary": "1-2句精炼核心论证",
  "recommended_title": "合规英文建议标题"
}}'''

sys_msg = (
    'You are an expert ecommerce compliance auditor for DeepSeek. '
    'In your thinking process, analyze intellectual property risks concisely and decisively (strictly within 100-180 words), '
    'then immediately output the final valid JSON.'
)

resp = cs.deepseek_client.chat.completions.create(
    model='deepseek-flash',
    messages=[
        {'role': 'system', 'content': sys_msg},
        {'role': 'user', 'content': compressed_prompt}
    ],
    temperature=0.1,
    max_tokens=1500
)

choice = resp.choices[0]
print('FINISH REASON:', choice.finish_reason)
print('CONTENT LEN:', len(choice.message.content or ''))
print('CONTENT:', choice.message.content)
print('USAGE:', resp.usage)
if hasattr(choice.message, 'reasoning_content'):
    print('REASONING LEN:', len(choice.message.reasoning_content or ''))
    print('REASONING:', choice.message.reasoning_content[:300])
