# -*- coding: utf-8 -*-
"""八类往来统一识别、过滤、统计和冲突门禁测试。"""
from __future__ import annotations

import importlib
import importlib.util

import pandas as pd
import pytest


def _load_current_accounts_api():
    spec = importlib.util.find_spec("src.core.current_accounts")
    assert spec is not None, "八类往来统一规则尚未实现"
    return importlib.import_module("src.core.current_accounts")


CASES = [
    ("112201", "1122 应收账款-国内", "应收账款", "debit"),
    ("1123", "预付账款", "预付账款", "debit"),
    ("1221", "其他应收款", "其他应收款", "debit"),
    ("1486", "合同资产", "合同资产", "debit"),
    ("2202", "应付账款", "应付账款", "credit"),
    ("2203", "预收账款", "预收账款", "credit"),
    ("2241", "其他应付款", "其他应付款", "credit"),
    ("2204", "合同负债", "合同负债", "credit"),
]


@pytest.mark.parametrize("code,name,category,direction", CASES)
def test_八类往来名称和编码识别(code, name, category, direction):
    api = _load_current_accounts_api()
    match = api.classify_current_account(code, name)
    assert match.status == "retained"
    assert match.category == category
    assert match.direction == direction


def test_名称和编码冲突时名称优先并警告():
    api = _load_current_accounts_api()
    match = api.classify_current_account("1122", "应付账款")
    assert match.status == "retained"
    assert match.category == "应付账款"
    assert match.direction == "credit"
    assert match.warning


@pytest.mark.parametrize("name", ["应收账款坏账准备", "合同资产减值准备"])
def test_准备类科目被排除(name):
    api = _load_current_accounts_api()
    match = api.classify_current_account("1122", name)
    assert match.status == "ignored"
    assert match.ignored_reason == "坏账或减值准备"


def test_名称为空时按编码识别并补标准名称():
    api = _load_current_accounts_api()
    match = api.classify_current_account(1122.0, None)
    assert match.status == "retained"
    assert match.category == "应收账款"
    assert match.basis == "code"
    assert match.display_name == "应收账款"


def test_源表缺少编码列或名称列时补齐标准输出列():
    api = _load_current_accounts_api()
    name_only = pd.DataFrame({
        "科目名称": ["应收账款"], "明细名称": ["客户A"],
        "借方金额": [100.0], "贷方金额": [0.0],
    })
    code_only = pd.DataFrame({
        "科目编码": ["2202"], "明细名称": ["供应商A"],
        "借方金额": [0.0], "贷方金额": [100.0],
    })
    name_result = api.filter_current_accounts(name_only, data_kind="journal", source_label="序时账/名称表")
    code_result = api.filter_current_accounts(code_only, data_kind="journal", source_label="序时账/编码表")
    assert name_result.dataframe.iloc[0]["科目编码"] == ""
    assert code_result.dataframe.iloc[0]["科目名称"] == "应付账款"


def test_序时账过滤统计相互勾稽且空明细不丢金额():
    api = _load_current_accounts_api()
    df = pd.DataFrame({
        "科目编码": ["1122", "2204", "6602", "1122", None, "1122"],
        "科目名称": ["应收账款", "合同负债", "管理费用", "应收账款坏账准备", None, None],
        "明细名称": [None, "客户B", "部门", "客户A", "未知", "客户C"],
        "借方金额": [100.0, 0.0, 50.0, 20.0, 30.0, 200.0],
        "贷方金额": [0.0, 80.0, 0.0, 0.0, 0.0, 0.0],
    })

    result = api.filter_current_accounts(
        df, data_kind="journal", source_label="序时账/明细",
    )

    assert result.original_rows == 6
    assert result.retained_rows == 3
    assert result.ignored_rows == 3
    assert result.ignored_counts == {
        "非八类往来": 1,
        "坏账或减值准备": 1,
        "识别关键字段均为空": 1,
    }
    assert result.category_counts == {
        "应收账款": 2, "预付账款": 0, "其他应收款": 0, "合同资产": 0,
        "应付账款": 0, "预收账款": 0, "其他应付款": 0, "合同负债": 1,
    }
    assert result.recognition_counts == {"按名称识别": 2, "按编码辅助识别": 1}
    assert result.dataframe["明细名称"].tolist().count("（未填明细）") == 1
    assert result.blank_detail_rows == 1
    assert result.blank_detail_amounts == {"借方金额": 100.0, "贷方金额": 0.0}
    assert result.original_rows == result.retained_rows + result.ignored_rows
    assert result.ignored_rows == sum(result.ignored_counts.values())


def test_期初空明细分别统计借贷余额():
    api = _load_current_accounts_api()
    df = pd.DataFrame({
        "科目编码": ["1122", "2202"],
        "科目名称": ["应收账款", "应付账款"],
        "明细名称": [None, None],
        "借方余额": [300.0, 20.0],
        "贷方余额": [10.0, 400.0],
    })
    result = api.filter_current_accounts(
        df, data_kind="opening", source_label="期初余额/期初",
    )
    assert result.blank_detail_rows == 2
    assert result.blank_detail_amounts == {"借方余额": 320.0, "贷方余额": 410.0}


def test_无期初数据返回全零统计():
    api = _load_current_accounts_api()
    result = api.filter_current_accounts(
        pd.DataFrame(), data_kind="opening", source_label="期初余额/(无期初)",
    )
    assert result.original_rows == 0
    assert result.retained_rows == 0
    assert result.ignored_rows == 0
    assert all(value == 0 for value in result.category_counts.values())


def test_同一科目编码跨来源分类冲突会阻断():
    api = _load_current_accounts_api()
    opening = pd.DataFrame({
        "科目编码": ["1122"], "科目名称": ["应收账款"], "明细名称": ["客户A"],
        "借方余额": [100.0], "贷方余额": [0.0],
    })
    journal = pd.DataFrame({
        "科目编码": ["1122"], "科目名称": ["应付账款"], "明细名称": ["供应商A"],
        "借方金额": [0.0], "贷方金额": [100.0],
    })
    opening_result = api.filter_current_accounts(
        opening, data_kind="opening", source_label="期初余额/期初表",
    )
    journal_result = api.filter_current_accounts(
        journal, data_kind="journal", source_label="序时账/明细",
    )
    conflicts = api.find_category_conflicts([opening_result, journal_result])
    assert len(conflicts) == 1
    assert conflicts[0].subject_code == "1122"
    text = api.format_category_conflicts(conflicts)
    assert "应收账款" in text
    assert "应付账款" in text
    assert "期初余额/期初表" in text
    assert "序时账/明细" in text


def test_空科目编码不参与编码冲突():
    api = _load_current_accounts_api()
    df = pd.DataFrame({
        "科目编码": [None, None],
        "科目名称": ["应收账款", "应付账款"],
        "明细名称": ["客户A", "供应商A"],
        "借方金额": [100.0, 0.0],
        "贷方金额": [0.0, 100.0],
    })
    result = api.filter_current_accounts(df, data_kind="journal", source_label="序时账/明细")
    assert api.find_category_conflicts([result]) == []
