# -*- coding: utf-8 -*-
from datetime import date
import pytest
import pandas as pd
from src.core.orchestrator import AgingOrchestrator


def test_批量计算_按科目明细分组():
    """应对 DataFrame 按 (科目编码, 明细名称) 分组，每组调用 FIFO"""
    # 期初余额
    opening_df = pd.DataFrame({
        "科目编码": ["1122", "1122"],
        "明细名称": ["客户A", "客户B"],
        "借方余额": [1000.0, 2000.0],
        "贷方余额": [0.0, 0.0],
    })
    # 序时账
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1), date(2024, 6, 1)],
        "科目编码": ["1122", "1122"],
        "明细名称": ["客户A", "客户B"],
        "借方金额": [500.0, 0.0],
        "贷方金额": [0.0, 300.0],
        "摘要": ["销售", "回款"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 2
    # 客户A：期初1000+借500=1500
    assert results[0].closing_balance == pytest.approx(1500.0)
    # 客户B：期初2000-贷300=1700
    assert results[1].closing_balance == pytest.approx(1700.0)


def test_批量计算_进度回调():
    """应调用进度回调函数，报告完成百分比"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1)],
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方金额": [500.0],
        "贷方金额": [0.0],
        "摘要": ["销售"],
    })
    progress_log = []

    def on_progress(current: int, total: int) -> None:
        progress_log.append((current, total))

    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
        on_progress=on_progress,
    )
    results = orch.run(opening_df, journal_df)
    assert len(progress_log) >= 1
    assert progress_log[-1] == (1, 1)


def test_批量计算_期末为零仍返回结果():
    """期末余额为 0 的明细也应返回 AgingResult（分档全 0），方便底稿完整展示"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1)],
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方金额": [0.0],
        "贷方金额": [1000.0],
        "摘要": ["全额回款"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    assert results[0].closing_balance == pytest.approx(0.0)


def test_批量计算_传递期初分档():
    """期初余额表含分档列时，应提取并传给 FIFOEngine"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
        "0-90天": [200.0],
        "90-180天": [300.0],
        "180-365天": [500.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1)],
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方金额": [500.0],
        "贷方金额": [0.0],
        "摘要": ["销售"],
    })
    aging_columns = {
        (0, 90): "0-90天",
        (90, 180): "90-180天",
        (180, 365): "180-365天",
    }
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 180), (180, 365)],
        direction="debit",
        opening_date=date(2024, 1, 1),
        opening_aging_columns=aging_columns,
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    # 期初分档应被记录
    assert len(results[0].opening_bucket_totals) == 3
    assert results[0].opening_date == date(2024, 1, 1)


def test_批量计算_序时账新增明细不漏算():
    """只在序时账出现、期初表中不存在的明细应被纳入计算"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [0.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2025, 1, 1)],
        "科目编码": ["1122"],
        "明细名称": ["客户B"],
        "借方金额": [100.0],
        "贷方金额": [0.0],
        "摘要": ["新增客户销售"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 2
    detail_names = {r.detail_name for r in results}
    assert "客户A" in detail_names
    assert "客户B" in detail_names
    # 客户B 的期末余额应为 100
    client_b = next(r for r in results if r.detail_name == "客户B")
    assert client_b.closing_balance == pytest.approx(100.0)


def test_批量计算_NaT日期行被过滤不崩溃():
    """序时账中的无效日期（NaT）应被过滤掉，不会导致 FIFO 崩溃"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [pd.NaT],
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方金额": [100.0],
        "贷方金额": [0.0],
        "摘要": ["坏日期"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    # NaT 行被过滤，期末 = 期初 = 1000
    assert results[0].closing_balance == pytest.approx(1000.0)


def test_批量计算_多行期初分档应求和():
    """同一明细在期初表有多行时，分档列应汇总而非只取首行"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122", "1122"],
        "明细名称": ["客户A", "客户A"],
        "科目名称": ["应收账款", "应收账款"],
        "借方余额": [300.0, 700.0],
        "贷方余额": [0.0, 0.0],
        "0-90天": [300.0, 0.0],
        "90-180天": [0.0, 700.0],
    })
    journal_df = pd.DataFrame({
        "日期": [],
        "科目编码": [],
        "明细名称": [],
        "借方金额": [],
        "贷方金额": [],
        "摘要": [],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 180), (180, 365), (365, 730)],
        direction="debit",
        opening_date=date(2025, 1, 1),
        opening_aging_columns={(0, 90): "0-90天", (90, 180): "90-180天"},
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    # 分档合计应为 [300.0, 700.0]，而非只取首行 [300.0, 0.0]
    assert results[0].opening_bucket_totals == pytest.approx([300.0, 700.0])
    assert results[0].opening_balance == pytest.approx(1000.0)


