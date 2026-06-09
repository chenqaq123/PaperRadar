# Paper Radar

Paper Radar 是一个 local-first 论文雷达 Web App。它把你的 Zotero 研究兴趣和 AI/CS 顶会中稿论文库做匹配，帮你更快找到值得读、值得加入 Zotero、值得参考图表表达的论文。

第一版重点是本地自用和隐私安全：

- 后端：FastAPI + SQLite
- 前端：React + Vite + TypeScript
- Zotero：支持本机 Zotero 只读导入、Better BibTeX/BibTeX 导入、本机 Zotero Connector 写入目录
- 匹配：本地 embedding + BM25 + tag/profile overlap + feedback
- 图表灵感墙：按会议/年份/类型/方向抽取 PDF 中的 figures/tables，默认不落盘缓存
- LLM：默认不使用，不需要云端 API

## 截图里的核心流程

1. 导入或刷新 Zotero 兴趣库。
2. 导入会议论文 CSV，或用脚本抓取支持的会议论文。
3. 在推荐列表里按研究方向、会议、年份筛选论文。
4. 勾选论文后导出 CSV，或写入 Zotero 指定目录。
5. 在图表灵感墙里按目标会议批量浏览 figure/table 灵感。

## 环境要求

- macOS / Linux / Windows WSL
- Python 3.10+
- Node.js 18+
- Zotero 桌面端可选，但推荐安装

## 快速启动

```bash
git clone https://github.com/chenqaq123/PaperRadar.git
cd PaperRadar
chmod +x scripts/start_paper_radar.sh
./scripts/start_paper_radar.sh
```

启动后打开：

```text
http://127.0.0.1:5173
```

后端默认地址：

```text
http://127.0.0.1:8000
```

脚本会自动创建 `.venv`、安装后端依赖，并在需要时安装前端依赖。

## 本地 embedding 设置

默认配置在 `.env.example`：

```bash
PAPER_RADAR_DB=data/paper_radar.sqlite
PAPER_RADAR_EMBED_BACKEND=auto
PAPER_RADAR_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
VITE_API_BASE=http://127.0.0.1:8000
```

如果你暂时不想下载 sentence-transformers 模型，可以用 fallback 向量器先跑通：

```bash
export PAPER_RADAR_EMBED_BACKEND=fallback
./scripts/start_paper_radar.sh
```

切回真实 embedding 后，在「设置」里点击「重建 embeddings」，再重新排序。

## Docker 启动

```bash
docker compose up --build
```

Docker 默认使用 fallback 本地向量器，避免首次启动下载模型。数据会保存在本地 `data/` 目录。

## 使用方法

### 1. 导入 Zotero

推荐方式：

1. 打开 Zotero 桌面端。
2. 在 Paper Radar 的「数据与导入」里点击查找/导入本机 Zotero。
3. Paper Radar 会只读读取 Zotero 数据，不直接修改 `zotero.sqlite`。

备选方式：

1. 在 Zotero 安装 Better BibTeX。
2. 导出你关心的 collection/tag 为 BibTeX。
3. 在 Paper Radar 上传 `.bib` 文件。

当前版本主要使用 title、abstract、keywords、tags，不索引 PDF 全文和 Zotero notes。

### 2. 导入会议论文

方式 A：上传 CSV

1. 在「数据与导入」里选择 accepted papers CSV。
2. 填写会议名和年份，例如 `cvpr` / `2026`。
3. 点击导入。

方式 B：用脚本抓取并导入

```bash
source .venv/bin/activate
python scripts/fetch_import_rank_conferences.py --conference cvpr --years 2026 --skip-ranking
```

抓取、导入并排序：

```bash
source .venv/bin/activate
python scripts/fetch_import_rank_conferences.py --conference cvpr --years 2026 --limit-per-profile 100
```

支持的会议配置在 `conference_registry.py`，当前包括：

- `cvpr`
- `iccv`
- `wacv`
- `eccv`
- `icml`
- `iclr`
- `neurips`
- `acl`
- `emnlp`

### 3. 推荐列表

推荐列表支持：

- 按会议/年份筛选
- 按 Zotero 自动生成的研究方向筛选
- 输入临时兴趣描述即时匹配
- 相关/不相关/想读/已读/隐藏反馈
- 勾选论文导出 CSV
- 勾选论文写入 Zotero 指定目录

排序公式 v1：

```text
0.55 * embedding_similarity
0.25 * BM25_similarity
0.10 * tag/profile_overlap
0.10 * feedback_adjustment
```

### 4. 写入 Zotero 目录

Paper Radar 可以通过本机 Zotero Connector 把选中的论文添加到 Zotero 已存在目录：

1. 打开 Zotero。
2. 打开 Zotero 设置，允许本机其他应用与 Zotero 通讯。
3. 在 Paper Radar 的「设置」或添加弹窗里读取 Zotero 目录。
4. 选择目标目录并添加。

写入时不直接修改本机 `zotero.sqlite`。如果论文没有 PDF 链接，后端会尝试通过 arXiv 匹配 PDF。

### 5. 图表灵感墙

「图表」页面用于快速找画图和做表灵感：

- 会议和年份是第一层筛选。
- Oral/Poster/Highlight 等类型是会议内筛选。
- 研究方向或临时描述是第二层召回/排序。
- 每批可抽取 4/8/12/16 篇论文。
- 左侧显示当前批次论文列表，右侧显示抽取出的 figures/tables。

图表抽取默认不落盘缓存。图片只存在于当前请求响应和浏览器页面状态里，刷新或关闭页面后释放。

## 隐私与数据

以下内容不会提交到 GitHub：

- `data/paper_radar.sqlite`
- `data/figure_cache/`
- `.env`
- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `output/`
- Python/pytest/TypeScript 构建缓存

开源仓库只保存代码、配置示例和少量可公开示例文件。不要把个人 Zotero 数据库、API key、私有论文列表提交到仓库。

## 开发命令

后端：

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端构建检查：

```bash
cd frontend
npm run build
```

后端语法检查：

```bash
python3 -m py_compile backend/app/*.py
```

测试：

```bash
pytest backend/tests
```

## 主要目录

```text
backend/       FastAPI API、SQLite、导入、匹配、Zotero、本地图表抽取
frontend/      React + Vite 前端
scripts/       一键启动、批量抓取/导入/排序脚本
data/          本地数据库与个人数据目录，默认不提交
output/        抓取产物与分析产物，默认不提交
```

## 常见问题

### 为什么不做 GitHub Pages 纯前端版？

Paper Radar 需要本地 SQLite、本地 Zotero、PDF 下载与图表抽取、本地 embedding。GitHub Pages 只能托管静态网页，不能稳定运行这些后端能力。因此当前推荐本地 Web App 或 Docker。

### Zotero 数据会上传吗？

不会。默认所有匹配和 embedding 都在本机运行。Zotero 导入结果保存在本地 SQLite，仓库的 `.gitignore` 会排除这些个人数据。

### 图表抽取会占磁盘吗？

默认不会。当前图表灵感墙使用临时目录和内联图片响应，不保留 `jpg` 或 `figures.json`。

### 会议论文库从哪里来？

你可以上传自己的 CSV，也可以用 `scripts/fetch_import_rank_conferences.py` 从公开会议页面抓取并导入。不同会议源站结构不同，抓取失败时建议先上传整理好的 CSV。
