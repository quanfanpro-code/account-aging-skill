# -*- coding: utf-8 -*-
"""往来科目固定方向及历史调用兼容判断。"""
from __future__ import annotations

from src.core.current_accounts import classify_current_account

DEBIT_KEYWORDS = [
    "其他应收", "应收账款", "应收", "预付账款", "预付",
    "待摊费用", "长期待摊", "待摊",
]
CREDIT_KEYWORDS = [
    "其他应付", "应付账款", "应付", "预收账款", "预收",
    "预提费用", "预提",
]


def resolve_direction(
    subject_code: str,
    subject_name: str,
    opening_debit_total: float,
    opening_credit_total: float,
    default_direction: str,
) -> tuple[str, str]:
    """八类往来按类别固定方向，其他历史调用保留原回退规则。"""
    match = classify_current_account(subject_code, subject_name)
    if match.status == "retained":
        return str(match.direction), match.basis

    if opening_debit_total > opening_credit_total:
        return "debit", "balance"
    if opening_credit_total > opening_debit_total:
        return "credit", "balance"

    code = str(subject_code).strip()
    if code.startswith("1"):
        return "debit", "code"
    if code.startswith("2"):
        return "credit", "code"

    name = str(subject_name or "")
    for keyword in DEBIT_KEYWORDS:
        if keyword in name:
            return "debit", "name"
    for keyword in CREDIT_KEYWORDS:
        if keyword in name:
            return "credit", "name"
    return default_direction, "default"
