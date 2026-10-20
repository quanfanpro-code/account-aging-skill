"""计算前的暂估归属、名称候选检测与用户确认；不更改原始数据。"""
from dataclasses import dataclass
from collections import defaultdict
from difflib import SequenceMatcher
import re
import pandas as pd
from src.core.current_accounts import classify_current_account, temporary_account_name, temporary_account_scope, CATEGORIES, BLANK_DETAIL

def text(value):
    return '' if value is None or pd.isna(value) else str(value).strip()

def name_format(value):
    return re.sub(r'\s+','',value).replace('(','（').replace(')','）').replace('\\','').replace('/','').replace('“','').replace('”','').replace('"','').rstrip('.')

def strip_auxiliary_code(value):
    return re.sub(r'[\\/][0-9]+(?:-[0-9]+)+$','',value).strip()

@dataclass(frozen=True)
class NameCandidate:
    key: tuple[str,str]
    reason: str
    evidence: str

@dataclass(frozen=True)
class TemporaryAccount:
    code: str
    name: str
    suggested: str
    evidence: str

@dataclass
class Preflight:
    temporary: list[TemporaryAccount]
    names: list[NameCandidate]

def detect(opening,journal):
    names=defaultdict(list);temporary=defaultdict(list);stats=defaultdict(lambda:[0.0]*4)
    for title,df in [('期初',opening),('序时账',journal)]:
        for number,(_,row) in enumerate(df.iterrows(),2):
            name=text(row.get('科目名称'));code=re.sub(r'\.0+$','',text(row.get('科目编码')))
            match=classify_current_account(code,name)
            if match.status=='ignored':continue
            source=text(row.get('_来源工作表')) or title
            evidence=f'{source} 第{number}行 / {name}'
            if temporary_account_name(name,code):
                scope=temporary_account_scope(code,name)
                temporary[scope].append((temporary_account_name(name,code),f'{scope} '+('一级暂估科目' if len(scope)==4 else '正常往来下的暂估明细')+'；'+evidence))
            detail=text(row.get('明细名称'))
            if not detail or detail==BLANK_DETAIL:continue
            names[detail].append(evidence)
            fields=['借方余额','贷方余额'] if title=='期初' else ['借方金额','贷方金额']
            offset=0 if title=='期初' else 2
            for i,field in enumerate(fields):
                value=pd.to_numeric(row.get(field),errors='coerce')
                if pd.notna(value):stats[detail][offset+i]+=float(value)
    temporary_items=[]
    for code,items in sorted(temporary.items()):
        name=items[0][0]
        suggested=next((x for x in CATEGORIES if x in name),'')
        if not suggested:
            suggested=('其他应收款' if '应收' in name else '其他应付款') if '其他' in name else next((category for token,category in [('应收','应收账款'),('应付','应付账款'),('预收','预收账款'),('预付','预付账款')] if token in name),'')
        temporary_items.append(TemporaryAccount(code,name,suggested,'；'.join(dict.fromkeys(x[1] for x in items))[:600]))
    ordered=sorted(names);found={}
    for i,a in enumerate(ordered):
        for b in ordered[i+1:]:
            reason=''
            if strip_auxiliary_code(a)==b or strip_auxiliary_code(b)==a:reason='名称后附带辅助编码'
            elif name_format(a)==name_format(b):reason='空格、括号、引号或分隔符不同'
            elif min(len(a),len(b))>=8 and all(re.search('公司|大学|研究所|研究院|实验室|经营部|加工厂|事务所',x) for x in (a,b)):
                # 不把不同编号的研究所列为同一主体候选。
                na=re.search(r'第([一二三四五六七八九十百零〇\d]+)研究所',a);nb=re.search(r'第([一二三四五六七八九十百零〇\d]+)研究所',b)
                if na and nb and na.group(1)!=nb.group(1):continue
                if '中国科学院' in a and '中国科学院' in b and a not in b and b not in a:
                    institution=lambda s:re.sub('中国科学院|研究所|研究院','',s)
                    if SequenceMatcher(None,institution(a),institution(b)).ratio()<.8:continue
                if '\\' in a or '\\' in b:
                    tail=lambda s:s.rsplit('\\',1)[-1]
                    if SequenceMatcher(None,tail(a),tail(b)).ratio()<.86:continue
                core=lambda s:re.sub('有限责任公司|股份有限公司|有限公司|股份|光电|光学|科技|技术|材料|机械','',name_format(s))
                if SequenceMatcher(None,name_format(a),name_format(b)).ratio()>=.86 or (min(len(core(a)),len(core(b)))>=4 and SequenceMatcher(None,core(a),core(b)).ratio()>=.80):reason='名称文字相近或存在简称，主体身份待确认'
            elif min(len(a),len(b))>=5 and (a in b or b in a) and any('公司' in x for x in (a,b)):reason='简称与全称可能对应'
            if reason:
                def explain(n):
                    od,oc,jd,jc=stats[n]
                    return f'{n}：期初借/贷 {od:,.2f}/{oc:,.2f}；期间借/贷 {jd:,.2f}/{jc:,.2f}；{names[n][0]}'
                found[(a,b)]=NameCandidate((a,b),reason,explain(a)+'\n'+explain(b))
    return Preflight(temporary_items,list(found.values()))

def confirmed_aliases(candidates,decisions):
    aliases={};kept=[]
    for candidate in candidates:
        if candidate.key not in decisions:raise ValueError('疑似同一单位名称尚未确认')
        selected=decisions[candidate.key]
        if selected is None:kept.append(candidate.key);continue
        selected=text(selected)
        if not selected or selected==BLANK_DETAIL:raise ValueError('合并后统一名称不能为空')
        for name in candidate.key:
            if name in aliases and aliases[name]!=selected:raise ValueError('关联名称请使用同一个统一名称')
            aliases[name]=selected
    for a,b in kept:
        if aliases.get(a,a)==aliases.get(b,b):raise ValueError('同一组名称存在合并与分别计算的矛盾决定')
    return aliases
