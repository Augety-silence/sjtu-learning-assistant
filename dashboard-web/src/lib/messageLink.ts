import type { MessageKind } from "@/lib/types";

export function parseMessageLink(value: string) {
  if (!value.startsWith("#/messages?")) return null;
  const params = new URLSearchParams(value.slice(value.indexOf("?") + 1));
  const kind = params.get("kind");
  const sourceId = params.get("source_id");
  const ref = params.get("ref");
  if (
    ["email", "announcement", "assignment"].includes(kind ?? "") &&
    ref &&
    new RegExp(`^${kind}:[0-9]{1,18}$`).test(ref) &&
    !sourceId
  )
    return { kind: kind as MessageKind, ref, sourceId: undefined };
  if (
    !["email", "announcement", "assignment"].includes(kind ?? "") ||
    !sourceId ||
    sourceId.length > 512 ||
    /[\u0000-\u001f\u007f]/.test(sourceId)
  )
    return null;
  return { kind: kind as MessageKind, sourceId, ref: undefined };
}
