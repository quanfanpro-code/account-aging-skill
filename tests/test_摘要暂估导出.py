from datetime import date
import importlib.util
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook


def test_实际导出保留暂估识别及输入补充依据(tmp_path):
    spec=importlib.util.spec_from_file_location('temporary_cli',Path(__file__).resolve().parents[1]/'scripts/计算往来账龄.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    source=tmp_path/'原始序时账.xlsx'
    journal=pd.DataFrame([
        [date(2023,1,1),'记-001','2202','应付账款','单位甲',0,200,'暂估入库'],
        [date(2025,6,1),'记-002','2202','应付账款','单位甲',0,-100,'红冲23年1#001号凭证'],
        [date(2025,6,1),'记-002','2202','应付账款','单位甲',0,113,'收到正式发票'],
    ],columns=['日期','凭证号','科目编码','科目名称','明细名称','借方金额','贷方金额','摘要'])
    journal['输入补充依据']='原单位为空，依据本行摘要明确名称补入单位甲'
    journal['原科目名称']='应付账款/单位甲'
    journal.to_excel(source,sheet_name='序时账',index=False)
    output=tmp_path/'最终账龄.xlsx'
    result=cli.execute({'input':str(source),'journal_sheet':'序时账','zero_opening':True,
        'cutoffs':['2025-12-31'],'include_receivable_bills':False,'output':str(output)})
    assert result['status']=='complete'
    book=load_workbook(output,data_only=True)
    def records(title):
        values=iter(book[title].values);headers=next(values)
        return [dict(zip(headers,row)) for row in values]
    identified=records('暂估识别依据')
    assert len(identified)==2 and identified[-1]['引用原账行号']==2
    inputs=records('计算用序时账')
    assert inputs[0]['输入补充依据']==journal.iloc[0]['输入补充依据']
    assert inputs[0]['原科目名称']=='应付账款/单位甲'
    assert records('内部转科目记录')[0]['承接金额']==100
    book.close()
