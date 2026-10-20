# -*- coding: utf-8 -*-
"""核心数据模型 — 用 dataclass 显式定义，pytest 断言以此为基准"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class AgingEntry:
    """FIFO 追溯过程中的一笔账龄条目"""
    source: str                # "期初余额" 或 交易日期的ISO字符串
    source_date: Optional[date]  # None 表示期初
    amount: float              # 该笔分配到余额的金额
    days: int                  # 账龄天数
    bucket_index: int          # 落到的账龄分档下标；-1 表示负余额单列
    reclassified_from: str = ""  # 本笔款项重分类前的科目；普通款项留空
    reclassification_total: float = 0.0  # 原分户反向余额总额，对抵前口径
    bill_transfer_note: str = ""  # 应收与票据之间转科目时保留的连续计龄路径


@dataclass
class AgingResult:
    """单个 (科目, 明细) 组合的账龄分析结果"""
    subject_code: str
    subject_name: str
    detail_name: str
    direction: str             # "debit" 或 "credit"
    opening_balance: float
    total_debit: float
    total_credit: float
    closing_balance: float
    entries: list[AgingEntry] = field(default_factory=list)
    bucket_totals: list[float] = field(default_factory=list)
    negative_balance: float = 0.0  # 负余额单列展示，不参与正常账龄分档
    opening_bucket_totals: list[float] = field(default_factory=list)  # 期初已有分档结构（偏移前）
    opening_date: Optional[date] = None  # 期初分档的计算日期

    @property
    def total_check(self) -> float:
        """各分档合计应等于期末余额（用于审计底稿平衡校验）"""
        return sum(self.bucket_totals) + self.negative_balance
