import { invoke } from "@/lib/api";
import type { ViewName } from "@/lib/types";

export interface AIConfigurationPreflight {
  enabled: boolean;
  configured: boolean;
  ready_for_request: boolean;
  connection_verified: boolean;
  model: string;
  key_status_available?: boolean;
  reasons: string[];
  streaming_implemented: boolean;
}

export interface AICapabilityInventory {
  schema_version: number;
  preflight: AIConfigurationPreflight;
  tasks: Array<{
    page_id: ViewName;
    task_id: string;
    title: string;
    implementation_status: "implemented" | "partial" | "planned";
    output_mode: string;
    budget_status: string;
  }>;
}

export function getAICapabilities(pageId?: ViewName) {
  return invoke<AICapabilityInventory>(
    "ai_capabilities",
    pageId ? { page_id: pageId } : {},
  );
}

export function explainAIReadiness(status: AIConfigurationPreflight) {
  if (status.key_status_available === false)
    return "暂时无法检查 AI 凭据，请检查设置后重试。";
  if (!status.enabled) return "AI 尚未启用，请先在设置中开启。";
  if (status.reasons.includes("key_not_saved"))
    return "尚未保存模型接口密钥，请先配置 AI 连接。";
  if (status.reasons.includes("model_not_supported"))
    return "当前模型与已接入的协议不匹配，请检查 AI 设置。";
  if (!status.ready_for_request) return "AI 配置尚未就绪，请检查设置。";
  return status.connection_verified
    ? "AI 连接已验证；学习材料在校验后完整发布。"
    : "AI 已配置，学习材料在校验后完整发布。";
}
