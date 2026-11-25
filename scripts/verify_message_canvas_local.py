"""真实Canvas往期消息回填：仅输出统计，不下载文件、不调用模型。"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import select
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.canvas_sync import get_token, build_http_client, DEFAULT_BASE_URL, DEFAULT_TIMEOUT_SECONDS
from sjtu_learning_assistant.course_material_sync import sync_historical_materials
from sjtu_learning_assistant.course_scope import current_course_ids
from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.message_archive import search_archive
from sjtu_learning_assistant.models import Course


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    engine=create_database_engine()
    result={'data':'actual Canvas account','model_calls':0,'file_downloads':0}
    try:
        before={k:search_archive(engine,kind=k)['total'] for k in ('announcement','assignment')}
        with Session(engine) as session:
            current=current_course_ids(session)
            current_sources=set(session.scalars(select(Course.source_id).where(Course.id.in_(current))))
        token,_=get_token(use_keychain=True)
        with build_http_client(DEFAULT_BASE_URL,token,DEFAULT_TIMEOUT_SECONDS) as client:
            historical=sync_historical_materials(engine,client,current_sources)
        with Session(engine) as session:
            same_scope=current_course_ids(session)==current
        result.update(sync='success',historical_courses=historical['courses'],
            restricted_resources=len(historical['issues']),current_scope_preserved=same_scope,
            before=before,after={k:search_archive(engine,kind=k)['total'] for k in ('announcement','assignment')})
    except Exception as exc:
        result.update(sync='failed',error_type=type(exc).__name__)
    finally:
        engine.dispose()
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(output+'\n')
    print(output)

if __name__=='__main__':main()
