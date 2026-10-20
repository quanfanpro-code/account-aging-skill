# -*- coding: utf-8 -*-
"""借贷混合计算测试"""
from datetime import date
import pytest
import pandas as pd
from src.core.orchestrator import AgingOrchestrator
from src.core.current_accounts import filter_current_accounts


def test_借贷混合一次算完():
    """借方科目和贷方科目在同一次计算中分别用对应口径"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122", "2202"],
        "科目名称": ["应收账款", "应付账款"],
        "明细名称": ["客户A", "供应商X"],
        "借方余额": [1000.0, 0.0],
        "贷方余额": [0.0, 800.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1), date(2024, 6, 1)],
        "科目编码": ["1122", "2202"],
        "明细名称": ["客户A", "供应商X"],
        "借方金额": [500.0, 200.0],
        "贷方金额": [0.0, 300.0],
        "摘要": ["销售", "采购"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",  # 默认方向
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 2
    # 1122 走借方口径
    r0 = next(r for r in results if r.subject_code == "1122")
    assert r0.direction == "debit"
    assert r0.closing_balance == pytest.approx(1500.0)  # 1000+500
    # 2202 走贷方口径
    r1 = next(r for r in results if r.subject_code == "2202")
    assert r1.direction == "credit"
    assert r1.closing_balance == pytest.approx(900.0)  # 800+300-200


def test_应收账款反向余额仍留在借方类():
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "科目名称": ["应收账款"],
        "明细名称": ["客户A"],
        "借方余额": [100.0],
        "贷方余额": [500.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1)],
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方金额": [0.0],
        "贷方金额": [200.0],
        "摘要": ["预收"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    assert results[0].direction == "debit"
    assert results[0].closing_balance == pytest.approx(-600.0)
    assert results[0].negative_balance == pytest.approx(-600.0)


def test_合同资产和合同负债一次算完():
    opening_df = pd.DataFrame({
        "科目编码": ["1486", "2204"],
        "科目名称": ["合同资产", "合同负债"],
        "明细名称": ["客户A", "客户B"],
        "借方余额": [300.0, 0.0],
        "贷方余额": [0.0, 500.0],
    })
    journal_df = pd.DataFrame(columns=[
        "日期", "科目编码", "科目名称", "明细名称", "借方金额", "贷方金额", "摘要",
    ])
    results = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1), buckets=[(0, 365)],
    ).run(opening_df, journal_df)
    assert {result.subject_name: result.direction for result in results} == {
        "合同资产": "debit", "合同负债": "credit",
    }


def test_科目编码为空时不同往来类别不会混成一组():
    opening = pd.DataFrame({
        "科目编码": [None, None],
        "科目名称": ["应收账款", "应付账款"],
        "明细名称": ["同一单位", "同一单位"],
        "借方余额": [100.0, 0.0],
        "贷方余额": [0.0, 200.0],
    })
    journal = pd.DataFrame(columns=[
        "日期", "科目编码", "科目名称", "明细名称", "借方金额", "贷方金额", "摘要",
    ])
    opening_filtered = filter_current_accounts(
        opening, data_kind="opening", source_label="期初余额/期初",
    ).dataframe
    journal_filtered = filter_current_accounts(
        journal, data_kind="journal", source_label="序时账/序时账",
    ).dataframe
    results = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1), buckets=[(0, 365)],
    ).run(opening_filtered, journal_filtered)
    assert len(results) == 2
    assert {result.subject_name: result.direction for result in results} == {
        "应收账款": "debit", "应付账款": "credit",
    }
