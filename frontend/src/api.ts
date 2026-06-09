export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export type Health = {
  ok: boolean;
  zotero_items: number;
  zotero_abstracts: number;
  conference_papers: number;
  profiles: number;
  match_runs: number;
  conferences: Array<{ conference: string; year: number; count: number }>;
};

export type Profile = {
  id: number;
  name: string;
  source_type: string;
  source_label?: string;
  quality?: "library" | "custom" | "curated" | "noisy";
  keywords: string;
  item_count: number;
};

export type Match = {
  id: number | string | null;
  paper_id: number;
  profile_id: number;
  profile_name: string;
  score: number;
  embedding_score: number;
  bm25_score: number;
  tag_score: number;
  feedback_score: number;
  title: string;
  abstract: string;
  authors: string;
  conference: string;
  year: number;
  url: string;
  pdf_url: string;
  decision: string;
  eventtype: string;
  reason: string;
  feedback_action?: string | null;
  feedback_at?: string | null;
  dynamic?: boolean;
  in_zotero?: boolean;
  matched_zotero_items: Array<{ id: number; title: string; score: number }>;
};

export type TaskState = {
  active: boolean;
  name: string;
  stage: string;
  current: number;
  total: number;
  message: string;
  percent: number;
};

export type ZoteroCollection = {
  key: string;
  name: string;
  parent?: string;
  level?: number;
};

export type ZoteroExportSettings = {
  mode?: "local" | "web";
  apiKey: string;
  libraryType: "users" | "groups";
  libraryId: string;
  collectionKey: string;
};

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail ?? detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function getHealth(): Promise<Health> {
  return parseResponse<Health>(await fetch(`${API_BASE}/api/health`));
}

export async function importZoteroBibtex(file: File): Promise<{ ok: boolean; imported: number; total: number; detail: string }> {
  const text = await file.text();
  return parseResponse(
    await fetch(`${API_BASE}/api/import/zotero-bibtex`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    })
  );
}

export async function discoverZotero(): Promise<{ found: boolean; path: string }> {
  return parseResponse(await fetch(`${API_BASE}/api/zotero/discover`));
}

export async function importZoteroLocal(path?: string, collection?: string): Promise<{ ok: boolean; imported: number; total: number; detail: string }> {
  return parseResponse(
    await fetch(`${API_BASE}/api/import/zotero-local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path || null, collection: collection || null })
    })
  );
}

export async function getZoteroCollections(settings: Omit<ZoteroExportSettings, "collectionKey">): Promise<ZoteroCollection[]> {
  const payload = await parseResponse<{ items: ZoteroCollection[] }>(
    await fetch(`${API_BASE}/api/zotero/collections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: settings.apiKey,
        library_type: settings.libraryType,
        library_id: settings.libraryId
      })
    })
  );
  return payload.items;
}

export async function getLocalZoteroStatus(): Promise<{ connector: boolean; local_api: boolean; message: string }> {
  return parseResponse(await fetch(`${API_BASE}/api/zotero/local/status`));
}

export async function getLocalZoteroCollections(): Promise<ZoteroCollection[]> {
  const payload = await parseResponse<{ items: ZoteroCollection[] }>(
    await fetch(`${API_BASE}/api/zotero/local/collections`)
  );
  return payload.items;
}

export async function exportToZotero(
  paperIds: number[],
  settings: ZoteroExportSettings
): Promise<{ ok: boolean; requested: number; successful: number; unchanged: number; failed: Array<{ title: string; detail: unknown }> }> {
  return parseResponse(
    await fetch(`${API_BASE}/api/export/zotero`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paper_ids: paperIds,
        api_key: settings.apiKey,
        library_type: settings.libraryType,
        library_id: settings.libraryId,
        collection_key: settings.collectionKey
      })
    })
  );
}

export async function exportToLocalZotero(
  paperIds: number[],
  collectionKey: string
): Promise<{ ok: boolean; requested: number; successful: number; unchanged: number; failed: Array<{ title: string; detail: unknown }> }> {
  return parseResponse(
    await fetch(`${API_BASE}/api/export/zotero-local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paper_ids: paperIds,
        collection_key: collectionKey
      })
    })
  );
}

export async function importConferenceCsv(file: File, conference: string, year: number): Promise<{ ok: boolean; imported: number; total: number; detail: string }> {
  const text = await file.text();
  return parseResponse(
    await fetch(`${API_BASE}/api/import/conference-csv`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, conference, year })
    })
  );
}

export async function runMatches(conference: string, year: number, limitPerProfile: number): Promise<{ ok: boolean; run_id: number; profiles: number; papers: number; results: number }> {
  return parseResponse(
    await fetch(`${API_BASE}/api/matches/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conference, year, limit_per_profile: limitPerProfile })
    })
  );
}