def test_批量计算_截止日期后的序时账被剔除并告警():
    """晚于截止日期的发生额不属于本期期末余额，应剔除并在 last_warnings 中说明"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2025, 3, 1), date(2025, 12, 31)],  # 第二笔晚于截止日 2025-06-01
        "科目编码": ["1122", "1122"],
        "明细名称": ["客户A", "客户A"],
        "借方金额": [500.0, 9999.0],
        "贷方金额": [0.0, 0.0],
        "摘要": ["期内销售", "期后销售"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    assert len(results) == 1
    # 期末 = 期初1000 + 期内500，期后的 9999 不计入
    assert results[0].closing_balance == pytest.approx(1500.0)
    assert orch.last_stats["dropped_future_rows"] == 1
    assert any("晚于截止日期" in w for w in orch.last_warnings)


def test_批量计算_记录NaT与期初分档差额告警():
    """期初分档与期初余额不平时，应直接报错而不是自动归入更早期初"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "科目名称": ["应收账款"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
        "0-90天": [200.0],
        "90-180天": [300.0],  # 分档合计只有500，少于期初1000
    })
    journal_df = pd.DataFrame({
        "日期": [pd.NaT],
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方金额": [100.0],
        "贷方金额": [0.0],
        "摘要": ["坏日期"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 180), (180, 365)],
        direction="debit",
        opening_date=date(2024, 1, 1),
        opening_aging_columns={(0, 90): "0-90天", (90, 180): "90-180天"},
    )
    with pytest.raises(ValueError) as exc:
        orch.run(opening_df, journal_df)
    assert "期初分档合计" in str(exc.value)


def test_键列数值编码归一化_期初文本拼上序时账浮点():
    """期初表科目编码是文本 "1122"，序时账是数值 1122.0，应归并为同一组"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1)],
        "科目编码": [1122.0],  # Excel 读出来的数值编码
        "明细名称": ["客户A"],
        "借方金额": [500.0],
        "贷方金额": [0.0],
        "摘要": ["销售"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    # 同一客户不被拆成两组，期初不丢失：期末 = 1000 + 500
    assert len(results) == 1
    assert results[0].closing_balance == pytest.approx(1500.0)


def test_明细名称为空的行不丢失且不显示nan():
    """明细名称为空的行应用占位名保留，而不是被 groupby 丢弃或变成 "nan" """
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2024, 6, 1)],
        "科目编码": ["1122"],
        "明细名称": [None],
        "借方金额": [500.0],
        "贷方金额": [0.0],
        "摘要": ["销售"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    results = orch.run(opening_df, journal_df)
    detail_names = {r.detail_name for r in results}
    assert "（未填明细）" in detail_names
    assert "nan" not in detail_names
    empty_row = next(r for r in results if r.detail_name == "（未填明细）")
    assert empty_row.closing_balance == pytest.approx(500.0)


def test_期初分档列含千分位文本不崩且数值正确():
    """自动检测到的分档列含 "1,000" 这类千分位文本时，应清洗后求和而不是 ValueError"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [3000.0],
        "贷方余额": [0.0],
        "0-90天": ["1,000"],
        "90-180天": ["2,000"],
    })
    journal_df = pd.DataFrame({
        "日期": [], "科目编码": [], "明细名称": [],
        "借方金额": [], "贷方金额": [], "摘要": [],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 180), (180, 365)],
        direction="debit",
        opening_date=date(2024, 1, 1),
        opening_aging_columns={(0, 90): "0-90天", (90, 180): "90-180天"},
    )
    results = orch.run(opening_df, journal_df)
    assert results[0].opening_bucket_totals == pytest.approx([1000.0, 2000.0])
    assert results[0].closing_balance == pytest.approx(3000.0)


def test_有分档列但无期初日期_写警告():
    """检测到期初分档列但 opening_date 为空时，分档被忽略且必须写中文警告"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
        "0-90天": [1000.0],
    })
    journal_df = pd.DataFrame({
        "日期": [], "科目编码": [], "明细名称": [],
        "借方金额": [], "贷方金额": [], "摘要": [],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 365)],
        direction="debit",
        opening_date=None,
        opening_aging_columns={(0, 90): "0-90天"},
    )
    results = orch.run(opening_df, journal_df)
    assert any("分档列已忽略" in w for w in orch.last_warnings)
    # 分档被忽略，期初按铁板模式整体计算
    assert results[0].opening_bucket_totals == []


def test_序时账早于期初日期_写警告():
    """序时账日期早于期初日期时，应警告数据时间段可能重叠"""
    opening_df = pd.DataFrame({
        "科目编码": ["1122"],
        "明细名称": ["客户A"],
        "借方余额": [1000.0],
        "贷方余额": [0.0],
    })
    journal_df = pd.DataFrame({
        "日期": [date(2023, 6, 1), date(2024, 6, 1)],  # 第一笔早于期初日期
        "科目编码": ["1122", "1122"],
        "明细名称": ["客户A", "客户A"],
        "借方金额": [100.0, 200.0],
        "贷方金额": [0.0, 0.0],
        "摘要": ["去年旧账", "今年销售"],
    })
    orch = AgingOrchestrator(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
        opening_date=date(2024, 1, 1),
    )
    orch.run(opening_df, journal_df)
    assert orch.last_stats["early_rows"] == 1
    assert any("早于期初余额日期" in w for w in orch.last_warnings)
