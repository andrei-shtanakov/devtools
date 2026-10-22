"""Normative install automaton of spec 3.3 as data: the single source of its rules.

Each row of the table is a tuple of cubes; a cube maps a fact to its allowed values
(a missing fact means "any value"). The spec's tables (`install-table`,
`install-settle`, `install-protocols`, `install-routes`) and the plan's
`install-route-calls` are renderings of this data, byte for byte; the acceptance
automaton (`df.accept`) classifies, settles and runs routes with it. Disjointness of
the rows is proved by cube intersection, not by sampling.

The values were ported from the development model (`r18-local-check/table.py`);
nothing here reads or names that artifact at run time. Pure data and pure functions:
the only work at import is `check_data`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: One step of a protocol: (op, arg, text). `op` is a key of `OPS`.
Step = tuple[str, str, str]
Steps = tuple[Step, ...]
#: A cube: fact -> allowed values; a missing fact means any value.
Cube = Mapping[str, frozenset[object]]
#: A route step: (guard, call, target); `guard` None means unconditional.
RouteStep = tuple[str | None, str, str]
#: A sync-twin case: (protocol, variant, step index[, parent]).
TwinCase = tuple[str | int | None, ...]

ROOT = ("root-700", "root-755", "root-x")
DOMAINS: Mapping[str, tuple[object, ...]] = {
    "P": (0, 1),
    "r": ("absent", "b-empty", "b-full", *ROOT),
    "m": ("none", "built", "copied", "verified", "admin"),
    "full": (0, 1),
    "A": (0, 1),
    "S": (0, 1),
    "reg": (0, 1),
    "intact": (0, 1),
    "cur": (0, 1),
    "L": (0, 1),
}

# Durability sources (r5-N1): for every fact, the directories whose entries the fact
# is read from. `settle` syncs exactly the union, before every classification, so a
# fact that a crashed process made visible (rename done, parent not yet synced)
# cannot be used and then lost by an OS crash. Tree *content* is not settled: it is
# trusted only through the mark (copy/admin sync the tree before writing it).
DIRS: Mapping[str, str] = {
    "install": "`/var/lib/df/install/` (`pending.toml`, `accounts.toml`)",
    "accept": "`/var/lib/df/install/accept/` (отметки)",
    "etc_df": "`/etc/df/` (`releases.toml`, `install.toml`)",
    "opt_df": "`/opt/df/` (ссылка `current`)",
    "releases": "`/opt/df/releases/`",
    "release_root": "корень `releases/<id>`, если он у root (его режим)",
    "staging": "`/opt/df/staging/` и `staging/<id>/`, если есть",
    "admin": "`/opt/df/admin/`",
    "admin_python": "`/opt/df/admin-python/` (административный Python)",
    "opt_bin": "`/opt/df/bin/` (`darkfactory`, `darkfactory-entry.py`)",
    "local_bin": "`/usr/local/bin/`, если ссылка создаётся",
    "home_parent": "родитель домашнего каталога `df-build` (`/Users/`, `/home/`)",
}
FACT_SOURCES: Mapping[str, tuple[str, ...]] = {
    "P": ("install",),
    "reg": ("etc_df",),
    "cur": ("opt_df",),
    "intact": ("etc_df", "releases", "release_root", "admin", "admin_python"),
    "L": ("opt_bin", "local_bin"),
    "m": ("accept",),
    "r": ("releases", "release_root"),
    "full": ("releases", "release_root", "accept"),
    "A": ("admin", "accept", "admin_python"),
    "S": ("staging",),
}
#: Facts computed by the admin readiness predicate (`admin_ready`, Task 8a) and the
#: directories that predicate reads: the admin code and the admin Python.
ADMIN_READY_FACTS = ("A", "intact")
ADMIN_READY_DIRS = ("admin", "admin_python")

# r6-N1: facts used by recovery decisions outside `drive` (account creation). They
# are settled by `settle_on_acquire` only — at lock acquisition, before the writer
# snapshot, `recover_pending` and `create_build_user` — with every table source.
ACCOUNT_SOURCES: Mapping[str, tuple[str, ...]] = {
    "account-intent": ("install",),  # accounts.toml
    "account-home": ("home_parent",),  # the home's name (rename without replacement)
}
_ACCOUNT_NAMES = {
    "account-intent": "учётка: запись `accounts.toml`",
    "account-home": "учётка: домашний каталог",
}

# r7-N1: durability protocols of the primitives that change shared state. A "change"
# is followed by fault_point (the window "visible, not durable"); "parent" is the
# directory the caller passes. r11-N4: the operations of a protocol step — one source
# for every selector (`op in SYNC_OPS`, never `op != "change"`).
OPS: Mapping[str, str] = {
    "change": "изменение; за ним точка сбоя",
    "sync_dir": "закрепление каталога",
    "sync_tree": "закрепление дерева",
    "process": "процессы `df-build` завершены; файлов не меняет",
    # r12-N1: the cleaner's run is a step of its own: it may change (also partly) what
    # is inside df-build's tree before any outcome, so no variant that ran it is empty.
    "builder": "запуск от `df-build`: меняет (в т. ч. частично) только содержимое "
    "названного дерева `df-build`; root его не открывает",
}
SYNC_OPS = ("sync_dir", "sync_tree")
# Directories a variant may sync besides DIRS: not fact sources, not settled. r12-N2:
# the model represents the quarantine to check the order of its syncs.
NON_FACT_DIRS: Mapping[str, str] = {"quarantine": "`/opt/df/staging/.quarantine/`"}

_SEAL_SYNC_STAGING = "`sync_dir(/opt/df/staging/)`, `sync_dir(staging/<id>)`"
_CLEANER = ("builder", "tree", "очиститель от `df-build`")

PROTOCOLS: Mapping[str, Steps | Mapping[str, Steps]] = {
    "write_atomic": (
        ("change", "rename", "временный файл, `sync_file`, `rename`"),
        ("sync_dir", "parent", ""),
    ),
    "remove_durably": (("change", "unlink", "`unlink`"), ("sync_dir", "parent", "")),
    "remove_own_tree": (
        ("change", "unlink", "`rmtree` дерева root"),
        ("sync_dir", "parent", ""),
    ),
    # r11-N1: a primitive whose outcomes differ in effects has variants, each its own
    # exact steps. r12-N1/N2: the model executes every variant it can reach, chosen by
    # VARIANT_WHEN (no step is skipped by a condition); NOT_MODELED names the others.
    "cleanup_tree": {
        "tree": (
            _CLEANER,
            ("change", "unlink", "`rmdir` корня"),
            ("sync_dir", "parent", ""),
        ),
        "link": (
            ("change", "unlink", "ссылка или не каталог: `unlink`"),
            ("sync_dir", "parent", ""),
        ),
        # destination first: the name is durable in the quarantine before it leaves
        # the parent, so no crash order loses the tree from both.
        "quarantine": (
            _CLEANER,
            ("change", "renamed", "очиститель не справился: `rename` в карантин"),
            ("sync_dir", "quarantine", ""),
            ("sync_dir", "parent", ""),
        ),
        "absent": (),
        "failed": (_CLEANER,),
    },
    # r8-N2: the descriptor is taken while the directory is still root's and empty;
    # the transfer to df-build is the fchown; after it root only fsyncs and closes it.
    "prepare_dir": (
        (
            "change",
            "created",
            (
                "`mkdir releases/<id>` root `0700`, `open` "
                "(`O_DIRECTORY|O_NOFOLLOW`) до передачи"
            ),
        ),
        ("change", "chown", "`fchown(fd, df-build)` — передача"),
        ("sync_dir", "release_root", "`fsync(fd)`, `close(fd)`"),
        ("sync_dir", "releases", ""),
    ),
    # r12-N1: the container is created only when absent — two variants, not a
    # skipped step.
    "seal": {
        "new": (
            ("change", "created", "`mkdir staging/<id>` root `0711`"),
            ("change", "renamed", "`rename releases/<id> → staging/<id>/tree`"),
            ("sync_dir", "releases", ""),
            ("sync_dir", "staging", _SEAL_SYNC_STAGING),
        ),
        "reuse": (
            ("change", "renamed", "`rename releases/<id> → staging/<id>/tree`"),
            ("sync_dir", "releases", ""),
            ("sync_dir", "staging", _SEAL_SYNC_STAGING),
        ),
    },
    # r10-N1: the producer's end is a declared step of the protocol (receive_tree owns
    # the lifecycle; SpawnedProducer.end reaps df-build's processes), and receive_tree
    # is the single owner of both final syncs — accept_copy adds none.
    "accept_copy": (
        (
            "change",
            "created",
            "`spawn_as` производителя, `receive_tree`: частичная копия",
        ),
        ("process", "df-build", "`producer.end()`: цепочка производителя завершена"),
        ("change", "created", "`receive_tree`: каталоги `0755`, копия полна"),
        ("sync_tree", "R", "`sync_tree(releases/<id>)` (с корнем: его режим)"),
        ("sync_dir", "parent", "`sync_dir(releases)`"),
    ),
    "verify_chmod": (
        ("change", "chmod", "`chmod 0755` корня"),
        ("sync_dir", "release_root", ""),
    ),
    "install_admin": (
        ("change", "created", "копия `admin/<id>`: частичная"),
        ("change", "created", "копия `admin/<id>`: полна"),
        ("sync_tree", "admin", "`sync_tree(admin/<id>)`"),
        ("sync_dir", "admin", ""),
    ),
    "activate": (
        ("change", "renamed", "временная ссылка, `rename` поверх `current`"),
        ("sync_dir", "opt_df", ""),
    ),
    "link_command": (
        (
            "change",
            "renamed",
            "временная ссылка, `rename` поверх `/usr/local/bin/darkfactory`",
        ),
        ("sync_dir", "local_bin", ""),
    ),
    "remove_empty_dir": (
        ("change", "unlink", "`rmdir` пустого каталога root"),
        ("sync_dir", "parent", ""),
    ),
    # r9-N1: the admin Python is a leaf of the route `admin`, not a step hidden inside
    # install_admin. uv installs into `.staging`; the tree is synced, then renamed.
    # r10: what uv leaves in `.staging` besides the interpreter is removed by the next
    # route step, not here.
    "install_admin_python": (
        ("change", "created", "`uv python install` в `admin-python/.staging`"),
        ("sync_tree", "py", "`sync_tree(.staging/cpython-…)`"),
        ("change", "renamed", "`rename` в `admin-python/cpython-…`"),
        ("sync_dir", "admin_python", ""),
    ),
    # r9-N1: removal inside build_home is df-build's, never root's; build_home content
    # is not a fact source and is never trusted, so there is no sync: a removal lost
    # by an OS crash is repeated by the next verify. Absence is decided by df-build
    # (ABSENT_OK). r11-N1: absent is an outcome without effects — a variant of its own.
    # r12-N1: the observation boundary is cleanup_tree's — root's changes and syncs,
    # and the runs as df-build; everything here is a run as df-build.
    "cleanup_build_home": {
        "tree": (
            (
                "builder",
                "build_home",
                "`find` и очиститель от `df-build`: дерево удалено вместе с корнем",
            ),
        ),
        "absent": (("builder", "build_home", "`find` от `df-build`: имени нет"),),
    },
}

# r12-N1: the condition that selects each variant — part of the contract, rendered in
# the spec. "среда": the outcome is decided by the environment (the cleaner's result).
# r13-N1: one outcome "cleanup failed" for both quarantine and failed — the cleaner's
# command failed, or the final rmdir found the tree not empty (a live df-build process).
_CLEANUP_FAILED = (
    "команда очистителя не уложилась в срок или вернула код ≠ 0, либо "
    "`rmdir` вернул `ENOTEMPTY`"
)
VARIANT_WHEN: Mapping[str, Mapping[str, str]] = {
    "cleanup_tree": {
        "tree": "каталог; все команды очистителя — код 0 в срок, и `rmdir` "
        "успешен (среда)",
        "link": "ссылка или не каталог",
        "quarantine": f"каталог; очистка не удалась ({_CLEANUP_FAILED}; среда), "
        "есть `quarantine_label`",
        "absent": "имени нет",
        "failed": f"каталог; очистка не удалась ({_CLEANUP_FAILED}; среда), "
        "`quarantine_label=None`",
    },
    "seal": {
        "new": "нет `staging/<id>`",
        "reuse": "есть пустой `staging/<id>` (повтор после сбоя за `mkdir`)",
    },
    "cleanup_build_home": {
        "tree": "`find` от `df-build` нашёл имя",
        "absent": "`find` от `df-build` имени не нашёл",
    },
}


def cleanup_tree_variant(
    obj: str, cleaner: str | None, rmdir: str | None, label: bool
) -> str:
    """Select cleanup_tree's variant: VARIANT_WHEN as a function (r13-N1).

    obj: absent | link | dir; cleaner: ok | timeout | exit; rmdir: ok | ENOTEMPTY |
    None (not attempted after a failed command); label: quarantine_label is given.
    """
    if obj != "dir":
        return obj
    if cleaner == "ok" and rmdir == "ok":
        return "tree"
    return "quarantine" if label else "failed"


def _cleaner_inputs(obj: str) -> tuple[str | None, ...]:
    return ("ok", "timeout", "exit") if obj == "dir" else (None,)


def _rmdir_inputs(obj: str, cleaner: str | None) -> tuple[str | None, ...]:
    return ("ok", "ENOTEMPTY") if obj == "dir" and cleaner == "ok" else (None,)


#: Every input of the selection with its variant (the plan's selection test runs each).
CLEANUP_TREE_CASES: tuple[tuple[tuple[str, str | None, str | None, bool], str], ...] = (
    tuple(
        ((obj, cl, rm, lb), cleanup_tree_variant(obj, cl, rm, lb))
        for obj in ("absent", "link", "dir")
        for cl in _cleaner_inputs(obj)
        for rm in _rmdir_inputs(obj, cl)
        for lb in (True, False)
    )
)
#: The value `cleanup_tree` returns in each variant.
CLEANUP_OUTCOME: Mapping[str, str] = {
    "tree": "removed",
    "link": "removed",
    "absent": "removed",
    "quarantine": "quarantined",
    "failed": "failed",
}

# r12-N1/N2: variants the model does not execute, with the reason. Every other
# variant must be executed by the model, so no equivalence argument stands in for it.
NOT_MODELED: Mapping[tuple[str, str], str] = {
    ("cleanup_tree", "link"): "родители root: `df-build` не может подменить "
    "`releases/<id>` или `staging/<id>/tree` ссылкой; защитная ветвь — тест плана",
    ("cleanup_tree", "absent"): "каждый вызов в маршрутах под условием наличия; "
    "отсутствие встречает двойник без условия",
    ("cleanup_tree", "failed"): "только `retry_quarantine` (`quarantine_label=None`); "
    "карантин — не источник фактов",
}

# r8-N1: routes — the composition of every action and of the rollback: ordered steps
# (guard, call, target). A guard is a fact read by `lstat` through the root-owned
# parent's descriptor just before the step; a step whose guard is false is skipped —
# the only idempotence, primitives refuse an absent object. `call` is a protocol,
# another route, or a step without shared-state effect (run_build, checks).
ROUTES: Mapping[str, tuple[RouteStep, ...]] = {
    "build-dir": ((None, "prepare_dir", "releases/<id>"),),
    "build": (
        (None, "run_build", "от `df-build`"),
        (None, "write_atomic", "accept/<id>.toml: built"),
    ),
    "seal": (
        (None, "reap_user_processes", "df-build"),
        (None, "seal", "releases/<id>"),
    ),
    "copy": (
        (None, "accept_copy", "releases/<id>"),
        (None, "write_atomic", "accept/<id>.toml: copied"),
    ),
    "copy-partial": (
        (None, "remove_own_tree", "releases/<id>"),  # row: root tree
        (None, "accept_copy", "releases/<id>"),
        (None, "write_atomic", "accept/<id>.toml: copied"),
    ),
    "verify": (
        (None, "cleanup_staging", ""),
        (None, "check_links", "releases/<id>"),
        (None, "verify_chmod", "releases/<id>"),
        (None, "verify_sources", "releases/<id>"),
        (None, "distribution_problems", "releases/<id>"),
        (None, "static_problems", "releases/<id>"),
        (None, "cleanup_build_home", "build_home/tmp"),
        (None, "cleanup_build_home", "build_home/cache"),
        (None, "transfer_check", "releases/<id>"),
        (None, "tree_digest", "releases/<id> = отметке"),
        (None, "write_atomic", "accept/<id>.toml: max(стадия, verified)"),
    ),
    "admin": (
        ("py-staging=present", "remove_own_tree", "admin-python/.staging"),
        ("py=absent", "install_admin_python", "admin-python/cpython-…"),
        ("py-staging=present", "remove_own_tree", "admin-python/.staging"),
        ("admin=present", "remove_own_tree", "admin/<id>"),
        (None, "install_admin", "admin/<id>"),
        (None, "write_atomic", "accept/<id>.toml: admin"),
    ),
    "register": (
        (None, "write_atomic", "releases/<id>/release.json"),
        (None, "write_atomic", "/etc/df/releases.toml"),
    ),
    "activate": ((None, "activate", "/opt/df/current"),),
    "launcher": (
        (None, "write_atomic", "bin/darkfactory-entry.py"),
        (None, "write_atomic", "bin/darkfactory"),
        ("local_bin=trusted", "link_command", "/usr/local/bin/darkfactory"),
    ),
    "commit-config": (
        (None, "write_atomic", "/etc/df/install.toml"),
        (None, "remove_durably", "pending.toml"),
    ),
    "cleanup": (
        ("r=root", "remove_own_tree", "releases/<id>"),
        ("r=builder", "cleanup_tree", "releases/<id>"),
        (None, "cleanup_staging", ""),
        ("admin=present", "remove_own_tree", "admin/<id>"),
        ("m=present", "remove_durably", "accept/<id>.toml"),
    ),
    "cleanup_staging": (
        ("S=tree", "cleanup_tree", "staging/<id>/tree"),
        ("S=container", "remove_empty_dir", "staging/<id>"),
    ),
    "rollback": (
        ("not reg", "cleanup", ""),
        (None, "remove_durably", "pending.toml"),
    ),
}
GUARD_TEXT: Mapping[str, str] = {
    "r=root": "корень `releases/<id>` у root",
    "r=builder": "корень у `df-build`",
    "S=tree": "есть `staging/<id>/tree`",
    "S=container": "есть `staging/<id>` без `tree`",
    "admin=present": "есть `admin/<id>`",
    "m=present": "есть отметка",
    "not reg": "id не в `releases.toml`",
    "py=absent": "нет `admin-python/cpython-…`",
    "py-staging=present": "есть `admin-python/.staging`",
    "local_bin=trusted": "`/usr/local/bin` проходит политику доверенных путей",
}
GUARD_NAMES: frozenset[str] = frozenset(GUARD_TEXT)
# r9-N1: every guard reads only directories that settle syncs (else a guard could
# skip a step on a fact an OS crash then loses).
GUARD_SOURCES: Mapping[str, tuple[str, ...]] = {
    "r=root": ("releases",),
    "r=builder": ("releases",),
    "S=tree": ("staging",),
    "S=container": ("staging",),
    "admin=present": ("admin",),
    "m=present": ("accept",),
    "not reg": ("etc_df",),
    "py=absent": ("admin_python",),
    "py-staging=present": ("admin_python",),
    "local_bin=trusted": ("local_bin",),
}

# r9-N1: every route leaf that is not a protocol has a declared kind and its complete
# effects. A leaf contains no other leaf: what changes state is a protocol step or a
# route step, never a hidden call inside a check or a primitive.
CALL_KINDS: Mapping[str, str] = {
    "check": "ничего не меняет: ни одного изменения файлов, ни одного запуска от "
    "`df-build`",
    "process": "только процессы `df-build`; файлы не меняет",
    "builder": "запуск от `df-build`; меняет только названные объекты `df-build`, "
    "root их не открывает",
}
CALLS: Mapping[str, tuple[str, str]] = {
    "run_build": (
        "builder",
        "`releases/<id>` (до `seal`), `build_home/tmp`, `build_home/cache`",
    ),
    "reap_user_processes": ("process", ""),
    "check_links": ("check", ""),
    "verify_sources": ("check", ""),
    "distribution_problems": ("check", ""),
    "static_problems": ("check", ""),
    "transfer_check": (
        "builder",
        "создаёт `build_home/tmp`, `build_home/cache`; требует, чтобы их не было",
    ),
    "tree_digest": ("check", ""),
}
# r10-N1: the expected effects of a leaf as an ordered token sequence. A token per
# protocol step: a change — its kind; `sync_dir(<DIRS name | parent>)`;
# `sync_tree(<arg>)`; `process(df-build)`. A call of kind builder is
# `run_as(df-build)+` (one or more runs as df-build), process — `process(df-build)`,
# check — nothing. A refusal (exception) is a prefix of a variant's events, not a
# variant; an outcome returned to the caller with other effects is a variant.
KIND_TOKENS: Mapping[str, tuple[str, ...]] = {
    "check": (),
    "process": ("process(df-build)",),
    "builder": ("run_as(df-build)+",),
}

#: Plan functions that are exactly one call of a protocol with a computed argument.
WRAPS: Mapping[str, str] = {
    "write_mark": "write_atomic",
    "write_release_json": "write_atomic",
    "register": "write_atomic",
    "drop_mark": "remove_durably",
}
#: Primitives for which an absent object is a success with this outcome; every other
#: primitive refuses an absent object (skipping is the guard's job).
ABSENT_OK: Mapping[str, str] = {
    "cleanup_tree": "`removed`",
    "cleanup_build_home": "`absent` — так решает `df-build` (`find build_home "
    "-mindepth 1 -maxdepth 1 -name <имя>`), root не смотрит",
}
#: Preconditions a primitive checks itself (refusal, not skip).
REQUIRES: Mapping[str, str] = {
    "remove_own_tree": "объект есть, владелец корня root; для `releases/<id>`, "
    "`admin/<id>` — id не в `releases.toml`",
    "cleanup_tree": "родитель есть (root открывает его); ссылка или не каталог — "
    "`unlink`; иначе очиститель",
    "cleanup_build_home": "очиститель запущен и уложился в срок; ошибка доступа — "
    "отказ, не отсутствие",
    "install_admin_python": "`.staging` нет; `uv` сверяет загрузку (§3.9)",
    "remove_empty_dir": "объект есть, пуст, владелец root",
    "remove_durably": "файл есть",
    "accept_copy": "`staging/<id>/tree` не порван (`built` после смены boot — "
    "пересборка)",
    "write_atomic": "отметка: стадия не ниже действующей",
    "activate": "запись `allowed`, дерево и `admin` целы",
}

# r13-N3: the cases of the sync twins. A protocol in SYNC_PARENTS is called with
# several parents whose expectations differ, so each of its sync steps is a case per
# parent; write_atomic's parent sync is one case for all its parents. SYNC_NEEDED —
# the cases whose removal gives a counterexample: the change is followed by another
# durable step with no settle between.
SYNC_PARENTS: Mapping[str, tuple[str, ...]] = {
    "remove_own_tree": ("releases", "admin", "admin_python"),
    "cleanup_tree": ("releases", "staging"),
    "remove_durably": ("install", "accept"),
    "remove_empty_dir": ("staging",),
}
SYNC_NEEDED: frozenset[TwinCase] = frozenset(
    {
        ("write_atomic", None, 1),
        ("remove_own_tree", None, 1, "releases"),
        ("remove_own_tree", None, 1, "admin"),
        ("remove_own_tree", None, 1, "admin_python"),
        ("cleanup_tree", "tree", 2, "releases"),
        ("cleanup_tree", "quarantine", 2, "releases"),
        ("cleanup_tree", "quarantine", 2, "staging"),
        ("cleanup_tree", "quarantine", 3, "releases"),
        ("accept_copy", None, 3),
        ("install_admin", None, 2),
        ("remove_empty_dir", None, 1, "staging"),
        ("install_admin_python", None, 1),
    }
)

# r12: directories settle syncs FIRST, before the fact sources. A rename into the
# quarantine removes a tree's entry from a fact source; a process crash between the
# rename and the quarantine's sync leaves both unsynced, and the next settle would
# make the removal durable while the quarantine entry is not — an OS crash then loses
# the tree from both. Not fact sources: no fact reads them.
SETTLE_FIRST = ("quarantine",)


@dataclass(frozen=True)
class Row:
    """A row of the table: its phase, cubes, action and allowed next phases.

    `post` is ordered as the spec renders it; membership is tested with `in`.
    """

    phase: str
    cubes: tuple[Cube, ...]
    action: str
    post: tuple[str, ...]


#: Every pre-registration cube contains these.
PRE: Cube = {"P": frozenset({1}), "reg": frozenset({0}), "cur": frozenset({0})}


def _cube(**facts: object) -> Cube:
    return {
        k: frozenset(v) if isinstance(v, tuple) else frozenset({v})
        for k, v in facts.items()
    }


def _pre(**facts: object) -> Cube:
    return {**PRE, **_cube(**facts)}


ROWS: tuple[Row, ...] = (
    Row("committed", (_cube(P=0),), "", ()),
    Row(
        "launched",
        (_cube(P=1, reg=1, intact=1, cur=1, L=1),),
        "commit-config",
        ("committed",),
    ),
    Row(
        "active", (_cube(P=1, reg=1, intact=1, cur=1, L=0),), "launcher", ("launched",)
    ),
    Row(
        "registered",
        (_cube(P=1, reg=1, intact=1, cur=0),),
        "activate",
        ("active", "launched"),
    ),
    Row("damaged", (_cube(P=1, reg=1, intact=0), _cube(P=1, reg=0, cur=1)), "stop", ()),
    Row(
        "admin-ready",
        (_pre(m="admin", r="root-755", full=1, A=1, S=0),),
        "register",
        ("registered",),
    ),
    Row(
        "verified",
        (
            _pre(m="verified", r="root-755", full=1, S=0),
            _pre(m="admin", r="root-755", full=1, A=0, S=0),
        ),
        "admin",
        ("admin-ready",),
    ),
    Row(
        "copied",
        (
            _pre(m="copied", r=ROOT, full=1),
            _pre(m=("verified", "admin"), r=("root-700", "root-x"), full=1),
        ),
        "verify",
        ("verified", "admin-ready"),
    ),
    Row("partial-copy", (_pre(m="built", r=ROOT, S=1),), "copy-partial", ("copied",)),
    Row("sealed", (_pre(m="built", r="absent", S=1),), "copy", ("copied",)),
    Row("built", (_pre(m="built", r=("b-empty", "b-full"), S=0),), "seal", ("sealed",)),
    Row("prepared", (_pre(m="none", r="b-empty", S=0),), "build", ("built",)),
    Row("none", (_pre(m="none", r="absent", S=0),), "build-dir", ("prepared",)),
    Row("building", (_pre(m="none", r="b-full", S=0),), "cleanup", ("none",)),
)
#: The phase of a point no row contains (always pre-registration, see the tests).
UNKNOWN_ROW = Row("unknown", (), "cleanup", ("none",))


def matches(cube: Cube, facts: Mapping[str, object]) -> bool:
    """True if the point `facts` lies in `cube`."""
    return all(facts[k] in values for k, values in cube.items())


def classify(facts: Mapping[str, object]) -> str:
    """Phase of the only row with a cube containing `facts`; none -> "unknown".

    More than one row is a program error (`AssertionError`): there is no row order
    and no "best match".
    """
    hits = [r.phase for r in ROWS if any(matches(c, facts) for c in r.cubes)]
    if len(hits) > 1:
        raise AssertionError(f"rows {hits} overlap at {dict(facts)}")
    return hits[0] if hits else UNKNOWN_ROW.phase


def row_of(phase: str) -> Row:
    """The row of `phase`, `UNKNOWN_ROW` included; an unknown name is a KeyError."""
    if phase == UNKNOWN_ROW.phase:
        return UNKNOWN_ROW
    for r in ROWS:
        if r.phase == phase:
            return r
    raise KeyError(phase)


def _intersect(a: Cube, b: Cube) -> bool:
    return all(
        not a.get(k, frozenset(d)).isdisjoint(b.get(k, frozenset(d)))
        for k, d in DOMAINS.items()
    )


def overlaps(rows: Sequence[Row] = ROWS) -> list[tuple[str, str]]:
    """Every pair of different rows whose cubes intersect (exact, by value sets)."""
    out: list[tuple[str, str]] = []
    for i, r1 in enumerate(rows):
        for r2 in rows[i + 1 :]:
            hit = any(_intersect(a, b) for a in r1.cubes for b in r2.cubes)
            if hit:
                out.append((r1.phase, r2.phase))
    return out


# ---------- durability sources ----------


def settle_dirs(
    sources: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    """Union of the durability sources of all facts, in DIRS order."""
    sources = FACT_SOURCES if sources is None else sources
    used = {d for ds in sources.values() for d in ds}
    return tuple(d for d in DIRS if d in used)


def acquire_dirs(
    sources: Mapping[str, tuple[str, ...]] | None = None,
    account: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    """settle_on_acquire: table sources and account sources, in DIRS order."""
    account = ACCOUNT_SOURCES if account is None else account
    used = set(settle_dirs(sources)) | {d for ds in account.values() for d in ds}
    return tuple(d for d in DIRS if d in used)


# ---------- protocols and leaves ----------


def variants(name: str) -> dict[str | None, Steps]:
    """{variant: steps} of a protocol; a protocol without variants is {None: steps}."""
    p = PROTOCOLS[name]
    if isinstance(p, tuple):
        return {None: p}
    return {v: steps for v, steps in p.items()}


def modeled_variants(name: str) -> list[str | None]:
    """The variants the model executes (all but NOT_MODELED)."""
    return [v for v in variants(name) if v is None or (name, v) not in NOT_MODELED]


def step_token(op: str, arg: str) -> str:
    """The event token of one protocol step."""
    if op == "builder":
        return "run_as(df-build)+"
    return arg if op == "change" else f"{op}({arg})"


def leaf_variants(name: str) -> dict[str | None, tuple[str, ...]]:
    """{variant: expected event tokens} of a leaf; None is the only key of a leaf
    without variants. A WRAPS name is its protocol."""
    name = WRAPS.get(name, name)
    if name in PROTOCOLS:
        return {
            v: tuple(step_token(op, arg) for op, arg, _ in steps)
            for v, steps in variants(name).items()
        }
    return {None: KIND_TOKENS[CALLS[name][0]]}


def leaf_effects(name: str) -> tuple[str, ...]:
    """The expected event tokens of a leaf without variants."""
    vs = leaf_variants(name)
    if list(vs) != [None]:
        raise AssertionError(f"{name} has variants {list(vs)}")
    return vs[None]


def sync_steps() -> list[tuple[str, str | None, int, str]]:
    """(protocol, variant, index, arg) of every SYNC_OPS step the model executes."""
    return [
        (n, v, i, arg)
        for n in PROTOCOLS
        for v in modeled_variants(n)
        for i, (op, arg, _) in enumerate(variants(n)[v])
        if op in SYNC_OPS
    ]


def sync_twin_cases() -> list[TwinCase]:
    """Every case (protocol, variant, step[, parent]) of the sync twins, in order."""
    out: list[TwinCase] = []
    for n, v, i, _ in sync_steps():
        if n in SYNC_PARENTS:
            out += [(n, v, i, p) for p in SYNC_PARENTS[n]]
        else:
            out.append((n, v, i))
    return out


# ---------- routes ----------

#: Every route an action of a row (or the rollback) runs; cleanup_staging is nested.
ACTION_ROUTES = tuple(r for r in ROUTES if r != "cleanup_staging")


def unfold_route(
    name: str, guards: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str, str]]:
    """Every leaf call of the route in execution order, nested routes inlined, each
    with the conjunction of the guards it runs under: [(guards, call, target)]."""
    out: list[tuple[tuple[str, ...], str, str]] = []
    for guard, call, target in ROUTES[name]:
        g = guards + ((guard,) if guard else ())
        if call in ROUTES and call not in PROTOCOLS:
            out += unfold_route(call, g)
        else:
            out.append((g, call, target))
    return out


def route_leaves() -> list[str]:
    """Every call of every route that is not a nested route, in first-use order. A
    name that is both a protocol and a route (seal, activate) is the protocol."""
    out: list[str] = []
    for steps in ROUTES.values():
        for _, call, _ in steps:
            if (call in PROTOCOLS or call not in ROUTES) and call not in out:
                out.append(call)
    return out


def check_data(*, fact_sources: Mapping[str, tuple[str, ...]] | None = None) -> None:
    """Refuse inconsistent table data with an `AssertionError` listing every defect.

    Every fact has durability sources; every route leaf is exactly one of a protocol
    and a CALLS kind; every guard has text and settled sources; ABSENT_OK primitives
    are protocols with a variant "absent" without root changes or syncs; variants are
    selected by VARIANT_WHEN and NOT_MODELED names existing variants; the cleanup
    selection reaches every variant; every op is in OPS and every synced directory is
    named. The facts of the admin readiness predicate read the admin code and the
    admin Python. `fact_sources` replaces FACT_SOURCES (for twins).
    """
    sources = FACT_SOURCES if fact_sources is None else fact_sources
    bad: list[str] = []
    if set(sources) != set(DOMAINS):
        bad.append("every fact needs durability sources")
    bad += [
        f"{f}: sources {sources.get(f)} lack {d}"
        for f in ADMIN_READY_FACTS
        for d in ADMIN_READY_DIRS
        if d not in sources.get(f, ())
    ]
    bad += [c for c in route_leaves() if (c in PROTOCOLS) == (c in CALLS)]
    settled = set(settle_dirs(sources))
    bad += [
        f"guard {g}"
        for st in ROUTES.values()
        for g, _, _ in st
        if g and (g not in GUARD_TEXT or not set(GUARD_SOURCES[g]) <= settled)
    ]
    bad += [f"ABSENT_OK {k}" for k in ABSENT_OK if k not in PROTOCOLS]
    # r11-N1, r12: an absent object changes nothing root sees (a df-build run that
    # finds nothing is allowed: cleanup_build_home asks df-build).
    bad += [
        f"{k}: no variant absent without root changes"
        for k in ABSENT_OK
        if k in PROTOCOLS
        and (
            "absent" not in variants(k)
            or any(
                op == "change" or op in SYNC_OPS for op, _, _ in variants(k)["absent"]
            )
        )
    ]
    bad += [
        f"{n}: conditions {sorted(VARIANT_WHEN.get(n, {}))} != variants"
        for n, p in PROTOCOLS.items()
        if not isinstance(p, tuple) and set(VARIANT_WHEN.get(n, {})) != set(p)
    ]
    bad += [
        f"{n}: VARIANT_WHEN without variants"
        for n in VARIANT_WHEN
        if n not in PROTOCOLS or isinstance(PROTOCOLS[n], tuple)
    ]
    bad += [f"NOT_MODELED {k}" for k in NOT_MODELED if k[1] not in variants(k[0])]
    # r13-N1: the selection reaches every variant; ENOTEMPTY after a clean cleaner is
    # a failed cleanup, not a tree.
    selected = {w for _, w in CLEANUP_TREE_CASES}
    bad += [
        f"cleanup_tree: variant {v} never selected"
        for v in variants("cleanup_tree")
        if v not in selected
    ]
    if cleanup_tree_variant("dir", "ok", "ENOTEMPTY", True) != "quarantine":
        bad.append("cleanup_tree: ENOTEMPTY is not a failed cleanup")
    if set(CLEANUP_OUTCOME) != set(variants("cleanup_tree")):
        bad.append("CLEANUP_OUTCOME keys != cleanup_tree variants")
    cases = sync_twin_cases()
    bad += [f"SYNC_NEEDED {c} is not a case" for c in SYNC_NEEDED if c not in cases]
    for n in PROTOCOLS:
        for v, st in variants(n).items():
            bad += [f"{n}/{v}: op {op}" for op, _, _ in st if op not in OPS]
            bad += [
                f"{n}/{v}: sync of {a}"
                for op, a, _ in st
                if op == "sync_dir"
                and a not in DIRS
                and a not in NON_FACT_DIRS
                and a != "parent"
            ]
    if bad:
        raise AssertionError(f"table data: {bad}")


# ---------- rendering for the spec and the plan ----------

_NAMES = {
    "P": "P",
    "r": "R",
    "m": "m",
    "full": "full",
    "A": "A",
    "S": "S",
    "reg": "reg",
    "intact": "intact",
    "cur": "cur",
    "L": "L",
}
_ORDER = ("P", "reg", "intact", "cur", "L", "m", "r", "full", "A", "S")
_ACTION_TEXT = {
    "": "—",
    "stop": "стоп: «релиз повреждён» (§3.8)",
    "commit-config": "`commit-config`",
    "launcher": "`launcher`",
    "activate": "`activate`",
    "register": "`register`",
    "admin": "`admin`",
    "verify": "`verify`",
    "copy-partial": "`copy-partial`",
    "copy": "`copy`",
    "seal": "`seal`",
    "build": "`build`",
    "build-dir": "`build-dir`",
    "cleanup": "`cleanup`",
}
_DIR_SHORT = {
    "install": "/var/lib/df/install/",
    "accept": "/var/lib/df/install/accept/",
    "etc_df": "/etc/df/",
    "opt_df": "/opt/df/",
    "releases": "/opt/df/releases/",
    "release_root": "releases/<id>",
    "staging": "/opt/df/staging/",
    "admin": "/opt/df/admin/",
    "admin_python": "/opt/df/admin-python/",
    "opt_bin": "/opt/df/bin/",
    "local_bin": "/usr/local/bin/",
    "home_parent": "<родитель home>",
    "quarantine": "staging/.quarantine/",
}


def _is_pre(c: Cube) -> bool:
    return all(c.get(x) == PRE[x] for x in PRE)


def _render_cube(c: Cube) -> str:
    skip = {"P"} | (set(PRE) if _is_pre(c) else set())
    parts: list[str] = []
    for k in _ORDER:
        if k not in c or k in skip:
            continue
        v = sorted(c[k], key=list(DOMAINS[k]).index)
        n = _NAMES[k]
        if DOMAINS[k] == (0, 1):
            parts.append(f"`{n}`" if v == [1] else f"не `{n}`")
        elif len(v) == 1:
            parts.append(f"`{n} = {v[0]}`")
        else:
            parts.append(f"`{n} ∈ {{{', '.join(map(str, v))}}}`")
    return ", ".join(parts)


def render_table() -> str:
    """Spec 3.3 table, byte for byte as between the `install-table` markers."""
    lines = ["| Фаза | Условие | Действие | После |", "|---|---|---|---|"]
    for r in ROWS:
        if r.phase == "committed":
            cond = "нет `P`"
        else:
            cond = "; либо ".join(_render_cube(c) for c in r.cubes)
            if all(_is_pre(c) for c in r.cubes):
                cond = "до регистрации: " + cond
        after = ", ".join(f"`{p}`" for p in r.post) or "—"
        lines.append(f"| `{r.phase}` | {cond} | {_ACTION_TEXT[r.action]} | {after} |")
    u = UNKNOWN_ROW
    after = ", ".join(f"`{p}`" for p in u.post)
    lines.append(
        f"| `{u.phase}` | ни одна строка | {_ACTION_TEXT[u.action]} | {after} |"
    )
    return "\n".join(lines)


def render_settle() -> str:
    """Spec 3.3 item 1 table, byte for byte as between the `install-settle` markers."""
    when = "при захвате и в каждой итерации `drive`"
    lines = [
        "| Факт | Каталоги, закрепляемые до использования | Когда |",
        "|---|---|---|",
    ]
    for f in _ORDER:
        dirs = "; ".join(DIRS[d] for d in FACT_SOURCES[f])
        lines.append(f"| `{_NAMES[f]}` | {dirs} | {when} |")
    for f, ds in ACCOUNT_SOURCES.items():
        dirs = "; ".join(DIRS[d] for d in ds)
        lines.append(f"| {_ACCOUNT_NAMES[f]} | {dirs} | при захвате |")
    for d in SETTLE_FIRST:
        lines.append(
            f"| не факт: приёмник `cleanup_tree` | {NON_FACT_DIRS[d]} — **первым**, "
            f"до каталогов фактов | {when} |"
        )
    return "\n".join(lines)


def _render_steps(steps: Steps) -> str:
    parts: list[str] = []
    for op, arg, text in steps:
        if op in ("change", "builder") or text:
            parts.append(text)
        else:
            d = "родитель" if arg == "parent" else _DIR_SHORT[arg]
            parts.append(f"`sync_dir({d})`")
    return " → ".join(parts) or "ничего"


def render_protocols() -> str:
    """Spec 3.3 protocol table, as between the `install-protocols` markers."""
    lines = [
        "| Примитив | Шаги по порядку (после каждого изменения — точка сбоя) |",
        "|---|---|",
    ]
    for name in PROTOCOLS:
        vs = variants(name)
        if list(vs) == [None]:
            body = _render_steps(vs[None])
        else:
            body = "; ".join(
                f"вариант `{v}` ({VARIANT_WHEN[name][str(v)]}): {_render_steps(st)}"
                for v, st in vs.items()
            )
        lines.append(f"| `{name}` | {body} |")
    return "\n".join(lines)


def _render_call(guards: tuple[str, ...], call: str, target: str) -> str:
    what = f"`{call}`" + (f" {target}" if target else "")
    if not guards:
        return what
    return f"если {' и '.join(GUARD_TEXT[g] for g in guards)}: {what}"


def render_routes() -> str:
    """Spec 3.3 route tables, as between the `install-routes` markers."""
    lines = [
        (
            "| Маршрут | Шаги по порядку (условие проверяется перед шагом; ложно — "
            "шаг пропускается) |"
        ),
        "|---|---|",
    ]
    for name, steps in ROUTES.items():
        parts = [
            _render_call((guard,) if guard else (), call, target)
            for guard, call, target in steps
        ]
        lines.append(f"| `{name}` | " + " → ".join(parts) + " |")
    lines += [
        "",
        "| Примитив | Проверяет сам (иначе отказ) | Объекта нет |",
        "|---|---|---|",
    ]
    lines += [
        f"| `{k}` | {v} | {ABSENT_OK.get(k, 'отказ')} |" for k, v in REQUIRES.items()
    ]
    lines += ["", "| Вызов без протокола | Вид | Что меняет |", "|---|---|---|"]
    lines += [
        f"| `{k}` | {kind} | {eff or CALL_KINDS[kind]} |"
        for k, (kind, eff) in CALLS.items()
    ]
    return "\n".join(lines)


def render_route_calls() -> str:
    """The plan's Task 9 table `install-route-calls`: the full unfolding of every
    action route with guards and targets, repeats not collapsed (r10-N1)."""
    lines = ["| Действие | Вызовы по порядку (с условиями и объектами) |", "|---|---|"]
    for name in ACTION_ROUTES:
        parts = [
            _render_call(g, call, target) for g, call, target in unfold_route(name)
        ]
        lines.append(f"| `{name}` | " + " → ".join(parts) + " |")
    return "\n".join(lines)


check_data()
