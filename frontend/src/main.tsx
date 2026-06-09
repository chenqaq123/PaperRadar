import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Archive,
  BookmarkCheck,
  Brain,
  Check,
  ChevronRight,
  Database,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Heart,
  Image as ImageIcon,
  Loader2,
  Maximize2,
  Play,
  Radar,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Upload,
  X
} from "lucide-react";
import {
  API_BASE,
  Match,
  Profile,
  TaskState,
  ZoteroCollection,
  deleteCustomProfile,
  exportMatchesUrl,
  exportToLocalZotero,
  getCurrentTask,
  getHealth,
  getMatches,
  getProfiles,
  getLocalZoteroCollections,
  getLocalZoteroStatus,
  getFigurePapers,
  getPaperFigures,
  figureFileUrl,
  FigurePaper,
  Figure,
  discoverZotero,
  importConferenceCsv,
  importZoteroLocal,
  importZoteroBibtex,
  matchCustomText,
  rebuildEmbeddings,
  runMatches,
  saveCustomProfile,
  sendFeedback
} from "./api";
import "./styles.css";

type View = "recommendations" | "queue" | "figures" | "profiles" | "data" | "settings";
type FeedbackAction = "want_to_read" | "read" | "relevant" | "not_relevant" | "hide";
type ToastKind = "info" | "success" | "error";
type Toast = { id: number; kind: ToastKind; text: string };

const STORAGE_KEY = "paper-radar-ui-v3";
const NAV: Array<{ view: View; label: string; icon: React.ReactNode }> = [
  { view: "recommendations", label: "推荐", icon: <Sparkles size={18} /> },
  { view: "queue", label: "阅读队列", icon: <BookmarkCheck size={18} /> },
  { view: "figures", label: "图表", icon: <ImageIcon size={18} /> },
  { view: "profiles", label: "研究方向", icon: <Brain size={18} /> },
  { view: "data", label: "数据与导入", icon: <Database size={18} /> },
  { view: "settings", label: "设置", icon: <Settings size={18} /> }
];

