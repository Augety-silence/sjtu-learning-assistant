import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  authorizeArchiveRoot,
  createAiChatSession,
  createVideoSlidesPdf,
  deleteBackupToken,
  executeRestore,
  exportDebugBundle,
  getAiPresets,
  getArchiveDetail,
  getArchiveJobEvents,
  getArchiveJobs,
  getArchiveList,
  getBackupStatus,
  getMessageResource,
  getSettings,
  getTranscriptBatch,
  getTranscriptV2Artifacts,
  getVideoSubtitles,
  ingestAiAttachment,
  invoke,
  openExternal,
  openMailAttachment,
  planRestore,
  readTranscriptArtifact,
  readTranscriptV2Artifact,
  retryArchive,
  revealMailAttachment,
  saveBackupToken,
  sendAiChatMessage,
  startArchive,
  startCloudBackup,
  startTranscriptBatch,
  updateSettings,
} from "@/lib/api";

beforeEach(() => {
  vi.stubGlobal("window", globalThis);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("pywebview bridge client", () => {
  it("invokes the allowlisted bridge without fetch", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { status: "ok" } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    await expect(invoke<{ status: string }>("health")).resolves.toEqual({
      status: "ok",
    });
    expect(bridge).toHaveBeenCalledWith("health", {});
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("exports the debug bundle without renderer-controlled paths", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { status: "created", filename: "debug.zip", size: 321 },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await expect(exportDebugBundle()).resolves.toEqual({
      status: "created",
      filename: "debug.zip",
      size: 321,
    });
    expect(bridge).toHaveBeenCalledWith("debug_bundle_export", {});
  });

  it("dispatches remote subtitle and slide PDF actions with only source_id", async () => {
    const bridge = vi.fn().mockResolvedValue({ ok: true, data: {} });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await getVideoSubtitles("sjtu-video:12:99");
    expect(bridge).toHaveBeenLastCalledWith("video_subtitles", {
      source_id: "sjtu-video:12:99",
    });
    await createVideoSlidesPdf("sjtu-video:12:99");
    expect(bridge).toHaveBeenLastCalledWith("video_slides_pdf", {
      source_id: "sjtu-video:12:99",
    });
  });

  it("surfaces sanitized bridge errors", async () => {
    vi.stubGlobal("pywebview", {
      api: {
        invoke: vi.fn().mockResolvedValue({
          ok: false,
          error: { code: "operation_failed", message: "操作失败" },
        }),
      },
    });
    await expect(invoke("overview")).rejects.toThrow("操作失败");
  });

  it("loads settings through the non-secret status action only", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { archive_root: "/tmp/archive" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await getSettings();
    expect(bridge).toHaveBeenCalledOnce();
    expect(bridge).toHaveBeenCalledWith("settings_status", {});
  });

  it("sends only the settings update payload through the bridge", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { mail_account: "student-id" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await updateSettings({ mail_account: "student-id" });
    expect(bridge).toHaveBeenCalledWith("settings_update", {
      mail_account: "student-id",
    });
  });

  it("uses the rich message resource and mail attachment bridge DTOs", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { data_url: "data:image/png;base64,a" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getMessageResource("announcement", "message-1", "resource-1");
    expect(bridge).toHaveBeenLastCalledWith("message_resource", {
      kind: "announcement",
      source_id: "message-1",
      resource_id: "resource-1",
    });
    await openMailAttachment("mail-1", "attachment-1");
    expect(bridge).toHaveBeenLastCalledWith("mail_attachment_open", {
      kind: "email",
      source_id: "mail-1",
      attachment_id: "attachment-1",
    });
    await revealMailAttachment("mail-1", "attachment-1");
    expect(bridge).toHaveBeenLastCalledWith("mail_attachment_reveal", {
      kind: "email",
      source_id: "mail-1",
      attachment_id: "attachment-1",
    });
  });

  it("sends the complete agent preset contract for new sessions and turns", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { default_preset_id: "general", items: [] },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getAiPresets();
    expect(bridge).toHaveBeenLastCalledWith("ai_presets", {});

    await createAiChatSession("auto", "standard", "general");
    expect(bridge).toHaveBeenLastCalledWith("ai_chat_new", {
      model: "auto",
      thinking_depth: "standard",
      preset_id: "general",
    });

    await sendAiChatMessage(
      "session-1",
      "查询课程文件",
      "deepseek-chat",
      "deep",
      "review-planner",
    );
    expect(bridge).toHaveBeenLastCalledWith("ai_chat_send", {
      session_id: "session-1",
      content: "查询课程文件",
      model: "deepseek-chat",
      thinking_depth: "deep",
      preset_id: "review-planner",
    });
  });

  it("sends a dropped path only to the attachment ingest bridge action", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { id: 7, name: "notes.md", status: "local" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await ingestAiAttachment("/tmp/notes.md");

    expect(bridge).toHaveBeenCalledWith("ai_attachment_ingest", {
      path: "/tmp/notes.md",
    });
  });

  it("sends an explicit local-retention choice for the backup contract", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { status: "idle" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getBackupStatus();
    expect(bridge).toHaveBeenLastCalledWith("backup_status", {});
    await startCloudBackup(false);
    expect(bridge).toHaveBeenLastCalledWith("backup_start", {
      remove_local: false,
    });
  });

  it("sends the token only to Keychain save and uses an empty delete payload", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, data: { configured: true } })
      .mockResolvedValueOnce({ ok: true, data: { configured: false } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    const token = ["private", "pan", "token"].join("-");

    const saved = await saveBackupToken(token);
    expect(saved).toEqual({ configured: true });
    expect(JSON.stringify(saved)).not.toContain(token);
    expect(bridge).toHaveBeenLastCalledWith("backup_token_save", { token });

    await expect(deleteBackupToken()).resolves.toEqual({ configured: false });
    expect(bridge).toHaveBeenLastCalledWith("backup_token_delete", {});
  });

  it("routes external URLs through the bridge", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { status: "opened" } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await openExternal("https://example.edu");
    expect(bridge).toHaveBeenCalledWith("open_external", {
      url: "https://example.edu",
    });
  });
});

