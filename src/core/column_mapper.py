# -*- coding: utf-8 -*-
"""列名智能识别与手动映射 — 长词优先避免误匹配"""
from __future__ import annotations
from typing import Optional
import re

# 字段 → 关键词（按"长词在前、短词在后"排序，确保精确优先）
BALANCE_FIELD_KEYWORDS: dict[str, list[str]] = {
    "科目编码": ["科目编码", "科目代码", "科目编号", "科目"],
    "科目名称": ["科目名称"],
    "明细名称": ["辅助核算", "客户名称", "供应商名称", "客户", "供应商", "明细", "往来户", "对方"],
    "借方余额": ["借方余额", "期初借方余额", "期初借方", "借方"],
    "贷方余额": ["贷方余额", "期初贷方余额", "期初贷方", "贷方"],
}

JOURNAL_FIELD_KEYWORDS: dict[str, list[str]] = {
    "日期": ["记账日期", "业务日期", "日期"],
    "凭证号": ["凭证编号", "凭证号", "凭证"],
    "摘要": ["摘要"],
    "科目编码": ["科目编码", "科目代码", "科目编号", "科目"],
    "科目名称": ["科目名称"],
    "明细名称": ["辅助核算", "客户名称", "供应商名称", "客户", "供应商", "明细", "往来户", "对方"],
    "借方金额": ["借方金额", "借方"],
    "贷方金额": ["贷方金额", "贷方"],
}

REQUIRED_FIELDS = {
    "balance": ["明细名称", "借方余额", "贷方余额"],
    "journal": ["日期", "明细名称", "借方金额", "贷方金额"],
}

REQUIRED_ANY_FIELDS = {
    "balance": [("科目编码", "科目名称")],
    "journal": [("科目编码", "科目名称")],
}

# 匹配某些字段时要跳过的列名子串，防止误匹配：
# - 借方/贷方余额：排除"期末"（期初余额表的实际列名是"借方余额"/"期初借方余额"，不排除"期初"）
# - 科目编码：排除"名称"，防止兜底关键词"科目"抢到"科目名称"列
FIELD_EXCLUDE_SUBSTR: dict[str, list[str]] = {
    "借方余额": ["期末"],
    "贷方余额": ["期末"],
    "科目编码": ["名称"],
}


class ColumnMapper:
    """列名智能匹配器（长词优先）"""

    def __init__(self) -> None:
        self._mapping: dict[str, Optional[str]] = {}

    def auto_map(self, columns: list[str], field_type: str = "journal") -> dict[str, Optional[str]]:
        keywords_map = JOURNAL_FIELD_KEYWORDS if field_type == "journal" else BALANCE_FIELD_KEYWORDS
        result: dict[str, Optional[str]] = {}
        used_columns: set[str] = set()  # 已被占用的物理列，防止同一列被多个字段匹配
        # 每个字段的关键词按长度降序排，长词优先
        for field_name, keywords in keywords_map.items():
            sorted_keywords = sorted(keywords, key=len, reverse=True)
            exclude_substr = FIELD_EXCLUDE_SUBSTR.get(field_name, [])
            matched = self._find_best_match(columns, sorted_keywords, exclude=used_columns,
                                            exclude_substr=exclude_substr)
            result[field_name] = matched
            if matched is not None:
                used_columns.add(matched)
        self._mapping = result
        return result

    def set_mapping(self, field_name: str, column: Optional[str]) -> None:
        self._mapping[field_name] = column

    def get_mapping(self) -> dict[str, Optional[str]]:
        return dict(self._mapping)

    def validate_required(
        self,
        fields: list[str],
        any_of_groups: list[tuple[str, ...]] | None = None,
    ) -> None:
        """校验必填字段是否已映射，未映射抛 ValueError"""
        missing = [f for f in fields if not self._mapping.get(f)]
        for group in any_of_groups or []:
            if not any(self._mapping.get(field) for field in group):
                missing.append("或".join(group))
        if missing:
            raise ValueError(f"以下必填字段未映射: {', '.join(missing)}")

    @staticmethod
    def _find_best_match(
        columns: list[str],
        keywords: list[str],
        exclude: set[str] | None = None,
        exclude_substr: list[str] | None = None,
    ) -> Optional[str]:
        """在列名列表中找到最佳匹配：选匹配关键词最长的列，同长则选列名更长的"""
        exclude = exclude or set()
        exclude_substr = exclude_substr or []
        best_col: Optional[str] = None
        best_keyword_len: int = -1
        best_col_len: int = -1
        for col in columns:
            if col in exclude:
                continue
            if any(sub in col for sub in exclude_substr):
                continue
            col_lower = col.lower()
            for keyword in keywords:  # 调用方已按长度降序排
                if keyword.lower() in col_lower:
                    kw_len = len(keyword)
                    col_len = len(col)
                    # 关键词越长越好；关键词等长时列名越长越好（更精确）
                    if kw_len > best_keyword_len or (kw_len == best_keyword_len and col_len > best_col_len):
                        best_col = col
                        best_keyword_len = kw_len
                        best_col_len = col_len
                    break  # 第一个匹配的关键词已是最长的（因为关键词已排序）
        return best_col

    @staticmethod
    def detect_aging_columns(columns: list[str]) -> list[tuple[tuple[int, int], str]]:
        """从列名列表中检测账龄分档列

        支持的格式：
        - "0-90天"、"90-180天" ... "730天以上" / "730天+" / ">730天"
        - "1年以内"、"1-2年"、"2-3年" ... "N年以上"
        （年按 365 天近似换算，不区分闰年）
        返回: [((lo, hi), column_name), ...] 按区间下限升序排列
        """
        result: list[tuple[tuple[int, int], str]] = []

        for col in columns:
            # 尝试匹配 "数字-数字天" 格式
            m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)\s*天', col)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                result.append(((lo, hi), col))
                continue

            # 尝试匹配 "数字天以上" / "数字天+" / ">数字天"
            m = re.match(r'^[>＞]?\s*(\d+)\s*天[以之]?[上后\+]', col)
            if m:
                lo = int(m.group(1))
                result.append(((lo, 9999), col))
                continue

            # 尝试匹配 "N年以内"
            m = re.match(r'^(\d+)\s*年[以之]?内', col)
            if m:
                years = int(m.group(1))
                result.append(((0, years * 365), col))
                continue

            # 尝试匹配 "N-M年"
            m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)\s*年', col)
            if m:
                lo_y, hi_y = int(m.group(1)), int(m.group(2))
                result.append(((lo_y * 365, hi_y * 365), col))
                continue

            # 尝试匹配 "N年以上"
            m = re.match(r'^(\d+)\s*年[以之]?[上后]', col)
            if m:
                years = int(m.group(1))
                result.append(((years * 365, 9999), col))
                continue

        # 按区间 (下限, 上限) 升序稳定排序
        result.sort(key=lambda x: (x[0][0], x[0][1]))
        return result