function readUiState() {
  try {
    return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function App() {
  const savedUi = useMemo(() => readUiState(), []);
  const [view, setView] = useState<View>(savedUi.view ?? "recommendations");
  const [health, setHealth] = useState({ zotero_items: 0, zotero_abstracts: 0, conference_papers: 0, profiles: 0, match_runs: 0, conferences: [] as Array<{ conference: string; year: number; count: number }> });
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);
  const [selected, setSelected] = useState<Match | null>(null);
  const [selectedPaperIds, setSelectedPaperIds] = useState<number[]>([]);
  const [addedPaperIds, setAddedPaperIds] = useState<Set<number>>(new Set());
  const [hideInZotero, setHideInZotero] = useState<boolean>(savedUi.hideInZotero ?? false);
  const [profileId, setProfileId] = useState<number | undefined>(savedUi.profileId);
  const [showNoisyProfiles, setShowNoisyProfiles] = useState(false);
  const [conference, setConference] = useState(savedUi.conference ?? "cvpr");
  const [year, setYear] = useState(savedUi.year ?? 2026);
  const [matchConference, setMatchConference] = useState(savedUi.matchConference ?? "");
  const [matchYear, setMatchYear] = useState(savedUi.matchYear ?? "");
  const [queueAction, setQueueAction] = useState<FeedbackAction>(savedUi.queueAction ?? "want_to_read");
  const [limit, setLimit] = useState(savedUi.limit ?? 120);
  const [zoteroPath, setZoteroPath] = useState("");
  const [zoteroCollection, setZoteroCollection] = useState("");
  const [query, setQuery] = useState("");
  const [interestOpen, setInterestOpen] = useState(false);
  const [interestText, setInterestText] = useState("");
  const [interestName, setInterestName] = useState("");
  const [busy, setBusy] = useState(false);
  const [matchesLoading, setMatchesLoading] = useState(false);
  const matchReq = useRef(0);
  const [task, setTask] = useState<TaskState | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastSeq = useRef(0);
  const [collectionKey, setCollectionKey] = useState<string>(savedUi.collectionKey ?? "");
  const [zoteroCollections, setZoteroCollections] = useState<ZoteroCollection[]>([]);
  const [localZoteroStatus, setLocalZoteroStatus] = useState<{ connector: boolean; local_api: boolean; message: string } | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const bootstrapped = useRef(false);

  function pushToast(text: string, kind: ToastKind = "info") {
    const id = ++toastSeq.current;
    setToasts((items) => [...items, { id, kind, text }]);
    window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), kind === "error" ? 6000 : 4000);
  }
  function dismissToast(id: number) {
    setToasts((items) => items.filter((item) => item.id !== id));
  }

  async function refresh() {
    const [nextHealth, nextProfiles] = await Promise.all([getHealth(), getProfiles()]);
    nextHealth.conferences = nextHealth.conferences ?? [];
    nextHealth.zotero_abstracts = nextHealth.zotero_abstracts ?? 0;
    setHealth(nextHealth);
    setProfiles(nextProfiles);
    let nextProfileId = profileId;
    if (!profileId && nextProfiles.length > 0) {
      const preferred = nextProfiles.find((profile) => profile.quality === "custom" || profile.quality === "curated") ?? nextProfiles[0];
      setProfileId(preferred.id);
      nextProfileId = preferred.id;
    }
    if (nextHealth.conferences.length > 0) {
      const hasCurrent = nextHealth.conferences.some((item) => item.conference === conference && item.year === year);
      if (!hasCurrent) {
        setConference(nextHealth.conferences[0].conference);
        setYear(nextHealth.conferences[0].year);
      }
    }
    return { nextHealth, nextProfiles, nextProfileId };
  }

  async function refreshMatches(nextProfileId = profileId, nextConference = matchConference, nextYear = matchYear, nextAction = view === "queue" ? queueAction : "") {
    const reqId = ++matchReq.current;
    setMatchesLoading(true);
    try {
      const items = await getMatches({
        profileId: nextProfileId,
        conference: nextConference || undefined,
        year: nextYear ? Number(nextYear) : undefined,
        action: nextAction || undefined,
        limit
      });
      if (reqId !== matchReq.current) return; // a newer request superseded this one
      setMatches(items);
      setSelected(items[0] ?? null);
    } finally {
      if (reqId === matchReq.current) setMatchesLoading(false);
    }
  }

  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    refresh()
      .then(async ({ nextHealth, nextProfileId }) => {
        if (nextHealth.conference_papers > 0 && nextProfileId) {
          const bootConference = savedUi.matchConference || nextHealth.conferences?.[0]?.conference || "";
          const bootYear = savedUi.matchYear || (nextHealth.conferences?.[0]?.year ? String(nextHealth.conferences[0].year) : "");
          if (!savedUi.matchConference && bootConference) setMatchConference(bootConference);
          if (!savedUi.matchYear && bootYear) setMatchYear(bootYear);
          const nextAction = savedUi.view === "queue" ? (savedUi.queueAction ?? "want_to_read") : "";
          const items = await getMatches({
            profileId: nextProfileId,
            conference: bootConference || undefined,
            year: bootYear ? Number(bootYear) : undefined,
            action: nextAction || undefined,
            limit
          });
          setMatches(items);
          setSelected(items[0] ?? null);
          setView(savedUi.view ?? "recommendations");
        } else if (nextHealth.zotero_items === 0 || nextHealth.conference_papers === 0) {
          setView("data");
          pushToast("先在「数据与导入」里导入 Zotero 和会议论文。", "info");
        }
      })
      .catch((error) => pushToast(error.message, "error"));
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ view, profileId, conference, year, matchConference, matchYear, queueAction, limit, collectionKey, hideInZotero })
    );
  }, [view, profileId, conference, year, matchConference, matchYear, queueAction, limit, collectionKey, hideInZotero]);

  useEffect(() => {
    const available = new Set(matches.map((item) => item.paper_id));
    setSelectedPaperIds((items) => items.filter((paperId) => available.has(paperId)));
  }, [matches]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const next = await getCurrentTask();
        if (!cancelled) setTask(next);
      } catch {
        if (!cancelled) setTask(null);
      }
      if (!cancelled && busy) {
        timer = window.setTimeout(poll, 900);
      }
    }
    if (busy) poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [busy]);

  const isInZotero = (item: Match) => Boolean(item.in_zotero) || addedPaperIds.has(item.paper_id);
  const filteredMatches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    let result = matches;
    if (needle) {
      result = result.filter((item) =>
        [item.title, item.abstract, item.profile_name, item.reason, item.authors].join(" ").toLowerCase().includes(needle)
      );
    }
    if (hideInZotero) {
      result = result.filter((item) => !(item.in_zotero || addedPaperIds.has(item.paper_id)));
    }
    return result;
  }, [matches, query, hideInZotero, addedPaperIds]);
  const inZoteroCount = useMemo(() => matches.filter((item) => item.in_zotero || addedPaperIds.has(item.paper_id)).length, [matches, addedPaperIds]);
  const selectedPaperIdSet = useMemo(() => new Set(selectedPaperIds), [selectedPaperIds]);
  const selectedMatches = useMemo(
    () => matches.filter((item) => selectedPaperIdSet.has(item.paper_id)),
    [matches, selectedPaperIdSet]
  );
  const visiblePaperIds = useMemo(
    () => Array.from(new Set(filteredMatches.map((item) => item.paper_id))),
    [filteredMatches]
  );
  const allVisibleSelected = visiblePaperIds.length > 0 && visiblePaperIds.every((paperId) => selectedPaperIdSet.has(paperId));
  const visibleProfiles = useMemo(
    () => profiles.filter((profile) => showNoisyProfiles || profile.quality !== "noisy"),
    [profiles, showNoisyProfiles]
  );
  const conferenceOptions = useMemo(() => {
    return Array.from(new Set(health.conferences.map((item) => item.conference))).sort();
  }, [health.conferences]);
  const latestConference = health.conferences[0];
  const selectedConferenceCount = useMemo(() => {
    return health.conferences.find((item) => item.conference === conference && item.year === year)?.count ?? 0;
  }, [health.conferences, conference, year]);
  const coverageByConference = useMemo(() => {
    return conferenceOptions.map((name) => {
      const years = health.conferences
        .filter((item) => item.conference === name)
        .sort((a, b) => b.year - a.year);
      return { name, years, total: years.reduce((sum, item) => sum + item.count, 0) };
    });
  }, [conferenceOptions, health.conferences]);
  const yearOptions = useMemo(() => {
    const scoped = matchConference
      ? health.conferences.filter((item) => item.conference === matchConference)
      : health.conferences;
    return Array.from(new Set(scoped.map((item) => item.year))).sort((a, b) => b - a);
  }, [health.conferences, matchConference]);
  const matchScopeLabel = useMemo(() => {
    const conferenceLabel = matchConference ? matchConference.toUpperCase() : "全部会议";
    const yearLabel = matchYear || "全部年份";
    const paperCount = health.conferences
      .filter((item) => (!matchConference || item.conference === matchConference) && (!matchYear || item.year === Number(matchYear)))
      .reduce((sum, item) => sum + item.count, 0);
    return `${conferenceLabel} · ${yearLabel}${paperCount ? ` · ${paperCount} 篇` : ""}`;
  }, [health.conferences, matchConference, matchYear]);
  const profileCounts = useMemo(() => {
    return profiles.reduce<Record<string, number>>((acc, profile) => {
      const key = profile.quality ?? "unknown";
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    }, {});
  }, [profiles]);
  async function withBusy<T>(task: () => Promise<T>, success: string) {
    setBusy(true);
    try {
      const value = await task();
      if (success) pushToast(success, "success");
      await refresh();
      return value;
    } catch (error) {
      pushToast(error instanceof Error ? error.message : String(error), "error");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function onZoteroFile(file?: File) {
    if (!file) return;
    await withBusy(() => importZoteroBibtex(file), `已导入 Zotero BibTeX：${file.name}`);
  }
  async function onDiscoverZotero() {
    const result = await withBusy(() => discoverZotero(), "已检测本机 Zotero 数据目录。");
    if (result?.path) setZoteroPath(result.path);
  }
  async function onImportLocalZotero() {
    await withBusy(() => importZoteroLocal(zoteroPath || undefined, zoteroCollection || undefined), "已从本机 Zotero 数据库导入。");
  }
  async function onConferenceFile(file?: File) {
    if (!file) return;
    await withBusy(() => importConferenceCsv(file, conference, year), `已导入 ${conference.toUpperCase()} ${year}：${file.name}`);
  }
  async function onRunMatches() {
    const result = await withBusy(() => runMatches(conference, year, limit), `${conference.toUpperCase()} ${year} 排序完成。`);
    if (result) {
      const nextYear = String(year);
      setMatchConference(conference);
      setMatchYear(nextYear);
      await refreshMatches(profileId, conference, nextYear);
      setView("recommendations");
    }
  }
  async function onRebuildEmbeddings() {
    await withBusy(() => rebuildEmbeddings(), "已重建 embeddings，重新排序即可刷新推荐。");
  }

  async function onFeedback(action: string) {
    if (!selected) return;
    const result = await withBusy(() => sendFeedback(selected.paper_id, selected.profile_id, action), `已标记「${feedbackLabel(action)}」。`);
    if (result !== null) {
      const selectedKey = matchKey(selected);
      setSelected((current) => current ? { ...current, feedback_action: action } : current);
      setMatches((items) => {
        if (view === "queue" && action !== queueAction) {
          return items.filter((item) => matchKey(item) !== selectedKey);
        }
        return items.map((item) => matchKey(item) === selectedKey ? { ...item, feedback_action: action } : item);
      });
    }
  }

  async function onCustomMatch() {
    if (!interestText.trim()) {
      pushToast("先写一句你现在想看的研究方向。", "info");
      return;
    }
    const reqId = ++matchReq.current;
    setMatchesLoading(true);
    try {
      const items = await withBusy(
        () => matchCustomText(interestText, matchConference || undefined, matchYear ? Number(matchYear) : undefined, limit),
        `已按临时兴趣匹配：${matchScopeLabel}。`
      );
      if (items && reqId === matchReq.current) {
        setMatches(items);
        setSelected(items[0] ?? null);
      }
    } finally {
      if (reqId === matchReq.current) setMatchesLoading(false);
    }
  }

  async function onSaveCustomProfile() {
    if (!interestName.trim() || !interestText.trim()) {
      pushToast("保存方向需要填写名称和兴趣描述。", "info");
      return;
    }
    const result = await withBusy(() => saveCustomProfile(interestName, interestText), "已保存为研究方向。");
    if (result?.profile) {
      setProfileId(result.profile.id);
      const items = await getMatches({
        profileId: result.profile.id,
        conference: matchConference || undefined,
        year: matchYear ? Number(matchYear) : undefined,
        limit
      });
      setMatches(items);
      setSelected(items[0] ?? null);
      setInterestOpen(false);
    }
  }

  function selectProfile(value: string) {
    const next = value ? Number(value) : undefined;
    setProfileId(next);
    refreshMatches(next).catch((error) => pushToast(error.message, "error"));
  }
  function openProfilePapers(profile: Profile) {
    setProfileId(profile.id);
    setView("recommendations");
    refreshMatches(profile.id, matchConference, matchYear, "").catch((error) => pushToast(error.message, "error"));
  }
  async function onDeleteProfile(profile: Profile) {
    if (profile.source_type !== "custom_text") {
      pushToast("只有自定义研究方向可以删除。", "info");
      return;
    }
    if (!window.confirm(`删除自定义研究方向"${profile.name}"？已标注的论文反馈会保留。`)) return;
    const result = await withBusy(() => deleteCustomProfile(profile.id), `已删除：${profile.name}。`);
    if (result) {
      const nextProfiles = await getProfiles();
      const fallback = nextProfiles.find((item) => item.id !== profile.id && item.quality !== "noisy") ?? nextProfiles[0];
      setProfiles(nextProfiles);
      setProfileId(fallback?.id);
      if (profileId === profile.id) {
        await refreshMatches(fallback?.id, matchConference, matchYear, view === "queue" ? queueAction : "");
      }
    }
  }

  function openRecommendations() {
    setView("recommendations");
    if (matches.length === 0 && profileId) {
      refreshMatches(profileId, matchConference, matchYear, "").catch((error) => pushToast(error.message, "error"));
    }
  }
  function openQueue() {
    setView("queue");
    refreshMatches(profileId, matchConference, matchYear, queueAction).catch((error) => pushToast(error.message, "error"));
  }
  function selectMatchConference(value: string) {
    const availableYears = health.conferences.filter((item) => !value || item.conference === value).map((item) => item.year);
    const nextYear = matchYear && availableYears.includes(Number(matchYear)) ? matchYear : "";
    setMatchConference(value);
    setMatchYear(nextYear);
    refreshMatches(profileId, value, nextYear).catch((error) => pushToast(error.message, "error"));
  }
  function selectMatchYear(value: string) {
    setMatchYear(value);
    refreshMatches(profileId, matchConference, value).catch((error) => pushToast(error.message, "error"));
  }
  function useLatestScope() {
    if (!latestConference) return;
    setMatchConference(latestConference.conference);
    setMatchYear(String(latestConference.year));
    refreshMatches(profileId, latestConference.conference, String(latestConference.year), view === "queue" ? queueAction : "").catch((error) => pushToast(error.message, "error"));
  }
  function clearScope() {
    setMatchConference("");
    setMatchYear("");
    refreshMatches(profileId, "", "", view === "queue" ? queueAction : "").catch((error) => pushToast(error.message, "error"));
  }

  function togglePaperSelection(paperId: number) {
    setSelectedPaperIds((items) => (items.includes(paperId) ? items.filter((item) => item !== paperId) : [...items, paperId]));
  }
  function toggleVisibleSelection() {
    setSelectedPaperIds((items) => {
      const next = new Set(items);
      const shouldClear = visiblePaperIds.length > 0 && visiblePaperIds.every((paperId) => next.has(paperId));
      for (const paperId of visiblePaperIds) shouldClear ? next.delete(paperId) : next.add(paperId);
      return Array.from(next);
    });
  }

  function exportSelectedPapers() {
    if (selectedMatches.length === 0) {
      pushToast("先在列表里勾选要导出的论文。", "info");
      return;
    }
    const rows = selectedMatches.map((item) => ({
      "分数": item.score.toFixed(3),
      "标题": item.title,
      "作者": item.authors,
      "会议": item.conference.toUpperCase(),
      "年份": item.year,
      "URL": item.url,
      "PDF": item.pdf_url
    }));
    const stamp = new Date().toISOString().slice(0, 10);
    downloadTextFile(`paper-radar-selected-${stamp}.csv`, `﻿${csvFromRows(rows)}`, "text/csv;charset=utf-8");
    pushToast(`已导出 ${selectedMatches.length} 篇为 CSV。`, "success");
  }

  async function loadZoteroCollections() {
    const items = await withBusy(() => getLocalZoteroCollections(), "");
    if (items) {
      setZoteroCollections(items);
      if (items.length === 0) pushToast("没有读到目录，确认 Zotero 已打开并启用 Local API。", "info");
    }
    return items;
  }
  async function checkLocalZotero() {
    const status = await withBusy(() => getLocalZoteroStatus(), "");
    if (status) setLocalZoteroStatus(status);
    return status;
  }

  function openExportModal() {
    if (selectedPaperIds.length === 0) {
      pushToast("先在列表里勾选要添加的论文。", "info");
      return;
    }
    setExportOpen(true);
    checkLocalZotero();
    if (zoteroCollections.length === 0) loadZoteroCollections();
  }

  async function confirmExport() {
    if (!collectionKey.trim()) {
      pushToast("先选择一个目标目录。", "info");
      return;
    }
    const exportingIds = [...selectedPaperIds];
    const result = await withBusy(() => exportToLocalZotero(exportingIds, collectionKey), "");
    if (result) {
      const failed = result.failed?.length ?? 0;
      // Mark everything we just sent as in-library (only fully-failed papers are reported in `failed`).
      const failedTitles = new Set((result.failed ?? []).map((item) => String(item.title)));
      const addedNow = exportingIds.filter((id) => {
        const match = matches.find((item) => item.paper_id === id);
        return !match || !failedTitles.has(match.title);
      });
      setAddedPaperIds((prev) => new Set([...prev, ...addedNow]));
      if (failed === 0) {
        pushToast(`已添加到 Zotero：成功 ${result.successful} 篇${result.unchanged ? `，未变化 ${result.unchanged} 篇` : ""}。`, "success");
        setExportOpen(false);
      } else {
        pushToast(`添加完成：成功 ${result.successful}，未变化 ${result.unchanged}，失败 ${failed}。`, "error");
      }
    }
  }

  const collectionName = zoteroCollections.find((item) => item.key === collectionKey)?.name;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Radar size={20} /></span>
          <div>
            <strong>Paper Radar</strong>
            <span>本地论文雷达</span>
          </div>
        </div>
        <nav>
          {NAV.map((item) => (
            <button
              key={item.view}
              className={view === item.view ? "active" : ""}
              onClick={() => item.view === "recommendations" ? openRecommendations() : item.view === "queue" ? openQueue() : setView(item.view)}
            >
              {item.icon}<span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="side-metrics">
          <Metric label="Zotero" value={health.zotero_items} />
          <Metric label="论文" value={health.conference_papers} />
          <Metric label="方向" value={health.profiles} />
          <Metric label="排序" value={health.match_runs} />
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            <h1>{titleFor(view)}</h1>
            <p>{subtitleFor(view)}</p>
          </div>
          <div className="topbar-actions">
            {busy && (
              <span className="topbar-busy"><Loader2 size={15} className="spin" />{task?.message ? task.message : "处理中"}{task?.total ? ` ${task.current}/${task.total}` : ""}</span>
            )}
            <button className="ghost-btn" title="刷新状态" onClick={() => refresh().catch((error) => pushToast(error.message, "error"))}><RefreshCw size={16} /></button>
            <a className="ghost-btn" title="导出全部推荐 CSV" href={exportMatchesUrl()}><Download size={16} /></a>
          </div>
        </header>
        {busy && <div className="top-progress"><div style={{ width: `${task?.percent || 12}%` }} /></div>}

        <div className="view-area">
          {(view === "recommendations" || view === "queue") && (
            <section className="rec-layout">
              <div className="results-pane">
                {view === "recommendations" && (
                  <div className={`intent ${interestOpen ? "open" : ""}`}>
                    <button className="intent-toggle" onClick={() => setInterestOpen((open) => !open)}>
                      <Sparkles size={15} /><span>临时兴趣即时匹配</span><ChevronRight size={15} className="chev" />
                    </button>
                    {interestOpen && (
                      <div className="intent-body">
                        <textarea value={interestText} onChange={(event) => setInterestText(event.target.value)} placeholder="例如：我想看 text-to-image diffusion 里的 concept erasure、安全对齐、jailbreak、backdoor 和 benchmark 评测。" />
                        <div className="intent-actions">
                          <input value={interestName} onChange={(event) => setInterestName(event.target.value)} placeholder="可选：保存为方向的名称" />
                          <button className="primary" onClick={onCustomMatch}><Search size={15} />即时匹配</button>
                          <button className="soft" onClick={onSaveCustomProfile}><Brain size={15} />保存方向</button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <div className="toolbar">
                  <div className="toolbar-filters">
                    <select value={matchConference} onChange={(event) => selectMatchConference(event.target.value)} title="会议">
                      <option value="">全部会议</option>
                      {conferenceOptions.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
                    </select>
                    <select value={matchYear} onChange={(event) => selectMatchYear(event.target.value)} title="年份">
                      <option value="">全部年份</option>
                      {yearOptions.map((item) => <option key={item} value={String(item)}>{item}</option>)}
                    </select>
                    <select value={profileId ?? ""} onChange={(event) => selectProfile(event.target.value)} title="研究方向">
                      {visibleProfiles.map((profile) => <option key={profile.id} value={profile.id}>{qualityPrefix(profile.quality)}{displayProfileName(profile.name)}</option>)}
                    </select>
                    <div className="search-box">
                      <Search size={14} />
                      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题 / 摘要 / 理由" />
                    </div>
                    <button className="soft sm" onClick={useLatestScope} title="切到最新会议"><RefreshCw size={14} />最新</button>
                    <button className="soft sm" onClick={clearScope} title="清除会议/年份筛选"><X size={14} />全部</button>
                    <button className={`soft sm ${hideInZotero ? "toggle-on" : ""}`} onClick={() => setHideInZotero((value) => !value)} title="隐藏已在 Zotero 库中的论文">
                      <BookmarkCheck size={14} />{hideInZotero ? "显示已在库" : "隐藏已在库"}
                    </button>
                  </div>
                  <div className="scope-line">
                    <span>
                      {matchScopeLabel} · 显示 {filteredMatches.length}/{matches.length}{inZoteroCount ? ` · ${inZoteroCount} 篇已在库` : ""}
                      {matchesLoading && <span className="scope-loading"><Loader2 size={12} className="spin" />匹配中…</span>}
                    </span>
                    {selectedMatches.length > 0 && <span className="sel-count">已选 {selectedMatches.length} 篇</span>}
                  </div>
                </div>

                <div className="table-scroll">
                  {matchesLoading && (
                    <div className="table-loading" role="status" aria-live="polite">
                      <div className="table-loading-card">
                        <Loader2 size={20} className="spin" />
                        <span>正在匹配论文…</span>
                      </div>
                    </div>
                  )}
                  <table className={`matches ${matchesLoading ? "is-loading" : ""}`}>
                    <thead><tr>
                      <th className="select-cell"><input type="checkbox" title="全选当前结果" checked={allVisibleSelected} disabled={visiblePaperIds.length === 0} onChange={toggleVisibleSelection} /></th>
                      <th className="score-col">分数</th>
                      <th>论文</th>
                      <th className="signal-col">信号</th>
                    </tr></thead>
                    <tbody>
                      {filteredMatches.map((item) => (
                        <tr key={matchKey(item)} className={selected && matchKey(selected) === matchKey(item) ? "selected" : ""} onClick={() => setSelected(item)}>
                          <td className="select-cell">
                            <input type="checkbox" checked={selectedPaperIdSet.has(item.paper_id)} aria-label={`选择 ${item.title}`}
                              onChange={(event) => { event.stopPropagation(); togglePaperSelection(item.paper_id); }}
                              onClick={(event) => event.stopPropagation()} />
                          </td>
                          <td className="score-col"><ScorePill score={item.score} /></td>
                          <td className="paper-cell notranslate" translate="no">
                            <ProtectedText as="div" className="paper-title" text={item.title} />
                            <div className="paper-meta-line">
                              <ProtectedText as="span" className="paper-meta" text={`${item.conference.toUpperCase()} ${item.year} · ${item.eventtype || item.decision || "paper"}`} />
                              <span className="dir-chip">{displayProfileName(item.profile_name)}</span>
                              {isInZotero(item) && <span className="feedback-chip in-zotero"><Check size={11} />已在库</span>}
                              {item.dynamic && <span className="feedback-chip dynamic">即时</span>}
                              {item.feedback_action && <span className={`feedback-chip ${item.feedback_action}`}>{feedbackLabel(item.feedback_action)}</span>}
                            </div>
                          </td>
                          <td className="signal-col"><Signal item={item} /></td>
                        </tr>
                      ))}
                      {filteredMatches.length === 0 && (
                        <tr><td colSpan={4} className="empty-row">当前筛选下没有论文。换个会议/年份/方向，或在「数据与导入」里更新排序。</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="action-bar">
                  <span className="action-hint">{selectedMatches.length > 0 ? `已选 ${selectedMatches.length} 篇` : "勾选论文后可批量操作"}</span>
                  <div className="action-buttons">
                    <button className="soft sm" onClick={() => setSelectedPaperIds([])} disabled={selectedMatches.length === 0}><X size={14} />清空</button>
                    <button className="soft sm" onClick={exportSelectedPapers} disabled={selectedMatches.length === 0}><Download size={14} />导出 CSV</button>
                    <button className="primary sm" onClick={openExportModal} disabled={selectedMatches.length === 0}><Database size={14} />添加到 Zotero</button>
                  </div>
                </div>
              </div>

              <aside className="detail-pane">
                {selected ? (
                  <>
                    <div className="detail-head">
                      <ProtectedText as="h2" text={selected.title} />
                      <div className="detail-badges">
                        <ScorePill score={selected.score} large />
                        {isInZotero(selected) && <span className="feedback-chip in-zotero"><Check size={11} />已在 Zotero</span>}
                        {selected.feedback_action && <span className={`feedback-chip ${selected.feedback_action}`}>{feedbackLabel(selected.feedback_action)}</span>}
                      </div>
                    </div>
                    <div className="detail-actions">
                      <button onClick={() => onFeedback("relevant")}><Heart size={15} />相关</button>
                      <button onClick={() => onFeedback("not_relevant")}><X size={15} />不相关</button>
                      <button onClick={() => onFeedback("want_to_read")}><BookmarkCheck size={15} />想读</button>
                      <button onClick={() => onFeedback("read")}><Check size={15} />已读</button>
                      <button onClick={() => onFeedback("hide")}><Archive size={15} />隐藏</button>
                    </div>
                    {selected.reason && <section><h3>推荐理由</h3><p>{selected.reason}</p></section>}
                    {selected.matched_zotero_items.length > 0 && (
                      <section>
                        <h3>最相似的 Zotero 文献</h3>
                        {selected.matched_zotero_items.map((item) => (
                          <div className="zotero-hit notranslate" translate="no" key={item.id}><ProtectedText as="span" text={item.title} /><strong>{item.score.toFixed(2)}</strong></div>
                        ))}
                      </section>
                    )}
                    <section><h3>摘要</h3><ProtectedText as="p" text={selected.abstract || "暂无摘要。"} /></section>
                    <section className="links">
                      {selected.url && <a href={selected.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />会议页面</a>}
                      {selected.pdf_url && <a href={selected.pdf_url} target="_blank" rel="noreferrer"><FileText size={14} />PDF</a>}
                    </section>
                  </>
                ) : <div className="empty-state"><Sparkles size={22} /><p>选择左侧任意一篇论文查看详情、证据与反馈。</p></div>}
              </aside>
            </section>
          )}

          {view === "figures" && (
            <FiguresView
              conferences={health.conferences}
              conferenceOptions={conferenceOptions}
              profiles={profiles}
              pushToast={pushToast}
            />
          )}

          {view === "profiles" && (
            <section className="card">
              <div className="card-toolbar">
                <span className="muted">共 {profiles.length} 个方向 · 推荐 {profileCounts.curated ?? 0} · 自定义 {profileCounts.custom ?? 0} · 全库 {profileCounts.library ?? 0} · 原始 {profileCounts.noisy ?? 0}</span>
                <label className="inline-check"><input type="checkbox" checked={showNoisyProfiles} onChange={(event) => setShowNoisyProfiles(event.target.checked)} />显示原始/噪声</label>
              </div>
              <div className="table-scroll">
                <table className="data-table profiles-table">
                  <thead><tr><th>名称</th><th>类别</th><th>来源</th><th>条目</th><th>关键词</th><th>操作</th></tr></thead>
                  <tbody>
                    {visibleProfiles.map((profile) => (
                      <tr key={profile.id}>
                        <td>{displayProfileName(profile.name)}</td>
                        <td><span className={`quality ${profile.quality ?? "unknown"}`}>{qualityLabel(profile.quality)}</span></td>
                        <td className="muted">{profile.source_label ?? profile.source_type}</td>
                        <td>{profile.item_count}</td>
                        <td className="clamp muted">{safeKeywords(profile.keywords)}</td>
                        <td>
                          <div className="row-actions">
                            <button className="soft sm" onClick={() => openProfilePapers(profile)}><Eye size={14} />论文</button>
                            <button className="danger sm" disabled={profile.source_type !== "custom_text"} onClick={() => onDeleteProfile(profile)}><Trash2 size={14} />删除</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {view === "data" && (
            <section className="data-grid">
              <Card title="本地库概览" icon={<Database size={17} />}>
                <div className="overview">
                  <div><span>Zotero 条目</span><strong>{health.zotero_items}</strong></div>
                  <div><span>含摘要</span><strong>{health.zotero_abstracts}</strong></div>
                  <div><span>研究方向</span><strong>{health.profiles}</strong></div>
                  <div><span>会议论文</span><strong>{health.conference_papers}</strong></div>
                </div>
                {coverageByConference.length > 0 && (
                  <div className="coverage">
                    {coverageByConference.map((group) => (
                      <div key={group.name} className="coverage-row">
                        <span className="coverage-name">{group.name.toUpperCase()}</span>
                        <div className="coverage-years">
                          {group.years.map((item) => (
                            <button key={item.year} className="chip-btn" onClick={() => { setConference(item.conference); setYear(item.year); setMatchConference(item.conference); setMatchYear(String(item.year)); refreshMatches(profileId, item.conference, String(item.year), "").then(() => setView("recommendations")).catch((error) => pushToast(error.message, "error")); }}>
                              {item.year}<small>{item.count}</small>
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="导入 Zotero 兴趣库" icon={<FileText size={17} />}>
                <p className="muted">只读读取本机 Zotero，或上传 BibTeX。只有 Zotero 内容变化时才需要刷新。</p>
                <label className="field">Zotero 数据库路径<input value={zoteroPath} onChange={(event) => setZoteroPath(event.target.value)} placeholder="自动发现，或粘贴 zotero.sqlite 路径" /></label>
                <label className="field">Collection 过滤（可选）<input value={zoteroCollection} onChange={(event) => setZoteroCollection(event.target.value)} placeholder="精确 collection 名" /></label>
                <div className="btn-row">
                  <button className="soft" onClick={onDiscoverZotero}><Search size={15} />查找</button>
                  <button className="primary" onClick={onImportLocalZotero}><Database size={15} />导入 / 刷新</button>
                  <label className="upload"><input type="file" accept=".bib,.txt" onChange={(event) => onZoteroFile(event.target.files?.[0])} /><span><Upload size={15} />BibTeX</span></label>
                </div>
              </Card>

              <Card title="导入会议中稿论文" icon={<Database size={17} />}>
                <p className="muted">上传 accepted papers CSV，建立本地会议论文库。</p>
                <div className="field-row">
                  <label className="field">会议<input value={conference} onChange={(event) => setConference(event.target.value.toLowerCase())} /></label>
                  <label className="field">年份<input type="number" value={year} onChange={(event) => setYear(Number(event.target.value))} /></label>
                </div>
                <label className="upload wide"><input type="file" accept=".csv,text/csv" onChange={(event) => onConferenceFile(event.target.files?.[0])} /><span><Upload size={15} />选择 CSV</span></label>
              </Card>

              <Card title="排序计算" icon={<Play size={17} />}>
                <p className="muted">当前目标：{conference.toUpperCase()} {year}{selectedConferenceCount ? ` · ${selectedConferenceCount} 篇` : " · 未导入"}。已有推荐会自动加载，只有新增数据或想让反馈影响排序时才需重算。</p>
                <label className="field">每个方向结果数<input type="number" value={limit} min={20} max={500} onChange={(event) => setLimit(Number(event.target.value))} /></label>
                <button className="primary" disabled={busy || health.zotero_items === 0 || health.conference_papers === 0} onClick={onRunMatches}><Play size={15} />重新计算排序</button>
              </Card>
            </section>
          )}

          {view === "settings" && (
            <section className="data-grid">
              <Card title="Zotero 写入目标" icon={<Database size={17} />}>
                <p className="muted">通过本机 Zotero Connector 把论文写入已存在的目录（不直接改 zotero.sqlite）。也可以在添加时的弹窗里选择目录。</p>
                <div className="status-line">
                  <span className={`dot ${localZoteroStatus?.connector ? "ok" : "off"}`} />
                  {localZoteroStatus ? localZoteroStatus.message : "尚未检测本机 Zotero。"}
                </div>
                <CollectionPicker
                  collections={zoteroCollections}
                  value={collectionKey}
                  onChange={setCollectionKey}
                  onReload={loadZoteroCollections}
                  onCheck={checkLocalZotero}
                />
              </Card>
              <Card title="模型与隐私" icon={<Settings size={17} />}>
                <p className="muted">Embedding 默认在本机运行（auto：优先 sentence-transformers，不可用时退回本地 fallback 向量器）。LLM 解释当前默认关闭。</p>
                <button className="soft" onClick={onRebuildEmbeddings}><RefreshCw size={15} />重建 embeddings</button>
              </Card>
              <Card title="后端" icon={<Database size={17} />}>
                <p className="muted"><strong>API:</strong> {API_BASE}</p>
                <p className="muted"><strong>数据库:</strong> 本机 SQLite（PAPER_RADAR_DB）</p>
              </Card>
            </section>
          )}
        </div>
      </main>

      {exportOpen && (
        <div className="modal-backdrop" onClick={() => setExportOpen(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h2><Database size={18} />添加到 Zotero</h2>
              <button className="ghost-btn" onClick={() => setExportOpen(false)}><X size={16} /></button>
            </div>
            <div className="modal-body">
              <p className="muted">将 <strong>{selectedPaperIds.length}</strong> 篇选中论文写入本机 Zotero 的目标目录。没有 PDF 的论文会自动尝试从 arXiv 匹配并附上 PDF。</p>
              {selectedMatches.filter(isInZotero).length > 0 && (
                <div className="status-line"><Check size={14} />其中 {selectedMatches.filter(isInZotero).length} 篇已在 Zotero 库中（重复添加会被识别为「未变化」）。</div>
              )}
              <div className="status-line">
                <span className={`dot ${localZoteroStatus?.connector ? "ok" : "off"}`} />
                {localZoteroStatus ? localZoteroStatus.message : "正在检测本机 Zotero…"}
              </div>
              <CollectionPicker
                collections={zoteroCollections}
                value={collectionKey}
                onChange={setCollectionKey}
                onReload={loadZoteroCollections}
                onCheck={checkLocalZotero}
              />
            </div>
            <div className="modal-foot">
              <span className="muted">{collectionName ? `目标：${collectionName}` : "请选择目标目录"}</span>
              <div>
                <button className="soft" onClick={() => setExportOpen(false)}>取消</button>
                <button className="primary" disabled={busy || !collectionKey.trim()} onClick={confirmExport}>
                  {busy ? <Loader2 size={15} className="spin" /> : <Database size={15} />}添加 {selectedPaperIds.length} 篇
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="toast-stack">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`} onClick={() => dismissToast(toast.id)}>
            <span className="toast-icon">{toast.kind === "success" ? <Check size={15} /> : toast.kind === "error" ? <X size={15} /> : <Sparkles size={15} />}</span>
            <span className="toast-text">{toast.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CollectionPicker({ collections, value, onChange, onReload, onCheck }: {
  collections: ZoteroCollection[];
  value: string;
  onChange: (value: string) => void;
  onReload: () => Promise<unknown>;
  onCheck: () => Promise<unknown>;
}) {
  const [advanced, setAdvanced] = useState(false);
  const known = collections.some((item) => item.key === value);
  return (
    <div className="picker">
      <label className="field">目标目录
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">选择目录…</option>
          {value && !known && <option value={value}>{value}（手动）</option>}
          {collections.map((item) => (
            <option key={item.key} value={item.key}>
              {`${"   ".repeat(Math.max(0, (item.level ?? 1) - 1))}${(item.level ?? 1) > 1 ? "└ " : ""}${item.name}`}
            </option>
          ))}
        </select>
      </label>
      <div className="btn-row">
        <button className="soft sm" onClick={() => { onCheck(); onReload(); }}><RefreshCw size={14} />读取目录</button>
        <button className="soft sm" onClick={() => setAdvanced((open) => !open)}>{advanced ? "隐藏" : "手动输入 Key"}</button>
      </div>
      {advanced && (
        <label className="field">目录 Key<input value={value} onChange={(event) => onChange(event.target.value)} placeholder="Collection key，如 C32" /></label>
      )}
    </div>
  );
}

const FIGURE_KINDS: Array<{ value: string; label: string }> = [
  { value: "oral", label: "Oral" },
  { value: "spotlight", label: "Spotlight" },
  { value: "highlight", label: "Highlight" },
  { value: "poster", label: "Poster" },
  { value: "all", label: "全部" }
];

function FiguresView({ conferences, conferenceOptions, profiles, pushToast }: {
  conferences: Array<{ conference: string; year: number; count: number }>;
  conferenceOptions: string[];
  profiles: Profile[];
  pushToast: (text: string, kind?: ToastKind) => void;
}) {
  const [figConference, setFigConference] = useState(conferenceOptions[0] ?? "cvpr");
  const yearOptions = useMemo(
    () => Array.from(new Set(conferences.filter((item) => item.conference === figConference).map((item) => item.year))).sort((a, b) => b - a),
    [conferences, figConference]
  );
  const [figYear, setFigYear] = useState<number | undefined>(yearOptions[0]);
  const [kind, setKind] = useState("oral");
  const [relevanceMode, setRelevanceMode] = useState<"all" | "profile" | "custom">("all");
  const [figProfileId, setFigProfileId] = useState<number | "">("");
  const [figureInterestText, setFigureInterestText] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [paperListOpen, setPaperListOpen] = useState(false);
  const [batchSize, setBatchSize] = useState(8);
  const [batchOffset, setBatchOffset] = useState(0);
  const [papers, setPapers] = useState<FigurePaper[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [listLoading, setListLoading] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 });
  const [activePapers, setActivePapers] = useState<FigurePaper[]>([]);
  const [galleryItems, setGalleryItems] = useState<Array<{ paper: FigurePaper; figure: Figure }>>([]);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const batchReq = useRef(0);

  const effectiveYear = figYear ?? yearOptions[0];
  const visibleProfiles = useMemo(
    () => profiles.filter((profile) => profile.quality !== "noisy" && profile.source_type !== "tag"),
    [profiles]
  );

  useEffect(() => {
    if (!yearOptions.includes(figYear ?? -1)) setFigYear(yearOptions[0]);
  }, [yearOptions]);
  useEffect(() => {
    if (!figProfileId && visibleProfiles.length > 0) setFigProfileId(visibleProfiles[0].id);
  }, [figProfileId, visibleProfiles]);

  async function loadPapers(nextConference = figConference, nextYear = effectiveYear, nextKind = kind): Promise<FigurePaper[]> {
    if (!nextConference || !nextYear) return [];
    setListLoading(true);
    try {
      if (relevanceMode === "profile" && figProfileId) {
        const profile = visibleProfiles.find((item) => item.id === Number(figProfileId));
        const query = profile ? profileQueryText(profile) : "";
        if (!query) {
          pushToast("这个研究方向缺少可用于匹配的关键词。", "info");
          return [];
        }
        const matches = await matchCustomText(query, nextConference, nextYear, 5000);
        const all = dedupeFigurePapers(matches.map(matchToFigurePaper));
        const next = all.filter((paper) => nextKind === "all" || paper.kind === nextKind);
        setPapers(next);
        setCounts(countFigureKinds(all));
        return next;
      } else if (relevanceMode === "custom") {
        const text = figureInterestText.trim();
        if (!text) {
          pushToast("先写一句你现在感兴趣的论文方向或描述。", "info");
          return [];
        }
        const matches = await matchCustomText(text, nextConference, nextYear, 5000);
        const all = dedupeFigurePapers(matches.map(matchToFigurePaper));
        const next = all.filter((paper) => nextKind === "all" || paper.kind === nextKind);
        setPapers(next);
        setCounts(countFigureKinds(all));
        return next;
      } else {
        const result = await getFigurePapers(nextConference, nextYear, nextKind, 500);
        const items = dedupeFigurePapers(result.items);
        setPapers(items);
        setCounts(result.counts);
        return items;
      }
    } catch (error) {
      pushToast(error instanceof Error ? error.message : String(error), "error");
      return [];
    } finally {
      setListLoading(false);
    }
  }

  async function buildBatch(nextOffset = batchOffset, force = false, sourceOverride?: FigurePaper[]) {
    const source = dedupeFigurePapers(sourceOverride ?? papers);
    if (source.length === 0) {
      pushToast("当前会议 / 年份 / 条件下没有可用于抽取的论文。", "info");
      return;
    }
    const offset = Math.min(nextOffset, Math.max(0, source.length - 1));
    const batch = source.slice(offset, offset + batchSize);
    if (batch.length === 0) return;
    const reqId = ++batchReq.current;
    setBatchOffset(offset);
    setActivePapers(batch);
    setPaperListOpen(true);
    setGalleryItems([]);
    setBatchProgress({ current: 0, total: batch.length });
    setBatchLoading(true);
    try {
      const collected: Array<{ paper: FigurePaper; figure: Figure }> = [];
      for (let index = 0; index < batch.length; index += 1) {
        if (reqId !== batchReq.current) return;
        const paper = batch[index];
        setBatchProgress({ current: index + 1, total: batch.length });
        try {
          const result = await getPaperFigures(paper.paper_id, force, false);
          if (result.ok) {
            const nextItems = result.figures.map((figure) => ({ paper, figure }));
            collected.push(...nextItems);
            setGalleryItems([...collected]);
          }
        } catch {
          // Keep the wall flowing even if one PDF fails.
        }
      }
      if (reqId === batchReq.current && collected.length === 0) {
        pushToast("这一批没有抽到图表，可以换一批或改会议类型。", "info");
      }
    } catch (error) {
      pushToast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      if (reqId === batchReq.current) setBatchLoading(false);
    }
  }

  async function generateBatch() {
    const source = await loadPapers();
    await buildBatch(0, false, source);
  }

  function nextBatch() {
    if (papers.length === 0) {
      generateBatch();
      return;
    }
    const nextOffset = batchOffset + batchSize >= papers.length ? 0 : batchOffset + batchSize;
    buildBatch(nextOffset);
  }

  useEffect(() => { if (effectiveYear) loadPapers(); /* eslint-disable-next-line */ }, []);
  const sourceLabel = relevanceMode === "profile" ? "研究方向排序" : relevanceMode === "custom" ? "描述排序" : "全部方向";
  const kindLabel = FIGURE_KINDS.find((item) => item.value === kind)?.label ?? kind;

  return (
    <section className="figures-layout">
      <div className="figures-pane">
        <div className="toolbar">
          <div className="toolbar-filters">
            <select value={figConference} onChange={(event) => { setFigConference(event.target.value); }} title="会议">
              {conferenceOptions.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
            </select>
            <select value={effectiveYear ?? ""} onChange={(event) => setFigYear(Number(event.target.value))} title="年份">
              {yearOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <button className={`soft sm filter-toggle ${filtersOpen ? "open" : ""}`} onClick={() => setFiltersOpen((open) => !open)}>
              <ChevronRight size={14} />筛选条件
            </button>
            <select value={batchSize} onChange={(event) => setBatchSize(Number(event.target.value))} title="批量">
              {[4, 8, 12, 16].map((item) => <option key={item} value={item}>{item} 篇/批</option>)}
            </select>
            <button className="primary sm" disabled={listLoading || batchLoading} onClick={generateBatch}><ImageIcon size={14} />生成灵感墙</button>
            <button className="soft sm" disabled={batchLoading} onClick={nextBatch}><RefreshCw size={14} />换一批</button>
          </div>
          {filtersOpen && (
            <div className="figures-filter-panel">
              <div className="filter-fields">
                <label>相关性筛选
                  <select value={relevanceMode} onChange={(event) => setRelevanceMode(event.target.value as "all" | "profile" | "custom")}>
                    <option value="all">全部方向</option>
                    <option value="profile">按研究方向排序</option>
                    <option value="custom">按临时描述排序</option>
                  </select>
                </label>
                {relevanceMode === "profile" && (
                  <label>研究方向
                    <select value={figProfileId} onChange={(event) => setFigProfileId(Number(event.target.value))}>
                      {visibleProfiles.map((profile) => <option key={profile.id} value={profile.id}>{displayProfileName(profile.name)}</option>)}
                    </select>
                  </label>
                )}
              </div>
              {relevanceMode === "custom" && (
                <label className="figure-interest-box">临时描述
                  <textarea
                    value={figureInterestText}
                    onChange={(event) => setFigureInterestText(event.target.value)}
                    placeholder="例如：我想看 3D generation / text-to-image safety / dataset de-duplication 相关论文里的实验图和消融表。"
                  />
                </label>
              )}
              <div className="kind-tabs">
                {FIGURE_KINDS.map((item) => (
                  <button key={item.value} className={kind === item.value ? "active" : ""} onClick={() => setKind(item.value)}>
                    {item.label}{counts[item.value] != null ? <small>{counts[item.value]}</small> : null}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="scope-line">
            <span>
              {figConference.toUpperCase()} · {effectiveYear ?? "—"} · {sourceLabel} · {kindLabel} · 候选 {papers.length} 篇 · 当前 {activePapers.length} 篇 / {galleryItems.length} 个图表
              {listLoading && <span className="scope-loading"><Loader2 size={12} className="spin" />载入中…</span>}
              {batchLoading && <span className="scope-loading"><Loader2 size={12} className="spin" />抽取 {batchProgress.current}/{batchProgress.total}</span>}
            </span>
          </div>
        </div>

      </div>

      <div className={`figures-content ${paperListOpen && activePapers.length > 0 ? "with-paper-list" : ""}`}>
        {paperListOpen && activePapers.length > 0 && (
          <aside className="figure-paper-sidebar">
            <div className="figure-paper-sidebar-head">
              <div>
                <h2>当前批次论文</h2>
                <p>{figConference.toUpperCase()} · {effectiveYear ?? "—"} · {activePapers.length} 篇</p>
              </div>
              <button className="ghost-btn" onClick={() => setPaperListOpen(false)} title="收起论文列表"><X size={16} /></button>
            </div>
            <div className="figure-paper-sidebar-list">
              {activePapers.map((paper, index) => (
                <div className="batch-paper-row" key={paper.paper_id}>
                  <span className="batch-paper-index">{index + 1}</span>
                  <div>
                    <ProtectedText as="div" className="batch-paper-title" text={paper.title} />
                    <div className="fp-meta">
                      <span className={`kind-chip ${paper.kind}`}>{paper.kind}</span>
                      {!paper.has_pdf && <span className="kind-chip nopdf">需 arXiv</span>}
                      <ProtectedText as="span" className="fp-authors" text={paper.authors} />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </aside>
        )}

        <div className="figures-gallery">
          {galleryItems.length > 0 ? (
            <>
              <div className="gallery-head">
                <h2>{figConference.toUpperCase()} {effectiveYear} 图表灵感墙</h2>
                <div className="gallery-actions">
                  <button className={`soft sm filter-toggle ${paperListOpen ? "open" : ""}`} disabled={activePapers.length === 0} onClick={() => setPaperListOpen((open) => !open)}>
                    <ChevronRight size={14} />论文列表
                  </button>
                  <button className="soft sm" disabled={batchLoading || activePapers.length === 0} onClick={() => buildBatch(batchOffset, true)}><RefreshCw size={14} />重抽当前批</button>
                  <button className="soft sm" disabled={batchLoading} onClick={nextBatch}><RefreshCw size={14} />换一批</button>
                </div>
              </div>
              <div className="figure-grid inspiration-grid">
                {galleryItems.map(({ paper, figure }) => {
                  const src = figureImageUrl(paper.paper_id, figure);
                  return (
                    <figure key={`${paper.paper_id}-${figure.name}`} className="figure-card" onClick={() => setLightbox(src)}>
                      <img src={src} loading="lazy" alt={`${figureSourceLabel(figure.source)} ${paper.title}`} />
                      <figcaption>
                        <span>{figureSourceLabel(figure.source)} · p.{figure.page}</span>
                        <ProtectedText as="span" className="figure-card-title" text={paper.title} />
                        <Maximize2 size={13} />
                      </figcaption>
                    </figure>
                  );
                })}
              </div>
              {batchLoading && (
                <div className="gallery-inline-loading">
                  <Loader2 size={16} className="spin" />
                  <span>继续抽取 {batchProgress.current}/{batchProgress.total}</span>
                </div>
              )}
            </>
          ) : batchLoading || listLoading ? (
            <div className="gallery-loading">
              <Loader2 size={26} className="spin" />
              <p>{listLoading ? "正在载入候选论文…" : "正在下载 PDF 并抽取图表…"}</p>
              <span className="muted">{batchProgress.total ? `当前批次 ${batchProgress.current}/${batchProgress.total}` : "按会议和相关性准备候选"}</span>
            </div>
          ) : (
            <div className="empty-state"><ImageIcon size={22} /><p>选择会议和年份后生成一批图表。适合按目标会议快速找配图、表格和实验展示灵感。</p></div>
          )}
        </div>
      </div>

      {lightbox && (
        <div className="lightbox" onClick={() => setLightbox(null)}>
          <button className="lightbox-close" onClick={() => setLightbox(null)}><X size={20} /></button>
          <img src={lightbox} alt="figure" onClick={(event) => event.stopPropagation()} />
        </div>
      )}
    </section>
  );
}

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function Card({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return <section className="card"><div className="card-head">{icon}<h2>{title}</h2></div><div className="card-body">{children}</div></section>;
}

function ScorePill({ score, large }: { score: number; large?: boolean }) {
  const tone = score >= 0.45 ? "high" : score >= 0.3 ? "mid" : "low";
  return <span className={`score-pill ${tone} ${large ? "lg" : ""}`}>{score.toFixed(3)}</span>;
}

function ProtectedText({ as, className = "", text }: { as: "div" | "span" | "h2" | "p"; className?: string; text: string }) {
  const Component = as;
  return <Component className={`protected-text notranslate ${className}`.trim()} translate="no" lang="en">{text}</Component>;
}

function Signal({ item }: { item: Match }) {
  return (
    <span className="signals notranslate" translate="no">
      <em>语义</em> {item.embedding_score.toFixed(2)} · <em>词面</em> {item.bm25_score.toFixed(2)} · <em>标签</em> {item.tag_score.toFixed(2)}
    </span>
  );
}

function safeKeywords(raw: string) {
  try { return JSON.parse(raw).join(", "); } catch { return raw; }
}
function qualityLabel(value?: string) {
  return { library: "全库", custom: "自定义", curated: "推荐", noisy: "原始" }[value ?? ""] ?? "未知";
}
function qualityPrefix(value?: string) {
  return { library: "全库 · ", custom: "自定义 · ", curated: "", noisy: "原始 · " }[value ?? ""] ?? "";
}
function displayProfileName(name: string) {
  return name === "All Zotero" ? "Zotero 全库" : name;
}
function matchKey(item: Match) {
  return String(item.id ?? `${item.profile_id ?? "temp"}-${item.paper_id}`);
}
function csvFromRows(rows: Array<Record<string, string | number | null | undefined>>) {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  return [headers.map(csvCell).join(","), ...rows.map((row) => headers.map((header) => csvCell(row[header])).join(","))].join("\n");
}
function csvCell(value: string | number | null | undefined) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}
function downloadTextFile(filename: string, text: string, type: string) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
function feedbackLabel(action?: string | null) {
  return { relevant: "相关", not_relevant: "不相关", want_to_read: "想读", read: "已读", hide: "隐藏" }[action ?? ""] ?? "未标注";
}
function figureSourceLabel(source?: string) {
  if (source === "table") return "表";
  if (source === "embedded") return "嵌入图";
  return "图";
}
function figureImageUrl(paperId: number, figure: Figure) {
  return figure.data_url || figureFileUrl(paperId, figure.name);
}
function matchToFigurePaper(match: Match): FigurePaper {
  return {
    paper_id: match.paper_id,
    title: match.title,
    authors: match.authors,
    kind: classifyFigureKind(match.decision, match.eventtype),
    decision: match.decision,
    eventtype: match.eventtype,
    url: match.url,
    has_pdf: Boolean(match.pdf_url || match.url),
    cached: false
  };
}
function classifyFigureKind(decision?: string, eventtype?: string) {
  const text = `${decision ?? ""} ${eventtype ?? ""}`.toLowerCase();
  if (text.includes("oral")) return "oral";
  if (text.includes("spotlight")) return "spotlight";
  if (text.includes("highlight")) return "highlight";
  if (text.includes("poster")) return "poster";
  return "other";
}
function countFigureKinds(papers: FigurePaper[]) {
  const counts: Record<string, number> = { all: papers.length };
  for (const paper of papers) counts[paper.kind] = (counts[paper.kind] ?? 0) + 1;
  return counts;
}
function profileQueryText(profile: Profile) {
  const terms = safeKeywords(profile.keywords);
  return `${displayProfileName(profile.name)} ${terms}`.trim().slice(0, 1800);
}
function dedupeFigurePapers(papers: FigurePaper[]) {
  const byTitle = new Map<string, FigurePaper>();
  const order: string[] = [];
  for (const paper of papers) {
    const key = normalizePaperTitle(paper.title) || `id:${paper.paper_id}`;
    const existing = byTitle.get(key);
    if (!existing) {
      byTitle.set(key, paper);
      order.push(key);
    } else if (figurePaperRank(paper) < figurePaperRank(existing)) {
      byTitle.set(key, paper);
    }
  }
  return order.map((key) => byTitle.get(key)!).filter(Boolean);
}
function normalizePaperTitle(title: string) {
  return title.trim().toLowerCase().replace(/\s+/g, " ");
}
function figurePaperRank(paper: FigurePaper) {
  const text = `${paper.eventtype ?? ""} ${paper.url ?? ""}`.toLowerCase();
  const oralRank = text.includes("oral") ? 0 : 1;
  const pdfRank = paper.has_pdf ? 0 : 1;
  return oralRank * 10_000 + pdfRank * 1_000 + paper.paper_id;
}
function titleFor(view: View) {
  return { recommendations: "推荐列表", queue: "阅读队列", figures: "图表学习", profiles: "研究方向", data: "数据与导入", settings: "设置" }[view];
}
function subtitleFor(view: View) {
  return {
    recommendations: "按研究方向或临时兴趣浏览推荐论文，勾选后可一键加入 Zotero。",
    queue: "按想读 / 已读 / 相关 / 隐藏管理你的阅读队列。",
    figures: "选会议 / 年份 / 类型，点开某篇即时抽取 PDF 里的配图供你学习排版与可视化。",
    profiles: "查看由 Zotero 标签、collection 和自定义文本生成的研究方向。",
    data: "导入 Zotero 与会议论文，并维护本地排序。",
    settings: "Zotero 写入目标、本地模型与后端连接。"
  }[view];
}

createRoot(document.getElementById("root")!).render(<App />);
