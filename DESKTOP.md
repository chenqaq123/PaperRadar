# Paper Radar 桌面版 (Electron)

Paper Radar 从 Web App 转向桌面应用后,依然是 **local-first**:整个应用打包成一个原生桌面程序,内部由 Electron 外壳 + 现有 React 前端 + FastAPI 后端(作为子进程)组成。

```
Electron 主进程 (frontend/electron/main.cjs)
├─ renderer  : 现有 React/Vite 前端 (dev 用 Vite dev server, 打包用 dist/)
├─ preload   : 注入后端地址 window.paperRadar.apiBase
└─ sidecar   : FastAPI 后端子进程
                 · dev  → 仓库 .venv 里的 python -m uvicorn
                 · 打包 → PyInstaller 生成的 paper-radar-backend 二进制
```

后端数据库与图表缓存由环境变量 `PAPER_RADAR_DB` 决定:
- 开发时指向仓库 `data/paper_radar.sqlite`(沿用现有数据)
- 打包后指向系统 userData 目录(如 macOS `~/Library/Application Support/Paper Radar/data/`),避免写入只读的应用包

## 开发运行

一条命令启动(自动建 .venv、装依赖、开 Vite + Electron):

```bash
chmod +x scripts/start_paper_radar_desktop.sh
./scripts/start_paper_radar_desktop.sh
```

或手动:

```bash
cd frontend
npm install
npm run electron:dev   # Vite dev server + Electron(Electron 自己拉起后端)
```

## 生成"双击就用"的 App(自用,推荐)

在本机构建一个 `.app`,它记住本机仓库 + `.venv` 路径,双击时自动起后端、复用现有数据,**零命令行**:

```bash
cd frontend
npm run app:build
```

产物:`frontend/release/mac-arm64/Paper Radar.app`。

- 双击即用:app 内部自动运行 `.venv/bin/python -m uvicorn ...`,打开窗口。
- 复用现有数据:后端仍读写仓库的 `data/paper_radar.sqlite`(那 7 万+ 篇论文、Zotero 库都在)。
- 退出 app 时后端自动关闭,无残留进程。
- 可把 `Paper Radar.app` 拖进"访达 → 应用程序",或加到 Dock,像原生应用一样用。

> 注意:此 `.app` 依赖**这台电脑**的仓库和 `.venv`(路径写在 `electron/local-paths.json`),不能拷给别人用。仓库或 `.venv` 移动位置后,重新跑一次 `npm run app:build` 即可。

## 做成可分发安装包(可选,进阶)

若要发给没有 Python 环境的人,需要把后端用 PyInstaller 编成独立二进制并打进安装包:

1. 编译后端(体积大,含 torch/sentence-transformers):

   ```bash
   ./backend/build_backend.sh   # 产物:backend/dist/paper-radar-backend/
   ```

2. 取消 `electron-builder.yml` 里 `extraResources` 的注释,把 `main.cjs` 的后端启动改回读取 `resources/backend/` 二进制(见 git 历史),然后:

   ```bash
   cd frontend
   npm run dist        # .dmg / .exe / .AppImage,产物在 frontend/release/
   ```

## 说明

- 前端调用后端的地址不再写死:`src/api.ts` 优先读取 preload 注入的 `window.paperRadar.apiBase`,回退到 `VITE_API_BASE`,再回退到 `http://127.0.0.1:8000`。因此同一份前端代码既能跑桌面版也能跑纯 Web 版。
- 旧的 Web 启动脚本 `scripts/start_paper_radar.sh` 仍然保留,可继续用浏览器访问。
