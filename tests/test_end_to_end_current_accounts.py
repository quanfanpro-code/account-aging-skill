# -*- coding: utf-8 -*-
"""八类往来从选表、过滤、计算到汇总表头的端到端测试。"""
from __future__ import annotations

from datetime import date
import importlib
import importlib.util

import pandas as pd

from src.core.data_loader import DataLoader
from src.core.orchestrator import AgingOrchestrator
from src.core.report import ReportGenerator


def _load_current_accounts_api():
    spec = importlib.util.find_spec("src.core.current_accounts")
    assert spec is not None, "八类往来统一规则尚未实现"
    return importlib.import_module("src.core.current_accounts")


def test_八类往来按指定工作表一次生成借贷两类汇总(tmp_path):
    api = _load_current_accounts_api()
    opening_path = tmp_path / "期初余额.xlsx"
    journal_path = tmp_path / "序时账.xlsx"

    opening = pd.DataFrame({
        "科目编码": ["1122", "1123", "1221", "1486", "2202", "2203", "2241", "2204"],
        "科目名称": [
            "应收账款", None, "其他应收款", "合同资产",
            "应付账款", "预收账款", "其他应付款", "合同负债",
        ],
        "明细名称": [None, "供应商A", "员工A", "客户B", "供应商B", "客户C", "单位D", "客户E"],
        "借方余额": [100.0, 200.0, 300.0, 400.0, 0.0, 0.0, 0.0, 0.0],
        "贷方余额": [500.0, 0.0, 0.0, 0.0, 500.0, 600.0, 700.0, 800.0],
    })
    journal = pd.DataFrame({
        "日期": [date(2026, 1, 1)] * 11,
        "科目编码": [
            "1122", "1123", "1221", "1486", "2202", "2203", "2241", "2204",
            "6602", "1122", "1486",
        ],
        "科目名称": [
            "应收账款", "预付账款", "其他应收款", "合同资产",
            "应付账款", "预收账款", "其他应付款", "合同负债",
            "管理费用", "应收账款坏账准备", "合同资产减值准备",
        ],
        "明细名称": ["客户A", "供应商A", "员工A", "客户B", "供应商B", "客户C", "单位D", "客户E", "部门", "客户A", "客户B"],
        "借方金额": [0.0] * 11,
        "贷方金额": [0.0] * 11,
        "摘要": ["测试"] * 11,
    })

    with pd.ExcelWriter(opening_path) as writer:
        pd.DataFrame({"说明": ["不要读取本页"]}).to_excel(writer, sheet_name="说明", index=False)
        opening.to_excel(writer, sheet_name="期初", index=False)
    with pd.ExcelWriter(journal_path) as writer:
        pd.DataFrame({"说明": ["不要读取本页"]}).to_excel(writer, sheet_name="说明", index=False)
        journal.to_excel(writer, sheet_name="序时账", index=False)

    loader = DataLoader()
    opening_raw = loader.load_sheet(str(opening_path), "期初")
    journal_raw = loader.load_sheet(str(journal_path), "序时账")
    opening_result = api.filter_current_accounts(
        opening_raw, data_kind="opening", source_label="期初余额/期初",
    )
    journal_result = api.filter_current_accounts(
        journal_raw, data_kind="journal", source_label="序时账/序时账",
    )

    assert set(journal_result.dataframe[api.CATEGORY_COLUMN]) == {
        "应收账款", "预付账款", "其他应收款", "合同资产",
        "应付账款", "预收账款", "其他应付款", "合同负债",
    }
    assert journal_result.original_rows == 11
    assert journal_result.retained_rows == 8
    assert journal_result.ignored_rows == 3
    assert opening_result.recognition_counts["按编码辅助识别"] == 1
    assert not api.find_category_conflicts([opening_result, journal_result])

    results = AgingOrchestrator(
        cutoff_date=date(2026, 12, 31),
        buckets=[(0, 365)],
        opening_date=date(2025, 12, 31),
    ).run(opening_result.dataframe, journal_result.dataframe)

    assert {result.direction for result in results} == {"debit", "credit"}
    assert any(result.detail_name == "（未填明细）" for result in results)
    receivable = next(
        result for result in results
        if result.subject_code == "1122" and result.detail_name == "（未填明细）"
    )
    assert receivable.direction == "debit"
    assert receivable.negative_balance == -400.0

    config = {"buckets": [(0, 365)], "bucket_labels": ["1年以内"]}
    debit_headers = list(ReportGenerator()._build_summary(
        [result for result in results if result.direction == "debit"], config,
    ).columns)
    credit_headers = list(ReportGenerator()._build_summary(
        [result for result in results if result.direction == "credit"], config,
    ).columns)
    expected = [
        "科目编码", "科目名称", "明细名称", "1年以内",
        "反向余额（待核对）", "合计",
    ]
    assert debit_headers == expected
    assert credit_headers == expected
