from datetime import date
import pandas as pd
import pytest
from src.core.workflow import AgingWorkflow

BUCKETS=[(0,365),(366,9999)]

def opening(rows=()):
    return pd.DataFrame(rows,columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])

def journal(rows=()):
    return pd.DataFrame(rows,columns=['日期','科目编码','科目名称','明细名称','借方金额','贷方金额','摘要'])

def flow():return AgingWorkflow(date(2025,12,31),BUCKETS,opening_date=date(2023,1,1))

def test_计算前必须确认单位名称_合并后按全部历史计算():
    o=opening([['2202','应付账款','测试供应商有限公司\\01-02-0036',0,1025000]])
    j=journal([[date(2025,3,1),'2202','应付账款','测试供应商有限公司',1023400,0,'付款']])
    f=flow();p=f.preflight(o,j);assert len(p.names)==1
    with pytest.raises(ValueError,match='确认'):f.run(o,j)
    pair=p.names[0].key
    result=f.run(o,j,name_decisions={pair:'测试供应商有限公司'})
    assert len(result)==1 and result[0].closing_balance==1600
    assert result[0].subject_name=='应付账款'
    assert result[0].bucket_totals==[0,1600]
    assert o.iloc[0]['明细名称'].endswith('0036')

def test_暂估必须确认归属_不按2204冒认为合同负债():
    o=opening([['2202','应付账款','供应商甲',0,100],['2204','暂估应付款','供应商甲',0,200]])
    j=journal()
    f=flow();p=f.preflight(o,j);assert p.temporary[0].suggested=='应付账款'
    with pytest.raises(ValueError,match='暂估'):f.run(o,j)
    r=f.run(o,j,temporary_assignments={'2204':'应付账款'})
    assert len(r)==1 and r[0].subject_code=='2202' and r[0].closing_balance==300
    assert f.confirmation_records

def test_暂估应收可选择归属或不纳入():
    o=opening([['1199','暂估应收款','客户甲',200,0]])
    f=flow();assert f.preflight(o,journal()).temporary[0].suggested=='应收账款'
    assert f.run(o,journal(),temporary_assignments={'1199':None})==[]
    assert f.run(o,journal(),temporary_assignments={'1199':'合同资产'})[0].subject_name=='合同资产'

def test_应收反向重分类按收款来源日计算账龄并对抵():
    o=opening([['1122','应收账款','客户甲',100,0],['2203','预收账款','客户甲',0,30]])
    j=journal([[date(2025,6,1),'1122','应收账款','客户甲',0,160,'收款']])
    f=flow();r=f.run(o,j);assert len(r)==1
    assert r[0].subject_name=='预收账款' and r[0].direction=='credit'
    assert r[0].closing_balance==90 and r[0].bucket_totals==[60,30]
    assert f.reclassification_records[0]['转入科目']=='预收账款'
    assert f.source_results[0].closing_balance==-60

def test_同一单位成对对抵优先扣老账并保留科目汇总净额():
    o=opening([['1122','应收账款','客户甲',100,0],['2203','预收账款','客户甲',0,60]])
    j=journal([[date(2025,6,1),'1122','应收账款','客户甲',30,0,'销售']])
    f=flow();r=f.run(o,j)
    assert len(r)==1 and r[0].closing_balance==70 and r[0].bucket_totals==[30,40]
    assert sum(e.amount for e in r[0].entries)==70
    assert f.offset_records[0]['对抵金额']==60

def test_未填单位不冒认同一个单位对抵():
    o=opening([['1122','应收账款',None,100,0],['2203','预收账款',None,0,60]])
    r=flow().run(o,journal());assert len(r)==2
    assert sorted(x.closing_balance for x in r)==[60,100]

def test_名称相似允许分别计算_确认只对该批数据生效():
    o=opening([['1122','应收账款','南阳利达光电有限公司',100,0],['1122','应收账款','南洋利达光电有限公司',200,0]])
    f=flow();p=f.preflight(o,journal());assert len(p.names)==1
    assert len(f.run(o,journal(),name_decisions={p.names[0].key:None}))==2
    with pytest.raises(ValueError,match='确认'):flow().run(o,journal())

def test_反向重分类覆盖四对往来及红字():
    pairs=[('1122','应收账款','2203','预收账款'),('1123','预付账款','2202','应付账款'),('1221','其他应收款','2241','其他应付款'),('1486','合同资产','2204','合同负债')]
    for dc,dn,cc,cn in pairs:
        for code,name,target,d,c in [(dc,dn,cn,-50,0),(cc,cn,dn,50,0)]:
            j=journal([[date(2025,6,1),code,name,'单位甲',d,c,'调整']])
            r=flow().run(opening(),j)[0]
            assert r.subject_name==target and r.closing_balance==50 and r.bucket_totals==[50,0]
