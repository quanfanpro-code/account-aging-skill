"""统一期初与期间的往来名称，只改计算输入，按最终关系留痕。"""
from collections import Counter,defaultdict
from itertools import combinations
import re

import pandas as pd

from src.core.current_accounts import classify_current_account
from src.name_matching.归一引擎 import 清洗名称,归一分档


def text(value):
    return '' if value is None or pd.isna(value) else str(value).strip()


def clean(value):
    value=清洗名称(text(value))
    if re.fullmatch(r'[\u4e00-\u9fff]{2,4}\(个人\)',value):value=value[:-4]
    return value


def protected(value):
    if any(part in value for part in ('未列明','未注明','未填','不详','未知')):return True
    if re.fullmatch(r'(?:客户|供应商|单位)[甲乙丙丁一二三四五A-Za-z\d]+',value):return True
    if any(word in value for word in ('公司','商行','工厂','门店','事务所','合作社','研究所')):return False
    return bool(re.fullmatch(r'[\u4e00-\u9fff]{2,4}',value))


def normalize_accounts(opening,journal,config):
    frames={'opening':opening.copy(),'journal':journal.copy()}
    result={'status':'ready',**frames,'changes':[],'decisions':[],'pending':[],
        'stats':{'名称处理行数':0,'变更名称行数':0,'候选对数':0}}
    if config.get('normalize_names',True) is False:return result
    active={};originals={};occurrences=defaultdict(list)
    last_day=max(config.get('cutoffs',[]),default=None)
    for kind,frame in frames.items():
        originals[kind]=frame['明细名称'].copy()
        if '原始往来单位' not in frame:frame['原始往来单位']=frame['明细名称']
        if '原账行号' not in frame:frame['原账行号']=range(2,len(frame)+2)
        mask=[]
        for _,row in frame.iterrows():
            match=classify_current_account(row.get('科目编码'),row.get('科目名称'))
            bill=('应收票据' in text(row.get('科目名称')) or text(row.get('科目编码')).startswith('1121'))
            selected=match.status=='retained' or (config.get('include_receivable_bills',True) and bill
                and not any(word in text(row.get('科目名称')) for word in ('坏账准备','减值准备')))
            if kind=='journal' and last_day and pd.to_datetime(row.get('日期')).date().isoformat()>last_day:selected=False
            mask.append(selected)
        active[kind]=pd.Series(mask,index=frame.index,dtype=bool)
    row_basis={}
    used_rows=set()
    for decision in config.get('normalization_row_decisions',[]):
        kind=decision.get('source');canonical=text(decision.get('canonical'));basis=text(decision.get('basis'))
        if kind not in frames or not canonical or not basis:raise ValueError('逐行名称决定须填写来源、规范名称和具体依据')
        frame=frames[kind];rows=decision.get('rows',[])
        if not rows:raise ValueError('逐行名称决定未指定原账行号')
        for identifier in rows:
            key=(kind,identifier)
            selected=active[kind] & frame['原账行号'].eq(identifier)
            if selected.sum()!=1 or key in used_rows:raise ValueError(f'逐行名称决定的原账行不存在、重复或不在计算范围：{kind}第{identifier}行')
            used_rows.add(key);index=frame.index[selected][0]
            frame.at[index,'明细名称']=canonical
            row_basis[(kind,index)]=(basis,text(decision.get('evidence')))
    for kind,frame in frames.items():
        for index,row in frame[active[kind]].iterrows():
            name=text(row['明细名称'])
            if name:occurrences[name].append((kind,index,row))
    names=sorted(occurrences);parent={name:name for name in names};members={name:{name} for name in names}
    reasons=defaultdict(set);sources=defaultdict(set);blocked=set();audit=[]
    def find(name):
        while parent[name]!=name:
            parent[name]=parent[parent[name]];name=parent[name]
        return name
    decisions=list(config.get('normalization_decisions',[]))
    specified={frozenset(item.get('names',[])) for item in decisions}
    for old in config.get('name_decisions',[]):
        if frozenset(old.get('names',[])) not in specified:
            decisions.append({**old,'basis':'沿用本次已有名称决定','evidence':'本次计算配置'})
    for item in decisions:
        pair=list(dict.fromkeys(item.get('names',[])))
        if not pair or any(name not in parent for name in pair):raise ValueError('名称决定引用了本次往来范围以外的原始名称')
        if not text(item.get('basis')):raise ValueError('名称决定须保留具体归一或分别计算依据')
        if item.get('canonical') is not None and not text(item['canonical']):raise ValueError('规范名称不能为空')
        if item.get('canonical') is None:
            blocked.update(frozenset(pairing) for pairing in combinations(pair,2))
    registry={}
    for item in config.get('normalization_registry',[]):
        name=text(item.get('name'))
        if name not in parent or not text(item.get('registered_name')) or not text(item.get('source')):
            raise ValueError('登记事实须对应本次原名称，并保留登记全称及来源')
        if name in registry and registry[name]!=item:raise ValueError('同一名称的登记事实冲突，请先核对来源')
        registry[name]=item
        audit.append({'原始称谓':name,'处理结果':text(item['registered_name']),
            '处理依据':'工商登记全名；统一社会信用代码：'+(text(item.get('credit_code')) or '本次未取得'),
            '证据来源':text(item['source'])})
    for a,b in combinations(registry,2):
        ca,cb=text(registry[a].get('credit_code')),text(registry[b].get('credit_code'))
        if ca and cb and ca!=cb:
            blocked.add(frozenset((a,b)))
    def merge(a,b,basis,evidence='',strict=False):
        ra,rb=find(a),find(b)
        if ra==rb:return True
        if any(frozenset((x,y)) in blocked for x in members[ra] for y in members[rb]):
            if strict:raise ValueError('拟合并名称与已登记不同主体或已决定分别计算的关系冲突')
            return False
        parent[rb]=ra;members[ra]|=members.pop(rb)
        reasons[ra]|=reasons.pop(rb,set())|{basis};sources[ra]|=sources.pop(rb,set())
        if evidence:sources[ra].add(evidence)
        return True
    # 先记录确定的身份边界，再应用自动规则，避免规则覆盖事实。
    for item in decisions:
        if item.get('canonical') is not None:
            for name in item['names'][1:]:merge(item['names'][0],name,item['basis'],text(item.get('evidence')),True)
        audit.append({'原始称谓':' / '.join(item['names']),'处理结果':text(item.get('canonical')) or '分别保留',
            '处理依据':item['basis'],'证据来源':text(item.get('evidence'))})
    for a,b in combinations(registry,2):
        code=text(registry[a].get('credit_code'))
        if code and code==text(registry[b].get('credit_code')):
            merge(a,b,'登记统一社会信用代码一致',text(registry[a]['source'])+'；'+text(registry[b]['source']),True)
    by_clean=defaultdict(list)
    for name in names:by_clean[clean(name)].append(name)
    ambiguous=[]
    def known_id_count(group):
        return len({text(registry[name].get('credit_code')) for name in group
            if name in registry and text(registry[name].get('credit_code'))})
    for group in by_clean.values():
        if known_id_count(group)>1:
            ambiguous.extend((*pair,'同一简称或格式同时对应不同登记主体，须查清实际归属') for pair in combinations(group,2));continue
        for name in group[1:]:merge(group[0],name,'清洗后一致，或同一姓名的个人后缀差异')
    eligible=[name for name in names if not protected(clean(name))]
    classified=归一分档(eligible)
    for canonical,group in classified['自动归一'].items():
        if known_id_count(group)>1:
            ambiguous.extend((*pair,'同一简称或格式同时对应不同登记主体，须查清实际归属') for pair in combinations(group,2));continue
        for name in group[1:]:merge(group[0],name,'名称清洗、公司后缀或开头一致的简称包含规则')
    pending=[];seen=set()
    for a,b,reason in [*classified['候选'],*ambiguous]:
        ra,rb=find(a),find(b);key=frozenset((ra,rb))
        if ra==rb or key in seen:continue
        seen.add(key)
        if any(frozenset((x,y)) in blocked for x in members[ra] for y in members[rb]):
            continue
        entries=occurrences[a]+occurrences[b]
        amount=sum(abs(float(row.get(field,0) or 0)) for _,_,row in entries
            for field in ('借方金额','贷方金额','借方余额','贷方余额') if pd.notna(row.get(field,0)))
        pending.append({'名称':[a,b],'候选原因':reason,'涉及行数':len(entries),'涉及金额':round(amount,2),
            '共同长编号线索':sorted(set().union(*(set(re.findall(r'\d{8,}',text(row.get('摘要')))) for _,_,row in occurrences[a])) &
                set().union(*(set(re.findall(r'\d{8,}',text(row.get('摘要')))) for _,_,row in occurrences[b]))),
            '原账位置':[('期初' if kind=='opening' else '序时账')+f"第{row['原账行号']}行" for kind,_,row in entries[:6]],
            '查证顺序':'先查原发票、冲销更名关系及摘要；仍不能确定，再用本包查询名称登记工具查工商'})
    result.update(pending=pending,candidate_count=len(pending))
    result['stats']['候选对数']=len(pending)
    if len(pending)>50 and not (config.get('normalization_large_batch_approved') is True and text(config.get('normalization_scope_basis'))):
        result['status']='needs_name_scope';result['pending']=pending[:8];return result
    if pending:result['status']='needs_name_evidence';return result
    canonicals={};basis_by_name={};evidence_by_name={}
    for root,group in members.items():
        group_decisions=[item for item in decisions if item.get('canonical') is not None and set(item['names']) & group]
        explicit={text(item['canonical']) for item in group_decisions}
        registered={text(registry[name]['registered_name']) for name in group if name in registry}
        if len(explicit)>1 or (not explicit and len(registered)>1):raise ValueError('同一归一组存在多个冲突规范名称，请核对登记或已有决定')
        canonical=next(iter(explicit or registered),None) or sorted((clean(name) for name in group),key=lambda name:(-len(name),name))[0]
        group_reasons=reasons[root] | {item['basis'] for item in group_decisions}
        if registered:group_reasons.add('依据工商登记全名统一，已核对登记身份')
        basis='；'.join(sorted(group_reasons)) or '仅整理名称格式，保持主体不变'
        evidence='；'.join(sorted(sources[root] | {text(registry[name]['source']) for name in group if name in registry}
            | {text(item.get('evidence')) for item in group_decisions if text(item.get('evidence'))}))
        for name in group:
            canonicals[name]=canonical;basis_by_name[name]=basis;evidence_by_name[name]=evidence
    # 不同组若最终同名，也会在后续账龄中混在一起，必须检查已知不合并约束。
    for pair in blocked:
        if len(pair)==2:
            a,b=tuple(pair)
            if canonicals[a]==canonicals[b]:
                if a in registry and b in registry and text(registry[a].get('credit_code'))!=text(registry[b].get('credit_code')):
                    for identity in (a,b):
                        label=canonicals[identity]+'（信用代码'+text(registry[identity]['credit_code'])+'）'
                        for alias in members[find(identity)]:canonicals[alias]=label
                elif clean(a)==clean(b) and not any(set(item['names']) & pair and item.get('canonical') is not None for item in decisions):
                    canonicals[a],canonicals[b]=a,b
                else:raise ValueError('分别计算的名称最终仍同名，会混算余额，请明确区分名称')
    changes=Counter();row_count=0
    for kind,frame in frames.items():
        for index,row in frame[active[kind]].iterrows():
            name=text(row['明细名称']);raw_original=originals[kind].at[index]
            original='' if pd.isna(raw_original) else str(raw_original)
            if not name:continue
            canonical=canonicals[name];frame.at[index,'明细名称']=canonical;row_count+=1
            basis,evidence=row_basis.get((kind,index),(basis_by_name[name],evidence_by_name[name]))
            if original!=canonical:
                changes[(original,canonical,basis,evidence,'期初' if kind=='opening' else '序时账')]+=1
    result['changes']=[{'原始称谓':original,'规范名称':canonical,'涉及行数':count,'归一依据':basis,'证据来源':evidence,'资料范围':scope}
        for (original,canonical,basis,evidence,scope),count in sorted(changes.items())]
    result['decisions']=audit;result['stats'].update(名称处理行数=row_count,变更名称行数=sum(changes.values()))
    return result
