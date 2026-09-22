import { ChevronDown, Folder } from "lucide-react";
import { type DragEvent, useState } from "react";
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

export function MaterialTreeBranch({
  node,
  selected,
  select,
  dragSource,
  dropTargetId,
  hoverTarget,
  drop,
}: {
  node: MaterialNode;
  selected: string;
  select: (node: MaterialNode) => void;
  dragSource: MaterialDragSource | null;
  dropTargetId: string | null;
  hoverTarget: HoverHandler;
  drop: MaterialDropHandler;
}) {
  const [expanded, setExpanded] = useState(
    node.kind === "root" || node.kind === "term" || node.kind === "course",
  );
  const children = containerChildren(node);
  if (node.kind === "root") {
    return (
      <>
        {children.map((child) => (
          <MaterialTreeBranch
            key={child.id}
            node={child}
            selected={selected}
            select={select}
            dragSource={dragSource}
            dropTargetId={dropTargetId}
            hoverTarget={hoverTarget}
            drop={drop}
          />
        ))}
      </>
    );
  }
  const target = isMaterialDropTarget(node);
  return (
    <div className="tree-branch">
      <button
        type="button"
        aria-label={materialTargetAriaLabel(node)}
        aria-describedby={target ? "material-drop-help" : undefined}
        data-material-target-id={target ? node.id : undefined}
        className={`tree-row ${selected === node.id ? "tree-selected" : ""}${materialDropClass(node, dragSource, dropTargetId)}`}
        onClick={() => select(node)}
        onDragEnter={
          target
            ? (event) => {
                event.preventDefault();
                hoverTarget(node);
              }
            : undefined
        }
        onDragOver={
          target
            ? (event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect =
                  dragSource?.courseId === node.course_id ? "move" : "none";
                hoverTarget(node);
              }
            : undefined
        }
        onDragLeave={
          target
            ? (event) => {
                if (
                  !event.currentTarget.contains(event.relatedTarget as Node)
                ) {
                  hoverTarget(null);
                }
              }
            : undefined
        }
        onDrop={target ? (event) => drop(event, node) : undefined}
      >
        {children.length > 0 && (
          <ChevronDown
            className={`tree-chevron ${expanded ? "tree-chevron-open" : ""}`}
            aria-hidden="true"
            onClick={(event) => {
              event.stopPropagation();
              setExpanded((value) => !value);
            }}
          />
        )}
        <Folder className="tree-kind-icon" aria-hidden="true" />
        <span>{node.name}</span>
      </button>
      {expanded && children.length > 0 && (
        <div className="tree-children">
          {children.map((child) => (
            <MaterialTreeBranch
              key={child.id}
              node={child}
              selected={selected}
              select={select}
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
