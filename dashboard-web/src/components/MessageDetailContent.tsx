import {
  type KeyboardEvent,
  type MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  getMessageResource,
  openExternal,
  openMailAttachment,
  revealMailAttachment,
} from "@/lib/api";
import { formatSize } from "@/lib/format";
import type {
  MailMessageAttachment,
  MessageDetail,
  MessageKind,
} from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

const ALLOWED_TAGS = new Set([
  "p",
  "br",
  "div",
  "ul",
  "ol",
  "li",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "b",
  "strong",
  "i",
  "em",
  "u",
  "pre",
  "code",
  "blockquote",
  "a",
  "img",
]);
const DROP_WITH_CONTENT = new Set([
  "script",
  "style",
  "iframe",
  "object",
  "embed",
  "template",
  "noscript",
]);

function isHttpsUrl(value: string) {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function copySafeNode(node: Node, output: Document): Node | null {
  if (node.nodeType === Node.TEXT_NODE) {
    return output.createTextNode(node.textContent ?? "");
  }
  if (!(node instanceof Element)) return null;

  const tag = node.tagName.toLowerCase();
  if (DROP_WITH_CONTENT.has(tag)) return null;

  if (!ALLOWED_TAGS.has(tag)) {
    const fragment = output.createDocumentFragment();
    for (const child of Array.from(node.childNodes)) {
      const safeChild = copySafeNode(child, output);
      if (safeChild) fragment.append(safeChild);
    }
    return fragment;
  }

  const safeElement = output.createElement(tag);
  if (tag === "a") {
    const href = node.getAttribute("href");
    if (href && isHttpsUrl(href)) safeElement.setAttribute("href", href);
  } else if (tag === "img") {
    const resourceId = node.getAttribute("data-resource-id");
    const alt = node.getAttribute("alt");
    if (resourceId) {
      safeElement.setAttribute("data-resource-id", resourceId);
    }
    if (alt !== null) safeElement.setAttribute("alt", alt);
  }

  for (const child of Array.from(node.childNodes)) {
    const safeChild = copySafeNode(child, output);
    if (safeChild) safeElement.append(safeChild);
  }
  return safeElement;
}

export function sanitizeMessageHtml(html: string) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const output = document.implementation.createHTMLDocument("");
  const container = output.createElement("div");
  for (const child of Array.from(parsed.body.childNodes)) {
    const safeChild = copySafeNode(child, output);
    if (safeChild) container.append(safeChild);
  }
  return container.innerHTML;
}

function isMailAttachment(
  attachment: MessageDetail["attachments"][number],
): attachment is MailMessageAttachment {
  return "id" in attachment;
}

