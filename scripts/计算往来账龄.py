"""由agent准备参数、处理对话确认并调用；不弹窗，不要求用户填写配置。"""
import argparse
from datetime import date
from pathlib import Path
import json
import sys
import zipfile

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from make_excel import make_excel
from src.core.workflow import AgingWorkflow
from src.core.report import ReportGenerator
from src.core.receivable_bills import BillConfirmationRequired
from src.core.名称归一 import normalize_accounts

DEFAULT_BUCKETS=[(0,365),(365,730),(730,1095),(1095,1460),(1460,1825),(1825,9999)]
DEFAULT_LABELS=['1年以内','1—2年','2—3年','3—4年','4—5年','5年以上']


def load_inputs(config):
    source=Path(config['input']).resolve()
    if not source.is_file(): raise ValueError('找不到指定账表文件')
    journal=pd.read_excel(source,sheet_name=config.get('journal_sheet',0),header=config.get('journal_header',0))
    journal=journal.rename(columns=config.get('journal_columns',{}))
    if journal.columns.duplicated().any(): raise ValueError('列映射产生重名列，请agent选择唯一的科目及单位列')
    required=['日期','科目编码','科目名称','明细名称','借方金额','贷方金额']
    if any(name not in journal for name in required):
        raise ValueError('序时账缺少必需列，请agent先读取表头并完成列映射：'+','.join(name for name in required if name not in journal))
    if '原账行号' not in journal:
        journal['原账行号']=range(config.get('journal_header',0)+2,len(journal)+config.get('journal_header',0)+2)
    journal['日期']=pd.to_datetime(journal['日期'],errors='coerce')
    if journal['日期'].isna().any(): raise ValueError('序时账存在无效日期，请先核实原始行，不能静默剔除')
    for field in ['借方金额','贷方金额']:
        original=journal[field]
        converted=pd.to_numeric(original.astype(str).str.replace(',','',regex=False),errors='coerce')
        bad=original.notna() & (original.astype(str).str.strip()!='') & converted.isna()
        if bad.any(): raise ValueError(f'{field}存在无法识别的金额，请先核实原始行')
        journal[field]=converted.fillna(0)
    opening_sheet=config.get('opening_sheet')
    if opening_sheet is None:
        if config.get('zero_opening') is not True:
            raise ValueError('没有期初表时，须先通过对话确认历史完整且期初为0，再设置zero_opening')
        opening=pd.DataFrame(columns=['科目编码','科目名称','明细名称','借方余额','贷方余额'])
    else:
        opening=pd.read_excel(source,sheet_name=opening_sheet,header=config.get('opening_header',0)).rename(columns=config.get('opening_columns',{}))
        if opening.columns.duplicated().any():raise ValueError('期初列映射产生重名列，请选择唯一映射')
        if '原账行号' not in opening:
            opening['原账行号']=range(config.get('opening_header',0)+2,len(opening)+config.get('opening_header',0)+2)
        for field in ['科目编码','科目名称','明细名称','借方余额','贷方余额']:
            if field not in opening: raise ValueError('期初表缺少必需列：'+field)
        for field in ['借方余额','贷方余额']:
            opening[field]=pd.to_numeric(opening[field],errors='raise').fillna(0)
    return source,opening,journal


