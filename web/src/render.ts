import type { AutoIndexItem, Citation, SearchResult } from "./types.js";

export function citationLabel(citation: Citation, index: number): string {
  const title = citation.title || citation.resource_id;
  let locator = "";
  if (citation.start_seconds != null) {
    locator = ` · ${formatSeconds(citation.start_seconds)}`;
  } else if (citation.page != null) {
    locator = ` · p.${citation.page}`;
  }
  return `${index + 1}. ${title}${locator}`;
}

export function formatSeconds(s: number): string {
  const total = Math.floor(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function resultTitle(result: SearchResult): string {
  return result.chunk.title || `${result.chunk.kind} ${result.chunk.position}`;
}

export function scoreLabel(score: number): string {
  return `${Math.round(score * 100)}%`;
}

export function clippedText(text: string, limit = 700): string {
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

export function autoIndexItemMeta(item: AutoIndexItem): string {
  const stage = item.stage ? `${item.stage} · ` : "";
  if (item.status === "indexed") {
    return `${stage}${item.kind} · ${item.chunks} chunks`;
  }
  return `${stage}${item.kind} · ${item.status}`;
}
