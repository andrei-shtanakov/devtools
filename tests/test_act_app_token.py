"""Личность App и самоотчёт установок (спека §9.4, §6.2; Task 15a)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from governance import act_app_token  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

TRUST = {"approval-policy": 22, "df-trust": 11}
INST = "/app/installations?per_page=100&page={}"
REPOS = "/installation/repositories?per_page=100&page={}"


@pytest.fixture()
def key(tmp_path):
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        pk.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return path, pk.public_key()


class Http:
    """Ответы по `(метод, путь)`; репо установки — по её токену (`visible`)."""

    def __init__(
        self, routes: dict, visible: dict[int, list[int]] | None = None
    ) -> None:
        self.routes, self.visible, self.seen = routes, visible or {}, []

    def __call__(self, method: str, path: str, auth: str, body: dict | None = None):
        self.seen.append((method, path, auth))
        if (method, path) in self.routes:
            return self.routes[(method, path)]
        if path == REPOS.format(1) and auth.startswith("token ghs_"):
            ids = self.visible.get(int(auth.removeprefix("token ghs_")), [])
            return 200, {
                "total_count": len(ids),
                "repositories": [{"id": i} for i in ids],
            }
        return 502, None


def _installs(*pages: list[dict]) -> dict:
    """Маршруты списка установок по страницам и токены метаданных для каждой."""
    routes: dict = {
        ("GET", INST.format(n)): (200, page) for n, page in enumerate(pages, 1)
    }
    for page in pages:
        for item in page:
            routes[("POST", f"/app/installations/{item['id']}/access_tokens")] = (
                201,
                {"token": f"ghs_{item['id']}"},
            )
    return routes


def _repos(ids: list[int], total: object = None) -> dict:
    count = len(ids) if total is None else total
    body = {"total_count": count, "repositories": [{"id": i} for i in ids]}
    return {("GET", REPOS.format(1)): (200, body)}


IDENTITY_OK = {
    ("GET", "/app"): (200, {"slug": "df-probe-reviewer"}),
    ("GET", "/repos/o/p/installation"): (200, {"id": 9}),
    ("POST", "/app/installations/9/access_tokens"): (201, {"token": "ghs_x"}),
}


def test_identity_uses_jwt_not_gh_user(key) -> None:
    path, public = key
    http = Http(dict(IDENTITY_OK))
    got = act_app_token.app_identity(5285716, path, "o/p", http=http, now=1_700_000_000)
    assert got == ("df-probe-reviewer[bot]", "ghs_x")
    # Подпись проверяется; срок — отдельно от часов прогона (ревизия b1, B1-16).
    claims = jwt.decode(
        http.seen[0][2].removeprefix("Bearer "),
        public,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "5285716"
    assert (claims["iat"], claims["exp"]) == (1_700_000_000 - 60, 1_700_000_000 + 540)
    assert not any(p == "/user" for _, p, _ in http.seen)
    # Токен — на установку целевого репо, без сужения тела запроса.
    assert http.seen[1:] == [
        ("GET", "/repos/o/p/installation", http.seen[0][2]),
        ("POST", "/app/installations/9/access_tokens", http.seen[0][2]),
    ]


def test_identity_unreadable_is_unavailable(key) -> None:
    """B3-8: ответ `/app` не принят — отказ этой проверки; установка и токен отвечают
    успешно, чтобы отключённая проверка не была подменена следующей."""
    http = Http({**IDENTITY_OK, ("GET", "/app"): (403, {"message": "x"})})
    got = act_app_token.app_identity(1, key[0], "o/p", http=http, now=0)
    assert isinstance(got, Unavailable) and got.reason == "GET /app: HTTP 403"


@pytest.mark.parametrize(
    "body",
    [{}, {"slug": ""}, {"slug": 7}, {"slug": "a b"}, {"slug": "a\nghs_y"}, []],
    ids=["missing", "empty", "not_str", "space", "newline", "not_object"],
)
def test_identity_bad_slug_is_unavailable(key, body) -> None:
    """Slug — одна строка stdout без пробелов: иначе обвязка разобрала бы логин
    и токен не из тех строк."""
    http = Http({**IDENTITY_OK, ("GET", "/app"): (200, body)})
    got = act_app_token.app_identity(1, key[0], "o/p", http=http, now=0)
    assert isinstance(got, Unavailable) and got.reason == "GET /app: HTTP 200"


def test_installation_unreadable_is_unavailable(key) -> None:
    """Установка App на целевой репо не прочитана — отказ этой проверки."""
    http = Http({**IDENTITY_OK, ("GET", "/repos/o/p/installation"): (404, None)})
    got = act_app_token.app_identity(1, key[0], "o/p", http=http, now=0)
    assert (
        isinstance(got, Unavailable) and got.reason == "установка App на o/p: HTTP 404"
    )


@pytest.mark.parametrize("ident", ["9", True, None], ids=["str", "bool", "none"])
def test_installation_id_not_int_is_unavailable(key, ident) -> None:
    http = Http(
        {**IDENTITY_OK, ("GET", "/repos/o/p/installation"): (200, {"id": ident})}
    )
    got = act_app_token.app_identity(1, key[0], "o/p", http=http, now=0)
    assert isinstance(got, Unavailable) and "установка App на o/p" in got.reason


@pytest.mark.parametrize(
    "answer",
    [(201, {"token": ""}), (201, {}), (200, {"token": "ghs_x"}), (403, None)],
    ids=["empty", "missing", "not_201", "forbidden"],
)
def test_identity_token_refused_is_unavailable(key, answer) -> None:
    routes = {**IDENTITY_OK, ("POST", "/app/installations/9/access_tokens"): answer}
    got = act_app_token.app_identity(1, key[0], "o/p", http=Http(routes), now=0)
    assert isinstance(got, Unavailable)
    assert got.reason == f"токен установки 9: HTTP {answer[0]}"


@pytest.mark.parametrize("repo", ["o", "o/p/q", "../o/p", "o/p?x=1", "o /p"])
def test_identity_bad_repo_no_request(key, repo) -> None:
    """Slug целевого репо подставляется в путь: не `owner/name` — без запросов."""
    http = Http(dict(IDENTITY_OK))
    got = act_app_token.app_identity(1, key[0], repo, http=http, now=0)
    assert isinstance(got, Unavailable) and http.seen == []


def test_installation_on_trust_repo_is_violation(key) -> None:
    http = Http({**_installs([{"id": 9}]), **_repos([7, 22])})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == ("approval-policy", "установка App 9 видит approval-policy (22)")


def test_installations_clean(key) -> None:
    http = Http({**_installs([{"id": 9}]), **_repos([7])})
    assert (
        act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0) is None
    )


def test_installations_metadata_token_is_narrowed(key) -> None:
    """Токен на чтение списка репо установки — только `metadata: read`."""
    seen: list[dict | None] = []
    http = Http({**_installs([{"id": 9}]), **_repos([7])})

    def spy(method: str, path: str, auth: str, body: dict | None = None):
        if method == "POST":
            seen.append(body)
        return http(method, path, auth, body)

    act_app_token.installations_refusal(1, key[0], TRUST, http=spy, now=0)
    assert seen == [{"permissions": {"metadata": "read"}}]


def test_installation_token_refused_is_unavailable(key) -> None:
    """b4: токен метаданных установки не выдан — список не проверен, не «чисто»."""
    http = Http({("GET", INST.format(1)): (200, [{"id": 9}])})  # POST → 502
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and got.reason == "токен установки 9: HTTP 502"


def test_violation_on_second_installations_page(key) -> None:
    """B1-12: установка на репо доверия на второй странице не теряется."""
    first = [{"id": 1000 + n} for n in range(100)]
    http = Http(_installs(first, [{"id": 9}]), visible={9: [11]})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == ("df-trust", "установка App 9 видит df-trust (11)")


def test_violation_after_clean_installation(key) -> None:
    """Чистая первая установка не завершает проверку: смотрятся все."""
    http = Http(_installs([{"id": 8}, {"id": 9}]), visible={8: [7], 9: [22]})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == ("approval-policy", "установка App 9 видит approval-policy (22)")


def test_full_last_page_requires_empty_next(key) -> None:
    """Ровно 100 установок: полнота — по пустой следующей странице, не по догадке."""
    page = [{"id": n} for n in range(1, 101)]
    http = Http(_installs(page))  # страница 2 не отвечает (502)
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and "стр. 2" in got.reason
    http = Http({**_installs(page), ("GET", INST.format(2)): (200, [])})
    assert (
        act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0) is None
    )


def test_repositories_second_page_violation(key) -> None:
    """B1-12: и список репо установки читается до неполной страницы."""
    first = {"total_count": 101, "repositories": [{"id": 1000 + n} for n in range(100)]}
    second = {"total_count": 101, "repositories": [{"id": 11}]}
    http = Http(
        {
            **_installs([{"id": 9}]),
            ("GET", REPOS.format(1)): (200, first),
            ("GET", REPOS.format(2)): (200, second),
        }
    )
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == ("df-trust", "установка App 9 видит df-trust (11)")


@pytest.mark.parametrize(
    "total", [150, True, "1", None], ids=["more", "bool", "str", "none"]
)
def test_repositories_total_mismatch_is_unavailable(key, total) -> None:
    routes = {**_installs([{"id": 9}]), **_repos([7], total=150)}
    routes[("GET", REPOS.format(1))][1]["total_count"] = total
    got = act_app_token.installations_refusal(
        1, key[0], TRUST, http=Http(routes), now=0
    )
    assert isinstance(got, Unavailable) and "total_count" in got.reason


def test_too_many_installation_pages_is_unavailable(key, monkeypatch) -> None:
    monkeypatch.setattr(act_app_token, "MAX_PAGES", 2)
    full = [{"id": n} for n in range(100)]
    http = Http(
        {("GET", INST.format(1)): (200, full), ("GET", INST.format(2)): (200, full)}
    )
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and "больше 2 страниц" in got.reason


@pytest.mark.parametrize("item", [{"id": "9"}, {"id": True}, {}, 9], ids=str)
def test_installation_without_int_id_is_unavailable(key, item) -> None:
    http = Http({("GET", INST.format(1)): (200, [item])})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == Unavailable("установка без id")


@pytest.mark.parametrize(
    "answer", [(200, {"x": []}), (500, None), (200, None)], ids=["shape", "5xx", "null"]
)
def test_installations_list_unreadable_is_unavailable(key, answer) -> None:
    got = act_app_token.installations_refusal(
        1, key[0], TRUST, http=Http({("GET", INST.format(1)): answer}), now=0
    )
    assert isinstance(got, Unavailable) and "стр. 1" in got.reason


# --- секреты не уходят в тексты отказов -------------------------------------


def test_repos_failure_reason_has_no_token(key) -> None:
    http = Http({**_installs([{"id": 9}]), ("GET", REPOS.format(1)): (502, None)})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and "репо установки 9" in got.reason
    assert "ghs_" not in got.reason


def test_bad_key_reason_has_no_key_bytes(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pem"
    bad.write_text(
        "-----BEGIN PRIVATE KEY-----\nU0VDUkVUS0VZQllURVM=\n-----END PRIVATE KEY-----\n"
    )
    http = Http(dict(IDENTITY_OK))
    for got in (
        act_app_token.app_identity(1, bad, "o/p", http=http, now=0),
        act_app_token.installations_refusal(1, bad, TRUST, http=http, now=0),
    ):
        assert isinstance(got, Unavailable) and "не подписывает JWT" in got.reason
        assert "U0VDUkVU" not in got.reason and "BEGIN" not in got.reason
    assert http.seen == []


def test_missing_key_is_unavailable(tmp_path: Path) -> None:
    got = act_app_token.app_identity(
        1, tmp_path / "nope.pem", "o/p", http=Http({}), now=0
    )
    assert isinstance(got, Unavailable) and "не прочитан" in got.reason


# --- HTTP-шов и CLI ----------------------------------------------------------


def test_http_does_not_follow_redirects() -> None:
    """B1-12: 3xx возвращается кодом — запрос с Authorization на новый адрес не идёт."""
    handler = act_app_token._NoRedirect()
    req = urllib.request.Request(
        "https://api.github.com/app", headers={"Authorization": "Bearer x"}
    )
    assert (
        handler.redirect_request(
            req, None, 302, "Found", {}, "https://evil.example/app"
        )
        is None
    )
    assert any(
        isinstance(h, act_app_token._NoRedirect) for h in act_app_token._OPENER.handlers
    )


class _Answer(io.BytesIO):
    def __init__(self, status: int, body: object) -> None:
        super().__init__(json.dumps(body).encode())
        self.status = status


class _Opener:
    """Подмена `_OPENER`: ответы по пути запроса; запросы — для сверки."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes, self.requests = routes, []

    def open(self, req: urllib.request.Request, timeout: float) -> object:
        self.requests.append(req)
        path = req.full_url.removeprefix(act_app_token.API)
        got = self.routes.get(path)
        if isinstance(got, Exception):
            raise got
        if got is None:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)  # type: ignore[arg-type]
        status, body = got  # type: ignore[misc]
        return _Answer(status, body)


