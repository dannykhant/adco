from __future__ import annotations

import os


SKIP_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__",
    ".git", ".mypy_cache", "dist", "build",
    ".tox", "env", ".env", "vendor", "target", ".next",
}
BINARY_EXTS = {".pyc", ".pyo", ".so", ".dll", ".dylib", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".eot", ".ttf", ".o", ".a"}
TEXT_EXTS = {".py", ".js", ".ts", ".java", ".go", ".rb", ".php", ".rs", ".kt", ".scala", ".swift", ".sql", ".md", ".txt", ".ini", ".cfg", ".config", ".json", ".yml", ".yaml", ".toml", ".sh", ".env", ".css", ".html", ".xml", ".yaml", ".yml"}


def build_project_tree(root: str) -> str:
    root_name = os.path.basename(root.rstrip(os.sep))
    lines = [f"{root_name}/"]

    def _walk(prefix: str, dir_path: str) -> None:
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return
        entries = [e for e in entries if not e.startswith(".") and e not in SKIP_DIRS and not any(e.endswith(ext) for ext in BINARY_EXTS)]
        dirs = [e for e in entries if os.path.isdir(os.path.join(dir_path, e))]
        files = [e for e in entries if os.path.isfile(os.path.join(dir_path, e))]
        combined = dirs + files
        for i, e in enumerate(combined):
            is_last = i == len(combined) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{e}")
            if os.path.isdir(os.path.join(dir_path, e)):
                child_prefix = prefix + ("    " if is_last else "│   ")
                _walk(child_prefix, os.path.join(dir_path, e))

    _walk("", root)
    return "\n".join(lines)


def list_source_files(root: str) -> list[str]:
    paths = []
    if os.path.isfile(root):
        ext = os.path.splitext(root)[1]
        if ext in TEXT_EXTS:
            return [os.path.abspath(root)]
        return []

    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for fname in sorted(files):
            fpath = os.path.join(r, fname)
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1]
            if ext in BINARY_EXTS or ext not in TEXT_EXTS:
                continue
            paths.append(os.path.abspath(fpath))
    return paths


def scan_project(path: str) -> tuple[str, list[str]]:
    path = os.path.abspath(path)
    tree = build_project_tree(path)
    files = list_source_files(path)
    return tree, files
