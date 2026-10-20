# -*- coding: utf-8 -*-
"""数据加载与清洗"""
from __future__ import annotations
import pandas as pd
import re
from typing import Optional


class DataLoader:
    """从 Excel 加载数据、应用列映射、清洗与类型转换"""

    _THOUSANDS_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")

    def list_sheet_names(self, path: str) -> list[str]:
        """返回工作簿中的全部工作表名称。"""
        with pd.ExcelFile(path) as workbook:
            return list(workbook.sheet_names)

    @staticmethod
    def initial_sheet(sheet_names: list[str]) -> str | None:
        """单工作表自动选择，多工作表或空列表交给用户选择。"""
        return sheet_names[0] if len(sheet_names) == 1 else None

    def load_sheet(self, path: str, sheet_name: str) -> pd.DataFrame:
        """按用户明确选择的名称读取一个工作表。"""
        return pd.read_excel(path, sheet_name=sheet_name)

    def apply_mapping(self, df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
        """按 {逻辑字段: 物理列名} 字典重命名列；映射为 None 的字段忽略"""
        rename = {v: k for k, v in mapping.items() if v is not None and v in df.columns}
        return df.rename(columns=rename).copy()

    def clean(self, df: pd.DataFrame, key_column: str) -> pd.DataFrame:
        """删除关键字段为空的行"""
        if key_column not in df.columns:
            return df.copy()
        return df.dropna(subset=[key_column]).copy()

    def convert_types(
        self,
        df: pd.DataFrame,
        amount_columns: Optional[list[str]] = None,
        date_columns: Optional[list[str]] = None,
    ) -> tuple[pd.DataFrame, list[str]]:
        """转换金额列为 float，日期列为 datetime；空值填充 0

        返回 (清洗后的DataFrame, 警告消息列表)。
        金额列在转换前会先去除千分位逗号，转换失败（无法解析为数字）的值
        会被置为 0.0 并在警告中报告。
        """
        result = df.copy()
        warnings: list[str] = []
        for col in amount_columns or []:
            if col in result.columns:
                # 仅对规范千分位格式去逗号，避免把 "1,2,3" 之类脏数据误当成合法金额。
                result[col] = result[col].apply(self._normalize_amount_value)
                # 标记哪些值无法转为数字
                coerced = pd.to_numeric(result[col], errors="coerce")
                bad_mask = coerced.isna() & result[col].notna()
                bad_count = int(bad_mask.sum())
                if bad_count > 0:
                    warnings.append(
                        f"金额列 '{col}' 中有 {bad_count} 个值无法解析为数字，已置为 0"
                    )
                result[col] = coerced.fillna(0.0).astype(float)
        for col in date_columns or []:
            if col in result.columns:
                # format="mixed" 允许同一列混用多种日期写法（pandas>=2 支持）
                converted = pd.to_datetime(result[col], errors="coerce", format="mixed")
                bad_count = int((converted.isna() & result[col].notna()).sum())
                if bad_count > 0:
                    warnings.append(
                        f"日期列 '{col}' 中有 {bad_count} 行日期写法无法识别，已跳过，"
                        "请统一为 2024-01-31 这种格式"
                    )
                result[col] = converted
        return result, warnings

    @classmethod
    def _normalize_amount_value(cls, value):
        """标准化金额文本。

        仅允许规范千分位格式去掉逗号；异常逗号格式保持原样，让后续 numeric 转换失败并报警。
        支持括号负数写法 "(1,234.00)" → "-1234.00"。
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1].strip()
        if "," in text:
            if not cls._THOUSANDS_RE.match(text):
                return value  # 异常逗号格式原样返回，交给后续转换报警
            text = text.replace(",", "")
        return f"-{text}" if negative else text
