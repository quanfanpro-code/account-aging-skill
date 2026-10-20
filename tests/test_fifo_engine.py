# -*- coding: utf-8 -*-
from datetime import date
import pytest
from src.core.fifo_engine import FIFOEngine


# ============ 借方方向 ============
def test_借方FIFO_单笔发生额():
    """期初1000 + 借方500 = 期末1500；500归属365天档，1000归属>365天档"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 500.0, "credit": 0.0},
        ],
    )
    assert result.closing_balance == pytest.approx(1500.0)
    assert result.bucket_totals[0] == pytest.approx(500.0)
    assert result.bucket_totals[1] == pytest.approx(1000.0)
    assert result.total_check == pytest.approx(result.closing_balance)


def test_借方FIFO_期末为零无需追溯():
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 0.0, "credit": 1000.0},
        ],
    )
    assert result.closing_balance == pytest.approx(0.0)
    assert all(t == 0.0 for t in result.bucket_totals)


def test_借方FIFO_多笔按日期降序消耗():
    engine = FIFOEngine(
        cutoff_date=date(2025, 1, 1),
        buckets=[(0, 180), (180, 365), (365, 9999)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2024, 1, 1), "debit": 500.0, "credit": 0.0},
            {"date": date(2024, 7, 1), "debit": 300.0, "credit": 0.0},
            {"date": date(2024, 10, 1), "debit": 200.0, "credit": 0.0},
        ],
    )
    assert result.closing_balance == pytest.approx(1000.0)
    # 最近200 → 0-180天档
    assert result.bucket_totals[0] == pytest.approx(200.0)
    # 中间300 → 180-365天档（2024-07-01距2025-01-01=184天）
    assert result.bucket_totals[1] == pytest.approx(300.0)
    # 最早500 → >365天档
    assert result.bucket_totals[2] == pytest.approx(500.0)


# ============ 贷方方向（关键！） ============
def test_贷方FIFO_消耗贷方发生额():
    """贷方方向时，FIFO消耗贷方发生额，账龄按贷方发生日期算"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="credit",
    )
    result = engine.calculate(
        subject_code="2202", subject_name="应付", detail_name="X",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 0.0, "credit": 500.0},
        ],
    )
    # 期末 = 期初1000 + 贷方发生500 = 1500
    assert result.closing_balance == pytest.approx(1500.0)
    assert result.bucket_totals[0] == pytest.approx(500.0)
    assert result.bucket_totals[1] == pytest.approx(1000.0)


# ============ 边界 ============
def test_借方FIFO_期初为0_全靠发生额():
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 1000.0, "credit": 0.0},
        ],
    )
    assert result.closing_balance == pytest.approx(1000.0)
    assert result.bucket_totals[0] == pytest.approx(1000.0)


def test_借方FIFO_分配额不超过余额():
    """FIFO 分配额应取 min(剩余余额, 该笔同方向发生额)"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 1, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 5000.0, "credit": 0.0},
        ],
    )
    assert result.closing_balance == pytest.approx(5000.0)
    assert result.bucket_totals[0] == pytest.approx(5000.0)
    assert result.total_check == pytest.approx(5000.0)


# ============ 负数（红字冲减）==========
def test_借方FIFO_借方负数冲减():
    """借方负数视为红字冲销：期初1000 + 借方-200 = 期末800"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": -200.0, "credit": 0.0},
        ],
    )
    # 净额：1000 + (-200) = 800
    assert result.closing_balance == pytest.approx(800.0)
    # 800 都归属期初 → 365-730天档
    assert result.bucket_totals[1] == pytest.approx(800.0)


def test_贷方FIFO_贷方负数冲减():
    """贷方负数视为红字冲减：期初1000 + 贷方-100 = 期末900"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="credit",
    )
    result = engine.calculate(
        subject_code="2202", subject_name="应付", detail_name="X",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 0.0, "credit": -100.0},
        ],
    )
    # 净额：1000 + (-100) = 900
    assert result.closing_balance == pytest.approx(900.0)


def test_借方FIFO_红字冲至余额为0():
    """负余额应单列展示，不应并入正常账龄分档"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": -1500.0, "credit": 0.0},
        ],
    )
    assert result.closing_balance == pytest.approx(-500.0)
    assert all(t == 0.0 for t in result.bucket_totals)
    assert result.negative_balance == pytest.approx(-500.0)
    assert result.total_check == pytest.approx(result.closing_balance)


