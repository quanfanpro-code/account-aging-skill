from datetime import date

import pandas as pd
import pytest

from src.core.workflow import AgingWorkflow


def calculate(rows, cutoff=date(2025, 12, 31)):
    journal = pd.DataFrame(rows, columns=['日期','凭证号','科目编码','科目名称','明细名称','借方金额','贷方金额','摘要'])
    opening = pd.DataFrame(columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])
    flow = AgingWorkflow(cutoff, [(0,365),(366,9999)])
    results = flow.run(opening, journal)
    return flow, results


def row(day, voucher, credit, memo, unit='单位甲'):
    return [date.fromisoformat(day), voucher, '2202', '应付账款', unit, 0, credit, memo]


def test_同一正常科目摘要暂估冲回转正式连续计龄():
    flow, results = calculate([
        row('2023-01-01','记-001',200,'暂估入库'),
        row('2025-06-01','记-002',-100,'红冲暂估入库'),
        row('2025-06-01','记-002',113,'收到正式发票'),
    ])
    assert results[0].bucket_totals == [13,200]
    assert sum(item['承接金额'] for item in flow.transfer_records) == 100
    assert len(flow.temporary_identification_records) == 2


@pytest.mark.parametrize('memo', ['红冲1#001号凭证材料', '冲销23年1#001号凭证', '【冲销 2023-01-01 记-001号凭证】材料'])
def test_未写暂估但明确引用原暂估凭证的冲回可追溯(memo):
    flow, results = calculate([
        row('2023-01-01','记-001',200,'暂估入库'),
        row('2025-06-01','记-002',-100,memo),
        row('2025-06-01','记-002',113,'收到正式发票'),
    ])
    assert results[0].bucket_totals == [13,200]
    record = flow.temporary_identification_records[-1]
    assert record['引用原账行号'] == 2
    assert '原暂估凭证' in record['识别依据']


@pytest.mark.parametrize('memo', ['暂估款付款','暂估采购退货'])
def test_摘要暂估的实际付款退货仍真实核销(memo):
    flow, results = calculate([
        row('2023-01-01','记-001',200,'暂估入库'),
        row('2025-06-01','记-002',-100,memo),
        row('2025-06-01','记-002',113,'收到正式发票'),
    ])
    assert results[0].bucket_totals == [113,100]
    assert not flow.transfer_records


@pytest.mark.parametrize('memo', ['收到暂估业务正式发票','非暂估采购'])
def test_正式发票与否定暂估字样不能当暂估新增(memo):
    flow, results = calculate([
        row('2023-01-01','记-001',200,'暂估入库'),
        row('2025-06-01','记-002',-100,'红冲暂估'),
        row('2025-06-01','记-002',113,memo),
    ])
    assert results[0].bucket_totals == [13,200]
    assert not bool(flow.journal_input.iloc[-1]['_是否暂估'])


def test_引用凭证跨单位或重号年份不明不能猜测为暂估冲回():
    flow, _ = calculate([
        row('2023-01-01','记-001',200,'暂估入库'),
        row('2024-01-01','记-001',200,'暂估入库'),
        row('2025-06-01','记-002',-100,'红冲1#001号凭证'),
        row('2025-06-01','记-003',-100,'红冲23年1#001号凭证','单位乙'),
    ])
    assert list(flow.journal_input['_是否暂估']) == [True,True,False,False]


def test_截止日后暂估不得参与识别记录与承接():
    flow, results = calculate([
        row('2023-01-01','记-001',200,'暂估入库'),
        row('2026-01-01','记-002',-100,'红冲暂估'),
        row('2026-01-01','记-002',113,'收到正式发票'),
    ])
    assert results[0].bucket_totals == [0,200]
    assert len(flow.temporary_identification_records) == 1
    assert not flow.transfer_records


@pytest.mark.parametrize('memo', ['未收到正式发票，暂估入库','暂估采购，尚未取得正式发票'])
def test_未收到发票不能误判成收到正式发票(memo):
    flow, _ = calculate([row('2023-01-01','记-001',200,memo)])
    assert bool(flow.journal_input.iloc[0]['_是否暂估'])
