"""Инвариант делегирования запуска авторского агента (TASK-005, дизайн §19).

Разбор AST всех `governance/**/*.py`:

1. тела `RealOps.author` и `RealOps.author_disp` — только делегирование в
   `governance/author_launch` (`launch_author`, `refuse_disp`);
2. каждое место порождения процесса вне защищённой цепочки (ЗЦ) — с
   литеральной программой из `allowed_literal_programs` контракта
   `contracts/author-sandbox/v1/spawn-sites.toml` либо внесено туда поимённо
   (`module` + `function`, ровно `count` мест); бинари харнессов и общие
   запускатели литералом вне ЗЦ — только поимённо;
3. `ctypes`, `importlib`, `multiprocessing`, `__import__` и `getattr` по
   `subprocess`/`os` вне ЗЦ — только поимённо (`[[dynamic_import]]`);
4. модули ЗЦ импортируют только stdlib и друг друга; пакетной или
   extension-формы модуля ЗЦ нет;
5. каждый модуль ЗЦ, освобождённый от правил 2–3, и этот тест — под
   authority-root и в `harness_files` spec-runner.

ЧЕСТНАЯ ГРАНИЦА (дизайн §19 п.3, §17 п.11). Проверка статическая, охватывает
только `governance/` и ловит прямые и случайные формы обхода границы. Обход
через уже разрешённое место — `git` с исполняемой конфигурацией
(`alias.x=!…`, `core.sshCommand`, `core.pager`), адаптер с произвольным argv
(`edge_check/reviewer.py:run_reviewer`), новое место с литеральным `git` —
она НЕ ловит, как и `exec`/`eval`/`compile`/`runpy`. Это остаточный риск
ревью PR. Тест не утверждает невозможности обхода.
"""

from __future__ import annotations

import ast
import shutil
import sys
import tomllib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from governance import authority_root

ROOT = Path(__file__).resolve().parents[1]
SPAWN_SITES = Path("contracts/author-sandbox/v1/spawn-sites.toml")

#: Модули ЗЦ — освобождены от правил 2–3 (они и есть граница).
ZC_MODULES = ("safe_io", "sandbox", "author_launch")
#: Прочие пути ЗЦ, которые этот тест обязан видеть под защитой (правило 5).
ZC_FILES = (
    "tests/test_author_sandbox_invariant.py",
    "tests/test_governance_sandbox.py",
    "tests/test_governance_sandbox_live.py",
    "tests/test_governance_safe_io.py",
    str(SPAWN_SITES),
    "contracts/author-sandbox/v1/admitted.toml",
)

_SPAWN = {
    "subprocess": {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    },
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "pty": {"spawn"},
}
_DYNAMIC_MODULES = {"ctypes", "importlib", "multiprocessing"}
_PROCESS_MODULES = {"subprocess", "os"}


def _is_spawn(module: str, name: str) -> bool:
    if module == "os":
        return name in {"system", "popen"} or name.startswith(
            ("exec", "spawn", "posix_spawn")
        )
    return name in _SPAWN.get(module, set())


