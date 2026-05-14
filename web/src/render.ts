import type { AutoIndexItem, Citation, SearchResult } from "./types.js";

export function citationLabel(citation: Citation, index: number): string {
  const title = citation.title || citation.resource_id;
  const position = citation.position === null || citation.position === undefined ? "" : ` · #${citation.position}`;
  return `${index + 1}. ${title}${position}`;
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
