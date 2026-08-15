from __future__ import annotations

import argparse
import ast
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrypoint", action="append", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--extra", action="append", default=[])
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.out.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    pending = [Path(entrypoint).resolve() for entrypoint in args.entrypoint]
    visited: set[Path] = set()

    while pending:
        path = pending.pop()
        if path in visited or not path.exists() or path.suffix != ".py":
            continue
        visited.add(path)
        if path.is_relative_to(source_root):
            destination = output_root / path.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            pending.extend(_package_initializers(path, source_root))
        for module, level in _imports(path):
            resolved = _resolve_module(module, level, path, source_root)
            if resolved is not None and resolved not in visited:
                pending.append(resolved)

    for extra in args.extra:
        extra_path = source_root / extra
        if extra_path.is_dir():
            destination = output_root / extra
            shutil.copytree(extra_path, destination, dirs_exist_ok=True)
        elif extra_path.is_file():
            destination = output_root / extra
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extra_path, destination)

    _copy_package_initializers(visited, source_root, output_root)
    print(f"service_bundle_files={len(visited)}")


def _imports(path: Path) -> set[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("estateflow.") or alias.name == "estateflow":
                    modules.add((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                module = node.module or ""
                modules.add((module, node.level))
            elif node.module and (
                node.module.startswith("estateflow.") or node.module == "estateflow"
            ):
                modules.add((node.module, 0))
    return modules


def _resolve_module(module: str, level: int, current: Path, source_root: Path) -> Path | None:
    if module.startswith("estateflow"):
        parts = module.split(".")
        base = source_root.joinpath(*parts)
    else:
        base = current.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        if module:
            base = base / Path(*module.split("."))
    file_path = base.with_suffix(".py")
    if file_path.exists():
        return file_path
    init_path = base / "__init__.py"
    return init_path if init_path.exists() else None


def _copy_package_initializers(visited: set[Path], source_root: Path, output_root: Path) -> None:
    packages: set[Path] = set()
    for path in visited:
        if not path.is_relative_to(source_root):
            continue
        parent = path.parent
        while parent >= source_root:
            packages.add(parent)
            if parent == source_root:
                break
            parent = parent.parent
    for package in packages:
        initializer = package / "__init__.py"
        if initializer.exists():
            destination = output_root / initializer.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(initializer, destination)


def _package_initializers(path: Path, source_root: Path) -> list[Path]:
    initializers: list[Path] = []
    parent = path.parent
    while parent.is_relative_to(source_root):
        initializer = parent / "__init__.py"
        if initializer.exists():
            initializers.append(initializer)
        if parent == source_root:
            break
        parent = parent.parent
    return initializers


if __name__ == "__main__":
    main()
