# -*- coding: utf-8 -*-
"""FIFO 账龄计算引擎"""
from __future__ import annotations
import logging
from collections import deque
from datetime import date, timedelta
from typing import Optional
from src.core.models import AgingResult, AgingEntry

logger = logging.getLogger(__name__)

# 核销收尾的浮点容差：小于此值的残差视为 0，避免 0.1+0.1+0.1-0.3 残留 2.78e-17 的尘埃层
_EPS = 1e-9


class FIFOEngine:
    """先进先出法账龄计算引擎

    direction="debit": 应收/预付/其他应收，用借方发生额消耗余额
    direction="credit": 应付/预收/其他应付，用贷方发生额消耗余额
    """

    def __init__(
        self,
        cutoff_date: date,
        buckets: list[tuple[int, int]],
        direction: str = "debit",
    ) -> None:
        if direction not in ("debit", "credit"):
            raise ValueError(f"direction 必须为 debit 或 credit, 得到 {direction}")
        self.cutoff_date = cutoff_date
        self.buckets = sorted(buckets, key=lambda b: b[0])
        self.direction = direction
        self._effective_buckets = self._build_effective_buckets()

    def calculate(
        self,
        subject_code: str,
        subject_name: str,
        detail_name: str,
        opening_balance: float,
        transactions: list[dict],
        opening_buckets: Optional[list[float]] = None,
        opening_date: Optional[date] = None,
        opening_bucket_ranges: Optional[list[tuple[int, int]]] = None,
    ) -> AgingResult:
        # 净额法：sum 直接累加（含负数），红字自然抵消
        total_debit = sum(t["debit"] for t in transactions)
        total_credit = sum(t["credit"] for t in transactions)
        if self.direction == "debit":
            closing_balance = opening_balance + total_debit - total_credit
        else:
            closing_balance = opening_balance + total_credit - total_debit

        # 先做红字归一化：例如借方口径下 credit=-30 应视作 debit=30。
        normalized_transactions = self._normalize_amounts(transactions)
        # 内部转科目只改变挂账位置。归集后等额两腿不参与账龄层的核销和新增，原批次逐笔保留。
        if abs(sum(float(t.get("_转科目借方", 0)) - float(t.get("_转科目贷方", 0)) for t in normalized_transactions)) > .005:
            raise ValueError("内部转科目借贷抵消金额不平衡")
        for txn in normalized_transactions:
            for key, marker in [("debit", "_转科目借方"), ("credit", "_转科目贷方")]:
                moved = float(txn.get(marker, 0))
                if moved < 0 or moved - txn[key] > .005:
                    raise ValueError("内部转科目金额超过原发生额")
                txn[key] = max(0.0, txn[key] - moved)
        increase_key = "debit" if self.direction == "debit" else "credit"
        decrease_key = "credit" if self.direction == "debit" else "debit"
        sorted_txns = sorted(normalized_transactions, key=lambda t: t["date"])

        # 铁板模式且无期初日期时，用序时账最早日期的前一天作为默认期初（正负期初统一口径）
        effective_opening_date = opening_date
        if effective_opening_date is None and not opening_buckets and opening_balance != 0:
            earliest_txn = min((t["date"] for t in normalized_transactions), default=None)
            if earliest_txn is not None:
                effective_opening_date = earliest_txn - timedelta(days=1)
                logger.info(
                    f"铁板模式无期初日期，使用序时账最早日期前一天作为默认期初: {effective_opening_date}"
                )

        positive_layers = deque(self._build_opening_positive_layers(
            opening_balance=opening_balance,
            opening_buckets=opening_buckets,
            opening_date=effective_opening_date,
            opening_bucket_ranges=opening_bucket_ranges,
        ))
        negative_layers = deque(self._build_opening_negative_layers(
            opening_balance=opening_balance,
            opening_date=effective_opening_date,
        ))

        for txn in sorted_txns:
            increase_amount = float(txn.get(increase_key, 0.0))
            decrease_amount = float(txn.get(decrease_key, 0.0))
            if increase_amount > 0:
                increase_amount = self._offset_negative_layers(negative_layers, increase_amount)
                if increase_amount > 0:
                    positive_layers.append({
                        "kind": "txn",
                        "source": txn["date"].isoformat(),
                        "source_date": txn["date"],
                        "amount": increase_amount,
                    })
            if decrease_amount > 0:
                decrease_amount = self._consume_positive_layers(positive_layers, decrease_amount)
                if decrease_amount > 0:
                    negative_layers.append({
                        "source": txn["date"].isoformat(),
                        "source_date": txn["date"],
                        "amount": decrease_amount,
                    })

        bucket_totals = [0.0] * len(self.buckets)
        entries: list[AgingEntry] = []
        earliest_txn_date: Optional[date] = min((t["date"] for t in normalized_transactions), default=None)

        for layer in positive_layers:
            amount = float(layer["amount"])
            if amount <= 0:
                continue
            if layer["kind"] == "opening_bucket":
                lo, hi = layer["opening_range"]
                shift_days = (self.cutoff_date - layer["source_date"]).days
                shifted_lo = lo + shift_days
                shifted_hi = hi + shift_days
                bucket_idx = self._find_oldest_overlap_bucket(shifted_lo, shifted_hi)
                days = shifted_hi
            else:
                layer_date = layer.get("source_date")
                if layer_date is not None:
                    days = (self.cutoff_date - layer_date).days
                else:
                    base_date = (
                        earliest_txn_date - timedelta(days=1)
                        if earliest_txn_date is not None else self.cutoff_date
                    )
                    days = (self.cutoff_date - base_date).days
                bucket_idx = self._find_bucket(days)
            entries.append(AgingEntry(
                source=layer["source"],
                source_date=layer.get("source_date"),
                amount=amount,
                days=days,
                bucket_index=bucket_idx,
            ))
            bucket_totals[bucket_idx] += amount

        negative_balance = 0.0
        for layer in negative_layers:
            amount = float(layer["amount"])
            if amount <= 0:
                continue
            negative_balance -= amount
            layer_date = layer.get("source_date")
            days = (self.cutoff_date - layer_date).days if layer_date is not None else 0
            entries.append(AgingEntry(
                source=layer["source"],
                source_date=layer_date,
                amount=-amount,
                days=days,
                bucket_index=-1,
            ))

        return AgingResult(
            subject_code=subject_code, subject_name=subject_name,
            detail_name=detail_name, direction=self.direction,
            opening_balance=opening_balance,
            total_debit=total_debit, total_credit=total_credit,
            closing_balance=closing_balance,
            entries=entries, bucket_totals=bucket_totals,
            negative_balance=negative_balance,
            opening_bucket_totals=list(opening_buckets) if opening_buckets else [],
            opening_date=opening_date,
        )

    def _find_bucket(self, days: int) -> int:
        """找到包含 days 的分档下标。

        规则：边界点归前档。例如分档为 0-90、90-180 时，90 天归入前一档。
        """
        for idx, (lo, hi) in enumerate(self._effective_buckets):
            if lo <= days <= hi:
                return idx
        if days < self._effective_buckets[0][0]:
            return 0
        last_hi = self._effective_buckets[-1][1]
        if days > last_hi:
            # 超出最老档上限，归入最老档并记录警告
            logger.warning(f"天数 {days} 超出最老分档上限 {last_hi} 天，归入最老档")
        else:
            # 落在分档配置的缺口里（各档区间未首尾相接），同样归入最老档并记录警告
            logger.warning(f"天数 {days} 不在任何分档区间内（分档配置存在缺口），归入最老档")
        return len(self.buckets) - 1

    def _build_effective_buckets(self) -> list[tuple[int, int]]:
        """构建去歧义后的有效分档区间。

        原始配置允许相邻分档首尾相接（如 0-90, 90-180）。
        为避免边界点落入两档，这里统一解释为“边界点归前档”，
        即第二档的有效起点会从上一档上限 + 1 开始。
        """
        effective: list[tuple[int, int]] = []
        last_hi: Optional[int] = None
        for lo, hi in self.buckets:
            eff_lo = lo if last_hi is None else max(lo, last_hi + 1)
            effective.append((eff_lo, hi))
            last_hi = hi
        return effective

    def _find_oldest_overlap_bucket(self, shifted_lo: int, shifted_hi: int) -> int:
        """整档顺延时采用审计保守口径：跨档即整体归入更老的重叠档。"""
        overlap_indexes: list[int] = []
        for idx, (bucket_lo, bucket_hi) in enumerate(self._effective_buckets):
            if max(shifted_lo, bucket_lo) <= min(shifted_hi, bucket_hi):
                overlap_indexes.append(idx)
        if overlap_indexes:
            return max(overlap_indexes)
        return self._find_bucket(shifted_hi)

    @staticmethod
    def _normalize_amounts(transactions: list[dict]) -> list[dict]:
        """规范化金额：把负数红字改写到对方列，保留真实业务方向。"""
        normalized = []
        for t in transactions:
            debit = float(t.get("debit", 0.0) or 0.0)
            credit = float(t.get("credit", 0.0) or 0.0)
            if debit < 0:
                credit += -debit
                debit = 0.0
            if credit < 0:
                debit += -credit
                credit = 0.0
            normalized.append({
                **t,
                "debit": debit,
                "credit": credit,
            })
        return normalized

    def _build_opening_positive_layers(
        self,
        opening_balance: float,
        opening_buckets: Optional[list[float]],
        opening_date: Optional[date],
        opening_bucket_ranges: Optional[list[tuple[int, int]]],
    ) -> list[dict]:
        layers: list[dict] = []
        if opening_balance <= 0:
            return layers
        if opening_buckets and opening_date is not None:
            ranges = opening_bucket_ranges or self.buckets[:len(opening_buckets)]
            paired = sorted(
                zip(ranges, opening_buckets),
                key=lambda item: item[0][1],
                reverse=True,
            )
            for (lo, hi), amount in paired:
                if amount <= 0:
                    continue
                layers.append({
                    "kind": "opening_bucket",
                    "source": f"期初余额({lo}-{hi}天)",
                    "source_date": opening_date,
                    "amount": float(amount),
                    "opening_range": (lo, hi),
                })
            return layers
        layers.append({
            "kind": "opening_single",
            "source": "期初余额",
            "source_date": opening_date,
            "amount": float(opening_balance),
        })
        return layers

    @staticmethod
    def _build_opening_negative_layers(
        opening_balance: float,
        opening_date: Optional[date],
    ) -> list[dict]:
        if opening_balance >= 0:
            return []
        return [{
            "source": "期初负余额",
            "source_date": opening_date,
            "amount": float(-opening_balance),
        }]

    @staticmethod
    def _consume_positive_layers(layers: deque, amount: float) -> float:
        remaining = amount
        while remaining > _EPS and layers:
            head = layers[0]
            available = float(head["amount"])
            if available <= remaining + _EPS:
                # 整层消耗（含浮点尘埃），残差小于容差时视为核销干净
                remaining = max(remaining - available, 0.0)
                layers.popleft()
            else:
                head["amount"] = available - remaining
                remaining = 0.0
        return remaining

    @staticmethod
    def _offset_negative_layers(layers: deque, amount: float) -> float:
        remaining = amount
        while remaining > _EPS and layers:
            head = layers[0]
            outstanding = float(head["amount"])
            if outstanding <= remaining + _EPS:
                remaining = max(remaining - outstanding, 0.0)
                layers.popleft()
            else:
                head["amount"] = outstanding - remaining
                remaining = 0.0
        return remaining
