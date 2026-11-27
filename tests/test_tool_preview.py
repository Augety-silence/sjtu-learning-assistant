from sjtu_learning_assistant.agent_runtime.tool_preview import build_tool_preview
from sjtu_learning_assistant.agent_runtime.tools import _clip, _redact_body


def preview(result):
    return build_tool_preview(result, _clip, _redact_body)


def test_wiki_names_topics_and_documents_are_readable_and_bounded():
    value = preview({"items": [{"course": "离散数学", "term": "秋季", "topics": [{"title": "图论"}], "documents": [{"title": "第六讲"}]}] * 9})
    assert len(value["items"]) == 6
    assert value["more_items"] is True
    assert value["items"][0]["title"] == "离散数学"
    assert "图论" in value["items"][0]["detail"]
    assert "第六讲" in value["items"][0]["detail"]


def test_body_and_search_passages_keep_evidence_without_raw_objects():
    value = preview({"title": "课程说明", "text": "第一段\n第二段" + "字" * 2000, "credentials": "unsafe"})
    assert len(value["text"]) == 1600
    assert value["text_truncated"] is True
    assert "第一段\n第二段" in value["text"]
    assert "unsafe" not in str(value)
    search = preview({"items": [{"title": "图论讲义", "passages": [{"text": "欧拉路径"}]}]})
    assert search["items"][0]["excerpt"] == "欧拉路径"


def test_redaction_runs_before_truncation_in_every_field():
    value = preview({"items": [{"title": "课程 token=secret", "preview": "password=secret /Users/person/private http://u:p@host/a"}], "body": "api_key=secret\n" + "字" * 2000})
    assert "secret" not in str(value)
    assert "/Users/person" not in str(value)
    assert "u:p" not in str(value)
    assert "已隐藏" in str(value)


def test_status_only_and_malformed_collections_do_not_invent_details():
    assert preview({"count": 5, "status": "ok"}) is None
    assert preview({"items": [None, 4, {"credentials": "unsafe"}]}) is None
