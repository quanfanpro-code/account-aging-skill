"""应收账款与应收票据双向转科目，承接原债权账龄并保留各自余额。"""
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
import re

import pandas as pd

from src.core.fifo_engine import FIFOEngine
from src.core.models import AgingEntry, AgingResult

NAMES={'1121':'应收票据','1122':'应收账款'}
RULE_URL='https://www.csrc.gov.cn/csrc/c101802/c7118278/content.shtml'


class BillConfirmationRequired(ValueError):
    def __init__(self,candidates):
        self.candidates=candidates
        super().__init__('票据类型或连续计龄依据尚未明确，请由agent结合资料确认后重算')


def text(value):
    return '' if value is None or pd.isna(value) else str(value).strip()


def money(value):
    number=0.0 if value is None or pd.isna(value) else float(value)
    if not math.isfinite(number):
        raise ValueError('金额不能为无穷大或无效数值')
    result=int((Decimal(str(number))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if abs(number-result/100)>.000001:
        raise ValueError('账龄输入须为本位币、精确到分的金额')
    return result


class ReceivableBillEngine:
    def __init__(self,cutoff_date,buckets,*,opening_date=None,opening_aging_columns=None):
        self.cutoff_date=cutoff_date
        self.buckets=buckets
        self.opening_date=opening_date
        self.opening_aging_columns=opening_aging_columns or {}
        self.fifo=FIFOEngine(cutoff_date,buckets)
        self.transfer_records=[]
        self.policy_records=[]

    def _rows(self,journal):
        rows=[]
        for position,(_,series) in enumerate(journal.iterrows()):
            row=series.to_dict()
            code=text(row.get('科目编码'))[:4]
            if code not in NAMES:
                continue
            day=pd.to_datetime(row.get('日期'),errors='coerce')
            if pd.isna(day):
                raise ValueError('应收或票据分录日期无效，不能跳过后继续计算')
            identifier=row.get('原账行号',position+2)
            if pd.isna(identifier): identifier=position+2
            if isinstance(identifier,(int,float)): identifier=int(identifier)
            unit=text(row.get('明细名称'))
            if not unit or unit=='（未填明细）':
                raise ValueError(f'第{identifier}行缺少结算客户，不能以承兑银行名称代替客户')
            normalized=FIFOEngine._normalize_amounts([{'debit':row.get('借方金额',0),'credit':row.get('贷方金额',0)}])[0]
            debit=money(normalized['debit'])-money(row.get('_转科目借方',0))
            credit=money(normalized['credit'])-money(row.get('_转科目贷方',0))
            if min(debit,credit)<0: raise ValueError('暂估承接标记超过原分录金额')
            row.update(id=identifier,position=position,day=day.date(),code=code,unit=unit,
                debit=debit,credit=credit,bill_id=text(row.get('票据号')),voucher=text(row.get('凭证号')))
            rows.append(row)
        if len({r['id'] for r in rows})!=len(rows):
            raise ValueError('原账行号必须唯一')
        currencies={text(r.get('币种')) for r in rows if text(r.get('币种'))}
        if len(currencies)>1: raise ValueError('先统一为单一本位币后计算，不得混合不同币种')
        return rows

    @staticmethod
    def _commercial(row):
        value=text(row.get('票据类型')) or ' '.join(text(row.get(k)) for k in ('科目名称','_原科目名称','摘要'))
        return ('商业承兑' in value or value=='商承') and not re.search(r'银行承兑|非商业承兑|不是商业承兑|非商承',value)

    def _plan(self,rows,decisions):
        by_id={r['id']:r for r in rows}
        remaining={(r['id'],side):r[side] for r in rows for side in ('debit','credit')}
        links=[]
        def register(source,target,amount,continuous,basis):
            if source['unit']!=target['unit'] or source['day']!=target['day']:
                raise ValueError('票据转科目须为同一结算客户、同一记账日；跨日记录需先核实原账')
            if {source['code'],target['code']}!={'1121','1122'}:
                raise ValueError('票据关联只允许应收账款与应收票据双向转科目')
            if amount<=0 or amount>remaining[(source['id'],'credit')] or amount>remaining[(target['id'],'debit')]:
                raise ValueError('票据承接金额超过可关联分录金额或重复使用原账行')
            remaining[(source['id'],'credit')]-=amount
            remaining[(target['id'],'debit')]-=amount
            self.policy_records.append({'单位':source['unit'],'转出行':source['id'],'转入行':target['id'],
                '金额':amount/100,'连续计龄':continuous,'依据':basis})
            if continuous:
                links.append({'source':source,'target':target,'amount':amount,'basis':basis})
        for decision in decisions or []:
            sid,tid=decision['转出行'],decision['转入行']
            if sid not in by_id or tid not in by_id:
                if sid in self.all_rows and tid in self.all_rows and self.all_rows[sid]['day']>self.cutoff_date and self.all_rows[tid]['day']>self.cutoff_date:
                    continue
                raise ValueError('票据确认记录的关联行不在本截止期，或行号不存在')
            continuous=decision.get('连续计龄')
            if not isinstance(continuous,bool) or not text(decision.get('依据')):
                raise ValueError('票据确认记录须明确是否连续计龄并填写具体依据')
            register(by_id[sid],by_id[tid],money(decision['金额']),continuous,text(decision['依据']))
        grouped=defaultdict(list)
        for row in rows:
            if row['voucher']: grouped[(row['day'],row['voucher'],row['unit'])].append(row)
        pending=[]
        for group in grouped.values():
            for source_code,target_code in [('1122','1121'),('1121','1122')]:
                sources=[r for r in group if r['code']==source_code]
                targets=[r for r in group if r['code']==target_code]
                for source in sources:
                    for target in targets:
                        amount=min(remaining[(source['id'],'credit')],remaining[(target['id'],'debit')])
                        if amount<=0: continue
                        bill=target if target_code=='1121' else source
                        if source_code=='1122' and not self._commercial(bill):
                            pending.append({'转出行':source['id'],'转入行':target['id'],'金额':amount/100,
                                '单位':source['unit'],'日期':str(source['day']),'凭证号':source['voucher'],
                                '票据类型':text(bill.get('票据类型')) or '未明确','待确认事项':'原债权是否终止确认、本次是否连续计龄'})
                            remaining[(source['id'],'credit')]-=amount
                            remaining[(target['id'],'debit')]-=amount
                        else:
                            register(source,target,amount,True,'同日同凭证同客户两边对应；发行类第5号5-2第五项：'+RULE_URL)
        if pending: raise BillConfirmationRequired(pending)
        # 摘要明示转科目而未关联时，即使有凭证号也不能当普通收款重置账龄。
        linked_ids={r['转出行'] for r in self.policy_records}|{r['转入行'] for r in self.policy_records}
        for row in rows:
            memo=text(row.get('摘要'))
            if row['id'] not in linked_ids and any(s in memo for s in ('转票据','转为票据','转回应收','退票','拒付')):
                raise ValueError(f"第{row['id']}行缺少票据转科目对应关系，请核对同日对应分录；跨日关联当前不支持，不能按新款重算")
        return links

    def _take(self,queue,amount,*,bill_id='',strict=False):
        remaining=amount
        parts=[]
        for layer in sorted(queue,key=lambda x:x['entry'].days,reverse=True):
            if bill_id and layer['bill_id']!=bill_id: continue
            used=min(layer['amount'],remaining)
            if used:
                parts.append({**layer,'amount':used})
                layer['amount']-=used
                remaining-=used
            if not remaining: break
        queue[:]=[layer for layer in queue if layer['amount']]
        if remaining and strict:
            raise ValueError(f'转出科目或指定票据余额不足，无法承接{remaining/100:,.2f}元；不得虚构期初或使用未来分录')
        return parts,remaining

    def _increase(self,key,layer):
        _,remaining=self._take(self.negative[key],layer['amount'])
        if remaining: self.positive[key].append({**layer,'amount':remaining})

    def run(self,opening,journal,*,decisions=None):
        self.transfer_records=[]
        self.policy_records=[]
        all_rows=self._rows(journal)
        self.all_rows={r['id']:r for r in all_rows}
        rows=[r for r in all_rows if r['day']<=self.cutoff_date]
        currencies={text(r.get('币种')) for r in rows if text(r.get('币种'))}
        if '币种' in opening:
            currencies.update(text(value) for value in opening['币种'] if text(value))
        if len(currencies)>1: raise ValueError('期初及序时账须统一本位币后计算')
        links=self._plan(rows,decisions)
        self.positive=defaultdict(list)
        self.negative=defaultdict(list)
        stats=defaultdict(lambda:{'opening':0,'debit':0,'credit':0,'opening_buckets':[]})
        for _,series in opening.iterrows():
            row=series.to_dict();code=text(row.get('科目编码'))[:4]
            if code not in NAMES: continue
            unit=text(row.get('明细名称'))
            if not unit or unit=='（未填明细）': raise ValueError('应收及票据期初须明确结算客户')
            key=(unit,code)
            amount=money(row.get('借方余额',0))-money(row.get('贷方余额',0))
            stats[key]['opening']+=amount
            bucket_values=None;ranges=None
            if self.opening_aging_columns and amount:
                if self.opening_date is None: raise ValueError('有期初账龄分档时须提供期初日期')
                ranges=[k for k in sorted(self.opening_aging_columns)]
                bucket_values=[float(row.get(self.opening_aging_columns[k],0) or 0) for k in ranges]
                if money(sum(bucket_values))!=amount: raise ValueError('期初分档与应收或票据期初余额不一致')
                stats[key]['opening_buckets'].append(bucket_values)
            effective_date=self.opening_date
            dates=[r['day'] for r in rows if (r['unit'],r['code'])==key]
            if effective_date is None and dates: effective_date=min(dates)-timedelta(days=1)
            initial_fifo=FIFOEngine(self.cutoff_date,self.buckets,'credit' if amount<0 else 'debit')
            initial=initial_fifo.calculate(code,NAMES[code],unit,abs(amount)/100,[],opening_buckets=[-v for v in bucket_values] if amount<0 and bucket_values else bucket_values,
                opening_date=effective_date,opening_bucket_ranges=ranges)
            for entry in initial.entries:
                if amount<0: entry=replace(entry,amount=-entry.amount,bucket_index=-1)
                target=self.positive if entry.amount>=0 else self.negative
                target[key].append({'amount':abs(money(entry.amount)),'entry':entry,'bill_id':text(row.get('票据号'))})
        debit_mark=defaultdict(int);credit_mark=defaultdict(int);events=defaultdict(list)
        for link in links:
            source,target=link['source'],link['target']
            credit_mark[source['id']]+=link['amount'];debit_mark[target['id']]+=link['amount']
            first=min((source,target),key=lambda r:r['position'])
            events[first['id']].append(link)
        for row in sorted(rows,key=lambda r:(r['day'],r['position'])):
            key=(row['unit'],row['code'])
            stats[key]['debit']+=money(row.get('借方金额',0));stats[key]['credit']+=money(row.get('贷方金额',0))
            for link in events[row['id']]:
                source,target=link['source'],link['target']
                source_key=(source['unit'],source['code']);target_key=(target['unit'],target['code'])
                pieces,_=self._take(self.positive[source_key],link['amount'],
                    bill_id=source['bill_id'] if source['code']=='1121' else '',strict=True)
                for piece in pieces:
                    entry=piece['entry']
                    hop=f"{source['day']} {NAMES[source['code']]}→{NAMES[target['code']]}"
                    history=(entry.bill_transfer_note+'；' if entry.bill_transfer_note else '')+hop
                    carried={**piece,'entry':replace(entry,bill_transfer_note=history),
                        'bill_id':target['bill_id'] if target['code']=='1121' else ''}
                    self._increase(target_key,carried)
                    self.transfer_records.append({'单位':source['unit'],'原科目':NAMES[source['code']],
                        '转入科目':NAMES[target['code']],'转科目日期':source['day'],'承接金额':piece['amount']/100,
                        '原始发生日期':entry.source_date,'票据号':target['bill_id'] or source['bill_id'],
                        '凭证号':source['voucher'] or target['voucher'],
                        '原账行号':f"{source['id']} → {target['id']}",'依据':link['basis']})
            debit=row['debit']-debit_mark[row['id']];credit=row['credit']-credit_mark[row['id']]
            days=(self.cutoff_date-row['day']).days
            entry=AgingEntry(str(row['day']),row['day'],0.0,days,self.fifo._find_bucket(days))
            if debit: self._increase(key,{'amount':debit,'entry':entry,'bill_id':row['bill_id']})
            if credit:
                _,remaining=self._take(self.positive[key],credit,bill_id=row['bill_id'] if row['code']=='1121' else '',strict=row['code']=='1121' and bool(row['bill_id']))
                if remaining: self.negative[key].append({'amount':remaining,'entry':entry,'bill_id':row['bill_id']})
        results=[]
        for key,value in sorted(stats.items()):
            unit,code=key
            entries=[replace(p['entry'],amount=p['amount']/100) for p in self.positive[key]]
            entries += [replace(n['entry'],amount=-n['amount']/100,bucket_index=-1) for n in self.negative[key]]
            totals=[sum(e.amount for e in entries if e.bucket_index==i) for i in range(len(self.buckets))]
            closing=(value['opening']+value['debit']-value['credit'])/100
            if money(sum(e.amount for e in entries))!=money(closing):
                raise ValueError(f'{unit}/{NAMES[code]} 连续计龄结果与原账余额不平衡')
            opening_buckets=[sum(part[i] for part in value['opening_buckets']) for i in range(len(next(iter(value['opening_buckets']),[])))]
            results.append(AgingResult(code,NAMES[code],unit,'debit',value['opening']/100,
                value['debit']/100,value['credit']/100,closing,entries=entries,bucket_totals=totals,
                negative_balance=sum(e.amount for e in entries if e.amount<0),
                opening_bucket_totals=opening_buckets,opening_date=self.opening_date))
        return results
