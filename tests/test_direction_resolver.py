# -*- coding: utf-8 -*-
"""借贷方向判断测试"""
from src.core.direction_resolver import resolve_direction


def test_余额借方大于贷方判为借方():
    direction, basis = resolve_direction("1122", "应收账款", 1000.0, 200.0, "debit")
    assert direction == "debit"
    assert basis in ("name", "code")


def test_余额贷方大于借方判为贷方():
    direction, basis = resolve_direction("2202", "应付账款", 100.0, 800.0, "debit")
    assert direction == "credit"
    assert basis in ("name", "code")


def test_余额相等降级到编码首位():
    direction, basis = resolve_direction("1122", "应收账款", 0.0, 0.0, "debit")
    assert direction == "debit"
    assert basis in ("name", "code")


def test_编码2开头判为贷方():
    direction, basis = resolve_direction("2202", "应付账款", 0.0, 0.0, "debit")
    assert direction == "credit"
    assert basis in ("name", "code")


def test_编码非12降级到名称关键词():
    direction, basis = resolve_direction("9999", "应收账款", 0.0, 0.0, "debit")
    assert direction == "debit"
    assert basis == "name"


def test_名称含应付判为贷方():
    direction, basis = resolve_direction("9999", "应付账款", 0.0, 0.0, "debit")
    assert direction == "credit"
    assert basis == "name"


def test_名称含其他应收长词优先():
    direction, basis = resolve_direction("9999", "其他应收款", 0.0, 0.0, "credit")
    assert direction == "debit"
    assert basis == "name"


def test_三维度都失败用默认方向():
    direction, basis = resolve_direction("9999", "清算资金", 0.0, 0.0, "debit")
    assert direction == "debit"
    assert basis == "default"


def test_应收账款反向余额仍归借方():
    direction, basis = resolve_direction("1122", "应收账款", 100.0, 500.0, "debit")
    assert direction == "debit"
    assert basis in ("name", "code")


def test_合同资产和合同负债使用固定方向():
    assert resolve_direction("1486", "合同资产", 0.0, 900.0, "credit")[0] == "debit"
    assert resolve_direction("2204", "合同负债", 900.0, 0.0, "debit")[0] == "credit"
