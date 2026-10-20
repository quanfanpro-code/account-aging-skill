from datetime import date
import pandas as pd
import pytest
from src.core.workflow import AgingWorkflow

def frames(name='暂估预付款'):
    opening=pd.DataFrame([['1199',name,'单位甲',100,0]],columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])
    journal=pd.DataFrame(columns=['日期','科目编码','科目名称','明细名称','借方金额','贷方金额'])
    return opening,journal

@pytest.mark.parametrize('name,target',[('暂估预付款','预付账款'),('暂估预收款','预收账款'),('暂估合同资产','合同资产'),('暂估合同负债','合同负债'),('暂估其他应收款','其他应收款'),('暂估其他应付款','其他应付款')])
def test_其他暂估往来也必须由用户确认(name,target):
    o,j=frames(name);f=AgingWorkflow(date(2025,12,31),[(0,365)])
    assert f.preflight(o,j).temporary[0].suggested==target
    with pytest.raises(ValueError,match='暂估'):f.run(o,j)

def test_仅摘要提到暂估不会触发科目归属确认():
    o,j=frames('应收账款');o['科目编码']='1122';o['摘要']='暂估应付款转入'
    assert not AgingWorkflow.preflight(o,j).temporary

def test_不同编号的研究所不提示为同一单位():
    o,j=frames('应收账款');o['科目编码']='1122'
    o=pd.concat([o,o],ignore_index=True);o['明细名称']=['中国集团第七一七研究所','中国集团第七〇七研究所']
    assert not AgingWorkflow.preflight(o,j).names
