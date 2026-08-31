"""Фабрики минимального профиля и бандла для characterization-тестов steward.

Профиль — урезанный team-exp: requirements -> behaviour-spec. Файлы бандла несут
frontmatter с spec_stage (узел определяется по нему, не по имени файла — спека §3).

Открытия о формате steward (пин 4a1c7c44a85accf609b40cb14115eccefb26f6c2),
зафиксированные здесь после первого честного прогона характеризационных тестов
(brief предполагал именно это — черновик фикстур не совпал с форматом steward):

1. Профиль (``steward.graph.load_profile_data``) — верхнеуровневый ключ списка
   узлов называется ``artifacts``, НЕ ``nodes``; обязателен ещё и строковый
   ``profile: <имя>``. ``solo_auto_approve`` необязателен (по умолчанию False).
2. Каталог ролей (``steward.roles.load_roles_catalog``) требует ровно
   ``version`` (int >= 1), ``slug_pattern`` (regex-строка) и
   ``roles: [{slug, display}]`` — поля называются ``slug``/``display``, а не
   ``id``/``title``; лишние top-level или per-role ключи — фатальная ошибка
   (fail-closed).
3. Тело behaviour-spec/requirements — не свободный текст с ``Trace:``/
   ``Checked-by: {...}``, а узкий DSL (``steward.gatecheck.behaviour``):
   - определение требования — заголовок ``#### FR-NN: Title`` (ровно 4 `#`,
     также подходит ``NFR-NN``); приоритет — ОТДЕЛЬНАЯ строка вида
     ``**Priority**: Must`` (``Must|Should|Could|Won't``) где-то в блоке до
     следующего заголовка;
   - сценарий — заголовок ``#### BEH-NN: Title``; трейс — инлайн code-span
     `` `traces: [FR-NN, ...]` `` в блоке сценария (frontmatter'ный
     ``traces_to`` — это отдельный, node-уровневый гейт ``GC-TRACE`` в
     ``checks.py``, к behaviour-DSL отношения не имеет);
   - check-binding — строка, начинающаяся ровно с ``- **checked_by**:``,
     дальше — только backtick key:value спаны, например
     `` `status: planned` `kind: e2e` `owner: qa` `target: tests/x.py` ``.
     ``kind`` обязан быть одним из {atp, contract, integration, e2e, manual}
     — ``pytest`` (как было в черновике брифа) не входит в множество и
     дал бы GC-CHECK-PLANNED вместо честного прохождения.
4. Подтверждено без изменений: ``Artifact.path`` — уже ``str`` (POSIX-путь
   относительно бандла), ``ArtifactMeta.upstream_hashes`` — ``tuple[tuple[str,
   str], ...]``, файл без ``spec_stage``/frontmatter не попадает в список
   artifacts вообще (a `continue`, не запись с ``node_id=None``).

BEHAVIOUR_BAD_MD намеренно не трейсит ничего: это даёт GC-BEH-TRACE и
GC-BEH-COVERAGE, но НЕ GC-CHECK-PLANNED — тот гейт смотрит только сценарии,
которые трейсят Must-приоритетный FR/NFR (``_check_planned`` пропускает
сценарий без единого traces-ref). Множество gate_id в тесте — подмножество,
поэтому это ожидаемо и покрытие остаётся честным.

Профиль несёт третий узел, ``tasks``, с ``delegate: spec-runner`` — он живёт
вне бандла по построению (см. ``steward/profiles/team-exp.yaml`` в проде) и
нужен, чтобы characterization/candidate-тесты видели реальный delegate-узел,
а не только два обязательных (финальное ревью, находка I-5).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PROFILE_YAML = """\
profile: mini
solo_auto_approve: true
artifacts:
  - {id: requirements, template: requirements.md, owner_role: analysts}
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: analysts
    upstream: [requirements]
  - id: tasks
    owner_role: analysts
    delegate: spec-runner
"""

ROLES_YAML = """\
version: 1
slug_pattern: "^[a-z][a-z-]*$"
roles:
  - {slug: analysts, display: Analysts}
"""

REQUIREMENTS_MD = """\
---
spec_stage: requirements
status: approved
owner_role: analysts
---
# Requirements

#### FR-01: Список
**Priority**: Must

Пользователь видит список.
"""

BEHAVIOUR_OK_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: analysts
traces_to: [requirements]
upstream_hashes:
  requirements: "{req_hash}"
---
# Behaviour

#### BEH-01: Просмотр списка
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: e2e` `owner: qa` `target: tests/test_x.py`
"""

BEHAVIOUR_BAD_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: analysts
---
# Behaviour

#### BEH-01: Без трейса и без checked_by

Сценарий без Trace и без checked_by.
"""

BEHAVIOUR_NO_CHECKED_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: analysts
traces_to: [requirements]
---
# Behaviour

#### BEH-01: Трейсит Must-FR, но без checked_by
`traces: [FR-01]`

Сценарий трейсит Must-приоритетный FR-01, но не несёт checked_by-биндинг —
единственный триггер GC-CHECK-PLANNED (финальное ревью, находка I-1).
"""

# Без ``upstream_hashes`` и без парного 10-requirements.md в бандле: свежий
# behaviour-spec на S3, каким его штампует автор до approval-пина steward.
# Триггерит candidate-срез blocked-статус (финальное ревью, находка C-1).
BEHAVIOUR_NO_UPSTREAM_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: analysts
traces_to: [requirements]
---
# Behaviour

#### BEH-01: Просмотр списка
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: e2e` `owner: qa` `target: tests/test_x.py`
"""


def blob_hash(text: str) -> str:
    """git hash-object содержимого — чистым stdlib, git не нужен."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00%s" % (len(data), data)).hexdigest()


def make_profile(tmp_path: Path) -> Path:
    prof_dir = tmp_path / "profiles"
    prof_dir.mkdir(exist_ok=True)
    (prof_dir / "roles.yaml").write_text(ROLES_YAML)
    profile = prof_dir / "mini.yaml"
    profile.write_text(PROFILE_YAML)
    return profile


def make_bundle_with_behaviour(tmp_path: Path, behaviour_text: str) -> Path:
    """Бандл requirements + произвольный текст behaviour-spec (без notes.md)."""
    bundle = tmp_path / "spec"
    bundle.mkdir(exist_ok=True)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(behaviour_text)
    return bundle


def make_bundle(tmp_path: Path, *, behaviour_ok: bool) -> Path:
    text = (
        BEHAVIOUR_OK_MD.format(req_hash=blob_hash(REQUIREMENTS_MD))
        if behaviour_ok
        else BEHAVIOUR_BAD_MD
    )
    bundle = make_bundle_with_behaviour(tmp_path, text)
    (bundle / "notes.md").write_text("без frontmatter — должен пройти насквозь\n")
    return bundle
