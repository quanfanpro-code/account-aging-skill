# -*- coding: utf-8 -*-
"""无期初余额场景测试：期初视为 0，只用序时账计算账龄"""
from datetime import date
import pytest
import pandas as pd
from src.core.orchestrator import AgingOrchestrator

EMPTY_OPENING_COLS = ["科目编码", "科目名称", "明细名称", "借方余额", "贷方余额"]


def test_无期初仅序时账可算账龄():
    """无期初余额时，期初视为 0，账龄由序时账按 FIFO 得出"""
    opening_df = pd.DataFrame(columns=EMPTY_OPENING_COLS)
    journal_df = pd.DataFrame({
        "日期": [date(2024, 1, 1), date(2024, 10, 1), date(2024, 11, 1), date(2025, 2, 1)],
        "科目编码": ["1122", "1122", "1122", "1122"],
        "科目名称": ["应收账款", "应收账款", "应收账款", "应收账款"],
        "明细名称": ["客户A", "客户A", "客户A", "客户A"],
        "借方金额": [1000.0, 500.0, 0.0, 300.0],
        "贷方金额": [0.0, 0.0, 600.0, 0.0],
        "摘要": ["销售", "销售", "回款", "销售"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 9999)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    r = results[0]
    assert r.opening_balance == pytest.approx(0.0)
    assert r.closing_balance == pytest.approx(1200.0)
    # 2024-01-01 借方1000核销600后剩400 → 517天 老档；其余500+300 → 0-365档
    assert r.bucket_totals == pytest.approx([800.0, 400.0])