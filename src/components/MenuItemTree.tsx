"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  GripVertical,
  ExternalLink,
  Tag,
  Folder,
  Package,
  FileText,
  Pin,
} from "lucide-react";

// ─── Types — kept loose so the component stays decoupled from Prisma. ───

export interface MenuTreeNode {
  id: string;
  shopifyItemId: string | null;
  parentId: string | null;
  position: number;
  title: string;
  itemType: string;
  url: string | null;
  resourceId: string | null;
  iconUrl: string | null;
  badgeText: string | null;
  pinnedToTop: boolean;
  children: MenuTreeNode[];
}

interface MenuItemTreeProps {
  nodes: MenuTreeNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /**
   * Called when the user drops `dragId` onto `targetId`. `position`
   * is "before", "after", or "inside" — the parent re-runs the API
   * call to persist the new shape.
   */
  onMove: (
    dragId: string,
    targetId: string | null,
    position: "before" | "after" | "inside"
  ) => void;
  onAddChild: (parentId: string) => void;
  onDelete: (id: string) => void;
  level?: number;
}

// Pick an icon based on the Shopify item type. Lucide picks were
// chosen to match the existing collection / product / page UI on the
// rest of the dashboard.
function typeIcon(type: string) {
  switch (type) {
    case "COLLECTION":
    case "COLLECTIONS":
    case "CATALOG":
      return Folder;
    case "PRODUCT":
      return Package;
    case "PAGE":
    case "BLOG":
    case "ARTICLE":
    case "FRONTPAGE":
    case "SHOP_POLICY":
      return FileText;
    case "HTTP":
      return ExternalLink;
    case "SEARCH":
      return Tag;
    default:
      return Folder;
  }
}

export default function MenuItemTree({
  nodes,
  selectedId,
  onSelect,
  onMove,
  onAddChild,
  onDelete,
  level = 0,
}: MenuItemTreeProps) {
  return (
    <ul
      className={
        level === 0
          ? "space-y-0.5"
          : "space-y-0.5 border-l border-slate-200 ml-4 pl-2 mt-0.5"
      }
    >
      {nodes.map((node) => (
        <TreeRow
          key={node.id}
          node={node}
          selectedId={selectedId}
          onSelect={onSelect}
          onMove={onMove}
          onAddChild={onAddChild}
          onDelete={onDelete}
          level={level}
        />
      ))}
    </ul>
  );
}

interface TreeRowProps {
  node: MenuTreeNode;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onMove: (
    dragId: string,
    targetId: string | null,
    position: "before" | "after" | "inside"
  ) => void;
  onAddChild: (parentId: string) => void;
  onDelete: (id: string) => void;
  level: number;
}

function TreeRow({
  node,
  selectedId,
  onSelect,
  onMove,
  onAddChild,
  onDelete,
  level,
}: TreeRowProps) {
  const [expanded, setExpanded] = useState(true);
  const [dragOver, setDragOver] = useState<
    "before" | "after" | "inside" | null
  >(null);
  const Icon = typeIcon(node.itemType);
  const hasChildren = node.children && node.children.length > 0;

  const onDragStart = (e: React.DragEvent<HTMLDivElement>) => {
    e.dataTransfer.setData("text/plain", node.id);
    e.dataTransfer.effectAllowed = "move";
  };

  const onDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    // Pick the drop zone based on Y-position within the row. Top
    // third = before, bottom third = after, middle = inside (i.e. add
    // as a child). This is a tiny inline impl, not a full DnD library.
    const rect = e.currentTarget.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const h = rect.height;
    if (y < h / 3) setDragOver("before");
    else if (y > (h * 2) / 3) setDragOver("after");
    else setDragOver("inside");
  };

  const onDragLeave = () => setDragOver(null);

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const dragId = e.dataTransfer.getData("text/plain");
    const pos = dragOver || "after";
    setDragOver(null);
    if (!dragId || dragId === node.id) return;
    onMove(dragId, node.id, pos);
  };

  const isSelected = selectedId === node.id;

  return (
    <li>
      <div
        draggable
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => onSelect(node.id)}
        className={`group flex items-center gap-1 px-2 py-1.5 rounded text-sm cursor-pointer relative ${
          isSelected
            ? "bg-blue-100 text-blue-900"
            : "text-slate-800 hover:bg-slate-100"
        }`}
        style={{
          borderTop:
            dragOver === "before" ? "2px solid #2563eb" : "2px solid transparent",
          borderBottom:
            dragOver === "after" ? "2px solid #2563eb" : "2px solid transparent",
          background:
            dragOver === "inside"
              ? "rgba(37, 99, 235, 0.1)"
              : isSelected
                ? "rgb(219 234 254)"
                : undefined,
        }}
      >
        <GripVertical className="w-3.5 h-3.5 text-slate-400 cursor-grab flex-shrink-0" />

        {hasChildren ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
            className="flex-shrink-0 text-slate-500 hover:text-slate-800"
          >
            {expanded ? (
              <ChevronDown className="w-3.5 h-3.5" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5" />
            )}
          </button>
        ) : (
          <span className="w-3.5 flex-shrink-0" />
        )}

        <Icon className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />

        {node.iconUrl && (
          <span className="w-4 h-4 rounded overflow-hidden bg-slate-100 flex-shrink-0 flex items-center justify-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={node.iconUrl} alt="" className="w-full h-full object-cover" />
          </span>
        )}

        <span className="truncate flex-1">{node.title || "(untitled)"}</span>

        {node.pinnedToTop && (
          <Pin className="w-3 h-3 text-amber-500 flex-shrink-0" />
        )}

        {node.badgeText && (
          <span className="text-[10px] px-1 py-0 rounded bg-rose-100 text-rose-700 flex-shrink-0">
            {node.badgeText}
          </span>
        )}

        <span className="text-[10px] text-slate-400 font-mono flex-shrink-0">
          {node.itemType.toLowerCase()}
        </span>

        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onAddChild(node.id);
            }}
            title="Add child item"
            className="text-[11px] text-blue-600 hover:underline"
          >
            + child
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (confirm(`Delete "${node.title}" and all its children?`)) {
                onDelete(node.id);
              }
            }}
            title="Delete"
            className="text-[11px] text-red-600 hover:underline"
          >
            delete
          </button>
        </div>
      </div>

      {hasChildren && expanded && (
        <MenuItemTree
          nodes={node.children}
          selectedId={selectedId}
          onSelect={onSelect}
          onMove={onMove}
          onAddChild={onAddChild}
          onDelete={onDelete}
          level={level + 1}
        />
      )}
    </li>
  );
}

