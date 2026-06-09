#!/usr/bin/env python3
"""Render the CVPR 2026 analysis outputs as an HTML report."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=Path("output") / "cvpr2026_analysis",
        help="Directory containing analysis CSV/PNG/JSON outputs",
    )
    parser.add_argument(
        "--source-csv",
        type=Path,
        default=Path("output") / "cvpr2026" / "cvpr2026_accepted_papers.csv",
        help="Original accepted papers CSV",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "cvpr2026_analysis" / "report.html",
        help="Output HTML file",
    )
    return parser.parse_args()


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


def read_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_institution(value: str) -> str:
    value = html.unescape(value or "").strip()
    value = value.replace("&amp", "&")
    value = re.sub(r"Xi(?:&#x27|‘|')\s*", "Xi'an ", value)
    value = re.sub(r"xi(?:&#x27|‘|')\s*", "xi'an ", value)
    value = re.sub(r"\s+", " ", value).strip(" :")
    if not value:
        return ""
    if value.lower() in {"xi", "xi'an"}:
        return ""
    comma_parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(comma_parts) >= 2 and len(set(comma_parts)) == 1:
        value = comma_parts[0]
    return value


def top_institutions(source_csv: Path, limit: int = 15) -> List[Tuple[str, int]]:
    counter: Dict[str, int] = {}
    for row in csv.DictReader(source_csv.open(encoding="utf-8")):
        seen = set()
        for raw in row["institutions"].split(";"):
            inst = normalize_institution(raw)
            if inst:
                seen.add(inst)
        for inst in seen:
            counter[inst] = counter.get(inst, 0) + 1
    return sorted(counter.items(), key=lambda item: item[1], reverse=True)[:limit]


def escape(text: str) -> str:
    return html.escape(text or "")


def pct(value: str | float) -> str:
    f = float(value)
    return f"{f * 100:.1f}%"


def score_bar(value: str | float, max_value: float) -> str:
    score = float(value)
    width = 0 if max_value <= 0 else max(4.0, score / max_value * 100.0)
    return (
        f'<div class="scorebar"><span class="scorefill" style="width:{width:.1f}%"></span>'
        f'<span class="scoretext">{score:.2f}</span></div>'
    )


def build_key_takeaways(
    hotspots: Sequence[Dict[str, str]],
    directions: Sequence[Dict[str, str]],
    clusters: Sequence[Dict[str, str]],
) -> List[str]:
    takeaways: List[str] = []
    if hotspots:
        top3 = ", ".join(row["phrase"] for row in hotspots[:3])
        takeaways.append(f"热点短语最集中在 {top3}，说明 3D 表达、Gaussian 系路线和多模态大模型仍是今年主轴。")
    if directions:
        top_dir = directions[0]
        takeaways.append(
            f"潜力方向榜首是 {top_dir['topic_label']}，因为它同时具备较高 oral 占比、较广机构参与和明显的问题驱动特征。"
        )
        if len(directions) > 1:
            second = directions[1]
            takeaways.append(
                f"{second['topic_label']} 排名靠前，说明 embodied/action/world model 类问题已经从概念热度走向更明确的研究议程。"
            )
    if clusters:
        biggest = max(clusters, key=lambda row: int(row["paper_count"]))
        takeaways.append(
            f"最大主题簇是 {biggest['topic_label']}，共有 {biggest['paper_count']} 篇，适合用来理解今年方法论最密集的公共语境。"
        )
    return takeaways[:4]


def hotspot_cards(rows: Sequence[Dict[str, str]]) -> str:
    cards = []
    for idx, row in enumerate(rows[:10], start=1):
        cards.append(
            f"""
            <article class="mini-card hotspot">
              <div class="mini-rank">#{idx}</div>
              <h3>{escape(row['phrase'])}</h3>
              <div class="meta-line"><strong>{escape(row['paper_count'])}</strong> papers</div>
              <div class="meta-line">oral ratio {escape(pct(row['oral_ratio']))}</div>
              <p>{escape(row['sample_titles'][:180])}...</p>
            </article>
            """
        )
    return "\n".join(cards)


def direction_cards(rows: Sequence[Dict[str, str]]) -> str:
    if not rows:
        return ""
    max_score = max(float(row["frontier_score"]) for row in rows[:8])
    cards = []
    for idx, row in enumerate(rows[:8], start=1):
        cards.append(
            f"""
            <article class="direction-card">
              <div class="direction-head">
                <div>
                  <div class="eyebrow">Promising Direction #{idx}</div>
                  <h3>{escape(row['topic_label'])}</h3>
                </div>
                <div class="score-box">
                  <span>Frontier</span>
                  <strong>{escape(row['frontier_score'])}</strong>
                </div>
              </div>
              {score_bar(row['frontier_score'], max_score)}
              <div class="chip-row">
                <span class="chip">{escape(row['paper_count'])} papers</span>
                <span class="chip">oral {escape(pct(row['oral_ratio']))}</span>
                <span class="chip">{escape(row['institution_count'])} institutions</span>
                <span class="chip">span {escape(row['domain_span'])}</span>
              </div>
              <p class="reason">{escape(row['direction_reason'])}</p>
              <details>
                <summary>Representative papers</summary>
                <p>{escape(row['representative_papers'])}</p>
              </details>
              <details>
                <summary>Top terms</summary>
                <p>{escape(row['top_terms'])}</p>
              </details>
            </article>
            """
        )
    return "\n".join(cards)


def cluster_table(rows: Sequence[Dict[str, str]]) -> str:
    body = []
    for row in rows[:10]:
        body.append(
            f"""
            <tr>
              <td>C{escape(row['cluster_id'])}</td>
              <td>{escape(row['topic_label'])}</td>
              <td>{escape(row['paper_count'])}</td>
              <td>{pct(row['oral_ratio'])}</td>
              <td>{escape(row['institution_count'])}</td>
              <td>{escape(row['top_terms'])}</td>
            </tr>
            """
        )
    return "\n".join(body)


def institution_list(items: Sequence[Tuple[str, int]]) -> str:
    return "\n".join(
        f"<li><span>{escape(name)}</span><strong>{count}</strong></li>" for name, count in items
    )


def build_html(
    stats: Dict,
    hotspots: Sequence[Dict[str, str]],
    directions: Sequence[Dict[str, str]],
    clusters: Sequence[Dict[str, str]],
    institutions: Sequence[Tuple[str, int]],
    analysis_dir: Path,
) -> str:
    takeaways = build_key_takeaways(hotspots, directions, clusters)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hot_img = "hot_topics.png"
    cluster_img = "topic_cluster_sizes.png"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CVPR 2026 Research Directions Report</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: #fffaf3;
      --ink: #182126;
      --muted: #55636d;
      --line: #d8cec0;
      --accent: #0e6a73;
      --accent-2: #d77a2d;
      --accent-3: #824c71;
      --shadow: 0 18px 50px rgba(33, 28, 19, 0.08);
      --radius: 22px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(215,122,45,0.16), transparent 26%),
        radial-gradient(circle at 85% 10%, rgba(14,106,115,0.16), transparent 24%),
        linear-gradient(180deg, #f7f3ed 0%, #f1ece3 100%);
      line-height: 1.65;
    }}
    .page {{
      max-width: 1260px;
      margin: 0 auto;
      padding: 40px 24px 72px;
    }}
    .hero {{
      padding: 34px;
      border: 1px solid rgba(24,33,38,0.08);
      background: linear-gradient(135deg, rgba(255,250,243,0.96), rgba(251,244,231,0.92));
      border-radius: 28px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      width: 320px;
      height: 320px;
      border-radius: 999px;
      background: rgba(14,106,115,0.08);
      right: -120px;
      top: -120px;
    }}
    .eyebrow {{
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }}
    h1, h2, h3, h4 {{ margin: 0; line-height: 1.15; }}
    h1 {{
      margin-top: 10px;
      font-size: clamp(2rem, 4vw, 3.8rem);
      max-width: 780px;
    }}
    .hero p {{
      max-width: 760px;
      font-size: 1.05rem;
      color: var(--muted);
      margin: 16px 0 0;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 28px;
    }}
    .stat {{
      padding: 18px 18px 16px;
      border-radius: 18px;
      background: rgba(255,255,255,0.64);
      border: 1px solid rgba(24,33,38,0.08);
      backdrop-filter: blur(6px);
    }}
    .stat span {{
      display: block;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .stat strong {{
      display: block;
      margin-top: 8px;
      font-size: 1.9rem;
    }}
    .section {{
      margin-top: 28px;
      padding: 26px;
      background: var(--panel);
      border: 1px solid rgba(24,33,38,0.08);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .section h2 {{
      font-size: 1.65rem;
      margin-bottom: 8px;
    }}
    .section-intro {{
      color: var(--muted);
      max-width: 900px;
      margin-bottom: 22px;
    }}
    .takeaways {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .takeaway {{
      padding: 18px;
      background: linear-gradient(180deg, rgba(14,106,115,0.06), rgba(255,255,255,0.9));
      border-radius: 18px;
      border: 1px solid rgba(14,106,115,0.12);
    }}
    .hot-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 14px;
    }}
    .mini-card, .direction-card {{
      border-radius: 18px;
      padding: 16px;
      border: 1px solid rgba(24,33,38,0.08);
      background: #fff;
    }}
    .hotspot {{
      background: linear-gradient(180deg, rgba(130,76,113,0.08), rgba(255,255,255,1));
    }}
    .mini-rank {{
      color: var(--accent-3);
      font-weight: 800;
      font-size: 0.88rem;
      margin-bottom: 8px;
    }}
    .mini-card h3 {{
      font-size: 1.03rem;
      min-height: 50px;
    }}
    .mini-card p {{
      font-size: 0.88rem;
      color: var(--muted);
      margin: 10px 0 0;
    }}
    .meta-line {{
      font-size: 0.9rem;
      color: var(--muted);
      margin-top: 4px;
    }}
    .viz-grid {{
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 18px;
      align-items: start;
    }}
    .viz-card {{
      padding: 18px;
      border-radius: 18px;
      background: #fff;
      border: 1px solid rgba(24,33,38,0.08);
    }}
    .viz-card img {{
      width: 100%;
      border-radius: 14px;
      border: 1px solid rgba(24,33,38,0.08);
      margin-top: 12px;
    }}
    .direction-list {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .direction-head {{
      display: flex;
      gap: 12px;
      justify-content: space-between;
      align-items: start;
    }}
    .direction-head h3 {{
      font-size: 1.16rem;
      margin-top: 4px;
    }}
    .score-box {{
      min-width: 96px;
      text-align: right;
      color: var(--muted);
    }}
    .score-box strong {{
      display: block;
      font-size: 1.5rem;
      color: var(--accent);
      margin-top: 4px;
    }}
    .scorebar {{
      position: relative;
      height: 14px;
      background: rgba(14,106,115,0.08);
      border-radius: 999px;
      overflow: hidden;
      margin: 16px 0 14px;
    }}
    .scorefill {{
      display: block;
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
    }}
    .scoretext {{
      position: absolute;
      top: -30px;
      right: 0;
      font-size: 0.9rem;
      color: var(--muted);
    }}
    .chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(24,33,38,0.06);
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .reason {{
      margin: 0 0 10px;
    }}
    details {{
      margin-top: 10px;
      border-top: 1px dashed var(--line);
      padding-top: 10px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 600;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.94rem;
    }}
    th, td {{
      padding: 12px 10px;
      border-bottom: 1px solid rgba(24,33,38,0.08);
      vertical-align: top;
      text-align: left;
    }}
    th {{
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .institutions {{
      columns: 2;
      column-gap: 26px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .institutions li {{
      break-inside: avoid;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 0;
      border-bottom: 1px dashed rgba(24,33,38,0.12);
    }}
    .howto {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }}
    .howto article {{
      padding: 18px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(215,122,45,0.08), rgba(255,255,255,1));
      border: 1px solid rgba(215,122,45,0.14);
    }}
    footer {{
      margin-top: 22px;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    @media (max-width: 1080px) {{
      .hot-grid, .direction-list, .takeaways, .howto, .stats, .viz-grid {{
        grid-template-columns: 1fr 1fr;
      }}
    }}
    @media (max-width: 760px) {{
      .page {{ padding: 20px 14px 44px; }}
      .stats, .takeaways, .hot-grid, .viz-grid, .direction-list, .howto {{
        grid-template-columns: 1fr;
      }}
      .institutions {{ columns: 1; }}
      .hero {{ padding: 24px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">CVPR 2026 Analysis Dashboard</div>
      <h1>热点、潜力方向与研究语境，一页读懂</h1>
      <p>这份 HTML 报告把我们从 CVPR 2026 accepted papers 中抽出的热点短语、主题簇和潜在研究方向整理成更容易阅读的视觉面板。它适合用来快速扫领域、找选题切口、以及判断某个方向究竟是“已经很热”还是“值得切入”。</p>
      <div class="stats">
        <div class="stat"><span>Total Papers</span><strong>{stats.get('paper_count', 0)}</strong></div>
        <div class="stat"><span>Oral Papers</span><strong>{stats.get('oral_count', 0)}</strong></div>
        <div class="stat"><span>Poster Papers</span><strong>{stats.get('poster_count', 0)}</strong></div>
        <div class="stat"><span>Unique Institutions</span><strong>{stats.get('unique_institutions', 0)}</strong></div>
      </div>
    </section>

    <section class="section">
      <h2>先看结论</h2>
      <p class="section-intro">如果你没有时间把整份报告看完，先看下面这些高层判断。它们不是单纯复述统计，而是在帮你把数字翻译成研究直觉。</p>
      <div class="takeaways">
        {''.join(f'<div class="takeaway">{escape(item)}</div>' for item in takeaways)}
      </div>
    </section>

    <section class="section">
      <h2>热点短语</h2>
      <p class="section-intro">这里更像“今年大家都在写什么”的语言层地图。它适合用来发现主流方向，但不直接等于值得做的题目。</p>
      <div class="hot-grid">
        {hotspot_cards(hotspots)}
      </div>
    </section>

    <section class="section">
      <h2>整体可视化</h2>
      <p class="section-intro">左图展示热点短语的论文数量，右图展示主要主题簇的规模分布。它可以帮助你快速区分“点状热点”和“面状生态”。</p>
      <div class="viz-grid">
        <article class="viz-card">
          <h3>Hot Topics</h3>
          <img src="{hot_img}" alt="Hot topics chart">
        </article>
        <article class="viz-card">
          <h3>Cluster Sizes</h3>
          <img src="{cluster_img}" alt="Topic cluster sizes chart">
        </article>
      </div>
    </section>

    <section class="section">
      <h2>潜在研究方向</h2>
      <p class="section-intro">这里的 frontier score 不是“绝对真理”，而是一个用于筛选的启发式分数。它更偏向寻找那些同时具备研究价值、问题张力和社区关注度的方向。</p>
      <div class="direction-list">
        {direction_cards(directions)}
      </div>
    </section>

    <section class="section">
      <h2>主要主题簇</h2>
      <p class="section-intro">主题簇更适合帮助你理解“研究语境”。如果你准备做某一条线，这里能告诉你它通常与哪些方法词、应用词、问题词一起出现。</p>
      <table>
        <thead>
          <tr>
            <th>Cluster</th>
            <th>Topic Label</th>
            <th>Papers</th>
            <th>Oral Ratio</th>
            <th>Institutions</th>
            <th>Top Terms</th>
          </tr>
        </thead>
        <tbody>
          {cluster_table(clusters)}
        </tbody>
      </table>
    </section>

    <section class="section">
      <h2>高频机构</h2>
      <p class="section-intro">这个榜单不代表“质量排名”，但能帮助你判断某些热点方向背后是少数团队推动，还是已经形成了广泛竞争格局。</p>
      <ul class="institutions">
        {institution_list(institutions)}
      </ul>
    </section>

    <section class="section">
      <h2>怎么用这份报告选题</h2>
      <div class="howto">
        <article>
          <h3>1. 先看热点</h3>
          <p>如果某个短语热度很高，但 oral 占比一般，通常说明这个方向“人很多，但问题未必已经打透”。这时更适合找细分切口，而不是直接跟主流做同类题。</p>
        </article>
        <article>
          <h3>2. 再看潜力方向</h3>
          <p>优先关注 frontier score 靠前、同时代表论文风格又不完全一致的方向。这往往说明问题是真实存在的，但社区还没有完全收敛到固定解法。</p>
        </article>
        <article>
          <h3>3. 最后看主题簇</h3>
          <p>如果你打算切入一个方向，主题簇里的 top terms 和代表论文可以帮你快速建立“该读什么、该避开什么、该往哪里延伸”的阅读路径。</p>
        </article>
      </div>
    </section>

    <footer>
      Generated at {escape(generated)}. Source files are in {escape(str(analysis_dir))}.
    </footer>
  </main>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    analysis_dir = args.analysis_dir
    stats = read_json(analysis_dir / "stats.json")
    hotspots = read_csv_rows(analysis_dir / "hot_topics.csv")
    directions = read_csv_rows(analysis_dir / "promising_directions.csv")
    clusters = read_csv_rows(analysis_dir / "topic_clusters.csv")
    institutions = top_institutions(args.source_csv, limit=15)

    html_doc = build_html(
        stats=stats,
        hotspots=hotspots,
        directions=directions,
        clusters=clusters,
        institutions=institutions,
        analysis_dir=analysis_dir,
    )
    args.output.write_text(html_doc, encoding="utf-8")
    print(f"HTML report written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
