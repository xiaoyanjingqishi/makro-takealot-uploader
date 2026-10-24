# -*- coding: utf-8 -*-
import sys
import json
import base64
import requests
from openai import OpenAI

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import os
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import SessionLocal
from app.models.setting import SystemSetting

db = SessionLocal()
api_key = db.query(SystemSetting).filter(SystemSetting.key == 'deepseek_api_key').first().value
base_url = db.query(SystemSetting).filter(SystemSetting.key == 'deepseek_base_url').first().value
model_text = db.query(SystemSetting).filter(SystemSetting.key == 'deepseek_model').first().value
model_vision = db.query(SystemSetting).filter(SystemSetting.key == 'deepseek_vision_model').first().value

client = OpenAI(api_key=api_key, base_url=base_url)

print(f"Text Model: {model_text}, Vision Model: {model_vision}")
print(f"Base URL: {base_url}")

# 1. 测试文本提示词
title = 'Chengxixi 1 m USB-C to DisplayPort Cable 6.6ft (16K@60Hz 8K@120Hz), 80Gbps USB C to DP 2.1 Cord Thunderbolt 3/4/5 Compatible with MacBook Pro/Air, for iPhone 17 Pro Max, for iPad Pro, for iMac, for XPS.'
brand = 'Chengxixi'
category = 'Computer Cables'

from app.services.compliance_service import ComplianceService
cs = ComplianceService()
prompt_text = cs._build_title_ip_prompt(
    raw_title=title,
    makro_title=title,
    brand=brand,
    category=category,
    description="",
    is_piggyback=True,
    original_seller="Chengxixi"
)

print("\n" + "="*50)
print("--- [ROUND 1 TEXT PROMPT] ---")
print(prompt_text)
print("="*50)

try:
    resp_t = client.chat.completions.create(
        model=model_text,
        messages=[
            {"role": "system", "content": "You are a professional ecommerce IP compliance auditor for DeepSeek. Reply ONLY with valid JSON."},
            {"role": "user", "content": prompt_text}
        ],
        temperature=0.1,
        max_tokens=600
    )
    print("--- [ROUND 1 TEXT RAW RESPONSE] ---")
    print("Content:", resp_t.choices[0].message.content)
    if hasattr(resp_t.choices[0].message, "reasoning_content"):
        print("Reasoning:", resp_t.choices[0].message.reasoning_content)
except Exception as e:
    print("Round 1 error:", e)

# 2. 测试视觉提示词
img_url = 'https://www.makro.co.za/asset/cms/data-cable/hdmi-cable/i/f/v/liangheng-original-imahmfzgfzymtdzj.jpeg'
resp_img = requests.get(img_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.makro.co.za/'})
b64 = base64.b64encode(resp_img.content).decode('utf-8')
data_uri = f'data:image/jpeg;base64,{b64}'

prompt_vision = cs._build_vision_prompt(["Chengxixi", "MacBook", "Apple"], is_piggyback=True)

print("\n" + "="*50)
print("--- [ROUND 2 VISION PROMPT] ---")
print(prompt_vision)
print("="*50)

try:
    resp_v = client.chat.completions.create(
        model=model_vision,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_vision},
                    {"type": "image_url", "image_url": {"url": data_uri}}
                ]
            }
        ],
        temperature=0.1,
        max_tokens=600
    )
    print("--- [ROUND 2 VISION RAW RESPONSE] ---")
    print("Content:", resp_v.choices[0].message.content)
    if hasattr(resp_v.choices[0].message, "reasoning_content"):
        print("Reasoning:", resp_v.choices[0].message.reasoning_content)
except Exception as e:
    print("Round 2 error:", e)
