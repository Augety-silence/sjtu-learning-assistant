import { ChevronDown, Folder } from "lucide-react";
import type { DragEvent, KeyboardEvent, RefCallback } from "react";
import { containerChildren } from "@/lib/materialTree";
import type { MaterialNode } from "@/lib/types";

export type MaterialDragSource = {
  sourceId: string;
  courseId: string;
  name: string;
};

export type MaterialDropHandler = (
  event: DragEvent<HTMLElement>,
  node: MaterialNode,
) => void;

type HoverHandler = (node: MaterialNode | null) => void;

export function isMaterialDropTarget(node: MaterialNode) {
  return node.kind === "category" || node.kind === "folder";
}

export function materialDropClass(
  node: MaterialNode,
  dragSource: MaterialDragSource | null,
  dropTargetId: string | null,
) {
  if (dropTargetId !== node.id || !dragSource) return "";
  return node.course_id === dragSource.courseId
    ? " material-drop-allowed"
    : " material-drop-rejected";
}

export function materialTargetAriaLabel(node: MaterialNode) {
  return isMaterialDropTarget(node)
    ? `${node.name}，可接收同课程资料拖放`
    : node.name;
}

interface MaterialTreeBranchProps {
  node: MaterialNode;
  level: number;
  selectedId: string;
  activeId: string;
  expandedIds: ReadonlySet<string>;
  select: (node: MaterialNode) => void;
  focusItem: (id: string) => void;
  toggle: (id: string, expanded: boolean) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>, node: MaterialNode) => void;
  registerItem: (id: string) => RefCallback<HTMLElement>;
  dragSource: MaterialDragSource | null;
  dropTargetId: string | null;
  hoverTarget: HoverHandler;
  drop: MaterialDropHandler;
}

export function MaterialTreeBranch({
  node,
  level,
  selectedId,
  activeId,
  expandedIds,
  select,
  focusItem,
  toggle,
  onKeyDown,
  registerItem,
  dragSource,
  dropTargetId,
  hoverTarget,
  drop,
}: MaterialTreeBranchProps) {
  const children = containerChildren(node);
  const expanded = children.length > 0 && expandedIds.has(node.id);
  const target = isMaterialDropTarget(node);
  const dropClass = materialDropClass(node, dragSource, dropTargetId);

  return (
    <div
      ref={registerItem(node.id)}
      className={`tree-branch${dropClass}`}
      role="treeitem"
      tabIndex={activeId === node.id ? 0 : -1}
      aria-level={level}
      aria-selected={selectedId === node.id}
      aria-expanded={children.length > 0 ? expanded : undefined}
      aria-label={materialTargetAriaLabel(node)}
      aria-describedby={target ? "material-drop-help" : undefined}
      data-material-target-id={target ? node.id : undefined}
      onFocus={(event) => {
        if (event.target === event.currentTarget) focusItem(node.id);
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        onKeyDown(event, node);
      }}
      onClick={(event) => {
        event.stopPropagation();
        select(node);
        if (children.length > 0) toggle(node.id, !expanded);
      }}
      onDragEnter={
        target
          ? (event) => {
              event.preventDefault();
              event.stopPropagation();
              hoverTarget(node);
            }
          : undefined
      }
      onDragOver={
        target
          ? (event) => {
              event.preventDefault();
              event.stopPropagation();
              event.dataTransfer.dropEffect =
                dragSource?.courseId === node.course_id ? "move" : "none";
              hoverTarget(node);
            }
          : undefined
      }
      onDragLeave={
        target
          ? (event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node)) {
                hoverTarget(null);
              }
            }
          : undefined
      }
      onDrop={target ? (event) => drop(event, node) : undefined}
    >
      <div
        className={`tree-row ${selectedId === node.id ? "tree-selected" : ""}${dropClass}`}
      >
        {children.length > 0 ? (
          <ChevronDown
            className={`tree-chevron ${expanded ? "tree-chevron-open" : ""}`}
            aria-hidden="true"
          />
        ) : (
          <span className="tree-chevron-placeholder" aria-hidden="true" />
        )}
        <Folder className="tree-kind-icon" aria-hidden="true" />
        <span>{node.name}</span>
      </div>
      {expanded && (
        <div className="tree-children" role="group">
          {children.map((child) => (
            <MaterialTreeBranch
              key={child.id}
              node={child}
              level={level + 1}
              selectedId={selectedId}
              activeId={activeId}
              expandedIds={expandedIds}
              select={select}
              focusItem={focusItem}
              toggle={toggle}
              onKeyDown={onKeyDown}
              registerItem={registerItem}
              dragSource={dragSource}
              dropTargetId={dropTargetId}
              hoverTarget={hoverTarget}
              drop={drop}
            />
          ))}
        </div>
      )}
    </div>
  );
}
