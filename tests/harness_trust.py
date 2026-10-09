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
#: Закрытый список команд, которые НЕ исполняют код из дерева (ревью #573,
#: круг 2: перечень «опасных» слов пропускал `env python3 …`, `/usr/bin/python3`,
#: `python3.12`, `$py …`). Любая иная команда — нераспознанный запуск (отказ),
#: кроме распознанных форм ниже.
SAFE_COMMANDS = frozenset(
    {
        "[", "test", "echo", "printf", "exit", "return", "shift", "set", "unset",
        "local", "export", "read", "cd", "pwd", "dirname", "basename", "sed", "tr",
        "head", "tail", "grep", "cut", "wc", "true", "false", ":", "gh", "git",
        "jq", "sleep", "case", "esac", "fi", "done", "for", "break", "continue",
        # функции самих скриптов и подключаемых ssot_env.sh/approval_branches.sh
        # (их тела проверяются тем же разбором)
        "die", "usage", "gh_a", "gh_h", "approval_globs", "ssot_key",
        "brief_check_failed",
    }
)  # fmt: skip
_KEYWORDS = {"then", "else", "do", "if", "elif", "while", "until", "!", "time"}
_SOURCE = re.compile(r'^\.\s+"\$script_dir/([^"]+)"$')
_PY_STDLIB = re.compile(r'^python3\s+-I\s+"\$script_dir/([^"]+\.py)"(\s.*)?$')
_PY_UV = re.compile(
    r"^uv run --frozen --exact --no-config --no-env-file "
    r'python -I "\$script_dir/([^"]+\.py)"(\s.*)?$'
)
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S*)(\s+|$)")


#: Сверка по сырому тексту, не зависящая от разбора (ревью #573, круг 3:
#: автомат `case` пропускал команду после блока). Слово-интерпретатор
#: (вкл. путь `/usr/bin/python3`, версию `python3.12`) и ссылка на код в
#: дереве — только в распознанных запусках.
_RAW_PYTHON = re.compile(r"(?<![\w.-])(?:/[\w./-]*/)?python[0-9.]*(?![\w.-])")
_RAW_UV = re.compile(r"(?<![\w.-])uv(?![\w.-])")
_RAW_REF = re.compile(r"\$\{?script_dir\}?/([A-Za-z0-9_./-]+)")
_CODE_SUFFIXES = (".py", ".sh")


