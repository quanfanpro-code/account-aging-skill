from datetime import date
import pandas as pd
import pytest
from src.core.workflow import AgingWorkflow
from src.core.current_accounts import ACCOUNT_RULES
from src.core.report import ReportGenerator

def journal(rows):
    return pd.DataFrame(rows,columns=['日期','科目编码','科目名称','明细名称','借方金额','贷方金额','摘要'])

def opening(rows=()):
    return pd.DataFrame(rows,columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])

def flow(cutoff=date(2025,12,31)):
    return AgingWorkflow(cutoff,[(0,365),(366,9999)])

@pytest.mark.parametrize('name,code,direction',ACCOUNT_RULES)
@pytest.mark.parametrize('nested',[False,True])
def test_八类各层级暂估无单号无凭证号仍FIFO保留两批原账龄(name,code,direction,nested):
    tempcode=code+'02' if nested else '1999'
    tempname=name+'-暂估' if nested else '暂估'+name
    def inc(n):return (n,0) if direction=='debit' else (0,n)
    def dec(n):return (0,n) if direction=='debit' else (n,0)
    j=journal([
        [date(2023,3,1),tempcode,tempname,'单位甲',*inc(200),'暂估业务'],
        [date(2025,2,1),tempcode,tempname,'单位甲',*inc(100),'暂估业务'],
        [date(2025,5,1),code,name,'单位甲',*dec(60),'实际收付款'],
        [date(2025,8,3),tempcode,tempname,'单位甲',*dec(40),'转正式'],
        [date(2025,8,4),tempcode,tempname,'单位甲',*dec(70),'转正式'],
        [date(2025,8,5),code+'01',name+'-正式','单位甲',*inc(60),'正式入账'],
        [date(2025,8,6),code+'01',name+'-正式','单位甲',*inc(50),'正式入账'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={tempcode:name})
    assert r[0].closing_balance==240 and r[0].bucket_totals==[100,140]
    assert {e.source_date:e.amount for e in r[0].entries}=={date(2023,3,1):140,date(2025,2,1):100}
    assert [x['承接金额'] for x in f.transfer_records]==[40,20,50]
    assert all('FIFO' in x['承接规则'] for x in f.transfer_records)
    assert f.source_results[0].total_debit==(410 if direction=='debit' else 170)
    assert f.source_results[0].total_credit==(170 if direction=='debit' else 410)
    assert '_业务单号' not in f.journal_input.columns
    frames=dict(ReportGenerator().build_frames(r,{'buckets':[(0,365),(366,9999)],'transfer_records':f.transfer_records}))
    assert sum(frames['内部转科目记录']['承接金额'])==110

def test_红字部分转入差额继续计龄_实际付款核销最老余额():
    j=journal([
        [date(2023,3,1),'1999','暂估应付款','单位甲',0,200,'业务'],
        [date(2025,8,1),'1999','暂估应付款','单位甲',0,-100,'冲暂估'],
        [date(2025,8,3),'2202','应付账款','单位甲',0,113,'正式入账'],
        [date(2025,8,4),'2202','应付账款','单位甲',60,0,'付款'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].closing_balance==153 and r[0].bucket_totals==[13,140]
    assert f.transfer_records[0]['承接金额']==100

@pytest.mark.parametrize('memo',['普通采购付款','采购退货','退款'])
def test_直接暂估付款或退货按FIFO真实核销(memo):
    j=journal([
        [date(2023,3,1),'1999','暂估应付款','单位甲',0,200,'业务'],
        [date(2025,8,1),'1999','暂估应付款','单位甲',100,0,memo],
        [date(2025,8,2),'2202','应付账款','单位甲',0,100,'正式入账'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].bucket_totals==[100,100] and not f.transfer_records

def test_单据字段内容不影响FIFO承接():
    j=journal([
        [date(2023,3,1),'1999','暂估应付款','单位甲',0,200,'业务'],
        [date(2025,8,1),'1999','暂估应付款','单位甲',100,0,'冲暂估/AAA'],
        [date(2025,8,2),'2202','应付账款','单位甲',0,100,'正式入账/BBB'],
    ])
    j['业务单号']=['完全不同甲','完全不同乙','完全不同丙']
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].bucket_totals==[0,200] and f.transfer_records[0]['承接金额']==100

def test_截止日后的转入不能提前抵消_之后FIFO承接():
    j=journal([
        [date(2023,3,1),'1999','暂估应付款','单位甲',0,200,'业务'],
        [date(2025,12,31),'1999','暂估应付款','单位甲',100,0,'冲暂估'],
        [date(2026,1,2),'2202','应付账款','单位甲',0,100,'正式入账'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].closing_balance==100 and not f.transfer_records
    f=flow(date(2026,1,31));r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].bucket_totals==[0,200] and f.transfer_records[0]['承接金额']==100

def test_转暂估以前的旧年度正式业务不参与转入():
    j=journal([
        [date(2023,3,1),'2202','应付账款','单位甲',0,100,'业务'],
        [date(2024,3,1),'1999','暂估应付款','单位甲',0,200,'暂估业务'],
        [date(2025,8,1),'1999','暂估应付款','单位甲',100,0,'冲暂估'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].bucket_totals==[0,200] and not f.transfer_records

def test_同月先正式入账后冲暂估保持FIFO日期():
    j=journal([
        [date(2023,3,1),'1999','暂估应付款','单位甲',0,200,'业务'],
        [date(2025,8,1),'2202','应付账款','单位甲',0,100,'正式入账'],
        [date(2025,8,31),'1999','暂估应付款','单位甲',100,0,'冲暂估'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert r[0].bucket_totals==[0,200]

def test_不同单位不互相承接():
    j=journal([
        [date(2023,3,1),'1999','暂估应付款','单位甲',0,200,'业务'],
        [date(2025,8,1),'1999','暂估应付款','单位甲',100,0,'冲暂估'],
        [date(2025,8,2),'2202','应付账款','单位乙',0,100,'正式入账'],
    ])
    f=flow();r=f.run(opening(),j,temporary_assignments={'1999':'应付账款'})
    assert {x.detail_name:x.bucket_totals for x in r}=={'单位甲':[0,100],'单位乙':[100,0]}
    assert not f.transfer_records

def test_反向余额重分类同样保留期初原账龄():
    o=opening([['2202','应付账款','单位甲',200,0]])
    j=journal([
        [date(2025,8,1),'1999','暂估应付款','单位甲',100,0,'转正式'],
        [date(2025,8,2),'2202','应付账款','单位甲',0,100,'正式入账'],
    ])
    f=AgingWorkflow(date(2025,12,31),[(0,365),(366,9999)],opening_date=date(2023,1,1))
    r=f.run(o,j,temporary_assignments={'1999':'应付账款'})
    assert r[0].subject_name=='预付账款' and r[0].bucket_totals==[0,200]
