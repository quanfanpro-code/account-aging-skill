"""仅查询疑似名称的登记候选，使用本包数据层，不替用户猜定主体。"""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import quote

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.company_registry.browser_transport import OpenCliTransport,OpenCliBridgeError
from src.company_registry.client import CicpaClient,CicpaError


def lookup_name(name,client,company_id=None):
    matches=client.search_companies(name,page_size=10)
    if not matches:return {'name':name,'status':'no_match','matches':[],'说明':'首批未检索到，不代表主体不存在'}
    exact=[item for item in matches if item.name==name]
    selected=([item for item in matches if item.org_id==company_id] if company_id else
        exact if len(exact)==1 else matches if len(matches)==1 else [])
    if len(selected)!=1:
        return {'name':name,'status':'choose_company','matches':[asdict(item) for item in matches],
            '说明':'搜索有多个可能主体，助手结合原账选定具体主体后再查详情和股东，不逐个遍查'}
    match=selected[0];item=asdict(match);issues=[]
    if not match.org_id:
        return {'name':name,'status':'partial','matches':[item],'说明':'未取得具体企业标识，不能继续查详情和股东'}
    try:
        item.update(asdict(client.get_company_detail(match.org_id)));item['detail_status']='取得'
    except (CicpaError,OSError) as error:
        item['detail_status']='未取得';issues.append('企业详情：'+str(error))
    try:
        item['shareholders']=[asdict(shareholder) for shareholder in client.get_shareholders(match.org_id)]
        item['shareholders_status']='取得首批股东信息'
    except (CicpaError,OSError) as error:
        item['shareholders']=[];item['shareholders_status']='未取得';issues.append('股东：'+str(error))
    item['source']=CicpaClient.ZSK_BASE+'/open/enterprise_info_api/v3/find_company_basic_info?orgid='+quote(match.org_id,safe='')
    item['shareholders_source']=CicpaClient.ZSK_BASE+'/open/enterprise_info_api/v4/stock_holder_newest?orgid='+quote(match.org_id,safe='')
    item['queried_at']=datetime.now(timezone.utc).isoformat()
    return {'name':name,'status':'partial' if issues else 'found','matches':[item],'issues':issues,
        '说明':'单体查询只执行搜索、企业详情、首批股东，不调用批量接口；同股东不等于同一主体'}


def execute(names=(),*,cli_path=None,profile=None,login=False,check=False,company_id=None):
    if company_id and len(names)!=1:raise ValueError('指定企业主体时一次只核查一个名称')
    cli_path=cli_path or shutil.which('opencli')
    if not cli_path:return {'status':'unavailable','说明':'本机未找到浏览器连接工具；无需安装其他技能，按本包工商核查说明准备连接环境'}
    if not Path(cli_path).is_file() or '.workbuddy\\binaries\\' in str(cli_path).lower():
        raise ValueError('请使用已核实的本机原生浏览器工具路径')
    node=os.environ.get('RPI_OPENCLI_NODE') or shutil.which('node')
    if node and '.workbuddy\\binaries\\' in node.lower():raise ValueError('不能使用临时工具目录中的运行环境')
    if node:os.environ['RPI_OPENCLI_NODE']=node
    transport=OpenCliTransport(cli_path=cli_path,profile=profile or os.environ.get('RPI_OPENCLI_PROFILE'),session='account-aging-names')
    if not transport.verify_session():
        if login:
            transport.open_page(CicpaClient.ZSK_BASE+'/#/home_local')
        return {'status':'login_required','说明':'请在平时使用的Edge中登录注协知识库后重新检查，不提供或保存密码、登录凭据'}
    if check:return {'status':'ready','说明':'浏览器内登录状态有效，可查询名称登记'}
    client=CicpaClient(session=transport)
    results=[lookup_name(name,client,company_id) for name in dict.fromkeys(names)]
    return {'status':'complete' if all(item['status'] in ('found','no_match') for item in results) else 'needs_review','results':results}


def main():
    parser=argparse.ArgumentParser(description='供助手核查疑似往来名称，不要求用户操作命令')
    parser.add_argument('--name',action='append',default=[])
    parser.add_argument('--cli-path');parser.add_argument('--profile')
    parser.add_argument('--company-id')
    parser.add_argument('--login',action='store_true');parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    try:
        if not (args.name or args.check or args.login):raise ValueError('未指定需核查的名称')
        result=execute(args.name,cli_path=args.cli_path,profile=args.profile,login=args.login,check=args.check,company_id=args.company_id)
    except (CicpaError,OpenCliBridgeError,OSError,ValueError) as error:
        result={'status':'unavailable','说明':str(error)}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status'] in ('ready','complete') else 1


if __name__=='__main__':raise SystemExit(main())
