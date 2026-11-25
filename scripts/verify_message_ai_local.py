"""使用现有内嵌助手验证一条历史通知引用，结果仅保留布尔值和计数。

此脚本会创建一段本机验收对话并调用已配置模型；不输出真实通知内容。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.dashboard_service import DashboardService


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    engine=create_database_engine()
    service=DashboardService(engine)
    result={'kind':'actual embedded assistant conversation','ok':False}
    try:
        session=service.ai_chat_new(preset_id='message-analyst')
        result['conversation_created']=True
        answer=service.ai_chat_send(session['id'],
            '使用本地消息检索，查找历史邮件中的考试通知。选择一条较早通知，返回标题、来源、时间及可点击的本地消息引用。不要输出通知正文或邮箱地址。',
            preset_id='message-analyst')
        response=service.ai_chat_session(session['id'])
        assistant='\n'.join(x.get('content','') for x in response.get('messages',[]) if x.get('role')=='assistant')
        citations=re.findall(r'#/messages\?kind=(?:email|announcement|assignment)&ref=(?:email|announcement|assignment):[0-9]+',assistant)
        result['citation_count']=len(set(citations))
        valid=0
        for citation in set(citations):
            query=parse_qs(citation.split('?',1)[1])
            service.message_reference(query['kind'][0],query['ref'][0])
            valid+=1
        result['valid_local_references']=valid
        result['ok']=valid>0
        result['trace_count']=len(response.get('traces',[]))
        result['private_content_exported']=False
    except Exception as exc:
        result['error_type']=type(exc).__name__
    finally:
        service.close()
        engine.dispose()
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(output+'\n')
    print(output)

if __name__=='__main__':main()
