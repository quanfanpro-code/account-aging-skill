# -*- coding: utf-8 -*-
import pandas as pd
import pytest
from src.core.data_loader import DataLoader


def test_应用列映射_重命名列():
    df = pd.DataFrame({
        "科目编码": ["1122", "1122"],
        "客户名称": ["A", "B"],
        "借方余额": [1000.0, 2000.0],
        "贷方余额": [0.0, 0.0],
    })
    mapping = {"科目编码": "科目编码", "明细名称": "客户名称",
               "借方余额": "借方余额", "贷方余额": "贷方余额"}
    loader = DataLoader()
    result = loader.apply_mapping(df, mapping)
    assert "明细名称" in result.columns
    assert result.iloc[0]["借方余额"] == 1000.0


def test_清洗_去除空行():
    df = pd.DataFrame({
        "科目编码": ["1122", None, "1122"],
        "借方余额": [100.0, 0.0, 200.0],
    })
    loader = DataLoader()
    result = loader.clean(df, key_column="科目编码")
    assert len(result) == 2


def test_类型转换_金额为float():
    df = pd.DataFrame({"借方金额": ["100", "200.5", "0"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, amount_columns=["借方金额"])
    assert result["借方金额"].dtype == float
    assert warnings == []


def test_类型转换_日期为datetime():
    df = pd.DataFrame({"日期": ["2024-01-01", "2024-06-01"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, date_columns=["日期"])
    assert pd.api.types.is_datetime64_any_dtype(result["日期"])


def test_类型转换_空值填充零():
    """金额列的 NaN 应填充为 0.0"""
    df = pd.DataFrame({"借方金额": [None, 100.0]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, amount_columns=["借方金额"])
    assert result["借方金额"].iloc[0] == 0.0


def test_类型转换_千分位逗号自动去除():
    """带千分位逗号的金额应被正确解析"""
    df = pd.DataFrame({"借方金额": ["1,234.56", "10,000.00"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, amount_columns=["借方金额"])
    assert result["借方金额"].iloc[0] == pytest.approx(1234.56)
    assert result["借方金额"].iloc[1] == pytest.approx(10000.00)
    assert warnings == []


def test_类型转换_异常金额报警告():
    """无法解析的金额应被置 0 并产生警告"""
    df = pd.DataFrame({"借方金额": ["abc", 100.0, "xyz"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, amount_columns=["借方金额"])
    assert result["借方金额"].iloc[0] == 0.0
    assert result["借方金额"].iloc[2] == 0.0
    assert len(warnings) == 1
    assert "2" in warnings[0]


def test_类型转换_错误逗号格式不应被误解析():
    """只有规范千分位格式才去逗号，异常逗号格式应报警并置0"""
    df = pd.DataFrame({"借方金额": ["1,2,3", "12,34"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, amount_columns=["借方金额"])
    assert result["借方金额"].tolist() == [0.0, 0.0]
    assert warnings


def test_类型转换_混合格式日期可解析():
    """同一列混用多种日期写法时不应被静默吞掉"""
    df = pd.DataFrame({"日期": ["2024-01-31", "2024/3/1"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, date_columns=["日期"])
    assert result["日期"].notna().all()
    assert warnings == []


def test_类型转换_坏日期警告文案友好():
    """无法识别的日期应给出零基础用户能看懂的中文警告"""
    df = pd.DataFrame({"日期": ["2024-01-31", "乱写的"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, date_columns=["日期"])
    assert len(warnings) == 1
    assert "无法识别" in warnings[0]
    assert "2024-01-31" in warnings[0]


def test_类型转换_括号负数():
    """"(1,234.00)" 括号负数写法应解析为负数"""
    df = pd.DataFrame({"借方金额": ["(1,234.00)", "(500)"]})
    loader = DataLoader()
    result, warnings = loader.convert_types(df, amount_columns=["借方金额"])
    assert result["借方金额"].tolist() == [-1234.0, -500.0]
    assert warnings == []


def test_工作表列表与按名称读取(tmp_path):
    path = tmp_path / "多表.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"标记": ["封面"]}).to_excel(writer, sheet_name="说明", index=False)
        pd.DataFrame({"标记": ["真实序时账"]}).to_excel(writer, sheet_name="序时账", index=False)

    loader = DataLoader()
    assert loader.list_sheet_names(str(path)) == ["说明", "序时账"]
    assert loader.initial_sheet(["说明", "序时账"]) is None
    assert loader.load_sheet(str(path), "序时账").iloc[0]["标记"] == "真实序时账"


def test_单工作表自动选中_空列表不选中():
    assert DataLoader.initial_sheet(["Sheet1"]) == "Sheet1"
    assert DataLoader.initial_sheet([]) is None
