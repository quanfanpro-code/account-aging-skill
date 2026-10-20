"""同一单位的暂估与正式往来，按FIFO承接内部转科目金额。"""
from collections import defaultdict
import re
import pandas as pd
from src.core.current_accounts import CATEGORY_COLUMN,DIRECTION_COLUMN,BLANK_DETAIL
from src.core.preflight import text
from src.core.fifo_engine import FIFOEngine

TRANSFER_COLUMNS=['单位','归集科目','暂估科目','正式科目','暂估日期','暂估凭证','正式日期','正式凭证','承接金额','承接规则','暂估来源','正式来源']
PAYMENT_OR_RETURN=re.compile(r'退货|退回|退款|银行|现金|(?<!应)(?<!预)[收付](?:货)?款|支付|汇款|付.*货款|收.*货款')

def identify_temporary(journal,cutoff):
    """摘要补足暂估身份；引用冲回须同单位、同科目且唯一指向原暂估凭证。"""
    result=journal.copy().reset_index(drop=True)
    records=[];events=[]
    for index,row in result.iterrows():
        day=pd.to_datetime(row.get('日期'),errors='coerce')
        if pd.isna(day) or day.date()>cutoff:continue
        normalized=FIFOEngine._normalize_amounts([{'debit':row.get('借方金额',0),'credit':row.get('贷方金额',0)}])[0]
        inc,dec=('debit','credit') if row[DIRECTION_COLUMN]=='debit' else ('credit','debit')
        memo=text(row.get('摘要'))
        event={'index':index,'row':row,'day':day.date(),'memo':memo,
            'inc':normalized[inc],'dec':normalized[dec],'basis':'','reference':''}
        if bool(row.get('_是否暂估',False)):
            event['basis']='原科目名称明确为暂估'
        elif '暂估' in memo and not re.search(r'非暂估|不暂估|未暂估|无需暂估',memo):
            # 收到发票转正式的新增即使提到旧暂估，也不能再次标成暂估。
            formal=(event['inc']>0 and re.search(r'正式|收到.*发票|收.*票.*入账',memo)
                and not re.search(r'未(?:收|取|转)|尚未|未到票|发票未到',memo))
            if not formal:event['basis']='摘要明确为暂估或暂估冲回'
        events.append(event)
    originals=[event for event in events if event['basis'] and event['inc']>0
        and not PAYMENT_OR_RETURN.search(event['memo'])]
    for event in events:
        if event['basis'] or event['dec']<=0 or PAYMENT_OR_RETURN.search(event['memo']):continue
        memo=event['memo']
        if not re.search(r'红冲|冲销|冲回|冲减',memo):continue
        full=re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})\s*([^\s\d]+)-0*(\d+)号?凭证',memo)
        short=re.search(r'(?:(\d{2,4})年)?(\d{1,2})(?:月)?#0*(\d+)',memo)
        if full:
            year,month,day,word,number=full.groups()
            reference=(int(year),int(month),int(day),word,int(number))
        elif short:
            year,month,number=short.groups()
            reference=(int(year)+(2000 if len(year)==2 else 0) if year else None,int(month),None,None,int(number))
        else:continue
        candidates=[]
        for original in originals:
            row=original['row'];voucher=re.fullmatch(r'(.*?)[-—]?0*(\d+)',text(row.get('凭证号')))
            if not voucher or row['明细名称'] in ('',BLANK_DETAIL):continue
            if row['明细名称']!=event['row']['明细名称'] or row[CATEGORY_COLUMN]!=event['row'][CATEGORY_COLUMN]:continue
            if original['day']>event['day'] or original['index']==event['index']:continue
            year,month,day,word,number=reference
            if original['day'].month!=month or int(voucher.group(2))!=number:continue
            if year is not None and original['day'].year!=year:continue
            if day is not None and original['day'].day!=day:continue
            if word is not None and voucher.group(1).rstrip('-—')!=word:continue
            if round(original['inc'],2)<round(event['dec'],2):continue
            candidates.append(original)
        if len(candidates)==1:
            original=candidates[0]
            event['basis']='冲回摘要引用原暂估凭证，同单位、同科目、发生日和金额范围核对一致'
            event['reference']=original['row'].get('原账行号',original['index']+2)
    for event in events:
        if not event['basis']:continue
        index=event['index'];row=event['row']
        result.at[index,'_是否暂估']=True
        records.append({'单位':row['明细名称'],'科目名称':row[CATEGORY_COLUMN],
            '日期':event['day'],'凭证号':text(row.get('凭证号')),
            '借方金额':row.get('借方金额',0),'贷方金额':row.get('贷方金额',0),
            '摘要':event['memo'],'识别依据':event['basis'],
            '原账行号':row.get('原账行号',index+2),'引用原账行号':event['reference']})
    return result,records

