"""本机消息归档验收：只输出数量和断言；可选读取 INBOX 并回填。

真实标题、正文、账户、凭据只在本机内存使用，不写入验收结果。
默认不调用远端；--sync 只同步邮件，不调用模型、不改变源端已读。
"""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import re
import sys
import time
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.models import Email
from sjtu_learning_assistant.repository import get_sync_state
from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry
from sjtu_learning_assistant.message_archive import search_archive
from sjtu_learning_assistant.text_content import content_to_plain_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sync', action='store_true')
    parser.add_argument('--native-html', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    engine = create_database_engine()
    settings = SettingsStore().load()
    service = DashboardService(engine)
    result = {'data': 'actual local account', 'model_calls': 0}
    try:
        before = service.message_archive('email')['total']
        result['mail_before'] = before
        if args.sync:
            from sync_data_to_db import sync_mail
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    sync_mail(engine, settings.mail_account, 100)
                result['sync'] = 'success'
            except Exception as exc:
                result['sync'] = 'failed'
                result['sync_error_type'] = type(exc).__name__
        page = service.message_archive('email', offset=100)
        result['mail_after'] = page['total']
        result['older_than_100_visible'] = bool(page['items'])
        state = get_sync_state(engine, 'email', 'inbox')
        try:
            cursor = json.loads(state.cursor or '{}') if state else {}
        except ValueError:
            cursor = {}
        result['history_complete'] = cursor.get('history_complete') is True
        result['cursor_account_matches'] = cursor.get('account') == settings.mail_account
        all_page = service.message_archive('all')
        result['all_messages'] = all_page['total']
        result['kinds'] = {k: service.message_archive(k)['total'] for k in ('email','announcement','assignment')}
        sample_page = search_archive(engine, kind='email', account=settings.mail_account, offset=max(100,page['total']-1), limit=1)
        if not sample_page['items']:
            sample_page = search_archive(engine, kind='email', account=settings.mail_account, limit=1)
        if sample_page['items']:
            item = sample_page['items'][0]
            detail = service.message_detail('email', item['source_id'])
            registry = ReadOnlyToolRegistry(engine, mail_account=settings.mail_account)
            ref = 'email:' + str(item['record_id'])
            cited = registry.execute('get_message_detail', {'kind':'email','ref':ref}, ('get_message_detail',)).result
            located = service.message_reference('email',ref)['item']
            result['reference_opens_same_message'] = located['source_id'] == item['source_id'] and cited['title'] == item['title']
            text = content_to_plain_text(detail.get('body_html') or detail.get('body')) or ''
            words = re.findall(r'[A-Za-z]{4,20}|[\u4e00-\u9fff]{2,6}', text)
            word = next((w for w in reversed(words) if w not in (item['title'] or '') and w not in (item['source_label'] or '')), '')
            result['old_message_has_body'] = bool(text)
            if word:
                hit = search_archive(engine,kind='email',query=word,account=settings.mail_account,limit=200)
                result['body_only_keyword_matches'] = hit['total'] > 0
                ai = registry.execute('search_messages',{'query':word,'kind':'email','limit':50},('search_messages',)).result
                result['ai_search_has_citations'] = bool(ai['items']) and all(x.get('citation','').startswith('#/messages?') for x in ai['items'])
            result['keyword_counts'] = {w: service.message_archive('email',query=w)['total'] for w in ('考试','作业','通知')}
            # 重新连接以确认页面与正文来自持久数据库。
            reopened = create_database_engine()
            try:
                result['reopen_preserves_count'] = search_archive(reopened,kind='email',account=settings.mail_account)['total'] == page['total']
            finally:
                reopened.dispose()
            if args.native_html:
                import webview
                from desktop_app import DesktopBridge
                bridge = DesktopBridge(service, None)
                class ReadOnlyBridge:
                    def invoke(self, action, payload=None):
                        if action in {'message_archive','message_detail','message_reference','capabilities','sync_status'}:
                            return bridge.invoke(action,payload)
                        if action == 'settings_status':
                            return {'ok':True,'data':{'ai_enabled':False,'ai_key_saved':False}}
                        if action == 'ai_chat_sessions':
                            return {'ok':True,'data':{'items':[]}}
                        return {'ok':False,'error':{'code':'verification_read_only','message':'本次仅验收消息读取'}}
                window = webview.create_window('消息中心真实数据只读验收',args.native_html.resolve().as_uri(),js_api=ReadOnlyBridge(),width=1280,height=820)
                def wait(expression):
                    deadline = time.monotonic() + 25
                    while not window.evaluate_js(expression):
                        if time.monotonic()>deadline:
                            raise RuntimeError('native_wait_timeout')
                        time.sleep(.1)
                def native_check():
                    try:
                        wait("!!document.querySelector('.sidebar')")
                        window.evaluate_js("location.hash = '/messages'")
                        wait("document.querySelectorAll('.message-open').length > 0")
                        window.evaluate_js("window.dispatchEvent(new CustomEvent('sjtu-open-message',{detail:"+json.dumps(cited['citation'])+"}))")
                        wait("document.querySelector('.message-reader h3')?.textContent === "+json.dumps(item['title']))
                        wait("!!document.querySelector('.message-reader [role=document],.message-detail-pending')")
                        result['native_real_reference'] = True
                        result['native_no_extra_labels'] = window.evaluate_js("!document.body.innerText.includes('本地可搜索') && !document.body.innerText.includes('本地归档')")
                        window.resize(760,720)
                        time.sleep(.3)
                        result['native_narrow_no_horizontal_overflow'] = window.evaluate_js("document.documentElement.scrollWidth <= innerWidth+1")
                    except Exception as exc:
                        result['native_error_type'] = type(exc).__name__
                    finally:
                        window.destroy()
                webview.start(native_check,gui='cocoa',http_server=True)
    finally:
        service.close()
        engine.dispose()
    output = json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(output+'\n')
    print(output)

if __name__ == '__main__':
    main()
