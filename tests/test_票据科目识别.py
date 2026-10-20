from datetime import date
import pandas as pd
import pytest
from src.core.workflow import AgingWorkflow


@pytest.mark.parametrize('code,name,expected',[
    ('1121','应收账款','应收账款'),
    ('1122','应收票据','应收票据'),
    (None,'应收票据','应收票据'),
])
def test_名称优先且票据只能计入一次(code,name,expected):
    row={'日期':date(2025,1,1),'科目名称':name,'明细名称':'客户甲','借方金额':100,'贷方金额':0}
    if code is not None:row['科目编码']=code
    opening=pd.DataFrame(columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])
    results=AgingWorkflow(date(2025,12,31),[(0,365),(366,9999)]).run(
        opening,pd.DataFrame([row]),include_receivable_bills=True)
    assert len(results)==1 and results[0].subject_name==expected and results[0].closing_balance==100


def test_有凭证号但跨日明示转票据不得默默按新款重算():
    journal=pd.DataFrame([
        [date(2023,1,1),'销售','1122','应收账款','客户甲',100,0,''],
        [date(2025,6,1),'记-001','1122','应收账款','客户甲',0,100,'应收转票据'],
        [date(2025,6,2),'记-002','1121','应收票据','客户甲',100,0,'商业承兑汇票'],
    ],columns=['日期','凭证号','科目编码','科目名称','明细名称','借方金额','贷方金额','摘要'])
    opening=pd.DataFrame(columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])
    with pytest.raises(ValueError,match='跨日关联当前不支持'):
        AgingWorkflow(date(2025,12,31),[(0,365),(366,9999)]).run(opening,journal,include_receivable_bills=True)
