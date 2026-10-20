# -*- coding: utf-8 -*-
"""批量调度层 — 按 (科目, 明细) 分组调用 FIFOEngine"""
from __future__ import annotations
from datetime import date
from typing import Callable, Optional
import pandas as pd
from src.core.fifo_engine import FIFOEngine
from src.core.models import AgingResult
from src.core.data_loader import DataLoader
from src.core.direction_resolver import resolve_direction
from src.core.current_accounts import CATEGORY_COLUMN, DIRECTION_COLUMN

ProgressCallback = Optional[Callable[[int, int], None]]


class AgingOrchestrator:
    """按科目+明细分组，批量调用 FIFOEngine"""

    def __init__(
        self,
        cutoff_date: date,
        buckets: list[tuple[int, int]],
        direction: str = "debit",
        on_progress: ProgressCallback = None,
        opening_date: Optional[date] = None,
        opening_aging_columns: Optional[dict[tuple[int, int], str]] = None,
    ) -> None:
        self.cutoff_date = cutoff_date
        self.buckets = buckets
        self.default_direction = direction
        self.on_progress = on_progress
        self.opening_date = opening_date
        self.opening_aging_columns = opening_aging_columns or {}
        self.last_warnings: list[str] = []
        self.last_stats: dict[str, int] = {}
        self.last_direction_log: list[str] = []

    def run(
        self,
        opening_df: pd.DataFrame,
        journal_df: pd.DataFrame,
    ) -> list[AgingResult]:
        """执行批量计算，返回所有分组的 AgingResult"""
        opening_df = opening_df.copy()
        journal_df = journal_df.copy()
        self.last_warnings = []
        self.last_stats = {}
        self.last_direction_log = []
        self._debit_engine = FIFOEngine(
            cutoff_date=self.cutoff_date,
            buckets=self.buckets,
            direction="debit",
        )
        self._credit_engine = FIFOEngine(
            cutoff_date=self.cutoff_date,
            buckets=self.buckets,
            direction="credit",
        )

        # 统一键列类型为 str，防止 int/str 混型导致 sorted() 崩溃；
        # 先去掉数值编码的 ".0" 尾巴（如 1122.0 → 1122），防止期初文本编码和序时账数值编码拼不上；
        # 明细名称为空的行先用占位名填充，防止 astype(str) 变成 "nan" 或 groupby 丢行
        for col in ["科目编码", "明细名称"]:
            for df in (opening_df, journal_df):
                if col not in df.columns:
                    continue
                if col == "明细名称":
                    df[col] = df[col].fillna("（未填明细）")
                df[col] = df[col].astype(str).str.replace(r"^(\d+)\.0+$", r"\1", regex=True)

        # 检测到的期初分档列也纳入金额清洗（可能含 "1,000" 这类千分位文本，直接求和会崩）
        aging_col_names = [c for c in self.opening_aging_columns.values() if c in opening_df.columns]
        if aging_col_names:
            opening_df, aging_warnings = DataLoader().convert_types(
                opening_df, amount_columns=aging_col_names
            )
            self.last_warnings.extend(aging_warnings)

        # 清洗序时账中的无效日期（NaT），避免后续 FIFO 崩溃
        nat_count = 0
        if "日期" in journal_df.columns:
            nat_count = int(journal_df["日期"].isna().sum())
            journal_df = journal_df[journal_df["日期"].notna()].copy()
        self.last_stats["dropped_nat_rows"] = nat_count
        if nat_count > 0:
            self.last_warnings.append(f"序时账中有 {nat_count} 行日期为 NaT，已跳过这些记录")

        # 过滤截止日期之后的序时账（审计口径：截止日后的发生额不属于本期期末余额）
        future_count = 0
        if "日期" in journal_df.columns and not journal_df.empty:
            dates = pd.to_datetime(journal_df["日期"], errors="coerce")
            future_mask = dates.dt.date > self.cutoff_date
            future_count = int(future_mask.sum())
            if future_count > 0:
                journal_df = journal_df[~future_mask].copy()
        self.last_stats["dropped_future_rows"] = future_count
        if future_count > 0:
            self.last_warnings.append(
                f"序时账中有 {future_count} 行日期晚于截止日期 {self.cutoff_date}，已按审计口径剔除"
            )

        # 统计早于期初日期的序时账：FIFO 假设期初最老，时间段重叠会导致同一笔账重复计算
        if self.opening_date and "日期" in journal_df.columns and not journal_df.empty:
            dates = pd.to_datetime(journal_df["日期"], errors="coerce")
            early_count = int((dates.dt.date < self.opening_date).sum())
            self.last_stats["early_rows"] = early_count
            if early_count > 0:
                self.last_warnings.append(
                    f"序时账中有 {early_count} 行日期早于期初余额日期 {self.opening_date}，"
                    "数据时间段可能重叠，同一笔账可能被重复计算，请核对"
                )

        use_category_key = all(CATEGORY_COLUMN in df.columns for df in (opening_df, journal_df))
        group_columns = (
            [CATEGORY_COLUMN, "科目编码", "明细名称"]
            if use_category_key else ["科目编码", "明细名称"]
        )
        opening_groups = (
            {key: group.copy() for key, group in opening_df.groupby(group_columns, sort=False, dropna=False)}
            if not opening_df.empty else {}
        )
        journal_groups = (
            {key: group.copy() for key, group in journal_df.groupby(group_columns, sort=False, dropna=False)}
            if not journal_df.empty else {}
        )

        # 按科目编码汇总期初借方/贷方余额（用于方向判断）
        subject_balance_map: dict[str, tuple[float, float]] = {}
        if not opening_df.empty:
            for subj_code, group in opening_df.groupby("科目编码", sort=False):
                debit_total = float(group["借方余额"].sum())
                credit_total = float(group["贷方余额"].sum())
                subject_balance_map[str(subj_code)] = (debit_total, credit_total)

        # 合并期初和序时账的所有 (科目编码, 明细名称) 组合，确保不漏算
        opening_keys = set(opening_groups.keys())
        journal_keys = set(journal_groups.keys())
        all_keys = sorted(opening_keys | journal_keys)

        # 构建期初分档的原始区间列表（按区间下限升序）
        opening_bucket_ranges: Optional[list[tuple[int, int]]] = None
        if self.opening_aging_columns and self.opening_date:
            opening_bucket_ranges = [
                interval for interval, _col_name
                in sorted(self.opening_aging_columns.items(), key=lambda x: x[0][0])
            ]
        elif self.opening_aging_columns and not self.opening_date:
            # 有分档列但没有期初日期：分档无法偏移，只能按铁板模式整体计算，必须告知用户
            self.last_warnings.append(
                "检测到期初分档列，但未填写期初余额日期，分档列已忽略，"
                "期初余额将按整体（铁板模式）计算账龄"
            )

        total = len(all_keys)
        results: list[AgingResult] = []

        for idx, key in enumerate(all_keys):
            if use_category_key:
                _category, subj_code, detail_name = key
            else:
                subj_code, detail_name = key
            # 获取期初余额（可能不存在）
            opening_rows = opening_groups.get(key, pd.DataFrame())
            txn_df = journal_groups.get(key, pd.DataFrame())

            # 获取科目名称
            if not opening_rows.empty:
                subject_name = (
                    str(opening_rows["科目名称"].iloc[0])
                    if "科目名称" in opening_rows.columns else subj_code
                )
            else:
                subject_name = (
                    str(txn_df["科目名称"].iloc[0])
                    if not txn_df.empty and "科目名称" in txn_df.columns
                    else subj_code
                )

            # 正式过滤数据直接使用统一规则写入的固定方向；旧调用保留兼容判断。
            direction_values = []
            for group in (opening_rows, txn_df):
                if DIRECTION_COLUMN in group.columns:
                    direction_values.extend(group[DIRECTION_COLUMN].dropna().astype(str).unique())
            direction_values = list(dict.fromkeys(direction_values))
            if len(direction_values) == 1:
                direction, basis = direction_values[0], "category"
            else:
                debit_total, credit_total = subject_balance_map.get(subj_code, (0.0, 0.0))
                direction, basis = resolve_direction(
                    subject_code=subj_code,
                    subject_name=subject_name,
                    opening_debit_total=debit_total,
                    opening_credit_total=credit_total,
                    default_direction=self.default_direction,
                )
            self.last_direction_log.append(
                f"{subj_code} {subject_name} -> {'借方' if direction == 'debit' else '贷方'} (依据: {basis})"
            )

            # 按方向算期初余额
            if not opening_rows.empty:
                opening_balance = (
                    opening_rows["借方余额"].sum() - opening_rows["贷方余额"].sum()
                    if direction == "debit"
                    else opening_rows["贷方余额"].sum() - opening_rows["借方余额"].sum()
                )
            else:
                opening_balance = 0.0

            transactions = [
                {
                    "date": row["日期"].date() if hasattr(row["日期"], "date") else row["日期"],
                    "debit": float(row["借方金额"]) if pd.notna(row["借方金额"]) else 0.0,
                    "credit": float(row["贷方金额"]) if pd.notna(row["贷方金额"]) else 0.0,
                    "摘要": str(row.get("摘要", "")),
                    "_转科目借方": float(row.get("_转科目借方", 0)),
                    "_转科目贷方": float(row.get("_转科目贷方", 0)),
                }
                for _, row in txn_df.iterrows()
            ]

            # 提取期初分档（如有）— 多行期初需对分档列求和
            opening_buckets_list: Optional[list[float]] = None
            if self.opening_aging_columns and self.opening_date and not opening_rows.empty:
                opening_buckets_list = []
                # 按分档区间下限升序排列列名
                sorted_aging = sorted(self.opening_aging_columns.items(), key=lambda x: x[0][0])
                for (lo, hi), col_name in sorted_aging:
                    if col_name in opening_rows.columns:
                        # 多行期初：对分档列求和，而非只取首行
                        val = float(opening_rows[col_name].sum())
                        opening_buckets_list.append(val)
                    else:
                        opening_buckets_list.append(0.0)
                opening_bucket_sum = float(sum(opening_buckets_list))
                if abs(opening_bucket_sum - opening_balance) > 0.005:
                    raise ValueError(
                        f"{subj_code}/{detail_name} 的期初分档合计为 {opening_bucket_sum:.2f}，"
                        f"与期初余额 {opening_balance:.2f} 不一致，请先核对上期底稿后再计算"
                    )

            engine = self._debit_engine if direction == "debit" else self._credit_engine
            result = engine.calculate(
                subject_code=subj_code,
                subject_name=subject_name,
                detail_name=detail_name,
                opening_balance=opening_balance,
                transactions=transactions,
                opening_buckets=opening_buckets_list,
                opening_date=self.opening_date,
                opening_bucket_ranges=opening_bucket_ranges,
            )
            results.append(result)

            if self.on_progress:
                self.on_progress(idx + 1, total)

        return results
