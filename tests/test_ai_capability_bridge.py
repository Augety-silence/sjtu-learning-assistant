from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService


def configuration_service():
    # No constructor: default services can recover jobs and create directories.
    service = DashboardService.__new__(DashboardService)
    service._effective_settings = lambda: SimpleNamespace(
        ai_enabled=True, ai_key_saved=True, ai_model="deepseek-chat"
    )
    service.ai_key_saved_checker = Mock(return_value=True)
    service.ai_key_loader = Mock(side_effect=AssertionError("must not load a key"))
    service.ai_client_factory = Mock(side_effect=AssertionError("must not call a model"))
    return service


def test_preflight_is_scoped_readonly_and_does_not_claim_connectivity():
    service = configuration_service()
    result = service.ai_capabilities("videos")
    assert result["preflight"]["ready_for_request"] is True
    assert result["preflight"]["connection_verified"] is False
    assert [task["page_id"] for task in result["tasks"]] == ["videos"]
    service.ai_key_loader.assert_not_called()
    service.ai_client_factory.assert_not_called()


def test_credential_status_failure_blocks_new_work_without_exposing_exception():
    service = configuration_service()
    service.ai_key_saved_checker.side_effect = RuntimeError("secret-not-for-renderer")
    result = service.ai_capabilities("videos")
    assert result["preflight"]["ready_for_request"] is False
    assert result["preflight"]["key_status_available"] is False
    assert "secret-not-for-renderer" not in str(result)


def test_bridge_rejects_renderer_provider_or_evidence_claims():
    class Service:
        def __getattr__(self, name):
            return lambda *args, **kwargs: {}

        def ai_capabilities(self, page_id=None):
            return configuration_service().ai_capabilities(page_id)

    bridge = DesktopBridge(Service())
    response = bridge.invoke("ai_capabilities", {"page_id": "videos"})
    assert response["ok"] is True
    for payload in ({"page_id": []}, {"page_id": "unknown"}, {"page_id": "videos", "connection_verified": True}, {"base_url": "https://example.invalid"}):
        assert bridge.invoke("ai_capabilities", payload)["ok"] is False


def test_unknown_page_never_substitutes_another_page():
    with pytest.raises(DashboardError):
        configuration_service().ai_capabilities("unknown")


@pytest.mark.parametrize("endpoint,model,expected", [
    ("https://models.sjtu.edu.cn/api/v1", "deepseek-chat", True),
    ("https://models.sjtu.edu.cn/api/v1/", "deepseek-chat", True),
    ("https://models.sjtu.edu.cn/api/v1", "qwen", False),
    ("https://models.sjtu.edu.cn/api/v1", "minimax", False),
    ("https://example.invalid/v1", "deepseek-chat", False),
])
def test_transcript_json_mode_requires_verified_provider_and_model(endpoint, model, expected):
    service = DashboardService.__new__(DashboardService)
    service._effective_settings = lambda: SimpleNamespace(ai_enabled=True, ai_model=model, ai_base_url=endpoint)
    client = object()
    service._get_ai_client = Mock(return_value=client)
    context = service._transcript_ai_context()
    assert context.json_mode is expected
    assert context.client is client
