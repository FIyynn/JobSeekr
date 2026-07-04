from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable


REPO_IMPORT_PREFIXES = {
    "api",
    "browser",
    "config",
    "core",
    "frontend",
    "infra",
    "legacy",
    "notebooks",
    "parsers",
    "runtime",
    "scripts",
    "services",
    "shared",
    "stages",
    "storage",
    "tasks",
    "tests",
    "worker",
}


@dataclass
class Issue:
    file: str
    kind: str
    message: str
    cell: int | None = None
    line: int | None = None
    names: list[str] | None = None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _iter_code_sources(path: Path) -> list[tuple[int, str]]:
    if path.suffix.lower() == ".py":
        return [(0, _read_text(path))]
    if path.suffix.lower() != ".ipynb":
        return []
    payload = json.loads(_read_text(path))
    sources: list[tuple[int, str]] = []
    for index, cell in enumerate(payload.get("cells", [])):
        if cell.get("cell_type") == "code":
            sources.append((index, "".join(cell.get("source", []))))
    return sources


def _repo_root_from(path: Path) -> Path:
    current = path.resolve().parent if path.is_file() else path.resolve()
    while current != current.parent:
        if (current / "tasks").exists() and (current / "notebooks").exists():
            return current
        current = current.parent
    return path.resolve().parent if path.is_file() else path.resolve()


def _is_repo_import(module: str | None) -> bool:
    if not module:
        return False
    return module.split(".", 1)[0] in REPO_IMPORT_PREFIXES


def _collect_names(tree: ast.AST) -> tuple[set[str], set[str], list[tuple[int, str]]]:
    assigned: set[str] = set()
    imported: set[str] = set()
    imports: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> Any:
            for target in node.targets:
                assigned.update(_assigned_names(target))
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
            assigned.update(_assigned_names(node.target))
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> Any:
            assigned.update(_assigned_names(node.target))
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> Any:
            assigned.update(_assigned_names(node.target))
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> Any:
            assigned.update(_assigned_names(node.target))
            self.generic_visit(node)

        def visit_With(self, node: ast.With) -> Any:
            for item in node.items:
                if item.optional_vars:
                    assigned.update(_assigned_names(item.optional_vars))
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> Any:
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".", 1)[0])
                imports.append((node.lineno, alias.name))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.add(alias.asname or alias.name)
            imports.append((node.lineno, node.module or ""))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            assigned.add(node.name)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
            assigned.add(node.name)
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            assigned.add(node.name)
            self.generic_visit(node)

    Visitor().visit(tree)
    return assigned, imported, imports


def _assigned_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            names |= _assigned_names(item)
    return names


def _top_level_used_names(node: ast.AST) -> list[tuple[int, str]]:
    used: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            return None

        def visit_ListComp(self, node: ast.ListComp) -> Any:
            return None

        def visit_SetComp(self, node: ast.SetComp) -> Any:
            return None

        def visit_DictComp(self, node: ast.DictComp) -> Any:
            return None

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> Any:
            return None

        def visit_Name(self, node: ast.Name) -> Any:
            if isinstance(node.ctx, ast.Load):
                used.append((getattr(node, "lineno", 0), node.id))

    Visitor().visit(node)
    return used


def _bootstrap_lines(source: str) -> set[int]:
    lines: set[int] = set()
    for index, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if "sys.path.insert" in stripped or "sys.path.append" in stripped:
            lines.add(index)
    return lines


def _analyze_tree(
    tree: ast.AST,
    *,
    file_label: str,
    cell_index: int | None,
    source: str,
    available_names: set[str],
    seen_bootstrap: bool,
) -> tuple[list[Issue], set[str], bool]:
    issues: list[Issue] = []
    bootstrap_lines = _bootstrap_lines(source)
    local_seen_bootstrap = seen_bootstrap
    top_level_names = set(dir(__builtins__)) | {"Path", "json", "sys"}
    assigned, imported, import_nodes = _collect_names(tree)
    known_names = available_names | assigned | imported | top_level_names
    used = _top_level_used_names(tree)

    unresolved = sorted({name for _, name in used if name not in known_names and not name.startswith("_")})
    if unresolved:
        issues.append(
            Issue(
                file=file_label,
                kind="name",
                message="Names may be missing from the notebook/module context",
                cell=cell_index,
                names=unresolved,
            )
        )

    if cell_index is not None:
        for lineno, module in import_nodes:
            if not _is_repo_import(module):
                continue
            if not local_seen_bootstrap and not any(line < lineno for line in bootstrap_lines):
                issues.append(
                    Issue(
                        file=file_label,
                        kind="path",
                        message="Repo import appears before sys.path bootstrap",
                        cell=cell_index,
                        line=lineno,
                        names=[module],
                    )
                )
                break

    if bootstrap_lines:
        local_seen_bootstrap = True

    available_names |= assigned | imported

    return issues, available_names, local_seen_bootstrap


def preflight_path(path: Path) -> list[Issue]:
    issues: list[Issue] = []
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.suffix.lower() in {".py", ".ipynb"}:
                issues.extend(preflight_path(child))
        return issues

    sources = _iter_code_sources(path)
    if not sources:
        return issues

    seen_bootstrap = False
    available_names = set(dir(__builtins__)) | {"Path", "json", "sys"}
    for cell_index, source in sources:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            issues.append(
                Issue(
                    file=str(path),
                    kind="syntax",
                    message=f"{exc.msg}",
                    cell=cell_index,
                    line=exc.lineno,
                )
            )
            continue
        cell_issues, available_names, seen_bootstrap = _analyze_tree(
            tree,
            file_label=str(path),
            cell_index=cell_index if path.suffix.lower() == ".ipynb" else None,
            source=source,
            available_names=available_names,
            seen_bootstrap=seen_bootstrap,
        )
        issues.extend(cell_issues)
    return issues


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        args = ["."]

    paths = [Path(arg) for arg in args]
    issues: list[Issue] = []
    for path in paths:
        issues.extend(preflight_path(path))

    payload = {"issues": [asdict(issue) for issue in issues]}
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
