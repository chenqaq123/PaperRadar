#!/usr/bin/env python3
"""Generate a conference-specific papers viewer HTML + JS pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{conference_label} Papers Viewer</title>
  <style>
    :root {{
      --panel: #fffaf3; --ink: #182126; --muted: #5d6a73; --line: #d9cfc1; --accent: #0e6a73; --accent-2: #d77a2d; --shadow: 0 16px 48px rgba(30, 24, 15, 0.08); --radius: 20px;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif; color: var(--ink); background: radial-gradient(circle at top left, rgba(14,106,115,0.14), transparent 24%), radial-gradient(circle at 90% 10%, rgba(215,122,45,0.14), transparent 20%), linear-gradient(180deg, #f7f3ec 0%, #f0eadf 100%); }}
    .page {{ max-width: 1220px; margin: 0 auto; padding: 28px 20px 60px; }}
    .hero, .panel {{ background: rgba(255, 250, 243, 0.94); border: 1px solid rgba(24, 33, 38, 0.08); border-radius: 24px; box-shadow: var(--shadow); }}
    .hero {{ padding: 28px; margin-bottom: 18px; }}
    .eyebrow {{ font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); font-weight: 700; }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: clamp(2rem, 3.8vw, 3.4rem); margin-top: 10px; max-width: 820px; line-height: 1.08; }}
    .subtitle {{ margin-top: 14px; color: var(--muted); max-width: 820px; line-height: 1.7; }}
    .controls {{ padding: 20px; display: grid; grid-template-columns: 1.2fr 1.2fr 0.9fr auto; gap: 12px; align-items: end; margin-bottom: 18px; }}
    .field label {{ display: block; font-size: 0.88rem; color: var(--muted); margin-bottom: 6px; }}
    .field input, .field select, .field button {{ width: 100%; border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; font: inherit; background: #fffdf8; color: var(--ink); }}
    .field button {{ background: linear-gradient(135deg, var(--accent), #0a5560); color: #fff; cursor: pointer; border: none; font-weight: 600; min-width: 150px; }}
    .statusbar {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }}
    .chip {{ display: inline-flex; align-items: center; padding: 8px 12px; border-radius: 999px; background: rgba(24, 33, 38, 0.05); color: var(--muted); font-size: 0.9rem; }}
    .notice {{ padding: 14px 16px; border-radius: 16px; margin-bottom: 18px; background: rgba(215, 122, 45, 0.08); color: #7b4c22; border: 1px solid rgba(215, 122, 45, 0.14); line-height: 1.6; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; max-width: 920px; margin: 0 auto; }}
    .card {{ padding: 22px 22px 20px; background: #fff; border: 1px solid rgba(24, 33, 38, 0.08); border-radius: var(--radius); box-shadow: 0 10px 28px rgba(25, 23, 17, 0.05); }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }}
    .meta span {{ font-size: 0.82rem; color: var(--muted); background: rgba(14, 106, 115, 0.08); padding: 4px 8px; border-radius: 999px; }}
    .card h3 {{ font-size: 1.2rem; line-height: 1.42; margin-bottom: 14px; }}
    .content-block + .content-block {{ margin-top: 14px; padding-top: 14px; border-top: 1px dashed rgba(24, 33, 38, 0.10); }}
    .content-label {{ display: block; font-size: 0.8rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin-bottom: 8px; }}
    .abstract {{ color: #37454d; line-height: 1.72; font-size: 0.96rem; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }}
    .footer-actions {{ display: flex; justify-content: center; margin-top: 22px; }}
    .footer-actions button {{ border: none; border-radius: 999px; padding: 12px 20px; background: linear-gradient(135deg, var(--accent-2), #c96615); color: #fff; font: inherit; font-weight: 700; cursor: pointer; }}
    .empty {{ padding: 34px; text-align: center; color: var(--muted); background: #fff; border-radius: var(--radius); border: 1px dashed rgba(24, 33, 38, 0.15); }}
    @media (max-width: 980px) {{ .controls {{ grid-template-columns: 1fr 1fr; }} }}
    @media (max-width: 640px) {{ .page {{ padding: 16px 12px 40px; }} .controls {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">CSV Driven Viewer</div>
      <h1>{conference_label} 论文标题与摘要浏览页</h1>
      <p class="subtitle">这个页面本身不内嵌论文数据。打开后由 JavaScript 动态读取 CSV，并把每篇文章完整渲染出来。若 CSV 里带有 zh_title，页面会展示英文标题、中文标题和原始英文摘要。</p>
    </section>
    <section class="panel controls">
      <div class="field"><label for="csvFile">选择 CSV 文件</label><input id="csvFile" type="file" accept=".csv,text/csv"></div>
      <div class="field"><label for="searchInput">搜索标题 / 摘要</label><input id="searchInput" type="search" placeholder="支持搜索英文/中文标题与摘要"></div>
      <div class="field"><label for="keywordInput">关键词搜索</label><input id="keywordInput" type="search" placeholder="多个关键词用逗号分隔，比如 gaussian, reconstruction"></div>
      <div class="field"><label for="eventFilter">展示范围</label><select id="eventFilter"><option value="all">全部</option><option value="oral">只看 Oral</option><option value="poster">只看 Poster</option></select></div>
      <div class="field"><label>&nbsp;</label><button id="autoloadBtn" type="button">尝试自动加载默认 CSV</button></div>
    </section>
    <div id="notice" class="notice">你可以直接点击“尝试自动加载默认 CSV”。如果当前是 file:// 打开且浏览器拦截本地 fetch，就改用“选择 CSV 文件”。页面会优先尝试读取翻译后 CSV，读不到时再回退到原始英文 CSV。关键词搜索会同时匹配 title + abstract + zh_title + keywords；如果 CSV 里已有 zh_abstract，也会一并检索。</div>
    <div class="statusbar">
      <div class="chip" id="sourceChip">数据源：未加载</div>
      <div class="chip" id="totalChip">总论文数：0</div>
      <div class="chip" id="filteredChip">当前结果：0</div>
      <div class="chip" id="shownChip">当前展示：0</div>
    </div>
    <section id="results"></section>
    <div class="footer-actions" id="loadMoreWrap" hidden><button id="loadMoreBtn" type="button">加载更多</button></div>
  </main>
  <script src="./papers_viewer.js"></script>
</body>
</html>
"""