def _raw_lines(text: str) -> str:
    """Текст без строк-комментариев (сверка по сырому тексту)."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


@dataclass(frozen=True)
class Launch:
    """Распознанное исполнение кода из дерева: `source` | `py-stdlib` | `py-uv`."""

    script: str
    kind: str
    path: str


def _commands(text: str) -> list[tuple[str, str]]:
    """Команды скрипта с учётом кавычек: границы — перевод строки, `;`, `&&`,
    `||`, `|`, `(`, `)`, `{`, `}` ВНЕ кавычек; `\\`+перевод строки — пробел;
    `#` в начале слова — комментарий; `$( … )` — отдельные команды (и внутри
    двойных кавычек). Текст в кавычках остаётся в команде как есть.
    Возвращает (команда, чем закончилась): `;;`, `)`, `(`, иное."""
    commands: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = []  # (буфер внешней команды, состояние)
    buf, state, i, n = "", "code", 0, len(text)

    def flush(term: str = "") -> None:
        nonlocal buf
        if buf.strip():
            commands.append((" ".join(buf.split()), term))
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
            elif text.startswith(";;", i):
                flush(";;")
                i += 2
                continue
            elif ch in "\n;|(){}":
                flush(ch)
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


def _strip_keywords(command: str) -> str:
    while command:
        word, _, rest = command.partition(" ")
        if word not in _KEYWORDS:
            return command
        command = rest.strip()
    return command


def _scan(root: Path, script: str, seen: set[str]) -> tuple[list[Launch], list[str]]:
    found: list[Launch] = []
    problems: list[str] = []
    text = (root / script).read_text(encoding="utf-8")
    case_depth = 0
    expect_pattern = False
    for segment, term in _commands(text):
        command, env = _strip_assignments(_strip_keywords(segment))
        command = _strip_keywords(command)
        word = command.split(" ", 1)[0] if command else ""
        if word == "case":
            case_depth += 1
            expect_pattern = True
            continue
        if word == "esac":
            case_depth = max(case_depth - 1, 0)
            expect_pattern = False
            continue
        if case_depth and expect_pattern and term in (")", "|"):
            expect_pattern = term == "|"  # шаблон ветки case (и альтернативы)
            continue
        if term == ";;":
            expect_pattern = case_depth > 0
        if term == "(" and command and " " not in command:
            continue  # определение функции `name()`; тело разбирается отдельно
        if not command:
            continue  # чистое присваивание
        if m := _SOURCE.match(command):
            path = m.group(1)
            found.append(Launch(script, "source", path))
            if path not in seen and (root / path).is_file():
                seen.add(path)
                more, extra = _scan(root, path, seen)
                found += more
                problems += extra
        elif m := _PY_STDLIB.match(command):
            found.append(Launch(script, "py-stdlib", m.group(1)))
        elif m := _PY_UV.match(command):
            if "UV_PROJECT_ENVIRONMENT" not in env:
                problems.append(
                    f"{script}: uv без собственного UV_PROJECT_ENVIRONMENT: {segment}"
                )
            found.append(Launch(script, "py-uv", m.group(1)))
        elif word not in SAFE_COMMANDS:
            problems.append(f"{script}: нераспознанный запуск: {segment}")
    problems += _raw_check(script, text, [x for x in found if x.script == script])
    return found, problems


def _raw_check(script: str, text: str, own: list[Launch]) -> list[str]:
    """Второй слой: по сырому тексту, без разбора. Число интерпретаторов и `uv`
    — ровно по распознанным запускам; ссылка на код в дереве — только запуск."""
    raw = _raw_lines(text)
    problems: list[str] = []
    py = [x for x in own if x.kind in ("py-stdlib", "py-uv")]
    if len(_RAW_PYTHON.findall(raw)) != len(py):
        problems.append(
            f"{script}: упоминаний интерпретатора {len(_RAW_PYTHON.findall(raw))}, "
            f"распознанных запусков {len(py)} — нераспознанный запуск"
        )
    uv = [x for x in own if x.kind == "py-uv"]
    if len(_RAW_UV.findall(raw)) != len(uv):
        problems.append(f"{script}: `uv` вне распознанного запуска")
    launched = {x.path for x in own}
    for ref in _RAW_REF.findall(raw):
        name = ref.rsplit("/", 1)[-1]
        is_code = ref.endswith(_CODE_SUFFIXES) or "." not in name
        if is_code and not ref.endswith("/") and ref not in launched:
            problems.append(f"{script}: ссылка на код {ref} вне распознанного запуска")
    return problems


def data_refs(root: Path, scripts=SCRIPTS) -> set[str]:
    """Файлы данных, которые скрипты (и подключаемые файлы) читают из дерева."""
    found, _ = launches(root, scripts)
    files = set(scripts) | {x.path for x in found if x.kind == "source"}
    refs: set[str] = set()
    for f in files:
        if (root / f).is_file():
            for ref in _RAW_REF.findall(_raw_lines((root / f).read_text("utf-8"))):
                name = ref.rsplit("/", 1)[-1]
                if not (ref.endswith(_CODE_SUFFIXES) or "." not in name):
                    refs.add(ref)
    return refs


def launches(root: Path, scripts=SCRIPTS) -> tuple[list[Launch], list[str]]:
    """Распознанные запуски кода из дерева (вкл. подключаемые shell-файлы) и
    нарушения: команда вне `SAFE_COMMANDS` и вне распознанных форм — отказ."""
    found: list[Launch] = []
    problems: list[str] = []
    seen: set[str] = set()
    for script in scripts:
        more, extra = _scan(root, script, seen)
        found += more
        problems += extra
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


@dataclass(frozen=True)
class Probe:
    """Боевой процесс входа: модули репо, сторонние пакеты, каталоги дерева в
    `sys.path` и ошибка загрузки (вход не грузится сам — тоже нарушение)."""

    repo_modules: list[str]
    third_party: set[str]
    tree_on_path: list[str]
    error: str


def _process_modules(root: Path, entry: str) -> Probe:
    """Исполнить `entry` как в бою — `python -I <файл>`, без подсказок путей
    (имя модуля не `__main__`, поэтому `main()` не зовётся) — и снять состояние."""
    probe = (
        "import runpy, sys\n"
        f"runpy.run_path({str(root / entry)!r}, run_name='_probe')\n"
        "for p in sys.path: print('PATH', p)\n"
        "for n, m in sorted(sys.modules.items()):\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if f: print('MOD', n, f)\n"
    )
    with tempfile.TemporaryDirectory() as pyc:
        done = subprocess.run(
            [sys.executable, "-I", "-X", f"pycache_prefix={pyc}", "-c", probe],
            cwd="/",
            capture_output=True,
            text=True,
            check=False,
        )
    if done.returncode != 0:
        tail = (done.stderr.strip().splitlines() or ["?"])[-1]
        return Probe([], set(), [], tail)
    real_root = root.resolve()

    def in_tree(f: str) -> str | None:
        p = Path(f).resolve()
        if (
            p.is_relative_to(real_root)
            and ".venv" not in p.relative_to(real_root).parts
        ):
            return os.path.relpath(p, real_root)
        return None

    rels: list[str] = []
    third: set[str] = set()
    tree: list[str] = []
    for line in done.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "PATH" and rest and (rel := in_tree(rest)) is not None:
            tree.append(rel)
        elif kind == "MOD":
            name, f = rest.split(" ", 1)
            if "site-packages" in f and not name.startswith("_virtualenv"):
                third.add(name.split(".")[0])
            if (rel := in_tree(f)) is not None:
                rels.append(rel)
    return Probe(rels, third, tree, "")


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
    probe = _process_modules(root, entry)
    third = probe.third_party
    if probe.error:
        problems.append(
            f"{entry}: не загружается без дерева в sys.path ({probe.error})"
        )
    problems += [
        f"{entry}: каталог дерева в sys.path процесса — {d or '.'} (подмена модулей)"
        for d in probe.tree_on_path
    ]
    for rel in probe.repo_modules:
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
    for ref in data_refs(root, scripts):
        required.setdefault(ref, "данные, читаемые скриптом мержа")
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
    return out | data_refs(root, scripts)