export async function getProfiles(): Promise<Profile[]> {
  const payload = await parseResponse<{ items: Profile[]; summary?: Record<string, number> }>(await fetch(`${API_BASE}/api/profiles`));
  return payload.items;
}

export async function getCurrentTask(): Promise<TaskState> {
  return parseResponse(await fetch(`${API_BASE}/api/tasks/current`));
}

export async function rebuildEmbeddings(): Promise<{ ok: boolean; imported: number; total: number; detail: string }> {
  return parseResponse(await fetch(`${API_BASE}/api/embeddings/rebuild`, { method: "POST" }));
}

export async function saveCustomProfile(name: string, description: string): Promise<{ ok: boolean; profile: Profile }> {
  return parseResponse(
    await fetch(`${API_BASE}/api/profiles/custom`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description })
    })
  );
}

export async function deleteCustomProfile(profileId: number): Promise<{ ok: boolean; deleted: number }> {
  return parseResponse(
    await fetch(`${API_BASE}/api/profiles/${profileId}`, {
      method: "DELETE"
    })
  );
}

export async function matchCustomText(text: string, conference?: string, year?: number, limit = 80): Promise<Match[]> {
  const payload = await parseResponse<{ items: Match[] }>(
    await fetch(`${API_BASE}/api/matches/custom`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, conference: conference || null, year: year || null, limit })
    })
  );
  return payload.items;
}

export async function getMatches(params: { profileId?: number; conference?: string; year?: number; limit?: number; action?: string }): Promise<Match[]> {
  const search = new URLSearchParams();
  if (params.profileId) search.set("profile_id", String(params.profileId));
  if (params.conference) search.set("conference", params.conference);
  if (params.year) search.set("year", String(params.year));
  if (params.limit) search.set("limit", String(params.limit));
  if (params.action) search.set("action", params.action);
  const payload = await parseResponse<{ items: Match[] }>(await fetch(`${API_BASE}/api/matches?${search}`));
  return payload.items;
}

export async function sendFeedback(paperId: number, profileId: number | null, action: string, note = ""): Promise<void> {
  await parseResponse(
    await fetch(`${API_BASE}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: paperId, profile_id: profileId, action, note })
    })
  );
}

export function exportMatchesUrl(): string {
  return `${API_BASE}/api/export/matches.csv`;
}

export type FigurePaper = {
  paper_id: number;
  title: string;
  authors: string;
  kind: string;
  decision: string;
  eventtype: string;
  url: string;
  has_pdf: boolean;
  cached: boolean;
};

export type Figure = { name: string; page: number; width: number; height: number; source?: string; data_url?: string };

export type FigureExtract = {
  ok: boolean;
  error?: string;
  detail?: string;
  paper_id: number;
  title: string;
  authors: string;
  conference: string;
  year: number;
  kind: string;
  url: string;
  pdf_url: string;
  cached: boolean;
  persisted?: boolean;
  figures: Figure[];
};

export async function getFigurePapers(
  conference: string,
  year: number,
  kind: string,
  limit = 300
): Promise<{ items: FigurePaper[]; counts: Record<string, number> }> {
  const search = new URLSearchParams({ conference, year: String(year), kind, limit: String(limit) });
  return parseResponse(await fetch(`${API_BASE}/api/figures/papers?${search}`));
}

export async function getPaperFigures(paperId: number, force = false, persist = false): Promise<FigureExtract> {
  const search = new URLSearchParams();
  if (force) search.set("force", "true");
  if (!persist) search.set("persist", "false");
  const suffix = search.toString() ? `?${search}` : "";
  return parseResponse(await fetch(`${API_BASE}/api/figures/paper/${paperId}${suffix}`));
}

export function figureFileUrl(paperId: number, name: string): string {
  return `${API_BASE}/api/figures/file/${paperId}/${name}`;
}

export async function getFigureCache(): Promise<{ papers: number; bytes: number }> {
  return parseResponse(await fetch(`${API_BASE}/api/figures/cache`));
}

export async function clearFigureCache(): Promise<{ ok: boolean; cleared_papers: number; cleared_bytes: number }> {
  return parseResponse(await fetch(`${API_BASE}/api/figures/cache`, { method: "DELETE" }));
}
