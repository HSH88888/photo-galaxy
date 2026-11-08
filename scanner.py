"""Folder scanner: walks the photo root and stores folders/photos in SQLite.

Folders are numbered in depth-first pre-order, and photos are numbered so
that each folder's photos form one contiguous id range. The client relies
on both properties.
"""
import datetime
import os
import re
import sqlite3
import time

SCHEMA = """
DROP TABLE IF EXISTS folders;
DROP TABLE IF EXISTS photos;
DROP TABLE IF EXISTS meta;
CREATE TABLE folders (id INTEGER PRIMARY KEY, parent INTEGER, name TEXT, path TEXT);
CREATE TABLE photos  (id INTEGER PRIMARY KEY, folder INTEGER, name TEXT, size INTEGER, mtime REAL);
CREATE TABLE meta    (key TEXT PRIMARY KEY, value TEXT);
"""

_num = re.compile(r"(\d+)")


def natural_key(name):
    return [int(p) if p.isdigit() else p.lower() for p in _num.split(name)]


def scan(root, extensions, exclude_names, db_path, log=print):
    exts = {e.lower() for e in extensions}
    excl = {n.lower() for n in exclude_names}
    folders = []  # (id, parent, name, path)
    photos = []   # (id, folder, name, size, mtime)
    t0 = time.time()

    def walk(path, parent, name):
        fid = len(folders)
        folders.append((fid, parent, name, path))
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        files, dirs = [], []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in excl:
                        dirs.append(e)
                elif os.path.splitext(e.name)[1].lower() in exts:
                    st = e.stat()
                    files.append((e.name, st.st_size, st.st_mtime))
            except OSError:
                pass
        files.sort(key=lambda f: natural_key(f[0]))
        for fname, size, mtime in files:
            photos.append((len(photos), fid, fname, size, mtime))
        if len(folders) % 200 == 0:
            log(f"  ... 폴더 {len(folders)}개, 사진 {len(photos)}장")
        dirs.sort(key=lambda d: natural_key(d.name))
        for d in dirs:
            walk(d.path, fid, d.name)

    walk(root, -1, os.path.basename(os.path.normpath(root)) or root)

    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO folders VALUES (?,?,?,?)", folders)
        con.executemany("INSERT INTO photos VALUES (?,?,?,?,?)", photos)
        con.executemany("INSERT INTO meta VALUES (?,?)", [
            ("root", root), ("scanned_at", str(time.time()))])
        con.commit()
    finally:
        con.close()
    log(f"스캔 완료: 폴더 {len(folders)}개, 사진 {len(photos)}장 ({time.time() - t0:.1f}초)")


def has_data(db_path, root):
    if not os.path.exists(db_path):
        return False
    try:
        con = sqlite3.connect(db_path)
        row = con.execute("SELECT value FROM meta WHERE key='root'").fetchone()
        con.close()
        return bool(row) and row[0] == root
    except sqlite3.Error:
        return False


def build_tree(db_path, mode="folder"):
    """Return (tree_dict, photo_paths). Supports mode = 'folder' | 'month' | 'day'."""
    con = sqlite3.connect(db_path)
    folders = con.execute("SELECT id, parent, name, path FROM folders ORDER BY id").fetchall()
    photos = con.execute("SELECT id, folder, name, mtime FROM photos ORDER BY id").fetchall()
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    con.close()

    root_name = folders[0][2] if folders else ""
    version_base = meta.get("scanned_at", "0").split(".")[0]

    if mode in ("month", "day"):
        class _Node:
            __slots__ = ("name", "children", "photos", "id", "parent", "start", "count", "total")
            def __init__(self, name):
                self.name = name
                self.children = {}
                self.photos = []
                self.id = 0
                self.parent = -1
                self.start = 0
                self.count = 0
                self.total = 0

        root_node = _Node(root_name)
        for _, fo, name, mtime in photos:
            try:
                dt = datetime.datetime.fromtimestamp(mtime if mtime and mtime > 0 else 0)
            except (OSError, ValueError, OverflowError):
                dt = datetime.datetime(2000, 1, 1)
            parts = [f"{dt.year:04d}년", f"{dt.month:02d}월"]
            if mode == "day":
                parts.append(f"{dt.day:02d}일")
            cur = root_node
            for part in parts:
                nxt = cur.children.get(part)
                if nxt is None:
                    nxt = _Node(part)
                    cur.children[part] = nxt
                cur = nxt
            cur.photos.append((mtime or 0.0, name, os.path.join(folders[fo][3], name)))

        ordered_folders = []
        ordered_photos = []

        def dfs(node, parent_id):
            node.id = len(ordered_folders)
            node.parent = parent_id
            ordered_folders.append(node)
            node.photos.sort(key=lambda item: (item[0], natural_key(item[1])))
            node.start = len(ordered_photos)
            node.count = len(node.photos)
            ordered_photos.extend(node.photos)
            for k in sorted(node.children.keys(), key=natural_key):
                dfs(node.children[k], node.id)

        dfs(root_node, -1)
        for f in reversed(ordered_folders):
            f.total += f.count
            if f.parent >= 0:
                ordered_folders[f.parent].total += f.total

        out = [[f.parent, f.name, f.start, f.count, f.total] for f in ordered_folders]
        paths = [p[2] for p in ordered_photos]
        names = [p[1] for p in ordered_photos]
        tree = {
            "root": meta.get("root", ""),
            "rootName": root_name,
            "mode": mode,
            "version": f"{version_base}_{mode}",
            "folders": out,
            "photos": names,
        }
        return tree, paths

    n = len(folders)
    start = [0] * n
    count = [0] * n
    for pid, fo, _, _ in photos:
        if count[fo] == 0:
            start[fo] = pid
        count[fo] += 1
    total = count[:]
    for i in range(n - 1, 0, -1):
        total[folders[i][1]] += total[i]

    remap = {}
    out = []
    for i, (_, parent, name, _) in enumerate(folders):
        if total[i] == 0 and i != 0:
            continue
        remap[i] = len(out)
        out.append([remap[parent] if parent >= 0 else -1, name, start[i], count[i], total[i]])

    paths = [os.path.join(folders[fo][3], name) for _, fo, name, _ in photos]
    tree = {
        "root": meta.get("root", ""),
        "rootName": root_name,
        "mode": "folder",
        "version": f"{version_base}_folder",
        "folders": out,
        "photos": [name for _, _, name, _ in photos],
    }
    return tree, paths