def test_main_prints_login_and_token(key, monkeypatch, capsys) -> None:
    opener = _Opener(
        {
            "/app": (200, {"slug": "df-rev"}),
            "/repos/o/p/installation": (200, {"id": 9}),
            "/app/installations/9/access_tokens": (201, {"token": "ghs_tok"}),
        }
    )
    monkeypatch.setattr(act_app_token, "_OPENER", opener)
    argv = ["--app-id", "5", "--key", str(key[0]), "--repo", "o/p"]
    assert act_app_token.main(argv) == 0
    out = capsys.readouterr()
    assert out.out == "df-rev[bot]\nghs_tok\n" and out.err == ""
    assert all(
        r.full_url.startswith("https://api.github.com/") for r in opener.requests
    )
    assert opener.requests[0].get_header("Authorization").startswith("Bearer ")


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("boom"), ValueError("bad json")],
    ids=["transport", "decode"],
)
def test_main_failure_is_2_without_secrets(key, monkeypatch, capsys, failure) -> None:
    opener = _Opener(
        {
            "/app": (200, {"slug": "df-rev"}),
            "/repos/o/p/installation": (200, {"id": 9}),
            "/app/installations/9/access_tokens": failure,
        }
    )
    monkeypatch.setattr(act_app_token, "_OPENER", opener)
    argv = ["--app-id", "5", "--key", str(key[0]), "--repo", "o/p"]
    assert act_app_token.main(argv) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "act_app_token: токен установки 9: HTTP 0\n"
    assert "PRIVATE" not in out.err and "Bearer" not in out.err