JS_TEMPLATE = """(function () {{
  "use strict";

  const DEFAULT_CSV_CANDIDATES = {default_csv_candidates};
  const PAGE_SIZE = 60;
  const state = {{ papers: [], filtered: [], visibleCount: PAGE_SIZE, sourceLabel: "未加载" }};

  const csvFileInput = document.getElementById("csvFile");
  const searchInput = document.getElementById("searchInput");
  const keywordInput = document.getElementById("keywordInput");
  const eventFilter = document.getElementById("eventFilter");
  const autoloadBtn = document.getElementById("autoloadBtn");
  const resultsEl = document.getElementById("results");
  const loadMoreBtn = document.getElementById("loadMoreBtn");
  const loadMoreWrap = document.getElementById("loadMoreWrap");
  const noticeEl = document.getElementById("notice");
  const sourceChip = document.getElementById("sourceChip");
  const totalChip = document.getElementById("totalChip");
  const filteredChip = document.getElementById("filteredChip");
  const shownChip = document.getElementById("shownChip");

  function normalizeSearchText(value) {{
    return String(value || "").toLowerCase().replace(/\\s+/g, " ").trim();
  }}

  function parseKeywordQuery(value) {{
    return String(value || "").split(/[,\\n]/).map((item) => normalizeSearchText(item)).filter(Boolean);
  }}

  function escapeHtml(value) {{
    return String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }}

  function parseCsv(text) {{
    const rows = [];
    let current = "";
    let row = [];
    let inQuotes = false;
    for (let i = 0; i < text.length; i += 1) {{
      const char = text[i];
      const next = text[i + 1];
      if (char === '"') {{
        if (inQuotes && next === '"') {{ current += '"'; i += 1; }} else {{ inQuotes = !inQuotes; }}
      }} else if (char === "," && !inQuotes) {{
        row.push(current); current = "";
      }} else if ((char === "\\n" || char === "\\r") && !inQuotes) {{
        if (char === "\\r" && next === "\\n") {{ i += 1; }}
        row.push(current); current = "";
        if (row.some((cell) => cell !== "")) {{ rows.push(row); }}
        row = [];
      }} else {{
        current += char;
      }}
    }}
    if (current !== "" || row.length > 0) {{ row.push(current); rows.push(row); }}
    if (rows.length === 0) {{ return []; }}
    const header = rows[0].map((item) => item.trim());
    return rows.slice(1).map((cells) => {{
      const record = {{}};
      header.forEach((key, index) => {{ record[key] = (cells[index] || "").trim(); }});
      return record;
    }});
  }}

  function normalizePaper(row) {{
    const title = row.title || "";
    const abstract = row.abstract || "";
    const zhTitle = row.zh_title || "";
    const zhAbstract = row.zh_abstract || "";
    const keywords = row.keywords || "";
    const searchBlob = normalizeSearchText(`${{title}} ${{abstract}} ${{zhTitle}} ${{zhAbstract}} ${{keywords}}`);
    return {{
      id: row.id || "",
      title: title || "(Untitled)",
      abstract: abstract || "",
      zhTitle,
      zhAbstract,
      keywords,
      eventtype: row.eventtype || "",
      decision: row.decision || "",
      titleLower: normalizeSearchText(title),
      abstractLower: normalizeSearchText(abstract),
      zhTitleLower: normalizeSearchText(zhTitle),
      zhAbstractLower: normalizeSearchText(zhAbstract),
      searchBlob,
    }};
  }}

  function setNotice(message, tone) {{
    noticeEl.textContent = message;
    if (tone === "error") {{
      noticeEl.style.background = "rgba(179, 52, 52, 0.10)";
      noticeEl.style.color = "#8a2b2b";
      noticeEl.style.borderColor = "rgba(179, 52, 52, 0.16)";
    }} else if (tone === "success") {{
      noticeEl.style.background = "rgba(14, 106, 115, 0.10)";
      noticeEl.style.color = "#185961";
      noticeEl.style.borderColor = "rgba(14, 106, 115, 0.16)";
    }} else {{
      noticeEl.style.background = "rgba(215, 122, 45, 0.08)";
      noticeEl.style.color = "#7b4c22";
      noticeEl.style.borderColor = "rgba(215, 122, 45, 0.14)";
    }}
  }}

  function updateChips() {{
    sourceChip.textContent = `数据源：${{state.sourceLabel}}`;
    totalChip.textContent = `总论文数：${{state.papers.length}}`;
    filteredChip.textContent = `当前结果：${{state.filtered.length}}`;
    shownChip.textContent = `当前展示：${{Math.min(state.visibleCount, state.filtered.length)}}`;
  }}

  function applyFilters() {{
    const query = normalizeSearchText(searchInput.value);
    const keywordTerms = parseKeywordQuery(keywordInput.value);
    const eventValue = eventFilter.value;
    state.filtered = state.papers.filter((paper) => {{
      const matchesEvent = eventValue === "all" || paper.eventtype.toLowerCase() === eventValue;
      const matchesQuery = !query || paper.titleLower.includes(query) || paper.abstractLower.includes(query) || paper.zhTitleLower.includes(query) || paper.zhAbstractLower.includes(query);
      const matchesKeywords = keywordTerms.length === 0 || keywordTerms.every((term) => paper.searchBlob.includes(term));
      return matchesEvent && matchesQuery && matchesKeywords;
    }});
    state.visibleCount = PAGE_SIZE;
    render();
  }}

  function render() {{
    updateChips();
    if (state.filtered.length === 0) {{
      resultsEl.innerHTML = '<div class="empty">当前没有匹配结果。你可以换个关键词，或者重新选择 CSV 文件。</div>';
      loadMoreWrap.hidden = true;
      return;
    }}
    const visible = state.filtered.slice(0, state.visibleCount);
    const cards = visible.map((paper) => `
      <article class="card">
        <div class="meta">
          <span>ID ${{escapeHtml(paper.id)}}</span>
          <span>${{escapeHtml(paper.eventtype || "Unknown")}}</span>
          <span>${{escapeHtml(paper.decision || "No decision")}}</span>
        </div>
        <div class="content-block"><span class="content-label">English Title</span><h3>${{escapeHtml(paper.title)}}</h3></div>
        <div class="content-block"><span class="content-label">中文标题</span><p class="abstract">${{escapeHtml(paper.zhTitle || "暂无中文标题，请先生成 zh_title 列。")}}</p></div>
        ${{paper.zhAbstract ? `<div class="content-block"><span class="content-label">中文摘要</span><p class="abstract">${{escapeHtml(paper.zhAbstract)}}</p></div>` : ""}}
        <div class="content-block"><span class="content-label">Original Abstract</span><p class="abstract">${{escapeHtml(paper.abstract || "No abstract available.")}}</p></div>
      </article>
    `).join("");
    resultsEl.innerHTML = `<div class="grid">${{cards}}</div>`;
    loadMoreWrap.hidden = state.visibleCount >= state.filtered.length;
    shownChip.textContent = `当前展示：${{visible.length}}`;
  }}

  function loadFromCsvText(text, sourceLabel) {{
    const rows = parseCsv(text);
    const papers = rows.map(normalizePaper).filter((paper) => paper.title || paper.abstract);
    state.papers = papers;
    state.sourceLabel = sourceLabel;
    state.filtered = papers.slice();
    state.visibleCount = PAGE_SIZE;
    setNotice(`已成功加载 ${{papers.length}} 篇论文。你现在可以按标题/摘要检索，也可以用多个关键词联合筛选。`, "success");
    render();
  }}

  async function tryAutoloadDefaultCsv() {{
    setNotice(`正在尝试自动读取默认 CSV ...`, "info");
    let lastError = null;
    for (const csvPath of DEFAULT_CSV_CANDIDATES) {{
      try {{
        const response = await fetch(csvPath);
        if (!response.ok) {{ throw new Error(`HTTP ${{response.status}}`); }}
        const text = await response.text();
        loadFromCsvText(text, csvPath);
        return;
      }} catch (error) {{
        lastError = error;
      }}
    }}
    setNotice(`自动加载失败。当前如果是 file:// 打开，浏览器可能拦截本地 fetch。请改用“选择 CSV 文件”。失败原因：${{lastError ? lastError.message : "unknown error"}}`, "error");
  }}

  csvFileInput.addEventListener("change", async (event) => {{
    const [file] = event.target.files || [];
    if (!file) {{ return; }}
    const text = await file.text();
    loadFromCsvText(text, file.name);
  }});
  searchInput.addEventListener("input", applyFilters);
  keywordInput.addEventListener("input", applyFilters);
  eventFilter.addEventListener("change", applyFilters);
  autoloadBtn.addEventListener("click", tryAutoloadDefaultCsv);
  loadMoreBtn.addEventListener("click", () => {{ state.visibleCount += PAGE_SIZE; render(); }});
}})();
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conference-label", required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--english-csv", type=Path, required=True)
    parser.add_argument("--translated-csv", type=Path, required=True)
    return parser.parse_args()


def relative_str(base: Path, target: Path) -> str:
    return Path(target).relative_to(base.parent).as_posix() if target.is_absolute() else target.as_posix()


def main() -> int:
    args = parse_args()
    args.analysis_dir.mkdir(parents=True, exist_ok=True)
    html_path = args.analysis_dir / "papers_viewer.html"
    js_path = args.analysis_dir / "papers_viewer.js"

    default_candidates = [
        f"../{args.translated_csv.parent.name}/{args.translated_csv.name}",
        f"../{args.english_csv.parent.name}/{args.english_csv.name}",
    ]
    html_path.write_text(
        HTML_TEMPLATE.format(conference_label=args.conference_label),
        encoding="utf-8",
    )
    js_path.write_text(
        JS_TEMPLATE.format(default_csv_candidates=json.dumps(default_candidates, ensure_ascii=False)),
        encoding="utf-8",
    )
    print(f"Viewer HTML written to: {html_path}")
    print(f"Viewer JS written to: {js_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