def test_借方FIFO_负余额也应保持分档与期末平衡():
    """负余额应走单列校验，而不是挤进最老账龄档"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": -1500.0, "credit": 0.0},
        ],
        opening_date=date(2024, 1, 1),
    )
    assert result.closing_balance == pytest.approx(-500.0)
    assert sum(result.bucket_totals) == pytest.approx(0.0)
    assert result.negative_balance == pytest.approx(-500.0)
    assert any(entry.amount < 0 for entry in result.entries)


def test_借方FIFO_正负混合净额法():
    """正数销售1000 + 红字冲销-300 = 净额700"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 1000.0, "credit": 0.0},
            {"date": date(2024, 12, 1), "debit": -300.0, "credit": 0.0},
        ],
    )
    # 净额 700
    assert result.closing_balance == pytest.approx(700.0)
    # 正数1000和红字-300都是2024年，账龄都在 <365天档
    assert result.bucket_totals[0] == pytest.approx(700.0)


# ============ 期初分档结构 ============
def test_期初分档_发生额不够消耗_剩余按期初分档填():
    """期初1000(分档: 0-365天=400, 365-730天=600) + 借方500 = 1500
    FIFO先消耗借方500→365天档, 剩余1000按期初分档填→0-365天档400, 365-730天档600"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 500.0, "credit": 0.0},
        ],
        opening_buckets=[400.0, 600.0],  # 按期初分档顺序：0-365天=400, 365-730天=600
        opening_date=date(2024, 1, 1),
    )
    assert result.closing_balance == pytest.approx(1500.0)
    # 期末=1500：借方500(365天档) + 期初400(偏移516天后→516天→365-730天档) + 期初600(偏移516天后→365-730天档)
    # 偏移: cutoff(2025-06-01) - opening(2024-01-01) = 516天
    # 期初0-365天的400, 偏移后=516天→落365-730档
    # 期初365-730天的600, 偏移后=881~1246天→落>730档(只有两档,落入最后一档即365-730)
    # bucket_totals[0] = 500(借方)
    # bucket_totals[1] = 400 + 600 = 1000
    assert result.bucket_totals[0] == pytest.approx(500.0)
    assert result.bucket_totals[1] == pytest.approx(1000.0)
    assert result.total_check == pytest.approx(1500.0)
    # 验证期初分档被记录
    assert result.opening_bucket_totals == [400.0, 600.0]
    assert result.opening_date == date(2024, 1, 1)


def test_期初分档_本期发生额全部消耗_不碰期初():
    """期初1000 + 借方3000 = 期末4000；借方3000消耗掉期末2000后剩余1000→期初
    实际上期末4000, 借方只有3000正数→消耗3000, 剩余1000→期初"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 3000.0, "credit": 0.0},
        ],
        opening_buckets=[400.0, 600.0],
        opening_date=date(2024, 1, 1),
    )
    assert result.closing_balance == pytest.approx(4000.0)
    # 借方3000(365天)消耗, 剩余1000→期初分档
    assert result.bucket_totals[0] == pytest.approx(3000.0)
    assert result.bucket_totals[1] == pytest.approx(1000.0)


def test_期初分档_无期初日期时退化到铁板模式():
    """不传 opening_date 时，剩余余额仍归入单一期初 AgingEntry（向后兼容）"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[{"date": date(2024, 6, 1), "debit": 500.0, "credit": 0.0}],
        # 不传 opening_buckets 和 opening_date
    )
    # 旧行为：期初1000+借500=1500；500入365天档，1000入365-730天档
    assert result.closing_balance == pytest.approx(1500.0)
    assert result.bucket_totals[0] == pytest.approx(500.0)
    assert result.bucket_totals[1] == pytest.approx(1000.0)
    assert result.opening_bucket_totals == []
    assert result.opening_date is None


def test_铁板模式_提供期初日期时使用精确期初账龄():
    """无期初分档时，如果给了期初日期，应直接以该日期计算期初账龄"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 180), (180, 365), (365, 730)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,
        transactions=[],
        opening_date=date(2024, 1, 1),
    )
    assert result.closing_balance == pytest.approx(1000.0)
    assert result.bucket_totals[2] == pytest.approx(1000.0)
    assert result.entries[0].days == (date(2025, 6, 1) - date(2024, 1, 1)).days


def test_期初分档_期初分档不够消耗_剩余归最老档():
    """该场景应在调度层被拦截，不再由引擎默默归入"更早期初"；
    引擎层表现为：分档合计只覆盖各分档金额，差额体现在 total_check 与 closing 的差异上"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 180), (180, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=1000.0,  # 分档合计只有 500，差 500 不允许被默默归最老档
        transactions=[],
        opening_buckets=[200.0, 300.0, 0.0],
        opening_date=date(2024, 1, 1),
    )
    # 分档合计 = 各分档金额之和（不多不少），没有被补到期初余额 1000
    assert sum(result.bucket_totals) == pytest.approx(500.0)
    # 勾稽差异 = 500，正是调度层要拦截的那个差额
    assert result.closing_balance - result.total_check == pytest.approx(500.0)


def test_期初分档_贷方方向():
    """贷方方向同样支持期初分档"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730)],
        direction="credit",
    )
    result = engine.calculate(
        subject_code="2202", subject_name="应付", detail_name="X",
        opening_balance=1000.0,
        transactions=[
            {"date": date(2024, 6, 1), "debit": 0.0, "credit": 500.0},
        ],
        opening_buckets=[400.0, 600.0],
        opening_date=date(2024, 1, 1),
    )
    assert result.closing_balance == pytest.approx(1500.0)
    assert result.bucket_totals[0] == pytest.approx(500.0)   # 贷方发生500
    assert result.bucket_totals[1] == pytest.approx(1000.0)  # 期初分档偏移后


