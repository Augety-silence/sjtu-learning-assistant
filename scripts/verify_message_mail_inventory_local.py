"""只读核对真实INBOX的UID与本地正文/附件统计；不输出账户或正文。"""
from __future__ import annotations
import argparse
import imaplib
import json
import sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.mail_sync import get_password, IMAP_HOST, IMAP_PORT, create_mail_tls_context
from sjtu_learning_assistant.models import Email, EmailAttachment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    engine=create_database_engine()
    result={'source':'actual INBOX UID inventory','source_read_only':True}
    try:
        account=SettingsStore().load().mail_account
        password,_=get_password(account,use_keychain=True,interactive=False)
        with imaplib.IMAP4_SSL(IMAP_HOST,IMAP_PORT,ssl_context=create_mail_tls_context(),timeout=20) as client:
            client.login(account,password)
            client.select('INBOX',readonly=True)
            validity=client.response('UIDVALIDITY')[1][0].decode('ascii')
            status,data=client.uid('search',None,'ALL')
            if status!='OK':raise RuntimeError('inventory_unavailable')
            server={int(x) for x in data[0].split()}
        prefix=account+':INBOX:'+validity+':'
        with Session(engine) as session:
            rows=session.scalars(select(Email).where(Email.source_id.startswith(prefix,autoescape=True))).all()
            stored={int(x.source_id.rsplit(':',1)[-1]) for x in rows}
            attachments=session.scalars(select(EmailAttachment).join(Email).where(Email.source_id.startswith(prefix,autoescape=True))).all()
            result.update(server_messages=len(server),stored_current_uidvalidity=len(stored),
                missing_source_uids=len(server-stored),all_source_uids_saved=server<=stored,
                parsed_full_content=sum(bool((x.raw_data or {}).get('rich_content_fetched')) for x in rows),
                parse_failed=sum(bool((x.raw_data or {}).get('parse_failed')) for x in rows),
                attachment_mappings=len(attachments),available_attachment_files=sum(bool(x.local_path and Path(x.local_path).is_file()) for x in attachments))
    except Exception as exc:
        result['error_type']=type(exc).__name__
    finally:engine.dispose()
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(output+'\n')
    print(output)

if __name__=='__main__':main()