def mark_transfers(journal,cutoff):
    """归集后按发生顺序承接等额转出转入；无需单号、发票、价税或物料对应。"""
    result=journal.copy().reset_index(drop=True)
    result['_转科目借方']=0.0;result['_转科目贷方']=0.0
    grouped=defaultdict(list)
    for index,row in result.iterrows():
        day=pd.to_datetime(row.get('日期'),errors='coerce')
        if pd.isna(day) or day.date()>cutoff or text(row.get('明细名称')) in ('',BLANK_DETAIL):continue
        if PAYMENT_OR_RETURN.search(text(row.get('摘要'))):continue
        normalized=FIFOEngine._normalize_amounts([{'debit':row.get('借方金额',0),'credit':row.get('贷方金额',0)}])[0]
        grouped[(row['明细名称'],row[CATEGORY_COLUMN])].append({
            'index':index,'date':day.date(),'row':row,
            'debit':round(normalized['debit'],2),'credit':round(normalized['credit'],2)})
    records=[]
    for (unit,category),events in grouped.items():
        events.sort(key=lambda e:e['date'])
        temp=[e for e in events if e['row'].get('_是否暂估',False)]
        normal=[e for e in events if not e['row'].get('_是否暂估',False)]
        if not temp or not normal:continue
        inc='debit' if events[0]['row'][DIRECTION_COLUMN]=='debit' else 'credit'
        dec='credit' if inc=='debit' else 'debit'
        def carry(outgoing,incoming):
            for out in outgoing:
                for entry in incoming:
                    if out[dec]<=0:break
                    # 同月先正式入账后冲估可承接；更早月份的普通业务不用于本次转入。
                    if entry['date']<out['date'] and entry['date'].strftime('%Y-%m')!=out['date'].strftime('%Y-%m'):continue
                    amount=round(min(out[dec],entry[inc]),2)
                    if amount<=0:continue
                    out[dec]=round(out[dec]-amount,2);entry[inc]=round(entry[inc]-amount,2)
                    for event,column in [(out,dec),(entry,inc)]:
                        marker='_转科目借方' if column=='debit' else '_转科目贷方'
                        result.at[event['index'],marker]=round(result.at[event['index'],marker]+amount,2)
                    t,n=(out,entry) if out['row']['_是否暂估'] else (entry,out)
                    def subject(event):
                        return text(event['row'].get('_原科目编码'))+' '+text(event['row'].get('_原科目名称'))
                    records.append({'单位':unit,'归集科目':category,'暂估科目':subject(t),'正式科目':subject(n),
                        '暂估日期':str(t['date']),'暂估凭证':text(t['row'].get('凭证号')),'正式日期':str(n['date']),
                        '正式凭证':text(n['row'].get('凭证号')),'承接金额':amount,'承接规则':'FIFO，保留原余额层及其发生日期',
                        '暂估来源':text(t['row'].get('_来源工作表')),'正式来源':text(n['row'].get('_来源工作表'))})
        carry(temp,normal)
        # 正常科目转回暂估须明示转暂估；普通核销与暂估新增不能自动互相抵消。
        reverse=[e for e in normal if re.search(r'转(?:回|入)?暂估',text(e['row'].get('摘要')))]
        carry(reverse,temp)
    return result,records