def test_期初分档_中间有零值不中断():
    """期初分档列表中某档为 0 时，应跳过该档继续处理后续非零档"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365), (365, 730), (730, 9999)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=800.0,
        transactions=[],
        opening_buckets=[0.0, 500.0, 300.0],
        opening_date=date(2024, 1, 1),
    )
    assert result.closing_balance == pytest.approx(800.0)
    # 分档合计应等于期末余额（不漏不重）
    assert result.total_check == pytest.approx(800.0)
    # 应有两条 entries（跳过了 0 值档）
    assert len(result.entries) == 2


def test_账龄边界点归前一档():
    """边界天数按前一档归属，避免 0-90 / 90-180 的 90 天歧义"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (90, 180), (180, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2025, 3, 3), "debit": 100.0, "credit": 0.0},  # 恰好90天
        ],
    )
    assert result.bucket_totals[0] == pytest.approx(100.0)
    assert result.bucket_totals[1] == pytest.approx(0.0)


def test_反向列负数_应归一化为当期同方向新增():
    """credit 为负时，在借方口径下应视作 debit 正数，而不是掉进期初"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 20), (20, 40)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2025, 5, 1), "debit": 100.0, "credit": 0.0},
            {"date": date(2025, 5, 15), "debit": 0.0, "credit": -30.0},
        ],
    )
    assert result.closing_balance == pytest.approx(130.0)
    assert result.bucket_totals[0] == pytest.approx(30.0)
    assert result.bucket_totals[1] == pytest.approx(100.0)


def test_期初分档_跨档时整档从老归档():
    """期初分档老化后跨两档时，应整档归入更老的那一档，不按比例拆分"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 1, 2),
        buckets=[(0, 90), (90, 180), (180, 270)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=90.0,
        transactions=[],
        opening_buckets=[90.0],
        opening_date=date(2025, 1, 1),
        opening_bucket_ranges=[(90, 180)],
    )
    assert result.bucket_totals[1] == pytest.approx(0.0)
    assert result.bucket_totals[2] == pytest.approx(90.0)


def test_核销浮点尘埃不产生残留层():
    """3 笔 0.1 被 0.3 回款核销后，不应残留 2.78e-17 的尘埃层"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=0.0,
        transactions=[
            {"date": date(2024, 1, 10), "debit": 0.1, "credit": 0.0},
            {"date": date(2024, 2, 10), "debit": 0.1, "credit": 0.0},
            {"date": date(2024, 3, 10), "debit": 0.1, "credit": 0.0},
            {"date": date(2024, 4, 10), "debit": 0.0, "credit": 0.3},
        ],
    )
    assert result.closing_balance == pytest.approx(0.0, abs=1e-9)
    assert all(abs(v) < 1e-9 for v in result.bucket_totals)
    assert result.entries == []


def test_分档配置缺口_落空时记警告(caplog):
    """天数落在分档缺口里（各档未首尾相接）时，归入最老档并记 warning"""
    import logging
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 90), (180, 365)],  # 91-179 是缺口
        direction="debit",
    )
    with caplog.at_level(logging.WARNING):
        result = engine.calculate(
            subject_code="1122", subject_name="应收", detail_name="A",
            opening_balance=0.0,
            transactions=[{"date": date(2025, 2, 21), "debit": 100.0, "credit": 0.0}],  # 账龄 100 天
        )
    assert result.entries[0].days == 100
    assert result.bucket_totals[1] == pytest.approx(100.0)  # 归入最老档
    assert "缺口" in caplog.text


def test_负期初层使用有效期初日期():
    """负期初余额且未给期初日期时，应与正期初一样回退到序时账最早日期前一天"""
    engine = FIFOEngine(
        cutoff_date=date(2025, 6, 1),
        buckets=[(0, 365)],
        direction="debit",
    )
    result = engine.calculate(
        subject_code="1122", subject_name="应收", detail_name="A",
        opening_balance=-500.0,
        transactions=[{"date": date(2024, 6, 1), "debit": 100.0, "credit": 0.0}],
        # 不传 opening_date
    )
    assert result.negative_balance == pytest.approx(-400.0)
    neg_entry = next(e for e in result.entries if e.amount < 0)
    # 有效期初日期 = 序时账最早日期(2024-06-01)前一天，账龄不应为 0
    assert neg_entry.source_date == date(2024, 5, 31)
    assert neg_entry.days == (date(2025, 6, 1) - date(2024, 5, 31)).days
