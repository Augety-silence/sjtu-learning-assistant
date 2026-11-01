import { ArrowDown, ArrowUp, ArrowUpDown, Ellipsis } from "lucide-react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
} from "react";
import { useEffect, useRef } from "react";
import { formatSize } from "@/lib/format";
import type { DriveItem, DriveSortField, DriveSortOrder } from "@/lib/types";
import { cn } from "@/lib/utils";
import { describeItem, formatModified, itemKey } from "./driveUtils";

export interface RowIntent {
  item: DriveItem;
  index: number;
}

const COLUMNS: Array<{
  field: DriveSortField;
  label: string;
  className: string;
}> = [
  { field: "name", label: "项目名称", className: "drive-col-name" },
  { field: "modified", label: "修改时间", className: "drive-col-modified" },
  { field: "size", label: "大小", className: "drive-col-size" },
];

/**
 * 文件列表：表头可排序、行可多选。单击选中、双击/Enter 打开，
 * 右键或“⋯”按钮弹出操作菜单（与交大云盘网页端的列表视图一致）。
 */
export function DriveTable({
  items,
  selected,
  focusIndex,
  sortBy,
  order,
  busyKeys,
  onSort,
  onFocusIndex,
  onSelect,
  onToggleAll,
  onOpen,
  onMenu,
  onRowKey,
}: {
  items: DriveItem[];
  selected: Set<string>;
  focusIndex: number;
  sortBy: DriveSortField;
  order: DriveSortOrder;
  busyKeys: Set<string>;
  onSort: (field: DriveSortField) => void;
  onFocusIndex: (index: number) => void;
  onSelect: (
    intent: RowIntent,
    mode: { toggle: boolean; range: boolean; checkbox?: boolean },
  ) => void;
  onToggleAll: (checked: boolean) => void;
  onOpen: (item: DriveItem) => void;
  onMenu: (
    intent: RowIntent,
    anchor: { x: number; y: number; alignRight?: boolean },
    trigger: HTMLElement | null,
  ) => void;
  onRowKey: (event: ReactKeyboardEvent<HTMLElement>, intent: RowIntent) => void;
}) {
  const rowRefs = useRef<Array<HTMLTableRowElement | null>>([]);
  const allSelected =
    items.length > 0 && items.every((item) => selected.has(itemKey(item)));
  const someSelected =
    !allSelected && items.some((item) => selected.has(itemKey(item)));
  const headerCheckRef = useRef<HTMLInputElement>(null);
  const focusWithinRef = useRef(false);

  useEffect(() => {
    if (headerCheckRef.current)
      headerCheckRef.current.indeterminate = someSelected;
  }, [someSelected]);

  useEffect(() => {
    // 仅当焦点已在列表内时才跟随移动，避免刷新列表时抢走工具栏焦点。
    if (!focusWithinRef.current) return;
    rowRefs.current[focusIndex]?.focus();
  }, [focusIndex]);

  const openMenuFromButton = (
    event: ReactMouseEvent<HTMLButtonElement>,
    intent: RowIntent,
  ) => {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    onMenu(
      intent,
      { x: rect.right, y: rect.bottom + 4, alignRight: true },
      event.currentTarget,
    );
  };

  return (
    <div
      className="drive-table-scroll"
      onFocus={() => {
        focusWithinRef.current = true;
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          focusWithinRef.current = false;
      }}
    >
      <table
        className="drive-table"
        aria-label="文件列表"
        aria-multiselectable="true"
      >
        <thead>
          <tr>
            <th scope="col" className="drive-col-check">
              <input
                ref={headerCheckRef}
                type="checkbox"
                className="drive-check"
                aria-label={allSelected ? "取消全选" : "全选本页"}
                checked={allSelected}
                disabled={items.length === 0}
                onChange={(event) => onToggleAll(event.target.checked)}
              />
            </th>
            {COLUMNS.map((column) => {
              const active = sortBy === column.field;
              const Icon = !active
                ? ArrowUpDown
                : order === "asc"
                  ? ArrowUp
                  : ArrowDown;
              return (
                <th
                  key={column.field}
                  scope="col"
                  className={column.className}
                  aria-sort={
                    active
                      ? order === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  <button
                    type="button"
                    className={cn("drive-sort", active && "is-active")}
                    onClick={() => onSort(column.field)}
                  >
                    {column.label}
                    <Icon aria-hidden="true" />
                  </button>
                </th>
              );
            })}
            <th scope="col" className="drive-col-actions">
              <span className="sr-only">操作</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const key = itemKey(item);
            const checked = selected.has(key);
            const meta = describeItem(item);
            const Icon = meta.icon;
            const intent = { item, index };
            return (
              <tr
                key={key}
                ref={(node) => {
                  rowRefs.current[index] = node;
                }}
                className={cn(
                  "drive-row",
                  checked && "is-selected",
                  busyKeys.has(key) && "is-busy",
                )}
                aria-selected={checked}
                tabIndex={index === focusIndex ? 0 : -1}
                data-kind={item.is_directory ? "folder" : "file"}
                onFocus={() => {
                  if (index !== focusIndex) onFocusIndex(index);
                }}
                onClick={(event) =>
                  onSelect(intent, {
                    toggle: event.metaKey || event.ctrlKey,
                    range: event.shiftKey,
                  })
                }
                onDoubleClick={(event) => {
                  if (
                    (event.target as HTMLElement).closest(
                      "button, input, select, textarea, a",
                    )
                  )
                    return;
                  onOpen(item);
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  if (!checked)
                    onSelect(intent, { toggle: false, range: false });
                  onMenu(
                    intent,
                    { x: event.clientX, y: event.clientY },
                    event.currentTarget,
                  );
                }}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  onRowKey(event, intent);
                }}
              >
                <td className="drive-col-check">
                  <input
                    type="checkbox"
                    className="drive-check"
                    tabIndex={-1}
                    aria-label={`选择“${item.name}”`}
                    checked={checked}
                    onClick={(event) => event.stopPropagation()}
                    onChange={() =>
                      onSelect(intent, {
                        toggle: true,
                        range: false,
                        checkbox: true,
                      })
                    }
                  />
                </td>
                <td className="drive-col-name">
                  <div className="drive-name-cell">
                    <span
                      className="drive-file-icon"
                      data-tone={meta.tone}
                      aria-hidden="true"
                    >
                      <Icon />
                    </span>
                    {item.is_directory ? (
                      <button
                        type="button"
                        tabIndex={-1}
                        className="drive-name-link"
                        title={item.name}
                        onClick={(event) => {
                          // 与网页端一致：单击文件夹名称直接进入。
                          event.stopPropagation();
                          onOpen(item);
                        }}
                      >
                        {item.name}
                      </button>
                    ) : (
                      <span className="drive-name-text" title={item.name}>
                        {item.name}
                      </span>
                    )}
                    <span className="sr-only">，{meta.label}</span>
                  </div>
                </td>
                <td className="drive-col-modified">
                  <time dateTime={item.modified_at ?? undefined}>
                    {formatModified(item.modified_at)}
                  </time>
                </td>
                <td className="drive-col-size">
                  {item.is_directory ? "—" : formatSize(item.size)}
                </td>
                <td className="drive-col-actions">
                  <button
                    type="button"
                    tabIndex={-1}
                    className="drive-row-more"
                    aria-label={`“${item.name}”的更多操作`}
                    aria-haspopup="menu"
                    onClick={(event) => {
                      if (!checked)
                        onSelect(intent, { toggle: false, range: false });
                      openMenuFromButton(event, intent);
                    }}
                  >
                    <Ellipsis aria-hidden="true" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
