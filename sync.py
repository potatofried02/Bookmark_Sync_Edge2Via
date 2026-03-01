#!/usr/bin/env python3
"""
Bookmark Sync v2: 双格式（edge.html Floccus 格式 + Via bookmarks.html Netscape 格式），
各端与自己的上次快照 diff 得到增量，合并后各写各的格式，保留 edge_id/edge_tags 与 add_date/last_modified。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DATA_ROOT = Path(os.environ.get("BOOKMARK_DATA_ROOT", "/data/webdav"))
INTEGRATED_DIR = Path(os.environ.get("BOOKMARK_INTEGRATED_DIR", str(DATA_ROOT / "bookmark" / "bookmarkIntegrated")))
EDGE_PATH = Path(os.environ.get("BOOKMARK_EDGE_PATH", str(INTEGRATED_DIR / "edge.html")))
VIA_PATH = Path(os.environ.get("BOOKMARK_VIA_PATH", str(INTEGRATED_DIR / "Via" / "bookmarks.html")))
VERSIONS_DIR = INTEGRATED_DIR / "versions"
STATE_FILE = INTEGRATED_DIR / "state.json"
ROOT_FOLDER = "Bookmarks Bar"
# Via 不允许空标题，用占位符；解析到占位符时在 canonical/Edge 侧视为空
VIA_EMPTY_TITLE_PLACEHOLDER = "这是一个Via超长占位符这是一个Via超长占位符这是一个Via超长占位符"


def canonical_entry(
    url: str,
    title: str = "",
    folder: str = "",
    edge_id: int = 0,
    edge_tags: str = "",
    add_date: int = 0,
    last_modified: int = 0,
) -> dict:
    return {
        "url": url,
        "title": title,  # 保留空标题，不 fallback 到 url（美观：用户会刻意清空标题）
        "folder": (folder or "").strip() or ROOT_FOLDER,
        "edge_id": edge_id,
        "edge_tags": edge_tags or "",
        "add_date": add_date,
        "last_modified": last_modified or add_date,
    }


def _title_if_not_url(title: str | None, url: str) -> str:
    """若 title 为空或与 url 相同（常见“无标题”导出），视为空标题，否则返回 strip 后的 title。"""
    t = (title or "").strip()
    u = (url or "").strip()
    if not t or t == u:
        return ""
    try:
        if html.unescape(t) == html.unescape(u):
            return ""
    except Exception:
        pass
    return t


# ---------------------------------------------------------------------------
# Edge HTML (Floccus) 解析与生成
# ---------------------------------------------------------------------------
def _edge_parse_line(line: str) -> tuple[str, str, str, int, str] | None:
    """<DT><A HREF="..." TAGS="..." ID="259">title</A> -> (url, title, tags, id, 'a')"""
    m = re.match(
        r'<DT><A\s+HREF="([^"]+)"\s+TAGS="([^"]*)"\s+ID="(\d+)"\s*>([^<]*)</A>',
        line.strip(),
    )
    if m:
        return (html.unescape(m.group(1)), (m.group(4) or "").strip(), m.group(2), int(m.group(3)), "a")  # 保留空标题
    return None


def edge_html_to_canonical(path: Path) -> tuple[list[dict], set[str], int, dict[str, int], dict[str, list[str]], dict[str, list[str]]]:
    """解析 Edge/Floccus HTML，返回 (条目列表, URL集合, highestId, folder_path->id, bookmark_order, subfolder_order)。"""
    if not path.exists():
        return [], set(), 0, {}, {}, {}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    out: list[dict] = []
    stack: list[str] = []
    folder_ids: dict[str, int] = {}
    max_id = 0
    # 文档顺序：每个文件夹下书签的 URL 顺序、每个父文件夹下子文件夹路径顺序
    bookmark_order: dict[str, list[str]] = {}
    subfolder_order: dict[str, list[str]] = {}

    for line in lines:
        stripped = line.strip()
        # <DT><H3 ID="1">Bookmarks Bar</H3>
        h3 = re.match(r'<DT><H3\s+ID="(\d+)"\s*>([^<]*)</H3>', stripped)
        if h3:
            fid, name = int(h3.group(1)), h3.group(2).strip()
            max_id = max(max_id, fid)
            stack.append(name)
            path_str = "/".join(stack) if stack else ROOT_FOLDER
            if path_str not in folder_ids:
                folder_ids[path_str] = fid
            # 记录子文件夹顺序（父 = path_str 的父路径）
            if "/" in path_str:
                parent = path_str.rsplit("/", 1)[0]
                if path_str not in subfolder_order.setdefault(parent, []):
                    subfolder_order[parent].append(path_str)
            continue
        if re.match(r'</DL>\s*<p>\s*$', stripped) or stripped == "</DL><p>":
            if stack:
                stack.pop()
            continue
        a = _edge_parse_line(line)
        if a:
            url, title, tags, eid, _ = a
            title = _title_if_not_url(title, url)
            max_id = max(max_id, eid)
            folder = "/".join(stack) if stack else ROOT_FOLDER
            bookmark_order.setdefault(folder, []).append(url)
            out.append(canonical_entry(url=url, title=title, folder=folder, edge_id=eid, edge_tags=tags, add_date=0, last_modified=0))
            continue

    # highestId 注释
    for line in lines:
        m = re.search(r"highestId\s*:\s*(\d+)", line)
        if m:
            max_id = max(max_id, int(m.group(1)))
            break

    return out, {e["url"] for e in out}, max_id, folder_ids, bookmark_order, subfolder_order


def canonical_to_edge_html(
    entries: list[dict],
    next_id: int,
    folder_ids: dict[str, int],
    edge_order: tuple[dict[str, list[str]], dict[str, list[str]]] | None = None,
) -> str:
    """按 Floccus 格式写回 edge.html，保留/分配 edge_id，文件夹用 folder_ids 或分配新 id。
    edge_order = (bookmark_order, subfolder_order) 时按 Edge 源文件顺序输出，否则按 entries 顺序且子文件夹按字母序。"""
    by_folder: dict[str, list[dict]] = {}
    by_url: dict[str, dict] = {e["url"]: e for e in entries}
    for e in entries:
        folder = e.get("folder") or ROOT_FOLDER
        if folder.startswith("/"):
            folder = folder.lstrip("/")
        if folder not in by_folder:
            by_folder[folder] = []
        by_folder[folder].append(e)
    all_folders = set(by_folder.keys()) | {ROOT_FOLDER}
    id_alloc = [max(next_id, 1)]
    bookmark_order, subfolder_order = edge_order or ({}, {})

    def alloc_id() -> int:
        id_alloc[0] += 1
        return id_alloc[0]

    def get_folder_id(path: str) -> int:
        if path in folder_ids:
            return folder_ids[path]
        fid = alloc_id() if path != ROOT_FOLDER else 1
        if path == ROOT_FOLDER:
            id_alloc[0] = max(id_alloc[0], 1)
        folder_ids[path] = fid
        return fid

    def esc(s: str) -> str:
        return html.escape(s, quote=True).replace('"', "&quot;")

    def direct_children(parent: str) -> list[str]:
        prefix = (parent + "/") if parent != ROOT_FOLDER else ROOT_FOLDER + "/"
        candidates = [
            f for f in all_folders
            if f and f != parent and f.startswith(prefix)
            and f.count("/") == parent.count("/") + 1
        ]
        if parent in subfolder_order:
            # 按 Edge 源文件中的子文件夹顺序，仅保留当前存在的
            order_set = set(subfolder_order[parent])
            ordered = [p for p in subfolder_order[parent] if p in candidates]
            rest = [f for f in candidates if f not in order_set]
            return ordered + sorted(rest)
        return sorted(candidates)

    def bookmarks_in_order(parent_path: str) -> list[dict]:
        folder_entries = by_folder.get(parent_path, [])
        if parent_path in bookmark_order:
            # 按 Edge 源文件中的书签顺序
            seen = set()
            result = []
            for url in bookmark_order[parent_path]:
                if url in by_url:
                    result.append(by_url[url])
                    seen.add(url)
            for e in folder_entries:
                if e["url"] not in seen:
                    result.append(e)
            return result
        return folder_entries

    def emit_folder(parent_path: str, indent: str) -> list[str]:
        buf = []
        for child_path in direct_children(parent_path):
            name = child_path.split("/")[-1]
            fid = get_folder_id(child_path)
            buf.append(f'{indent}<DT><H3 ID="{fid}">{esc(name)}</H3>')
            buf.append(f"{indent}<DL><p>")
            buf.extend(emit_folder(child_path, indent + "  "))
            buf.append(f'{indent}</DL><p>')
        for e in bookmarks_in_order(parent_path):
            eid = e.get("edge_id") or alloc_id()
            tags = e.get("edge_tags") or ""
            display_title = _title_if_not_url(e.get("title"), e.get("url", ""))
            if display_title == VIA_EMPTY_TITLE_PLACEHOLDER:
                display_title = ""
            buf.append(f'{indent}<DT><A HREF="{esc(e["url"])}" TAGS="{esc(tags)}" ID="{eid}">{esc(display_title)}</A>')
        return buf

    root_id = get_folder_id(ROOT_FOLDER)
    body = ["  <DT><H3 ID=\"{}\">{}</H3>".format(root_id, esc(ROOT_FOLDER)), "  <DL><p>"]
    body.extend(emit_folder(ROOT_FOLDER, "    "))
    body.append("  </DL><p>")
    highest = id_alloc[0]
    header = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        f"<!--- highestId :{highest}: for Floccus bookmark sync browser extension -->",
        "<DL><p>",
    ]
    return "\n".join(header + body + ["</DL><p>", ""])


# ---------------------------------------------------------------------------
# Via Netscape HTML 解析与生成
# ---------------------------------------------------------------------------
def via_html_to_canonical(path: Path) -> tuple[list[dict], set[str], dict[str, list[str]], dict[str, list[str]]]:
    """解析 Via bookmarks.html（Netscape + ADD_DATE），返回 (条目列表, URL集合, bookmark_order, subfolder_order)。"""
    if not path.exists():
        return [], set(), {}, {}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    out: list[dict] = []
    stack: list[str] = []
    bookmark_order: dict[str, list[str]] = {}
    subfolder_order: dict[str, list[str]] = {}

    for line in lines:
        stripped = line.strip()
        h3 = re.match(r'<DT><H3\s+ADD_DATE="(\d+)"\s*>([^<]*)</H3>', stripped)
        if h3:
            stack.append(h3.group(2).strip())
            path_str = ROOT_FOLDER + "/" + "/".join(stack) if stack else ROOT_FOLDER
            if "/" in path_str:
                parent = path_str.rsplit("/", 1)[0]
                if path_str not in subfolder_order.setdefault(parent, []):
                    subfolder_order[parent].append(path_str)
            continue
        if re.match(r'</DL>\s*<p>\s*$', stripped) or stripped == "</DL><p>":
            if stack:
                stack.pop()
            continue
        m = re.match(
            r'<DT><A\s+HREF="([^"]+)"\s+ADD_DATE="(\d+)"(?:\s+LAST_MODIFIED="(\d+)")?\s*>([^<]*)</A>',
            stripped,
        )
        if m:
            url = html.unescape(m.group(1))
            add_date = int(m.group(2))
            last_mod = int(m.group(3)) if m.group(3) else add_date
            raw_title = (m.group(4) or "").strip()
            if raw_title == VIA_EMPTY_TITLE_PLACEHOLDER:
                raw_title = ""
            title = _title_if_not_url(raw_title, url)
            folder = ROOT_FOLDER + "/" + "/".join(stack) if stack else ROOT_FOLDER
            bookmark_order.setdefault(folder, []).append(url)
            out.append(canonical_entry(url=url, title=title, folder=folder, edge_id=0, edge_tags="", add_date=add_date, last_modified=last_mod))
    return out, {e["url"] for e in out}, bookmark_order, subfolder_order


def canonical_to_via_html(
    entries: list[dict],
    via_order: tuple[dict[str, list[str]], dict[str, list[str]]] | None = None,
) -> str:
    """按 Netscape 格式写回 bookmarks.html，使用 add_date、last_modified。
    via_order = (bookmark_order, subfolder_order) 时按 Via 源文件顺序输出。"""
    by_folder: dict[str, list[dict]] = {}
    by_url: dict[str, dict] = {e["url"]: e for e in entries}
    for e in entries:
        folder = e.get("folder") or ROOT_FOLDER
        if folder.startswith("/"):
            folder = folder.lstrip("/")
        if folder not in by_folder:
            by_folder[folder] = []
        by_folder[folder].append(e)
    all_folders = set(by_folder.keys()) | {ROOT_FOLDER}
    bookmark_order, subfolder_order = via_order or ({}, {})

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def direct_children(parent: str) -> list[str]:
        candidates = [
            f for f in all_folders
            if f and f != parent and f.startswith(parent + "/")
            and f.count("/") == parent.count("/") + 1
        ]
        if parent in subfolder_order:
            order_set = set(subfolder_order[parent])
            ordered = [p for p in subfolder_order[parent] if p in candidates]
            rest = [f for f in candidates if f not in order_set]
            return ordered + sorted(rest)
        return sorted(candidates)

    def bookmarks_in_order(parent_path: str) -> list[dict]:
        folder_entries = by_folder.get(parent_path, [])
        if parent_path in bookmark_order:
            seen = set()
            result = []
            for url in bookmark_order[parent_path]:
                if url in by_url:
                    result.append(by_url[url])
                    seen.add(url)
            for e in folder_entries:
                if e["url"] not in seen:
                    result.append(e)
            return result
        return folder_entries

    def emit_folder(parent_path: str, indent: str) -> list[str]:
        buf = []
        for child_path in direct_children(parent_path):
            name = child_path.split("/")[-1]
            buf.append(f'{indent}<DT><H3 ADD_DATE="0">{esc(name)}</H3>')
            buf.append(f"{indent}<DL><p>")
            buf.extend(emit_folder(child_path, indent + "  "))
            buf.append(f'{indent}</DL><p>')
        for e in bookmarks_in_order(parent_path):
            add = e.get("add_date") or 0
            last = e.get("last_modified") or add
            display_title = _title_if_not_url(e.get("title"), e.get("url", ""))
            if not display_title:
                display_title = VIA_EMPTY_TITLE_PLACEHOLDER
            buf.append(f'{indent}<DT><A HREF="{esc(e["url"])}" ADD_DATE="{add}" LAST_MODIFIED="{last}">{esc(display_title)}</A>')
        return buf

    header = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        "<!-- This is an automatically generated file.",
        "     It will be read and overwritten.",
        "     DO NOT EDIT! -->",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]
    body = emit_folder(ROOT_FOLDER, "  ")
    return "\n".join(header + body + ["</DL><p>", ""])


# ---------------------------------------------------------------------------
# 合并（各端与上次快照 diff，双端元数据保留）
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"canonical": [], "edge_urls": set(), "via_urls": set(), "next_id": 2000, "edge_folder_ids": {}}
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    data["edge_urls"] = set(data.get("edge_urls", []))
    data["via_urls"] = set(data.get("via_urls", []))
    data["edge_folder_ids"] = data.get("edge_folder_ids") or {}
    return data


def save_state(
    canonical: list[dict],
    edge_urls: set[str],
    via_urls: set[str],
    next_id: int,
    edge_folder_ids: dict[str, int],
) -> None:
    INTEGRATED_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "canonical": canonical,
                "edge_urls": list(edge_urls),
                "via_urls": list(via_urls),
                "next_id": next_id,
                "edge_folder_ids": edge_folder_ids,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def merge(
    prev: dict,
    edge_entries: list[dict],
    edge_urls: set[str],
    via_entries: list[dict],
    via_urls: set[str],
    next_id: int,
    edge_folder_ids: dict[str, int],
) -> tuple[list[dict], int, dict[str, int]]:
    """各端与上次快照 diff：删除 = prev - curr；合并两源并保留双端元数据。返回 (canonical, next_id, edge_folder_ids)。"""
    prev_canonical = {e["url"]: dict(e) for e in prev.get("canonical", [])}
    prev_edge = prev.get("edge_urls") or set()
    prev_via = prev.get("via_urls") or set()
    prev_folder_ids = prev.get("edge_folder_ids") or {}
    # 合并本次解析到的 folder_ids（写回时优先用已存在的）
    for k, v in edge_folder_ids.items():
        prev_folder_ids[k] = v

    deleted = (prev_edge - edge_urls) | (prev_via - via_urls)
    for url in deleted:
        prev_canonical.pop(url, None)

    # 尊重「本端已删」：被任一端删除的 URL 不再用另一端的条目加回（避免 Via 旧文件把 Edge 已删项又加回）
    def apply_edge(entries: list[dict]) -> None:
        for e in entries:
            url = e["url"]
            if url in deleted:
                continue
            entry = prev_canonical.get(url)
            if not entry:
                prev_canonical[url] = dict(e)
                prev_canonical[url]["title"] = _title_if_not_url(e.get("title"), url)
                continue
            new_title = e.get("title") if e.get("title") is not None else entry.get("title")
            entry["title"] = _title_if_not_url(new_title, url)
            entry["folder"] = e.get("folder") or entry.get("folder")
            entry["edge_id"] = e.get("edge_id") or entry.get("edge_id")
            entry["edge_tags"] = e.get("edge_tags") if e.get("edge_tags") is not None else entry.get("edge_tags", "")

    def apply_via(entries: list[dict]) -> None:
        for e in entries:
            url = e["url"]
            if url in deleted:
                continue
            entry = prev_canonical.get(url)
            if not entry:
                prev_canonical[url] = dict(e)
                prev_canonical[url]["title"] = _title_if_not_url(e.get("title"), url)
                continue
            # 已存在的条目以 Edge 为准，不覆盖 title（避免 Via 把 Edge 清空的标题又写回）
            entry["folder"] = e.get("folder") or entry.get("folder")
            entry["add_date"] = e.get("add_date") or entry.get("add_date", 0)
            entry["last_modified"] = e.get("last_modified") or entry.get("last_modified", 0)

    apply_edge(edge_entries)
    apply_via(via_entries)

    canonical_list = list(prev_canonical.values())
    return canonical_list, max(next_id, 2000), prev_folder_ids


# ---------------------------------------------------------------------------
# 版本与主流程
# ---------------------------------------------------------------------------
def save_version(canonical: list[dict]) -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    VERSIONS_DIR.joinpath(f"{ts}.json").write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")


def run_once() -> None:
    INTEGRATED_DIR.mkdir(parents=True, exist_ok=True)
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    edge_entries, edge_urls, edge_max_id, edge_folder_ids, edge_bookmark_order, edge_subfolder_order = edge_html_to_canonical(EDGE_PATH)
    via_entries, via_urls, via_bookmark_order, via_subfolder_order = via_html_to_canonical(VIA_PATH)

    prev = load_state()
    next_id = max(prev.get("next_id", 2000), edge_max_id)
    folder_ids = prev.get("edge_folder_ids") or {}
    for k, v in edge_folder_ids.items():
        folder_ids[k] = v

    # 无历史 canonical 时视为「以 Edge 为唯一来源」的初始同步：仅用 Edge 生成 canonical，重写 Via
    if not prev.get("canonical"):
        canonical = []
        for e in edge_entries:
            entry = dict(e)
            entry["title"] = _title_if_not_url(entry.get("title"), entry["url"])
            entry.setdefault("add_date", 0)
            entry.setdefault("last_modified", 0)
            canonical.append(entry)
        save_state(canonical, edge_urls, edge_urls, next_id, folder_ids)
        save_version(canonical)
        EDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EDGE_PATH.write_text(
            canonical_to_edge_html(canonical, next_id, folder_ids, (edge_bookmark_order, edge_subfolder_order)),
            encoding="utf-8",
        )
        VIA_PATH.parent.mkdir(parents=True, exist_ok=True)
        VIA_PATH.write_text(
            canonical_to_via_html(canonical, (edge_bookmark_order, edge_subfolder_order)),
            encoding="utf-8",
        )
        print(f"ok (bootstrap from Edge) entries={len(canonical)}")
        return

    canonical, next_id, folder_ids = merge(
        prev, edge_entries, edge_urls, via_entries, via_urls, next_id, edge_folder_ids
    )

    # 写回前再次读取 Via：若文件在本次运行期间被更新（如客户端上传了删除），采纳滞后删除，避免覆盖用户操作
    via_entries2, via_urls2, via_bookmark_order2, via_subfolder_order2 = via_html_to_canonical(VIA_PATH)
    deleted_late = via_urls - via_urls2
    via_bookmark_order_out = via_bookmark_order
    via_subfolder_order_out = via_subfolder_order
    if deleted_late:
        canonical = [e for e in canonical if e["url"] not in deleted_late]
        via_urls = via_urls2
        via_bookmark_order_out = via_bookmark_order2
        via_subfolder_order_out = via_subfolder_order2

    save_state(canonical, edge_urls, via_urls, next_id, folder_ids)
    save_version(canonical)

    EDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EDGE_PATH.write_text(
        canonical_to_edge_html(canonical, next_id, folder_ids, (edge_bookmark_order, edge_subfolder_order)),
        encoding="utf-8",
    )

    VIA_PATH.parent.mkdir(parents=True, exist_ok=True)
    VIA_PATH.write_text(
        canonical_to_via_html(canonical, (via_bookmark_order_out, via_subfolder_order_out)),
        encoding="utf-8",
    )

    print(f"ok entries={len(canonical)} edge_urls={len(edge_urls)} via_urls={len(via_urls)}")


def main() -> None:
    try:
        run_once()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
