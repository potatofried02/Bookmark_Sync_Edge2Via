# Bookmark_Sync_Edge2Via

安卓的**Edge**过于卡顿，本脚本作用是把 **Edge（Floccus）** 和 **Via 浏览器** 的书签做成双向同步：两边改完都会合并，写回各自的文件（标题以 Edge 为准），达到在手机上使用 **Via 浏览器** 也能无感同步电脑端 **Edge（Floccus）** 书签的目的。

## 需要准备

- 一台 **WebDAV 服务器**（能上传/下载文件即可，例如 Dufs）
- **Edge** 里装好 **Floccus** 插件，书签同步到 WebDAV 的 `edge.html`
- 手机/平板的 **Via**，书签同步到同一 WebDAV 下的 `Via/bookmarks.html`

## 怎么用

1. **Edge**：在 Floccus 里填好 WebDAV 地址，指向你的 `edge.html`，先同步一次，让服务器上有了这个文件。
2. **第一次跑脚本**：在书签目录删掉 `state.json`（没有就忽略），然后执行一次 `python3 sync.py`，会按 Edge 生成一份 `Via/bookmarks.html`。
3. **Via**：在 Via 里把书签同步路径设成同一个目录下的 `Via/bookmarks.html`，拉取一次。
4. **以后**：定期运行 `python3 sync.py`（或用 systemd / inotify 自动跑），脚本会合并两边的增删改并写回两个文件；Edge 和 Via 各自再按自己的间隔拉取即可。

## 运行脚本

- 手动：在项目目录执行 `python3 sync.py`
- 自动：用仓库里的 `deploy/` 下 systemd 或 inotify 示例，按你本机路径改好后启用即可。

## 配置（可选）

用环境变量改路径，不设就用默认：

- `BOOKMARK_INTEGRATED_DIR`：放 `edge.html`、`Via/bookmarks.html` 和 `state.json` 的目录
- `BOOKMARK_EDGE_PATH`：Edge 书签文件完整路径
- `BOOKMARK_VIA_PATH`：Via 书签文件完整路径