def test_main_http_error_code(key, monkeypatch, capsys) -> None:
    monkeypatch.setattr(act_app_token, "_OPENER", _Opener({}))
    argv = ["--app-id", "5", "--key", str(key[0]), "--repo", "o/p"]
    assert act_app_token.main(argv) == 2
    assert capsys.readouterr().err == "act_app_token: GET /app: HTTP 404\n"


@pytest.mark.slow
def test_runs_as_isolated_file_with_jwt_in_governance(tmp_path: Path, key) -> None:
    """Боевая форма запуска настоящим `uv` (офлайн, из кэша): `pyjwt[crypto]` —
    в группе governance и подписывает RS256; отказ — код 2, stdout пуст."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv"),
        "UV_OFFLINE": "1",
    }
    root = Path(act_app_token.__file__).resolve().parent.parent
    form = [
        "uv", "run", "--frozen", "--exact", "--group", "governance", "--no-config",
        "--no-env-file", "python", "-I", str(root / "governance/act_app_token.py"),
    ]  # fmt: skip
    done = subprocess.run(
        [*form, "--app-id", "1", "--key", str(key[0]), "--repo", "o/p q"],
        cwd=root, env=env, capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    assert done.returncode == 2, done.stderr
    assert done.stdout == "" and "act_app_token: репо 'o/p q'" in done.stderr
    probe = subprocess.run(
        [*form[:-1], "-c", f"import jwt; jwt.encode({{}}, {key[0].read_bytes()!r}, 'RS256')"],
        cwd=root, env=env, capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    assert probe.returncode == 0, probe.stderr


# --- сквозной App → act_premerge → load_trust (ревизия b1, B1-11) -------------


def _app_world(monkeypatch, key, env: dict[str, str], visible: dict[int, list[int]]):
    from governance import act_premerge, act_trust
    from tests.test_act_premerge import World

    world = World(monkeypatch)
    world.real_perimeter()
    world.creds = act_trust.credentials(
        env, agent_dir=Path("/x/review"), merge_dir=Path("/x/merge")
    )
    world.perms = {"env:GH_TOKEN": {}}  # токен установки: `permissions` в ответе нет
    world.installations = act_premerge.app_installations(
        1, key[0], http=Http(_installs([{"id": 9}]), visible)
    )
    return world


def test_app_mode_premerge_clean(monkeypatch, key) -> None:
    """Запуск с `GH_TOKEN=""`: производный токен не держится и не самоотчитывается."""
    world = _app_world(monkeypatch, key, {"GH_TOKEN": ""}, {9: [7]})
    assert world.run() == 0 and world.journal.recorded == []


def test_app_token_leaked_into_env_is_unset(monkeypatch, key, capsys) -> None:
    """Двойник: токен установки в `GH_TOKEN` — самоотчёт невозможен (§6.2), 2."""
    world = _app_world(monkeypatch, key, {"GH_TOKEN": "ghs_inst"}, {9: [7]})
    assert world.run() == 2 and world.journal.recorded == []
    err = capsys.readouterr().err
    assert "самоотчёт env:GH_TOKEN" in err and "ghs_inst" not in err


def test_app_installation_on_policy_repo_registered(monkeypatch, key) -> None:
    world = _app_world(monkeypatch, key, {"GH_TOKEN": ""}, {9: [22]})
    assert world.run() == 2
    assert [r[0] for r in world.journal.recorded] == ["approval-policy"]


def test_app_installation_on_df_trust_repo_registered(monkeypatch, key) -> None:
    world = _app_world(monkeypatch, key, {"GH_TOKEN": ""}, {9: [11]})
    assert world.run() == 2
    assert [r[0] for r in world.journal.recorded] == ["df-trust"]