describe("cloud archive bridge payloads", () => {
  it("uses bounded backend pagination and exact detail/job payloads", async () => {
    const bridge = vi.fn().mockResolvedValue({ ok: true, data: { items: [] } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getArchiveList({
      limit: 20,
      query: "week",
      status: "archived",
      sort: "size_desc",
      cursor: "next-20",
    });
    expect(bridge).toHaveBeenLastCalledWith("archive_list", {
      limit: 20,
      query: "week",
      status: "archived",
      sort: "size_desc",
      cursor: "next-20",
    });
    await getArchiveList({ limit: 20, offset: 40 });
    expect(bridge).toHaveBeenLastCalledWith("archive_list", {
      limit: 20,
      offset: 40,
    });
    await getArchiveDetail("entry-1");
    expect(bridge).toHaveBeenLastCalledWith("archive_detail", {
      entry_id: "entry-1",
    });
    await getArchiveJobs(12, "failed");
    expect(bridge).toHaveBeenLastCalledWith("archive_jobs", {
      limit: 12,
      status: "failed",
    });
    await getArchiveJobEvents("job-1");
    expect(bridge).toHaveBeenLastCalledWith("archive_job_events", {
      job_id: "job-1",
    });
  });

  it("keeps native picker and restore payloads allowlisted", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { id: "result" } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await startArchive("request-1");
    expect(bridge).toHaveBeenLastCalledWith("archive_start", {
      idempotency_key: "request-1",
    });
    await retryArchive("job-1");
    expect(bridge).toHaveBeenLastCalledWith("archive_retry", {
      job_id: "job-1",
    });
    await authorizeArchiveRoot();
    expect(bridge).toHaveBeenLastCalledWith("archive_authorize_root", {});
    await planRestore("entry-1", {
      versionId: "version-2",
      mode: "choose_location",
      authorizedRootId: "root-1",
    });
    expect(bridge).toHaveBeenLastCalledWith("restore_plan", {
      entry_id: "entry-1",
      version_id: "version-2",
      mode: "choose_location",
      authorized_root_id: "root-1",
    });
    await executeRestore("restore-1", "compare", true);
    expect(bridge).toHaveBeenLastCalledWith("restore_execute", {
      job_id: "restore-1",
      conflict_policy: "compare",
      confirm_create_dirs: true,
    });
  });

  it("sends only opaque transcript identifiers through the bridge", async () => {
    const bridge = vi.fn().mockResolvedValue({ ok: true, data: { jobs: [] } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await startTranscriptBatch(12, ["sjtu-video:12:99"]);
    expect(bridge).toHaveBeenLastCalledWith("transcript_batch_start", {
      course_id: 12,
      source_ids: ["sjtu-video:12:99"],
    });
    const id = "a".repeat(32);
    await getTranscriptBatch(id);
    expect(bridge).toHaveBeenLastCalledWith("transcript_batch_get", {
      batch_id: id,
    });
    await readTranscriptArtifact(`${id}:summary`);
    expect(bridge).toHaveBeenLastCalledWith("transcript_artifact_read", {
      artifact_id: `${id}:summary`,
    });
    await getTranscriptV2Artifacts(id);
    expect(bridge).toHaveBeenLastCalledWith("transcript_v2_artifacts", {
      job_id: id,
    });
    const v2ArtifactId = `${id}:v2:${"b".repeat(32)}`;
    await readTranscriptV2Artifact(v2ArtifactId);
    expect(bridge).toHaveBeenLastCalledWith("transcript_v2_artifact_read", {
      artifact_id: v2ArtifactId,
    });
  });
});
