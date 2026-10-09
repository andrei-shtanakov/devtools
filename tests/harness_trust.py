"""Цепочка доверия кода, который исполняют `merge-pr.sh` и `human-merge.sh`.

Инвариант (ревью #344, #531, #573): всё, что скрипты мержа исполняют или
подключают из дерева devtools, — под authority-root И харнесс-гвардом, вместе
со всей цепочкой локальных импортов, файлами, которые эти модули читают по
путям, и (для запуска через `uv`) конфигурацией зависимостей. Иначе агентский
PR ослабил бы проверку, которую потом исполняет человек.

Форма запуска распознаётся явно; любая иная форма исполнения — нарушение
(отказ проверки), а не пропуск.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = ("merge-pr.sh", "human-merge.sh")
#: Конфигурация зависимостей, по которой `uv run --frozen --exact` ставит
#: окружение проверяльщика.
UV_DEPENDENCY_FILES = ("pyproject.toml", "uv.lock")
#: Команды, которые исполняют код: в позиции команды — только в распознанной
#: форме.
_EXEC_WORDS = {"python", "python3", "uv", "sh", "bash", "source", ".", "exec", "eval"}
_SOURCE = re.compile(r'^\.\s+"\$script_dir/([^"]+)"$')
_PY_STDLIB = re.compile(r'^python3\s+-I\s+"\$script_dir/([^"]+\.py)"(\s.*)?$')
_PY_UV = re.compile(
    r"^uv run --frozen --exact --no-config --no-env-file "
    r'python -I "\$script_dir/([^"]+\.py)"(\s.*)?$'
)
_ASSIGN = re.compile(r"^([A-Z_][A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S*)\s+")


@dataclass(frozen=True)
class Launch:
    """Распознанное исполнение кода из дерева: `source` | `py-stdlib` | `py-uv`."""

    script: str
    kind: str
    path: str


def _commands(text: str) -> list[str]:
    """Команды скрипта с учётом кавычек: границы — перевод строки, `;`, `&&`,
    `||`, `|`, `(`, `)`, `{`, `}` ВНЕ кавычек; `\\`+перевод строки — пробел;
    `#` в начале слова — комментарий; `$( … )` — отдельные команды (и внутри
    двойных кавычек). Текст в кавычках остаётся в команде как есть."""
    commands: list[str] = []
    stack: list[tuple[str, str]] = []  # (буфер внешней команды, состояние)
    buf, state, i, n = "", "code", 0, len(text)

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            commands.append(" ".join(buf.split()))
        buf = ""

    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] == "\n":
            buf += " "
            i += 2
            continue
        if state == "single":
            buf += ch
            state = "code" if ch == "'" else state
        elif state == "double":
            if ch == "\\" and i + 1 < n:
                buf += text[i : i + 2]
                i += 2
                continue
            if text.startswith("$(", i):
                stack.append((buf + "$(", "double"))
                buf, state = "", "code"
                i += 2
                continue
            buf += ch
            state = "code" if ch == '"' else state
        else:  # code
            if ch == "#" and (not buf or buf[-1] in " \t"):
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if ch in "'\"":
                buf += ch
                state = "single" if ch == "'" else "double"
            elif text.startswith("$(", i):
                stack.append((buf + "$(", "code"))
                buf = ""
                i += 2
                continue
            elif ch == ")" and stack:
                flush()
                buf, state = stack.pop()
                buf += ")"
            elif text.startswith("&&", i) or text.startswith("||", i):
                flush()
                i += 2
                continue
            elif ch in "\n;|(){}":
                flush()
            else:
                buf += ch
        i += 1
    flush()
    return commands


def _strip_assignments(segment: str) -> tuple[str, dict[str, str]]:
    env: dict[str, str] = {}
    while True:
        m = _ASSIGN.match(segment)
        if not m:
            return segment, env
        env[m.group(1)] = m.group(2).strip("\"'")
        segment = segment[m.end() :]


def launches(root: Path, scripts=SCRIPTS) -> tuple[list[Launch], list[str]]:
    """Распознанные запуски кода из дерева и нарушения (нераспознанные формы)."""
    found: list[Launch] = []
    problems: list[str] = []
    for script in scripts:
        for segment in _commands((root / script).read_text(encoding="utf-8")):
            if True:
                command, env = _strip_assignments(segment)
                word = command.split(" ", 1)[0] if command else ""
                if m := _SOURCE.match(command):
                    found.append(Launch(script, "source", m.group(1)))
                elif m := _PY_STDLIB.match(command):
                    found.append(Launch(script, "py-stdlib", m.group(1)))
                elif m := _PY_UV.match(command):
                    if "UV_PROJECT_ENVIRONMENT" not in env:
                        problems.append(
                            f"{script}: uv без собственного UV_PROJECT_ENVIRONMENT: "
                            f"{segment}"
                        )
                    found.append(Launch(script, "py-uv", m.group(1)))
                elif word in _EXEC_WORDS or word.startswith('"$script_dir'):
                    problems.append(f"{script}: нераспознанный запуск: {segment}")
    return found, problems


def _module_file(root: Path, dotted: str) -> list[str]:
    """Файлы модуля `governance.x` в репо: пакетные `__init__` по пути + сам."""
    parts = dotted.split(".")
    files: list[str] = []
    for i in range(1, len(parts) + 1):
        base = "/".join(parts[:i])
        if (root / base / "__init__.py").exists():
            files.append(f"{base}/__init__.py")
        elif i == len(parts) and (root / f"{base}.py").exists():
            files.append(f"{base}.py")
    return files


def _local_imports(root: Path, rel: str) -> list[str]:
    """Локальные импорты модуля (в т.ч. ленивые внутри функций) — файлами."""
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    files: list[str] = []
    for name in names:
        if (root / name.split(".")[0]).is_dir():
            files += _module_file(root, name)
    return files


def _anchor_dir(root: Path, rel: str, base: ast.AST) -> Path | None:
    """Каталог основания `Path(__file__).resolve().parent…` или None."""
    parents = 0
    while isinstance(base, ast.Attribute) and base.attr == "parent":
        parents += 1
        base = base.value
    if isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute):
        if base.func.attr == "resolve":
            base = base.func.value
    if (
        isinstance(base, ast.Call)
        and isinstance(base.func, ast.Name)
        and base.func.id == "Path"
        and len(base.args) == 1
        and isinstance(base.args[0], ast.Name)
        and base.args[0].id == "__file__"
        and parents >= 1
    ):
        d = (root / rel).resolve()
        for _ in range(parents):
            d = d.parent
        return d
    return None


def _path_reads(root: Path, rel: str) -> tuple[list[str], list[str]]:
    """Пути в репо из цепочек `Path(__file__)…parent / "a" / "b"` (наш код,
    `governance/`) и нераспознанные цепочки с именем верхнего уровня репо.
    Вендоренный код соседа защищается каталогом целиком и тут не разбирается."""
    if not rel.startswith("governance/"):
        return [], []
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    top = {p.name for p in root.iterdir()}
    inner = {
        id(node.left)
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
    }
    resolved: list[str] = []
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        if id(node) in inner:
            continue  # не максимальная цепочка
        parts: list[ast.AST] = []
        cur: ast.AST = node
        while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            parts.insert(0, cur.right)
            cur = cur.left
        names = [
            c.value
            for c in parts
            if isinstance(c, ast.Constant) and isinstance(c.value, str)
        ]
        if not any(x.split("/")[0] in top for x in names):
            continue
        base = _anchor_dir(root, rel, cur)
        if base is None or len(names) != len(parts):
            unresolved.append("/".join(names))
            continue
        full = base.joinpath(*names)
        try:
            resolved.append(str(full.relative_to(root.resolve())))
        except ValueError:
            unresolved.append(str(full))
    return resolved, unresolved


def _process_modules(root: Path, entry: str) -> tuple[list[str], set[str]]:
    """Модули репо, загруженные при импорте `entry` под `python -I`, и
    сторонние пакеты процесса (верхние имена из site-packages)."""
    probe = (
        "import sys, importlib.util\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        f"spec = importlib.util.spec_from_file_location('_probe', {str(root / entry)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "for n, m in sorted(sys.modules.items()):\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if f: print(n, f)\n"
    )
    with tempfile.TemporaryDirectory() as pyc:
        out = subprocess.run(
            [sys.executable, "-I", "-X", f"pycache_prefix={pyc}", "-c", probe],
            cwd="/",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    rels = []
    third: set[str] = set()
    for line in out:
        name, f = line.split(" ", 1)
        if "site-packages" in f and not name.startswith("_virtualenv"):
            third.add(name.split(".")[0])
        p = Path(f).resolve()
        if (
            p.is_relative_to(root.resolve())
            and ".venv" not in p.relative_to(root.resolve()).parts
        ):
            rels.append(os.path.relpath(p, root.resolve()))
    return rels, third


def closure(root: Path, entry: str) -> tuple[set[str], list[str], set[str]]:
    """Файлы цепочки доверия Python-входа `entry`, нераспознанные обращения и
    сторонние пакеты процесса."""
    files: set[str] = {entry}
    problems: list[str] = []
    queue = [entry]
    while queue:
        rel = queue.pop()
        for dep in _local_imports(root, rel):
            if dep not in files:
                files.add(dep)
                queue.append(dep)
    loaded, third = _process_modules(root, entry)
    for rel in loaded:
        if rel.endswith(".py") and rel not in files:
            files.add(rel)
            queue.append(rel)
        elif not rel.endswith(".py"):
            problems.append(f"{entry}: в процессе не-.py модуль репо {rel}")
    while queue:  # статический обход догруженных динамически
        rel = queue.pop()
        for dep in _local_imports(root, rel):
            if dep not in files:
                files.add(dep)
                queue.append(dep)
    for rel in sorted(files):
        if not rel.endswith(".py") or not (root / rel).is_file():
            continue
        resolved, unresolved = _path_reads(root, rel)
        files |= set(resolved)
        problems += [f"{rel}: нераспознанное обращение к {u!r}" for u in unresolved]
    return files, problems, third


def _covered(path: str, prefixes) -> bool:
    return any(path.startswith(p) for p in prefixes)


def violations(
    root: Path, authority_prefixes, harness_prefixes, scripts=SCRIPTS
) -> list[str]:
    """Все нарушения инварианта; пустой список — цепочка доверия закрыта."""
    found, problems = launches(root, scripts)
    required: dict[str, str] = {}
    for launch in found:
        required[launch.path] = f"{launch.script} ({launch.kind})"
        if launch.kind == "source":
            continue
        files, extra, third = closure(root, launch.path)
        problems += extra
        if launch.kind == "py-stdlib" and third:
            problems.append(
                f"{launch.path}: python3 -I без закреплённых зависимостей, а в "
                f"процессе сторонние пакеты {sorted(third)}"
            )
        for f in files:
            required.setdefault(f, f"цепочка {launch.path}")
        if launch.kind == "py-uv":
            for f in UV_DEPENDENCY_FILES:
                required.setdefault(f, f"зависимости {launch.path}")
    for path, why in sorted(required.items()):
        if not _covered(path, authority_prefixes):
            problems.append(f"{path} ({why}) — вне authority-root")
        if not _covered(path, harness_prefixes):
            problems.append(f"{path} ({why}) — вне _HARNESS_PREFIXES")
    return problems


def required_paths(root: Path, scripts=SCRIPTS) -> set[str]:
    """Все пути, которые инвариант требует защитить (для отрицательных проверок)."""
    found, _ = launches(root, scripts)
    out: set[str] = set()
    for launch in found:
        out.add(launch.path)
        if launch.kind != "source":
            out |= closure(root, launch.path)[0]
        if launch.kind == "py-uv":
            out |= set(UV_DEPENDENCY_FILES)
    return out