def _program(call: ast.Call) -> str | None:
    """Литеральная программа места запуска; None — динамическая."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.List | ast.Tuple) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


class _Scan(ast.NodeVisitor):
    """Места порождения процессов и динамической загрузки одного модуля."""

    def __init__(self) -> None:
        self.stack: list[str] = []
        # канонические имена видны и без импорта в этом модуле (реэкспорт)
        self.aliases: dict[str, str] = {m: m for m in (*_SPAWN, "os")}
        self.spawns: list[tuple[str, str | None, int]] = []
        self.dynamic: list[tuple[str, int]] = []

    def _where(self) -> str:
        return self.stack[-1] if self.stack else "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in _DYNAMIC_MODULES:
                self.dynamic.append((f"import {alias.name}", node.lineno))
            if alias.name in _SPAWN or alias.name == "os":
                self.aliases[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module.split(".")[0] in _DYNAMIC_MODULES:
            self.dynamic.append((f"from {module} import …", node.lineno))
        for alias in node.names:
            if module in _SPAWN or module == "os":
                if _is_spawn(module, alias.name):
                    # имя без модуля прятало бы место запуска от правила 2
                    self.spawns.append((self._where(), None, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = self.aliases.get(func.value.id)
            if module is not None and _is_spawn(module, func.attr):
                self.spawns.append((self._where(), _program(node), node.lineno))
        if isinstance(func, ast.Name) and func.id == "__import__":
            self.dynamic.append(("__import__", node.lineno))
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and self.aliases.get(node.args[0].id) in _PROCESS_MODULES
        ):
            self.dynamic.append((f"getattr({node.args[0].id}, …)", node.lineno))
        self.generic_visit(node)


def _modules(root: Path) -> Iterator[tuple[str, ast.Module]]:
    for path in sorted((root / "governance").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)


def _is_zc(rel: str) -> bool:
    return rel in {f"governance/{name}.py" for name in ZC_MODULES}


def violations(root: Path) -> list[str]:
    """Нарушения правил 2–3 в дереве `root` (копия репо в мутационных тестах)."""
    contract = tomllib.loads((root / SPAWN_SITES).read_text(encoding="utf-8"))
    allowed = set(contract["allowed_literal_programs"])
    listed = {(s["module"], s["function"]): s["count"] for s in contract["site"]}
    dynamic_ok = {d["module"] for d in contract["dynamic_import"]}
    found: Counter[tuple[str, str]] = Counter()
    out: list[str] = []
    for rel, tree in _modules(root):
        if _is_zc(rel):
            continue
        scan = _Scan()
        scan.visit(tree)
        for where, program, line in scan.spawns:
            if program in allowed:
                continue
            found[(rel, where)] += 1
            if (rel, where) not in listed:
                out.append(f"{rel}:{line} {where}: запуск {program or 'динамический'}")
        if rel not in dynamic_ok:
            out += [f"{rel}:{line}: {what}" for what, line in scan.dynamic]
    for key, count in listed.items():
        if found[key] != count:
            out.append(f"{key[0]}:{key[1]}: в перечне {count}, найдено {found[key]}")
    return out


def test_spawn_and_dynamic_load_sites_match_the_contract() -> None:
    assert violations(ROOT) == []


def _method(tree: ast.Module, cls: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"{cls}.{name} не найден")


@pytest.mark.parametrize(
    ("method", "target"),
    [("author", "launch_author"), ("author_disp", "refuse_disp")],
)
def test_real_ops_authoring_only_delegates_to_the_boundary(
    method: str, target: str
) -> None:
    """Правило 1: тело — (докстринг) + один `return author_launch.<target>(…)`."""
    tree = ast.parse((ROOT / "governance/ops.py").read_text(encoding="utf-8"))
    body = list(_method(tree, "RealOps", method).body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]
    assert len(body) == 1 and isinstance(body[0], ast.Return), ast.unparse(body)
    call = body[0].value
    assert isinstance(call, ast.Call) and ast.unparse(call.func) == (
        f"author_launch.{target}"
    ), ast.unparse(call)


@pytest.mark.parametrize("name", ZC_MODULES)
def test_zc_module_imports_only_stdlib_and_the_chain(name: str) -> None:
    """Правило 4: модуль ЗЦ — один файл `.py`, импорты — stdlib и ЗЦ."""
    gov = ROOT / "governance"
    assert not (gov / name).exists(), "пакетная форма модуля ЗЦ"
    assert [p.name for p in gov.glob(f"{name}.*")] == [f"{name}.py"]
    tree = ast.parse((gov / f"{name}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module == "governance":
                assert {a.name for a in node.names} <= set(ZC_MODULES), ast.dump(node)
                continue
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            top = mod.split(".")[0]
            chain = mod in {f"governance.{m}" for m in ZC_MODULES}
            assert top in sys.stdlib_module_names or top == "__future__" or chain, mod


def _harness_files() -> set[str]:
    import yaml

    data = yaml.safe_load((ROOT / "spec-runner.config.yaml").read_text("utf-8"))
    return {str(item) for item in data["harness_files"]}


def _covered(path: str, harness: set[str]) -> bool:
    parts = Path(path).parts
    return any("/".join(parts[:i]) in harness for i in range(1, len(parts) + 1))


@pytest.mark.parametrize(
    "path", [*(f"governance/{name}.py" for name in ZC_MODULES), *ZC_FILES]
)
def test_zc_paths_are_protected_in_both_lists(path: str) -> None:
    """Правило 5: освобождённый от правил 2–3 модуль ЗЦ без защиты — дыра,
    через которую агент сам расширил бы свои права."""
    assert authority_root.touched([path]) == [path]
    assert _covered(path, _harness_files()), path


# --- мутационные тесты: инвариант краснеет на прямых и случайных формах -------


@pytest.fixture
def tree_copy(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "governance", tmp_path / "governance")
    (tmp_path / SPAWN_SITES).parent.mkdir(parents=True)
    shutil.copy(ROOT / SPAWN_SITES, tmp_path / SPAWN_SITES)
    return tmp_path


def _append(root: Path, rel: str, code: str) -> None:
    path = root / rel
    path.write_text(path.read_text(encoding="utf-8") + code, encoding="utf-8")


def test_mutation_baseline_copy_is_clean(tree_copy: Path) -> None:
    """Контроль: неизменённая копия зелёная — красный мутант значим."""
    assert violations(tree_copy) == []


_MUTANTS = {
    "direct-codex-in-ops": (
        "governance/ops.py",
        (
            "\n\nclass _Sneaky:\n    def author_again(self):\n"
            "        return subprocess.run(['codex', 'exec', 'x'], check=False)\n"
        ),
    ),
    "dynamic-argv-helper-in-runner": (
        "governance/runner.py",
        "\n\ndef _helper(argv):\n    return subprocess.run(argv, check=False)\n",
    ),
    "getattr-subprocess-run": (
        "governance/runner.py",
        "\n\ndef _hidden():\n    return getattr(subprocess, 'run')(['claude'])\n",
    ),
    "aliased-from-import": (
        "governance/runner.py",
        "\nfrom subprocess import Popen as _P\n",
    ),
    "sh-c-literal": (
        "governance/runner.py",
        "\n\ndef _shell():\n    return subprocess.run(['sh', '-c', 'claude'])\n",
    ),
    "importlib-outside-zc": ("governance/runner.py", "\nimport importlib\n"),
}


@pytest.mark.parametrize("mutant", _MUTANTS)
def test_mutant_turns_the_invariant_red(tree_copy: Path, mutant: str) -> None:
    rel, code = _MUTANTS[mutant]
    _append(tree_copy, rel, code)
    assert violations(tree_copy), mutant


def test_stale_contract_entry_turns_red(tree_copy: Path) -> None:
    """Место ушло из кода, а запись осталась — перечень не честен."""
    _append(
        tree_copy,
        str(SPAWN_SITES),
        '\n[[site]]\nmodule = "governance/ops.py"\nfunction = "gone"\n'
        'count = 1\nreason = "x"\n',
    )
    assert violations(tree_copy) == ["governance/ops.py:gone: в перечне 1, найдено 0"]


def test_new_literal_git_site_passes_by_rule(tree_copy: Path) -> None:
    """Честная граница: новое место с литеральным `git` правило пропускает —
    поэтому оно и названо остаточным риском (§19 п.3)."""
    _append(
        tree_copy,
        "governance/runner.py",
        "\n\ndef _g():\n    return subprocess.run(['git', '-c', 'alias.x=!sh', 'x'])\n",
    )
    assert violations(tree_copy) == []
