"""Read-only AI capability inventory and configuration preflight.

This is a product contract, not a dispatcher. Planned tasks/budgets do not become
implemented features merely because configuration is present. No key, network,
database or worker is accessed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .ai_classifier import ALLOWED_AI_MODELS

OUTPUT_MODES = frozenset({
    "one_shot", "validated_artifact", "progress_then_artifact", "text_stream_candidate", "text_stream",
})


@dataclass(frozen=True)
class AITaskSpec:
    page_id: str
    task_id: str
    title: str
    implementation_status: str
    backend_actions: tuple[str, ...]
    output_mode: str
    input_sources: tuple[str, ...]
    input_requirements: tuple[str, ...]
    max_input_tokens: int
    max_output_tokens: int
    max_model_calls: int
    readonly_decisions: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    decision_boundary: str = "readonly_suggestions"
    budget_status: str = "enforced_for_page_ai"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for name, value in result.items():
            if isinstance(value, tuple):
                result[name] = list(value)
        result["page_ai_available"] = True
        result["task_runtime"] = "persistent_ledger"
        return result


TASK_SPECS = (
    AITaskSpec("overview", "overview_priorities", "今日学习优先级", "planned", (), "one_shot",
               ("courses", "deadlines", "messages"), ("synced_local_records",), 4000, 1000, 1,
               ("rank_priorities", "explain_evidence", "suggest_next_step")),
    AITaskSpec("calendar", "calendar_conflicts", "日程冲突与准备建议", "planned", (), "one_shot",
               ("schedule", "deadlines"), ("explicit_timezone", "event_times"), 4000, 1000, 1,
               ("identify_conflicts", "suggest_preparation")),
    AITaskSpec("messages", "message_actions", "消息重点与待办建议", "planned", (), "one_shot",
               ("message_detail", "message_attachments"), ("selected_message",), 6000, 1200, 1,
               ("extract_deadlines", "classify_actionability", "draft_reply"),
               ("no_send_or_create_deadline",)),
    AITaskSpec("assignments", "assignment_plan", "作业要求与完成计划", "planned", (), "validated_artifact",
               ("assignment_detail", "rubric", "selected_course_materials"), ("selected_assignment",), 8000, 2200, 2,
               ("explain_requirements", "suggest_steps", "identify_missing_evidence"),
               ("no_submission_or_deadline_mutation",)),
    AITaskSpec("materials", "material_classification", "课程资料分类", "implemented", ("archive_organize",), "validated_artifact",
               ("course_file_metadata",), ("current_course_files",), 4000, 1200, 1,
               ("suggest_category",), ("classification_is_metadata_only", "archive_action_has_separate_write_boundary")),
    AITaskSpec("grades", "grade_reflection", "成绩与复习薄弱点", "planned", (), "one_shot",
               ("grades", "rubrics", "feedback"), ("available_grade_records",), 4000, 1200, 1,
               ("identify_evidence_based_gaps", "suggest_review"), ("no_inferred_missing_grades",)),
    AITaskSpec("roster", "roster_questions", "名单查询与汇总建议", "planned", (), "one_shot",
               ("selected_roster_fields",), ("authorized_course_role", "minimum_necessary_fields"), 3000, 800, 1,
               ("summarize_selected_fields",), ("no_student_ability_inference", "no_roster_mutation")),
    AITaskSpec("grading", "rubric_feedback", "评分依据与反馈草稿", "planned", (), "validated_artifact",
               ("submission", "rubric"), ("authorized_grading_role", "selected_submission"), 8000, 2200, 2,
               ("suggest_rubric_evidence", "draft_feedback"), ("no_grade_publish_or_mutation",)),
    AITaskSpec("videos", "video_learning_products", "字幕、讲义与主动练习", "partial",
               ("transcript_batch_start", "transcript_jobs", "transcript_artifacts"), "progress_then_artifact",
               ("remote_subtitles", "subtitle_cues", "course_memory"), ("video_space_source", "usable_subtitles"),
               12000, 4096, 24, ("identify_topics", "generate_grounded_practice", "flag_uncertainty"),
               ("no_asr_fallback", "correction_not_fed_back_to_handout", "budget_not_enforced")),
    AITaskSpec("backup", "backup_guidance", "备份范围与异常解释", "planned", (), "one_shot",
               ("backup_manifest", "backup_status"), ("selected_backup_scope",), 3000, 800, 1,
               ("explain_status", "suggest_scope"), ("no_upload_restore_delete",)),
    AITaskSpec("local-projects", "project_next_steps", "本地项目资料与下一步", "planned", (), "one_shot",
               ("selected_project_index", "selected_project_notes"), ("explicit_project_scope",), 6000, 1400, 1,
               ("suggest_next_step", "identify_material_gaps"), ("no_file_mutation",)),
    AITaskSpec("knowledge", "knowledge_compile", "课程知识库编译", "partial",
               ("knowledge_compiler_start", "knowledge_compiler_status"), "progress_then_artifact",
               ("selected_markdown_sources",), ("explicit_source_and_target_scope",), 12000, 4096, 24,
               ("propose_notes", "link_concepts", "flag_uncertainty"),
               ("compiler_has_separate_file_write_boundary", "hardcoded_stage_models", "budget_not_enforced")),
    AITaskSpec("ai-chat", "learning_chat", "基于本地证据的学习问答", "implemented",
               ("ai_chat_stream_start", "ai_task_status", "ai_task_cancel", "ai_usage_summary"), "text_stream",
               ("chat_history", "readonly_local_tools", "selected_ai_attachments"), ("selected_session",),
               12000, 3000, 6, ("retrieve_evidence", "explain", "suggest_next_step"),
               ("tool_selection_steps_remain_one_shot", "no_transcript_or_course_memory_tool")),
    AITaskSpec("settings", "connection_diagnostics", "AI连接配置解释", "partial", ("test_ai_connection",), "one_shot",
               ("nonsecret_ai_settings", "explicit_connection_test_result"), ("no_raw_key_in_context",),
               1000, 500, 1, ("explain_configuration", "suggest_compatibility_check"),
               ("preflight_does_not_test_connection", "connection_test_requires_explicit_action")),
)


def ai_task_catalog(page_id: str | None = None) -> dict[str, object]:
    """Return inspectable specs; unknown page IDs raise instead of substituting."""
    if page_id is not None and page_id not in {task.page_id for task in TASK_SPECS}:
        raise ValueError("Unknown AI capability page")
    tasks = [task.to_dict() for task in TASK_SPECS if page_id is None or task.page_id == page_id]
    return {"schema_version": 1, "catalog_kind": "implementation_and_plan", "tasks": tasks}


def ai_configuration_preflight(
    settings: object, *, protocol_client_supported: bool = True,
    connection_verified: bool = False,
) -> dict[str, object]:
    """Check declared config only; key_saved never proves key access/connectivity.

    ``connection_verified`` may only be supplied from a separate explicit probe.
    A default preflight neither loads a key nor performs that probe.
    """
    if type(protocol_client_supported) is not bool or type(connection_verified) is not bool:
        raise ValueError("Preflight evidence flags must be booleans")

    def field(name: str) -> object:
        return settings.get(name) if isinstance(settings, Mapping) else getattr(settings, name, None)

    enabled = field("ai_enabled") is True
    key_saved = field("ai_key_saved") is True
    raw_model = field("ai_model")
    model = raw_model.strip() if type(raw_model) is str else ""
    model_supported = model in ALLOWED_AI_MODELS
    configured = key_saved and model_supported and protocol_client_supported
    reasons = []
    if not enabled:
        reasons.append("ai_disabled")
    if not key_saved:
        reasons.append("key_not_saved")
    if not model_supported:
        reasons.append("model_not_supported")
    if not protocol_client_supported:
        reasons.append("protocol_client_not_supported")
    return {
        "schema_version": 1, "enabled": enabled, "key_saved": key_saved,
        "model": model, "model_supported": model_supported,
        "protocol_client_supported": protocol_client_supported,
        "configured": configured, "ready_for_request": enabled and configured,
        "connection_verified": connection_verified if configured else False,
        "key_access_verified": False, "network_probe_performed": False,
        "reasons": reasons, "usage_status": "unknown_until_provider_response",
        "streaming_implemented": True,
    }


def ai_capabilities(
    settings: object, *, page_id: str | None = None,
    protocol_client_supported: bool = True, connection_verified: bool = False,
) -> dict[str, object]:
    """Combined pure DTO for DashboardService/desktop API integration."""
    return {
        **ai_task_catalog(page_id),
        "preflight": ai_configuration_preflight(
            settings, protocol_client_supported=protocol_client_supported,
            connection_verified=connection_verified,
        ),
    }
