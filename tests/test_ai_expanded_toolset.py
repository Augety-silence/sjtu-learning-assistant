"""实际综合助理 + 本地工作区 + 日历的工具集合能通过两种请求模式。"""
import json
import httpx
import pytest
from sqlalchemy import create_engine
from sjtu_learning_assistant.agent_runtime import PresetLoader, ReadOnlyToolRegistry
from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient, AIClassificationError
from sjtu_learning_assistant.ai_workspace import AIWorkspace
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge

@pytest.mark.parametrize("stream", [False, True])
def test_full_registered_toolset_and_real_workspace_read(tmp_path, stream):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.md").write_text("# 验收\n本地文件正文", encoding="utf-8")
    workspace = AIWorkspace(root, changes_dir=tmp_path / "changes")
    engine = create_engine("sqlite://")
    registry = ReadOnlyToolRegistry(engine,workspace=workspace,compiled_knowledge=CompiledKnowledge(projects_path=tmp_path / "projects.json"),course_file_resolver=lambda _: None,native_calendar=object())
    _, presets = PresetLoader(user_skills_path=tmp_path / "skills.json").load()
    definitions = registry.definitions(presets["general"].allowed_tools)
    assert len(definitions) > 16
    seen = []
    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        if stream:
            return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"完成"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',headers={"content-type":"text/event-stream"},request=request)
        return httpx.Response(200,json={"choices":[{"message":{"content":"完成"}}]},request=request)
    client = OpenAIClassificationClient(api_key="test",transport=httpx.MockTransport(respond),limiter=lambda:None)
    try:
        call = client.chat_completion_stream if stream else client.chat_completion
        assert call([{"role":"user","content":"读取本地文件"}],tools=definitions)["content"] == "完成"
        assert seen[0]["tools"] == definitions
        result = registry.execute("read_workspace_file",{"path":"note.md"},presets["general"].allowed_tools).result
        assert "本地文件正文" in json.dumps(result,ensure_ascii=False)
        with pytest.raises(AIClassificationError):
            call([{"role":"user","content":"x"}],tools=definitions * 4)
    finally:
        client.close()
        engine.dispose()
