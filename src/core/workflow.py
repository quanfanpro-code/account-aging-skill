"""统一业务流程：计算前确认、归集、重分类、成对对抵，返回唯一最终账龄。"""
from dataclasses import replace
from collections import defaultdict
import pandas as pd
from src.core.preflight import detect,confirmed_aliases,text
from src.core.current_accounts import ACCOUNT_RULES,CATEGORIES,CATEGORY_COLUMN,DIRECTION_COLUMN,BLANK_DETAIL,temporary_account_name,temporary_account_scope,filter_current_accounts
from src.core.orchestrator import AgingOrchestrator
from src.core.fifo_engine import FIFOEngine
from src.core.temporary_transfers import mark_transfers
from src.core.receivable_bills import ReceivableBillEngine

PAIRS=(('应收账款','预收账款'),('预付账款','应付账款'),('其他应收款','其他应付款'),('合同资产','合同负债'))
RULES={name:(code,direction) for name,code,direction in ACCOUNT_RULES}
OPPOSITE={a:b for pair in PAIRS for a,b in (pair,pair[::-1])}

class AgingWorkflow:
    def __init__(self,cutoff_date,buckets,*,opening_date=None,opening_aging_columns=None,on_progress=None):
        self.cutoff_date=cutoff_date;self.buckets=buckets;self.opening_date=opening_date
        self.opening_aging_columns=opening_aging_columns or {};self.on_progress=on_progress
        self.source_results=[];self.reclassification_records=[];self.offset_records=[];self.confirmation_records=[]
        self.last_warnings=[];self.last_stats={};self.last_direction_log=[];self.transfer_records=[]

    @staticmethod
    def preflight(opening,journal):return detect(opening,journal)

    def run(self,opening,journal,*,temporary_assignments=None,name_decisions=None,include_receivable_bills=False,bill_decisions=None):
        temporary_assignments=temporary_assignments or {};name_decisions=name_decisions or {}
        pending=self.preflight(opening,journal)
        for item in pending.temporary:
            if item.code not in temporary_assignments:raise ValueError(f'暂估科目 {item.code} {item.name} 尚未确认归属')
            if temporary_assignments[item.code] is not None and temporary_assignments[item.code] not in CATEGORIES:raise ValueError('暂估归属必须选择正常往来科目')
        aliases=confirmed_aliases(pending.names,name_decisions)
        self.confirmation_records=[{'类型':'科目归属','原名称':x.name,'用户决定':temporary_assignments[x.code] or '本次不纳入','依据':x.evidence} for x in pending.temporary]
        self.confirmation_records += [{'类型':'单位名称','原名称':' / '.join(x.key),'用户决定':name_decisions[x.key] or '分别计算','依据':x.evidence} for x in pending.names]
        def prepare(df,kind):
            df=df.copy()
            if include_receivable_bills and kind=='journal' and '原账行号' not in df:
                df['原账行号']=range(2,len(df)+2)
            selected=filter_current_accounts(df,data_kind=kind,source_label=kind).dataframe
            selected['_原科目编码']=selected.get('原科目编码',selected['科目编码']);selected['_原单位名称']=selected['明细名称']
            selected['_原科目名称']=selected.get('原科目名称',selected['_原科目名称'])
            selected['_是否暂估']=[bool(temporary_account_name(r['科目名称'],r['科目编码'])) for _,r in selected.iterrows()]
            drop=[]
            for index,row in selected.iterrows():
                category=row[CATEGORY_COLUMN]
                if row['_是否暂估']:
                    category=temporary_assignments[temporary_account_scope(row['科目编码'],row['科目名称'])]
                    if category is None:drop.append(index);continue
                code,direction=RULES[category]
                selected.loc[index,['科目编码','科目名称',CATEGORY_COLUMN,DIRECTION_COLUMN]]=[code,category,category,direction]
                selected.loc[index,'明细名称']=aliases.get(row['明细名称'],row['明细名称'])
            selected=selected.drop(index=drop)
            if include_receivable_bills:
                is_bill=df['科目编码'].astype(str).str.startswith('1121') | df['科目名称'].fillna('').astype(str).str.contains('应收票据')
                is_bill &= ~df['科目名称'].fillna('').astype(str).str.contains('坏账准备|减值准备')
                bills=df[is_bill].copy()
                bills['_原科目编码']=bills.get('原科目编码',bills['科目编码'])
                bills['_原科目名称']=bills.get('原科目名称',bills['科目名称'])
                bills['_原单位名称']=bills['明细名称']
                bills['明细名称']=bills['明细名称'].map(lambda name:aliases.get(name,name))
                bills['科目编码']='1121';bills['科目名称']='应收票据'
                bills[CATEGORY_COLUMN]='应收票据';bills[DIRECTION_COLUMN]='debit';bills['_是否暂估']=False
                selected=pd.concat([selected,bills],axis=0).sort_index(kind='stable')
            return selected
        self.opening_input=prepare(opening,'opening');self.journal_input=prepare(journal,'journal')
        self.journal_input,self.transfer_records=mark_transfers(self.journal_input,self.cutoff_date)
        orch=AgingOrchestrator(self.cutoff_date,self.buckets,opening_date=self.opening_date,opening_aging_columns=self.opening_aging_columns,on_progress=self.on_progress)
        source=orch.run(self.opening_input,self.journal_input)
        self.bill_transfer_records=[];self.bill_policy_records=[]
        if include_receivable_bills:
            bill_engine=ReceivableBillEngine(self.cutoff_date,self.buckets,opening_date=self.opening_date,opening_aging_columns=self.opening_aging_columns)
            bill_results=bill_engine.run(self.opening_input,self.journal_input,decisions=bill_decisions)
            source=[r for r in source if r.subject_code not in ('1121','1122')]+bill_results
            self.bill_transfer_records=bill_engine.transfer_records;self.bill_policy_records=bill_engine.policy_records
        self.source_results=source;self.last_warnings=orch.last_warnings;self.last_stats=orch.last_stats;self.last_direction_log=orch.last_direction_log
        self.reclassification_records=[];positive=[]
        for r in source:
            if r.subject_name=='应收票据' and r.closing_balance<-.005:
                raise ValueError(f'{r.detail_name}应收票据为贷方余额{-r.closing_balance:,.2f}元，需核对票据收付及结算客户，不能自动改列预收账款')
            if r.closing_balance>=-.005:positive.append(r);continue
            target=OPPOSITE[r.subject_name];code,direction=RULES[target]
            txn=self.journal_input[(self.journal_input['科目编码']==r.subject_code)&(self.journal_input['明细名称']==r.detail_name)]
            transactions=[{'date':x['日期'].date() if hasattr(x['日期'],'date') else x['日期'],'debit':float(x['借方金额'] or 0),'credit':float(x['贷方金额'] or 0),'_转科目借方':x.get('_转科目借方',0),'_转科目贷方':x.get('_转科目贷方',0)} for _,x in txn.iterrows() if pd.notna(x['日期']) and (x['日期'].date() if hasattr(x['日期'],'date') else x['日期'])<=self.cutoff_date]
            opening_buckets=[-x for x in r.opening_bucket_totals] if r.opening_balance<0 and r.opening_bucket_totals else None
            ranges=[k for k in sorted(self.opening_aging_columns)] if opening_buckets else None
            converted=FIFOEngine(self.cutoff_date,self.buckets,direction).calculate(code,target,r.detail_name,-r.opening_balance,transactions,opening_buckets=opening_buckets,opening_date=self.opening_date,opening_bucket_ranges=ranges)
            if include_receivable_bills and r.subject_code=='1122':
                entries=[replace(e,amount=-e.amount,bucket_index=FIFOEngine(self.cutoff_date,self.buckets)._find_bucket(e.days)) for e in r.entries if e.amount<0]
                converted=replace(converted,entries=entries,bucket_totals=[sum(e.amount for e in entries if e.bucket_index==i) for i in range(len(self.buckets))])
            if abs(converted.closing_balance+r.closing_balance)>.01:raise ValueError('重分类金额不平衡')
            # 来源随每笔余额层保留，合并和对抵后仍能区分普通款项与重分类转入款项。
            converted.entries=[replace(e,reclassified_from=r.subject_name,reclassification_total=round(-r.closing_balance,2)) for e in converted.entries]
            positive.append(converted)
            self.reclassification_records.append({'单位':r.detail_name,'原科目':r.subject_name,'原余额':round(r.closing_balance,2),'转入科目':target,'重分类金额':round(converted.closing_balance,2),'说明':'按转入科目的借贷性质重新追溯发生额计算账龄'})
        return self._offset(positive)

    def _offset(self,results):
        grouped=defaultdict(list);self.offset_records=[];final=[]
        for number,r in enumerate(results):
            if abs(r.closing_balance)<.005:continue
            if r.subject_name=='应收票据':
                final.append(r);continue
            pair=next(p for p in PAIRS if r.subject_name in p)
            # 无明细时不能认定为同一单位，按原科目隔离列示。
            key=(pair,r.detail_name, r.subject_name if r.detail_name==BLANK_DETAIL else '')
            grouped[key].append(r)
        for (pair,unit,_),items in sorted(grouped.items()):
            amounts={name:round(sum(x.closing_balance for x in items if x.subject_name==name),2) for name in pair}
            offset=min(amounts.values());net=round(amounts[pair[0]]-amounts[pair[1]],2)
            self.offset_records.append({'往来组合':' / '.join(pair),'单位':unit,'资产侧余额':amounts[pair[0]],'负债侧余额':amounts[pair[1]],'对抵金额':offset,'最终借方净额':net})
            if abs(net)<.005:continue
            target=pair[0] if net>0 else pair[1];surviving=[x for x in items if x.subject_name==target]
            first=surviving[0];entries=[replace(e) for x in surviving for e in x.entries if e.amount>0];remaining=offset
            # 同档也按实际来源的账龄从老到新扣减，追溯明细与最终分档一致。
            for index in sorted(range(len(entries)),key=lambda i:(entries[i].bucket_index,entries[i].days),reverse=True):
                e=entries[index];consumed=min(e.amount,remaining)
                entries[index]=replace(e,amount=e.amount-consumed);remaining-=consumed
            entries=[e for e in entries if e.amount>.000000001]
            buckets=[sum(e.amount for e in entries if e.bucket_index==i) for i in range(len(self.buckets))]
            if abs(sum(buckets)-abs(net))>.01:raise ValueError(f'{unit} 最终账龄分档不平衡')
            final.append(replace(first,entries=entries,bucket_totals=buckets,negative_balance=0,closing_balance=abs(net)))
        return final
