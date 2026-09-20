from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class ComplianceArbitrationLog(Base):
    """
    双 AI 合规检测分歧仲裁与提示词优化语料日志表
    记录所有由千问与 DeepSeek 交叉审查产生分歧并经人工裁决的商品历史数据。
    自动标注误差归因 (FP/FN)，作为后续提示词 Few-Shot 微调和优化的黄金语料库。
    """
    __tablename__ = "compliance_arbitration_logs"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # 审查要素快照
    takealot_title = Column(String(500), nullable=True)
    makro_title = Column(String(500), nullable=True)
    brand = Column(String(100), nullable=True)
    image_url = Column(String(1000), nullable=True)
    
    # 双 AI 独立诊断快照 (JSON 字符串)
    qwen_verdict = Column(Text, nullable=True)
    deepseek_verdict = Column(Text, nullable=True)
    qwen_status = Column(String(50), nullable=True)
    deepseek_status = Column(String(50), nullable=True)
    
    # 人工终审裁定
    human_verdict = Column(String(50), nullable=False)  # SAFE / RISK / PROHIBITED
    
    # 智能归因分析 (供提示词迭代优化引擎使用)
    # QWEN_FALSE_POSITIVE: 千问过度敏感误报 (DeepSeek 正确)
    # QWEN_FALSE_NEGATIVE: 千问遗漏侵权违规 (DeepSeek 正确检出)
    # DEEPSEEK_FALSE_POSITIVE: DeepSeek 过度敏感误报 (千问正确)
    # DEEPSEEK_FALSE_NEGATIVE: DeepSeek 遗漏侵权违规 (千问正确检出)
    # BOTH_MISJUDGED: 双方均与人工判定不符
    # CONSENSUS_AFFIRMED: 双方一致且获人工确认
    error_attribution = Column(String(50), nullable=True, index=True)
    
    # 争议关键词 (JSON 字符串，例如 ["Stanley", "Compatible"])
    dispute_keywords = Column(Text, nullable=True)
    # 人工裁定说明与修正备注
    human_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.now, index=True)
    arbitrated_at = Column(DateTime, default=datetime.now)

    # 关联商品
    product = relationship("Product", backref="arbitration_logs")
