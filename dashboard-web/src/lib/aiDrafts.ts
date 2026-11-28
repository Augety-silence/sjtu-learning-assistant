import { createContext } from "react";
import type { AIManagedAttachment } from "@/lib/types";

export interface AIComposerDraft {
  draft: string;
  attachments: AIManagedAttachment[];
}
export const AIComposerDraftContext = createContext<Map<
  string,
  AIComposerDraft
> | null>(null);
