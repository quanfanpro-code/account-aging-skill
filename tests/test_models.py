# -*- coding: utf-8 -*-
from datetime import date
import pytest
from src.core.models import AgingEntry, AgingResult


def test_AgingEntry_可创建并不可变():
    """AgingEntry 应为 frozen dataclass，赋值应抛错"""
    entry = AgingEntry(
        source="2024-01-01",
        source_date=date(2024, 1, 1),
        amount=100.0,
        days=100,
        bucket_index=0,
    )
    assert entry.amount == 100.0
    with pytest.raises(Exception):
        entry.amount = 200.0  # type: ignore[misc]


def test_AgingResult_total_check_等于各分档合计():
    """total_check 属性应等于 bucket_totals 之和"""
    result = AgingResult(
        subject_code="1122", subject_name="应收账款",
        detail_name="客户A", direction="debit",
        opening_balance=1000.0, total_debit=500.0, total_credit=0.0,
        closing_balance=1500.0,
        bucket_totals=[500.0, 1000.0],
    )
    assert result.total_check == 1500.0
    assert result.closing_balance == result.total_check


def test_AgingResult_可携带期初分档结构():
    """AgingResult 应支持 opening_bucket_totals 和 opening_date 字段"""
    result = AgingResult(
        subject_code="1122", subject_name="应收账款",
        detail_name="客户A", direction="debit",
        opening_balance=1000.0, total_debit=500.0, total_credit=0.0,
        closing_balance=1500.0,
        bucket_totals=[500.0, 1000.0],
        opening_bucket_totals=[300.0, 400.0, 300.0],
        opening_date=date(2024, 1, 1),
    )
    assert result.opening_bucket_totals == [300.0, 400.0, 300.0]
    assert result.opening_date == date(2024, 1, 1)
    assert result.total_check == 1500.0  # 不影响原有逻辑


def test_AgingResult_默认期初分档为空():
    """未提供期初分档时，opening_bucket_totals 应为空列表"""
    result = AgingResult(
        subject_code="1122", subject_name="应收", detail_name="A",
        direction="debit", opening_balance=100.0,
        total_debit=0.0, total_credit=0.0, closing_balance=100.0,
        bucket_totals=[100.0],
    )
    assert result.opening_bucket_totals == []
    assert result.opening_date is None


def test_AgingResult_负余额单列参与总校验():
    """负余额不进正常账龄分档，但应参与 total_check"""
    result = AgingResult(
        subject_code="1122", subject_name="应收", detail_name="A",
        direction="debit", opening_balance=100.0,
        total_debit=0.0, total_credit=200.0, closing_balance=-100.0,
        bucket_totals=[0.0, 0.0],
        negative_balance=-100.0,
    )
    assert result.total_check == -100.0
