"""真实 WKWebView 的聊天补充、并行会话和停止隔离验收。

仅打开独立构建的夹具页面，桥接仅使用内存数据。
不导入业务服务、不访问生产数据库、学校服务、模型密钥或付费推理。
"""

import argparse
import copy
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import webview


class Bridge:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.tasks = {}
        self.requests = {}
        self.calls = []
        self.poll_counts = {}
        self.unexpected_actions = []

    def _session_summary(self, session):
        return {key: session[key] for key in
                ("id", "title", "model", "thinking_depth", "preset_id")}

    def _snapshot(self, task, after=0):
        return dict(task={key: value for key, value in task.items() if key != "events"},
                    events=[event for event in task["events"] if event["sequence"] > after],
                    next_sequence=len(task["events"]))

    def _event(self, task, kind, payload):
        task["events"].append(dict(sequence=len(task["events"]) + 1,
                                   kind=kind, payload=payload))

    def invoke(self, action, payload=None):
        payload = payload or {}
        with self.lock:
            if action == "ai_task_status":
                task_id = payload.get("task_id")
                self.poll_counts[task_id] = self.poll_counts.get(task_id, 0) + 1
            else:
                self.calls.append(dict(action=action, payload=copy.deepcopy(payload)))
            try:
                return dict(ok=True, data=copy.deepcopy(self._invoke(action, payload)))
            except Exception as exc:
                return dict(ok=False, error=dict(code="fixture_rejected", message=str(exc)))

    def _invoke(self, action, payload):
        if action == "ai_presets":
            return dict(default_preset_id="general", items=[dict(
                id="general", name="学习数据 Agent", description="隔离聊天验收",
                allowed_tools=["list_courses"], is_default=True)])
        if action == "ai_workspace_status":
            return dict(configured=False, available=False, name=None, path=None, reason=None)
        if action == "settings_status":
            return dict(ai_chat_send_shortcut="enter", ai_reply_language="zh",
                        ai_attachment_context_budget="balanced", ai_auto_open_activity=False,
                        ai_code_line_numbers=False)
        if action == "ai_chat_sessions":
            return dict(items=[self._session_summary(session)
                               for session in self.sessions.values()])
        if action == "ai_chat_new":
            index = len(self.sessions) + 1
            assert index <= 2, "夹具不允许创建第三个会话"
            session_id = f"fixture-session-{index}"
            session = dict(id=session_id, title="合成会话 " + ("A" if index == 1 else "B"),
                           model="auto", thinking_depth="standard", preset_id="general",
                           messages=[], traces=[])
            self.sessions[session_id] = session
            return session
        if action == "ai_chat_session":
            return self.sessions[payload["session_id"]]
        if action == "ai_chat_stream_start":
            session_id = payload["session_id"]
            assert not any(task["session_id"] == session_id and task["status"] == "running"
                           for task in self.tasks.values()), "同会话重复创建任务"
            assert len(self.tasks) < 2, "补充指令不得创建第三个任务"
            task_id = "fixture-task-" + ("a" if session_id.endswith("1") else "b")
            stamp = datetime.now(timezone.utc).isoformat()
            message = dict(id=task_id + "-user", role="user", content=payload["content"])
            self.sessions[session_id]["messages"].append(message)
            task = dict(id=task_id, session_id=session_id, status="running", created_at=stamp,
                        started_at=stamp, cancel_requested=False, result=None,
                        user_message=message, events=[])
            self._event(task, "status", dict(state="model_call", step=1))
            self._event(task, "tool", dict(tool_name="list_courses", phase="model", status="ok",
                                          arguments_summary={}, result_summary=dict(count=2)))
            self.tasks[task_id] = task
            return self._snapshot(task)
        if action == "ai_task_status":
            return self._snapshot(self.tasks[payload["task_id"]], payload.get("after_sequence", 0))
        if action == "ai_chat_steer":
            request_id = payload["request_id"]
            if request_id in self.requests:
                previous = self.requests[request_id]
                assert previous["payload"] == payload, "幂等标识对应了不同的补充内容"
                return previous["result"]
            task = self.tasks[payload["task_id"]]
            assert task["session_id"] == payload["session_id"], "跨会话补充"
            assert task["status"] == "running", "终态拒绝新补充"
            message = dict(id="fixture-steering-" + request_id, role="user", content=payload["content"])
            self.sessions[task["session_id"]]["messages"].append(message)
            self._event(task, "steering_applied", dict(request_ids=[request_id], messages=[dict(
                content=message["content"], message_id=message["id"], request_id=request_id,
                attachment_context=[])]))
            result = dict(task_id=task["id"], user_message=message)
            self.requests[request_id] = dict(payload=copy.deepcopy(payload), result=result)
            return result
        if action == "ai_task_cancel":
            task = self.tasks[payload["task_id"]]
            task.update(status="cancelled", cancel_requested=True)
            return self._snapshot(task)
        self.unexpected_actions.append(action)
        raise ValueError("隔离验收拒绝动作: " + action)

    def complete(self, task_id):
        with self.lock:
            task = self.tasks[task_id]
            assert task["status"] == "running"
            session = self.sessions[task["session_id"]]
            trace = dict(id=task_id + "-trace", preset_id="general", status="completed",
                         steps=1, tool_runs=[event["payload"] for event in task["events"]
                                             if event["kind"] == "tool"])
            answer = dict(id=task_id + "-answer", role="assistant", content="合成任务 B 已完成",
                          trace_id=trace["id"], tool_runs=trace["tool_runs"])
            session["messages"].append(answer)
            session["traces"].append(trace)
            task.update(status="completed", result=dict(chat_result=dict(
                session=self._session_summary(session), trace=trace,
                user_message=task["user_message"], assistant_message=answer)))

    def state(self):
        with self.lock:
            return copy.deepcopy(dict(
                tasks={key: dict(id=value["id"], session_id=value["session_id"], status=value["status"])
                       for key, value in self.tasks.items()},
                calls=self.calls, poll_counts=self.poll_counts,
                steering_count=len(self.requests), unexpected_actions=self.unexpected_actions))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--width", type=int, default=1440)
    args = parser.parse_args()
    assert args.html.is_file(), "夹具 HTML 不存在"
    bridge = Bridge()
    window = webview.create_window("SJTU聊天补充指令隔离验收", args.html.resolve().as_uri(),
                                   js_api=bridge, width=args.width, height=900, text_select=True)
    outcome = dict(ok=False, native="WKWebView", production_database_accesses=0,
                   inference_calls=0, scenarios=[])

    def wait(expression, timeout=15):
        deadline = time.monotonic() + timeout
        while not window.evaluate_js(expression):
            assert time.monotonic() < deadline, "页面等待超时: " + expression
            threading.Event().wait(.1)

    def wait_state(predicate, label):
        deadline = time.monotonic() + 15
        while not predicate(bridge.state()):
            assert time.monotonic() < deadline, "桥接等待超时: " + label
            threading.Event().wait(.1)

    def click(label):
        window.evaluate_js("(() => {const b=Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==="
                           + json.dumps(label) + ");if(!b||b.disabled)throw new Error('按钮不可用');b.click();})()")

    def set_draft(text):
        window.evaluate_js("(() => {const t=document.querySelector('[aria-label=输入问题]');"
                           "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,"
                           + json.dumps(text) + ");t.dispatchEvent(new Event('input',{bubbles:true}));})()")
        wait("document.querySelector('[aria-label=输入问题]').value===" + json.dumps(text))
        threading.Event().wait(.1)

    def send(text, steering=False):
        set_draft(text)
        label = "补充指令" if steering else "发送消息"
        selector = '[aria-label="' + label + '"]'
        wait("Boolean(document.querySelector(" + json.dumps(selector) + ")&&!document.querySelector("
             + json.dumps(selector) + ").disabled)")
        window.evaluate_js("document.querySelector(" + json.dumps(selector) + ").click()")

    def open_session(letter, draft):
        click("合成会话 " + letter)
        wait("document.querySelector('.ai-conversation-heading strong').textContent==="
             + json.dumps("合成会话 " + letter))
        wait("document.querySelector('[aria-label=输入问题]').value===" + json.dumps(draft))

    def capture(name):
        if not args.screenshots:
            return
        from AppKit import NSBitmapImageRep, NSJPEGFileType, NSImageCompressionFactor
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        event = threading.Event()

        def snapshot():
            def done(image, error):
                if image is not None and error is None:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    data = bitmap.representationUsingType_properties_(NSJPEGFileType,
                                                                       {NSImageCompressionFactor: .7})
                    data.writeToFile_atomically_(str(args.screenshots / (name + ".jpg")), True)
                event.set()
            BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None, done)

        AppHelper.callAfter(snapshot)
        assert event.wait(10), "截图超时"
        assert (args.screenshots / (name + ".jpg")).is_file(), "原生截图失败"

    def check():
        started = time.monotonic()
        try:
            wait("Boolean(document.querySelector('.v2-sidebar .v2-brand img'))")
            def layout(selector):
                return window.evaluate_js("(() => {const e=document.querySelector(" + json.dumps(selector) + ");const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()")
            shell_brand = layout('.v2-sidebar .v2-brand img')
            shell_width = layout('.v2-sidebar')['width']
            window.evaluate_js("document.querySelector('[aria-label=\"AI 助手\"]').click()")
            wait("Boolean(document.querySelector('[aria-label=输入问题]'))")
            ai_brand = layout('.ai-sidebar .v2-brand img')
            ai_width = layout('.ai-sidebar')['width']
            expected_width = 248 if args.width >= 1200 else 224
            assert shell_width == ai_width == expected_width, (shell_width, ai_width)
            assert all(abs(shell_brand[k] - ai_brand[k]) < 1 for k in shell_brand), (shell_brand, ai_brand)
            outcome['layout'] = dict(shell_brand=shell_brand, ai_brand=ai_brand, sidebar_width=ai_width)
            set_draft('中央配置切换草稿')
            click('技能配置')
            wait("Boolean(document.querySelector('.ui2-config-page'))")
            assert not window.evaluate_js("Boolean(document.querySelector('aside .ui2-skill-library'))")
            click('外部技能')
            assert window.evaluate_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='导入 ZIP')")
            window.evaluate_js("document.querySelector('[aria-label=返回对话]').click()")
            wait("!document.querySelector('.ui2-config-page')")
            assert window.evaluate_js("document.querySelector('[aria-label=输入问题]').value==='中央配置切换草稿'")
            window.evaluate_js("document.querySelector('.ui2-agent-row').click()")
            wait("Boolean(document.querySelector('[data-ai-floating-menu]'))")
            assert window.evaluate_js("document.querySelector('[data-ai-floating-menu]').parentElement===document.body")
            option_size = window.evaluate_js("parseFloat(getComputedStyle(document.querySelector('[data-ai-floating-menu] [role=option]')).fontSize)")
            assert option_size <= 14, option_size
            capture('00-agent-menu')
            window.evaluate_js("document.querySelector('[data-ai-floating-menu] [role=option]').click()")
            wait("!document.querySelector('[data-ai-floating-menu]')")
            for label in ('模型：', '推理强度：'):
                selector = '[aria-label^="' + label + '"]'
                window.evaluate_js("document.querySelector(" + json.dumps(selector) + ").click()")
                wait("Boolean(document.querySelector('[data-ai-floating-menu]'))")
                assert window.evaluate_js("Array.from(document.querySelectorAll('[data-ai-floating-menu] strong')).every(e=>parseFloat(getComputedStyle(e).fontSize)<=14)")
                assert window.evaluate_js("(() => {const r=document.querySelector('[data-ai-floating-menu]').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()")
                window.evaluate_js("document.querySelector('[data-ai-floating-menu] [role=option][aria-selected=true]').click()")
                wait("!document.querySelector('[data-ai-floating-menu]')")
            outcome['scenarios'].append(f'品牌坐标与{expected_width}px侧栏稳定；中央配置保留草稿；助理/模型/推理菜单悬浮且字号14px')

            send("合成任务 A")
            wait_state(lambda state: state["tasks"].get("fixture-task-a", {}).get("status") == "running", "A 启动")
            set_draft("A 的未发草稿")
            assert window.evaluate_js("!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='新对话').disabled")
            click("新对话")
            wait("document.querySelector('.ai-conversation-heading strong').textContent==='合成会话 B'")
            wait("document.querySelector('[aria-label=输入问题]').value===''")
            send("合成任务 B")
            wait_state(lambda state: len(state["tasks"]) == 2 and all(task["status"] == "running"
                       for task in state["tasks"].values()), "A/B 并行")
            outcome["scenarios"].append("A执行中可新建B并启动第二个独立任务")
            set_draft("B 的未发草稿")
            capture("01-parallel-b")
            open_session("A", "A 的未发草稿")
            outcome["scenarios"].append("切换回A恢复A草稿，B草稿未混入")
            send("请按新增约束继续执行 A", steering=True)
            wait_state(lambda state: state["steering_count"] == 1, "A 的补充指令")
            state = bridge.state()
            steering = [call for call in state["calls"] if call["action"] == "ai_chat_steer"]
            assert len(steering) == 1 and steering[0]["payload"]["task_id"] == "fixture-task-a", state
            assert steering[0]["payload"]["session_id"] == "fixture-session-1", state
            assert len(state["tasks"]) == 2, state
            assert window.evaluate_js("JSON.parse(localStorage.getItem('ai-chat-running-task:fixture-session-1')).taskId==='fixture-task-a'")
            wait("document.querySelector('.ai-chat-messages').textContent.includes('请按新增约束继续执行 A')")
            assert not window.evaluate_js("document.body.textContent.includes('将在当前检索完成后执行')")
            outcome["scenarios"].append("A补充调用ai_chat_steer且保持原task_id，不创建第三轮")
            capture("02-steering-a")
            set_draft("A 的新草稿")
            open_session("B", "B 的未发草稿")
            open_session("A", "A 的新草稿")
            assert window.evaluate_js("!document.querySelector('[aria-label=停止本次任务]') && Boolean(document.querySelector('[aria-label=补充指令]'))")
            # 2.0 运行中的单按钮：有补充草稿时发送，清空后暂停；停止后恢复未发送草稿。
            set_draft("")
            wait("Boolean(document.querySelector('[aria-label=停止本次任务]'))")
            window.evaluate_js("document.querySelector('[aria-label=停止本次任务]').click()")
            wait_state(lambda state: state["tasks"]["fixture-task-a"]["status"] == "cancelled", "停止 A")
            wait("!document.querySelector('[aria-label=正在停止本次任务]')")
            state = bridge.state()
            cancellations = [call["payload"]["task_id"] for call in state["calls"] if call["action"] == "ai_task_cancel"]
            assert cancellations == ["fixture-task-a"], state
            assert state["tasks"]["fixture-task-b"]["status"] == "running", state
            set_draft("A 的新草稿")
            open_session("B", "B 的未发草稿")
            wait("Boolean(document.querySelector('[aria-label=补充指令]'))")
            assert window.evaluate_js("!document.querySelector('[aria-label=停止本次任务]')")
            outcome["scenarios"].append("停止A仅取消A；B仍运行且保留B草稿")
            capture("03-b-after-stop-a")
            bridge.complete("fixture-task-b")
            wait("document.querySelector('.ai-chat-messages').textContent.includes('合成任务 B 已完成')")
            wait("!document.querySelector('[aria-label=停止本次任务]')")
            assert window.evaluate_js("document.querySelector('[aria-label=输入问题]').value==='B 的未发草稿'")
            capture("04-b-completed")
            outcome["scenarios"].append("B可正常完成，完成后保留未发送草稿")
            errors = window.evaluate_js("window.__chatSteeringFixtureErrors")
            assert errors == [], errors
            final = bridge.state()
            assert final["unexpected_actions"] == [], final
            assert len(final["tasks"]) == 2 and final["steering_count"] == 1, final
            outcome.update(ok=True, state=final, javascript_errors=errors)
        except Exception as exc:
            outcome.update(error=str(exc), error_type=type(exc).__name__, state=bridge.state())
        finally:
            outcome["duration_seconds"] = round(time.monotonic() - started, 3)
            window.destroy()

    webview.start(check, gui="cocoa", private_mode=True)
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