function messageError(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

interface MessageDetailContentProps {
  detail: MessageDetail;
  kind: MessageKind;
  sourceId: string;
}

type PreviewImage = {
  alt: string;
  messageKey: string;
  src: string;
};

function MessageImagePreview({
  alt,
  onClose,
  src,
}: {
  alt: string;
  onClose: () => void;
  src: string;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useModalFocus(dialogRef, onClose, { initialFocusRef: closeButtonRef });

  return (
    <div
      className="message-dialog-layer message-image-preview-layer"
      data-modal-layer
    >
      <button
        type="button"
        className="message-dialog-backdrop"
        aria-label="关闭图片预览"
        onClick={onClose}
      />
      <section
        ref={dialogRef}
        className="message-image-preview-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="正文图片预览"
        tabIndex={-1}
      >
        <header className="message-image-preview-header">
          <span>{alt || "正文图片"}</span>
          <Button
            ref={closeButtonRef}
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
          >
            关闭
          </Button>
        </header>
        <div className="message-image-preview-stage">
          <img src={src} alt={alt || "正文图片"} />
        </div>
      </section>
    </div>
  );
}

export function MessageDetailContent({
  detail,
  kind,
  sourceId,
}: MessageDetailContentProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [busyAttachment, setBusyAttachment] = useState<string | null>(null);
  const [previewImage, setPreviewImage] = useState<PreviewImage | null>(null);
  const { showToast } = useToast();
  const messageKey = `${kind}:${sourceId}`;
  const safeHtml = useMemo(
    () =>
      detail.body_html?.trim() ? sanitizeMessageHtml(detail.body_html) : "",
    [detail.body_html],
  );

  useEffect(() => {
    const container = bodyRef.current;
    if (!container || detail.pending_body_sync || !safeHtml) return;

    let cancelled = false;
    const inlineData = new Map(
      detail.attachments
        .filter(isMailAttachment)
        .filter((attachment) => Boolean(attachment.inline_data_url))
        .map((attachment) => [
          attachment.id,
          attachment.inline_data_url as string,
        ]),
    );
    const resourceIds = new Set(
      detail.resources.map((resource) => resource.id),
    );
    const requests = new Map<string, Promise<string>>();
    const cleanups: Array<() => void> = [];

    for (const image of Array.from(
      container.querySelectorAll<HTMLImageElement>("img[data-resource-id]"),
    )) {
      const resourceId = image.dataset.resourceId;
      if (!resourceId) continue;
      const originalAlt = image.alt;
      let retryButton: HTMLButtonElement | null = null;

      const clearFailure = () => {
        image.classList.remove("message-resource-failed");
        image.alt = originalAlt;
        retryButton?.remove();
        retryButton = null;
      };
      const markPreviewable = () => {
        image.classList.add("message-resource-previewable");
        image.tabIndex = 0;
        image.setAttribute("role", "button");
        image.setAttribute(
          "aria-label",
          originalAlt ? `放大查看：${originalAlt}` : "放大查看正文图片",
        );
      };
      const markFailed = () => {
        image.removeAttribute("src");
        image.classList.remove("message-resource-previewable");
        image.removeAttribute("tabindex");
        image.removeAttribute("role");
        image.removeAttribute("aria-label");
        image.classList.add("message-resource-failed");
        image.alt = originalAlt || "图片加载失败";
        if (retryButton) return;
        retryButton = document.createElement("button");
        retryButton.type = "button";
        retryButton.className = "message-resource-retry";
        retryButton.textContent = "重试";
        image.insertAdjacentElement("afterend", retryButton);
      };
      const loadRemoteImage = (force = false) => {
        clearFailure();
        let request = force ? undefined : requests.get(resourceId);
        if (!request) {
          request = getMessageResource(kind, sourceId, resourceId).then(
            (result) => result.data_url,
          );
          requests.set(resourceId, request);
        }
        void request
          .then((dataUrl) => {
            if (!cancelled) {
              clearFailure();
              image.src = dataUrl;
              markPreviewable();
            }
          })
          .catch(() => {
            requests.delete(resourceId);
            if (!cancelled) markFailed();
          });
      };
      const handleImageError = () => markFailed();
      const handleRetry = (event: Event) => {
        if (event.target !== retryButton) return;
        event.preventDefault();
        event.stopPropagation();
        loadRemoteImage(true);
      };
      image.addEventListener("error", handleImageError);
      container.addEventListener("click", handleRetry);
      cleanups.push(() => {
        image.removeEventListener("error", handleImageError);
        container.removeEventListener("click", handleRetry);
        retryButton?.remove();
      });

      const inlineUrl = inlineData.get(resourceId);
      if (inlineUrl) {
        image.src = inlineUrl;
        markPreviewable();
        continue;
      }
      if (!resourceIds.has(resourceId)) {
        markFailed();
        continue;
      }
      loadRemoteImage();
    }

    return () => {
      cancelled = true;
      for (const cleanup of cleanups) cleanup();
    };
  }, [
    detail.attachments,
    detail.pending_body_sync,
    detail.resources,
    kind,
    safeHtml,
    sourceId,
  ]);

  const openLink = useCallback(
    async (url: string) => {
      try {
        await openExternal(url);
        showToast({ kind: "success", message: "已在浏览器打开链接。" });
      } catch (reason) {
        showToast({
          kind: "error",
          message: messageError(reason, "链接打开失败"),
        });
      }
    },
    [showToast],
  );

  const handleBodyClick = (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const image = target.closest<HTMLImageElement>(
      "img.message-resource-previewable",
    );
    if (image && event.currentTarget.contains(image) && image.src) {
      event.preventDefault();
      event.stopPropagation();
      setPreviewImage({ alt: image.alt, messageKey, src: image.src });
      return;
    }
    const anchor = target.closest("a");
    if (!anchor || !event.currentTarget.contains(anchor)) return;
    event.preventDefault();
    event.stopPropagation();
    const href = anchor.getAttribute("href");
    if (href && isHttpsUrl(href)) void openLink(href);
  };

  const handleBodyKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target;
    if (!(target instanceof HTMLImageElement)) return;
    if (
      !target.classList.contains("message-resource-previewable") ||
      !target.src
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setPreviewImage({ alt: target.alt, messageKey, src: target.src });
  };

  const runMailAction = async (
    attachment: MailMessageAttachment,
    action: "open" | "reveal",
  ) => {
    const key = `${attachment.id}:${action}`;
    setBusyAttachment(key);
    try {
      if (action === "open") {
        await openMailAttachment(sourceId, attachment.id);
      } else {
        await revealMailAttachment(sourceId, attachment.id);
      }
      showToast({
        kind: "success",
        message:
          action === "open"
            ? `已打开附件“${attachment.name}”。`
            : `已在 Finder 中显示附件“${attachment.name}”。`,
      });
    } catch (reason) {
      showToast({
        kind: "error",
        message: messageError(reason, "附件操作失败"),
      });
    } finally {
      setBusyAttachment(null);
    }
  };

  return (
    <div className="message-content-stack">
      {detail.pending_body_sync ? (
        <div className="message-detail-pending" role="status">
          正文尚未同步，请点击同步后再试
        </div>
      ) : safeHtml ? (
        <div
          ref={bodyRef}
          className="message-detail-body message-detail-html"
          role="document"
          tabIndex={0}
          onClick={handleBodyClick}
          onKeyDown={handleBodyKeyDown}
          dangerouslySetInnerHTML={{ __html: safeHtml }}
        />
      ) : (
        <div className="message-detail-body" role="document" tabIndex={0}>
          {detail.body || "（无正文）"}
        </div>
      )}

      {detail.attachments.length > 0 && (
        <section className="message-attachments" aria-label="附件">
          <h4>附件</h4>
          <ul>
            {detail.attachments.map((attachment, index) => {
              const mail = isMailAttachment(attachment);
              const key = mail ? attachment.id : `${attachment.url}:${index}`;
              return (
                <li key={key} className="message-attachment">
                  <div>
                    <strong>{attachment.name}</strong>
                    <span>
                      {attachment.type || "类型未知"} ·{" "}
                      {formatSize(attachment.size)}
                    </span>
                  </div>
                  <div className="message-attachment-actions">
                    {mail ? (
                      attachment.available ? (
                        <>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={busyAttachment !== null}
                            onClick={() =>
                              void runMailAction(attachment, "open")
                            }
                          >
                            {busyAttachment === `${attachment.id}:open`
                              ? "处理中…"
                              : "打开"}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busyAttachment !== null}
                            onClick={() =>
                              void runMailAction(attachment, "reveal")
                            }
                          >
                            {busyAttachment === `${attachment.id}:reveal`
                              ? "处理中…"
                              : "在 Finder 显示"}
                          </Button>
                        </>
                      ) : (
                        <span>附件过大或未缓存</span>
                      )
                    ) : isHttpsUrl(attachment.url) ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void openLink(attachment.url)}
                      >
                        在浏览器打开
                      </Button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {previewImage?.messageKey === messageKey && (
        <MessageImagePreview
          alt={previewImage.alt}
          src={previewImage.src}
          onClose={() => setPreviewImage(null)}
        />
      )}
    </div>
  );
}