def execute(config,*,inspect_only=False):
    source,opening,journal=load_inputs(config)
    cutoffs=[date.fromisoformat(item) for item in config['cutoffs']]
    if not cutoffs or len(set(cutoffs))!=len(cutoffs): raise ValueError('至少提供一个不重复的截止日期')
    opening_date=date.fromisoformat(config['opening_date']) if config.get('opening_date') else None
    if opening_date and any(day<=opening_date for day in cutoffs): raise ValueError('计算截止日须晚于期初日期')
    if opening_date and (journal['日期'].dt.date<=opening_date).any():
        raise ValueError('序时账包含期初日及以前的分录，可能与期初重复；请先按实际期初覆盖范围核实')
    buckets=[tuple(part) for part in config.get('buckets',DEFAULT_BUCKETS)]
    if buckets!=sorted(buckets) or any(lo<0 or hi<lo for lo,hi in buckets): raise ValueError('账龄分档须按时间递增且上下限有效')
    if buckets[0][0]!=0 or any(buckets[i][0] not in (buckets[i-1][1],buckets[i-1][1]+1) for i in range(1,len(buckets))):
        raise ValueError('账龄分档须覆盖从0天开始的连续区间')
    labels=config.get('bucket_labels',DEFAULT_LABELS if buckets==DEFAULT_BUCKETS else None)
    ReportGenerator._get_bucket_labels({'buckets':buckets,'bucket_labels':labels})
    normalized=normalize_accounts(opening,journal,config)
    if normalized['status']!='ready':
        return {'status':normalized['status'],'名称候选数量':normalized['candidate_count'],
            '名称核查':normalized['pending'],'说明':'先利用账内证据；仍不能确定时调用本包查询名称登记工具，不把技术候选直接退给用户'}
    opening,journal=normalized['opening'],normalized['journal']
    pending=AgingWorkflow.preflight(opening,journal)
    decisions=({item.key:None for item in pending.names} if config.get('normalize_names',True) else
        {tuple(item['names']):item.get('canonical') for item in config.get('name_decisions',[])})
    missing_names=[{'名称':list(item.key),'依据':item.evidence} for item in pending.names if item.key not in decisions]
    assignments=config.get('temporary_assignments',{})
    missing_temporary=[{'科目编码':item.code,'名称':item.name,'建议归属':item.suggested,'依据':item.evidence}
        for item in pending.temporary if item.code not in assignments]
    if missing_names or missing_temporary:
        return {'status':'needs_decisions','单位名称':missing_names,'暂估归属':missing_temporary}
    output=Path(config['output']).resolve() if not inspect_only else None
    if output and (output==source or output.exists()): raise ValueError('输出路径已存在或指向源文件；由agent按文件保护规则安排最终保存，不得覆盖源账')
    frames=[];shared={};summaries=[];generator=ReportGenerator()
    for cutoff in cutoffs:
        flow=AgingWorkflow(cutoff,buckets,opening_date=opening_date,
            opening_aging_columns={(part['lower'],part['upper']):part['column'] for part in config.get('opening_aging_columns',[])})
        try:
            results=flow.run(opening,journal,temporary_assignments=assignments,name_decisions=decisions,
                include_receivable_bills=config.get('include_receivable_bills',True),bill_decisions=config.get('bill_decisions'))
        except BillConfirmationRequired as error:
            return {'status':'needs_bill_decisions','截止日期':str(cutoff),'票据确认':error.candidates}
        report_config={'cutoff_date':cutoff,'buckets':buckets,'bucket_labels':labels,
            'aging_mode':'无期初，完整历史从0计算' if config.get('zero_opening') else '按提供的期初计算',
            'opening_date':opening_date,'source_results':flow.source_results,
            'reclassification_records':flow.reclassification_records,
            'offset_records':[item for item in flow.offset_records if item['对抵金额']>.005],
            'transfer_records':flow.transfer_records,'confirmation_records':[
                item for item in flow.confirmation_records if not config.get('normalize_names',True) or item['类型']!='单位名称'],
            'bill_transfer_records':flow.bill_transfer_records,
            'temporary_identification_records':flow.temporary_identification_records}
        for title,frame in generator.build_frames(results,report_config):
            if title=='账龄计算结果':
                frames.append(('账龄计算结果' if len(cutoffs)==1 else f'{cutoff}账龄结果',frame))
            elif not frame.empty:
                frame=frame.rename(columns={'用户决定':'处理决定'})
                frame.insert(0,'截止日期',cutoff)
                shared.setdefault(title,[]).append(frame)
        noncontinuous=[{'截止日期':cutoff,'单位':item['单位'],'处理金额':item['金额'],
            '事项':'应收转票据不连续计龄','处理依据':item['依据'],'原账行号':f"{item['转出行']} → {item['转入行']}"}
            for item in flow.bill_policy_records if not item['连续计龄']]
        if noncontinuous: shared.setdefault('特殊事项',[]).append(pd.DataFrame(noncontinuous))
        summaries.append({'截止日期':str(cutoff),'最终分户数':len(results),
            '借方余额':round(sum(r.closing_balance for r in results if r.direction=='debit'),2),
            '贷方余额':round(sum(r.closing_balance for r in results if r.direction=='credit'),2),
            '票据承接金额':round(sum(item['承接金额'] for item in flow.bill_transfer_records),2)})
    if inspect_only:
        return {'status':'ready','读取序时账行数':len(journal),'名称归一':normalized['stats'],'计算预览':summaries}
    # 相同日期、金额的两个余额层也可能是真实的两笔款项，不按展示内容去重。
    if normalized['changes']:frames.append(('名称归一对照',pd.DataFrame(normalized['changes'])))
    if normalized['decisions']:frames.append(('名称核查依据',pd.DataFrame(normalized['decisions'])))
    frames += [(title,pd.concat(parts,ignore_index=True)) for title,parts in shared.items()]
    journal_columns=[name for name in ['原账行号','日期','凭证号','科目编码','科目名称','原始往来单位','明细名称','借方金额','贷方金额','摘要','票据类型','票据号',
        '原科目编码','原科目名称','原账文件','原账工作表','输入补充依据'] if name in journal]
    frames.append(('计算用序时账',journal[journal_columns].rename(columns={'明细名称':'往来单位'})))
    if not opening.empty: frames.append(('计算用期初',opening))
    if config.get('include_receivable_bills',True):
        frames.append(('票据计算口径',pd.DataFrame([
        ('法规依据','证监会发行类第5号5-2第五项；适用范围及边界见技能包references/票据连续计龄规定.md'),
        ('连续计龄','商业承兑结算及票据转回应收，保留原始账龄；票据余额独立列示，不与应收账款合成一户'),
        ('匹配口径','同日同凭证同结算客户的对应分录；无明确商业承兑类型时由agent依据资料确认，并记录理由'),
        ('核销口径','普通收付款FIFO；有票据号时核销指定票据，不消耗其他票据'),
        ('期初口径','完整历史且已确认期初为0' if config.get('zero_opening') else '使用提供的期初和期初账龄，缺少原始来源时不虚构旧账龄'),
        ],columns=['项目','说明'])))
    result_titles={title for title,_ in frames[:len(cutoffs)]}
    def finish_layout(plans):
        for tp in plans:
            if tp.名称 in result_titles: tp.标签色='0070C0'
            ReportGenerator._highlight_remarks(tp)
    make_excel(frames,str(output),postprocess=finish_layout)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None: raise ValueError('导出文件损坏，停止交付')
    return {'status':'complete','output':str(output),'读取序时账行数':len(journal),'名称归一':normalized['stats'],'结果':summaries}


def main():
    parser=argparse.ArgumentParser(description='agent对话驱动的往来账龄计算入口')
    parser.add_argument('mode',choices=['inspect','calculate'])
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    try:
        config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        result=execute(config,inspect_only=args.mode=='inspect')
    except (ValueError,KeyError,FileNotFoundError) as error:
        result={'status':'error','说明':str(error)}
    print(json.dumps(result,ensure_ascii=False,default=str))
    return 1 if result['status']=='error' else 0

if __name__=='__main__':
    raise SystemExit(main())
