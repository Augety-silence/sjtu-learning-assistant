import { useCallback, useRef, useState } from "react";
import type { AIAgentLiveStatus } from "@/lib/types";

export interface ChatSessionTask {
  taskId: string | null;
  startedAt: number | string | null;
  stopping: boolean;
  liveStatus: { taskId: string; status: AIAgentLiveStatus } | null;
  controller: AbortController;
  pollController: AbortController;
}

// Controllers belong to a session, not the currently selected conversation.
// The ref is updated synchronously so two submissions cannot race a React render.
export function useChatSessionTasks() {
  const tasks = useRef(new Map<string, ChatSessionTask>());
  const [, setRevision] = useState(0);
  const update = useCallback(
    (sessionId: string, change: Partial<ChatSessionTask>) => {
      const task = tasks.current.get(sessionId);
      if (!task) return;
      Object.assign(task, change);
      setRevision((value) => value + 1);
    },
    [],
  );
  const begin = useCallback((sessionId: string, taskId: string | null) => {
    if (tasks.current.has(sessionId)) return null;
    const task: ChatSessionTask = {
      taskId,
      startedAt: null,
      stopping: false,
      liveStatus: null,
      controller: new AbortController(),
      pollController: new AbortController(),
    };
    tasks.current.set(sessionId, task);
    setRevision((value) => value + 1);
    return task;
  }, []);
  const finish = useCallback((sessionId: string, task: ChatSessionTask) => {
    if (tasks.current.get(sessionId) !== task) return;
    tasks.current.delete(sessionId);
    setRevision((value) => value + 1);
  }, []);
  return { tasks, update, begin, finish };
}
