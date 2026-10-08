# -*- coding: utf-8 -*-
"""
跟品定价与底价安全计算器
"""

from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.services.makro_piggyback_service import MakroPiggybackService


class PiggybackPricingCalculator:
    """
    负责跟品售价、划线价 MRP 以及保本底价 (Floor Price) 的核算
    """

    @staticmethod
    def eval_price_by_strategy(
        base_price: float,
        strategy: str = "MINUS_15",
        custom_delta: Optional[float] = None,
        min_floor: float = 0.0
    ) -> float:
        return MakroPiggybackService.eval_price_by_strategy(
            base_price=base_price,
            strategy=strategy,
            custom_delta=custom_delta,
            min_floor=min_floor
        )

    @staticmethod
    def calculate_default_floor(
        original_price: float,
        db: Optional[Session] = None
    ) -> float:
        return MakroPiggybackService.calculate_default_floor(
            original_price=original_price,
            db=db
        )

    @staticmethod
    def calculate_price(
        original_price: float,
        strategy: str = "MINUS_15",
        min_floor: float = 0.0,
        original_mrp: Optional[float] = None
    ) -> Tuple[float, float]:
        return MakroPiggybackService.calculate_price(
            original_price=original_price,
            strategy=strategy,
            min_floor=min_floor,
            original_mrp=original_mrp
        )
