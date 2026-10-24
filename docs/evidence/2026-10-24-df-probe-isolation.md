# Изоляция пробы моделей на настоящих харнессах — 2026-10-24

Task 11, Step 3 (11-B) плана этапа 1 df-installer; спека
`docs/superpowers/specs/2026-10-13-df-installer-design.md` §3.6 «Изоляция пробы» и
«Допуск». Тесты — `tests/df/test_probe_isolation.py` (маркер `harness`), заглушка —
`tests/df/harness_stub.py`, мир сценариев — `tests/df/harness_world.py`.

**Итог: ни одна версия не прошла. Обе записи `HARNESS_PROFILES` закреплены с
`isolation_verified=False`; пробы обеих версий — «не поддерживается: изоляция не
подтверждена».** Общая причина — серверная доставка managed-настроек, для которой нет
`Exclusion`, действующего при любом способе авторизации (R66: законный исход). У codex
есть и собственные провалы изоляции (ниже).

## Что проверялось

| харнесс | версия | путь | SHA-256 | формат |
|---|---|---|---|---|
| claude | 2.1.309 | `/Users/Andrei_Shtanakov/.local/share/claude/versions/2.1.309` | `5eadc0009cdcdbc0d06e58110d7b2baf88ced6088b1c33a60a21217fe410619c` | Mach-O arm64 (нативная сборка) |
| codex | 0.163.0 | `/opt/homebrew/Caskroom/codex/0.163.0/bin/codex` | `8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31` | Mach-O arm64 (Homebrew cask) |

Платформа: macOS 26.7.1 (25G241), arm64. Linux-сборки и их SHA — вне этого шага (Task 13).

## Как запускалось (R67)

- Только текущий пользователь, без sudo и `run_as`.
- Каждый запуск — под `/usr/bin/sandbox-exec` с профилем: исходящая сеть только на
  `localhost`, запрещены mach-сервисы связки ключей (`com.apple.SecurityServer`,
  `com.apple.securityd`, `com.apple.security.agent`, `com.apple.secd`) и чтение
  `~/.claude`, `~/.claude.json`, `~/.codex`, `~/Library/Keychains` владельца. Оба харнесса
  под ним работают. Проверено, что профиль режет внешнюю сеть: codex с закреплённым argv
  сам пытался открыть `wss://api.openai.com/v1/responses` и получил `Operation not
  permitted` — так и выяснилось, что `OPENAI_BASE_URL` им не читается.
- Окружение задаётся целиком (ничего не наследуется): то, что строит `probe._form`
  (`HOME` — свежий временный каталог, `PATH` — каталог цели + `/usr/bin:/bin`,
  `LANG=C.UTF-8`), плюс только тестовые добавки: claude — `ANTHROPIC_API_KEY=<фейковый
  ключ>` и `ANTHROPIC_BASE_URL=<заглушка>`; codex — фейковый ключ в `~/.codex/auth.json`
  роли и `-c openai_base_url="<заглушка>/v1"` перед промптом.
- argv — ровно `profile.argv` закреплённого профиля (через `probe._form`), кроме
  названных тестовых добавок (`-c openai_base_url`, `-c chatgpt_base_url`, `--debug-file`)
  и сознательно удалённого флага в «двойниках».
- Заглушка (`127.0.0.1`, эфемерный порт) записывает только метод, путь, поле `model`,
  число инструментов, поле effort/reasoning, какие из тестовых маркеров встретились в теле,
  и два булевых признака: предъявлен ли фейковый ключ и предъявлены ли **иные**
  учётные данные. Значения заголовков и тела не хранятся и не печатаются. Ни в одном
  закреплённом запуске иных учётных данных не было (`leaked() == False`).
- Платных вызовов нет: все запросы — к заглушке; внешняя сеть закрыта профилем.

## Итоговый argv

claude 2.1.309 (`probe.claude_argv`):

```
<h> -p --model <m> --output-format json --max-turns 1 --setting-sources "" \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools "" \
    [--effort <e>] -- "Reply with the single word OK."
```

Отличие от кандидата плана: `--` перед промптом. `--tools <tools...>` вариадический:
без `--` пустой список инструментов проглатывает промпт («Input must be provided either
through stdin or as a prompt argument»), тест
`test_claude_prompt_after_empty_tools_needs_separator`. `--tools=` тоже работает, но `--`
закрывает и будущие вариадические флаги. `--setting-sources ""` принимается и отключает
user, project и local настройки (см. сценарии).

codex 0.163.0 (`probe.codex_argv`):

```
<h> exec --ignore-user-config --ignore-rules -c cli_auth_credentials_store="file" \
    -c mcp_servers={} -c project_doc_max_bytes=0 -m <m> --json --skip-git-repo-check \
    --ephemeral --sandbox read-only [-c model_reasoning_effort="<e>"] "Reply with the single word OK."
```

Отличие от кандидата: `-c project_doc_max_bytes=0` — без него `AGENTS.md` проектного
корня (родитель cwd с `.git`) попадает в запрос.

## Сценарии и результаты

Посеяно в `HOME` роли и в родителе `cwd` (`project/`, cwd = `project/sub/cwd`, в
`project/` есть `.git`): sentinel-hooks, MCP-серверы, конфиг с иной моделью
(`df-sentinel-model`), effort и провайдером/`env`-переадресацией base URL на путь
`/<scope>-settings-env`, `apiKeyHelper`, memory/инструкции с маркерами. Срабатывание —
появление файла в каталоге sentinels; попадание инструкций — маркер в теле запроса.

### claude 2.1.309

| сценарий | результат | тест |
|---|---|---|
| закреплённый argv, всё посеяно (user `~/.claude/settings.json` с hooks/model/effortLevel/env/apiKeyHelper, `~/.claude/CLAUDE.md`, user skill и agent, `~/.claude.json` с MCP; project `.claude/settings.json`, `settings.local.json`, `CLAUDE.md`, `.mcp.json`) | sentinels: нет; один запрос `POST /v1/messages`, `model = claude-opus-5-5` (из ModelChoice), `output_config.effort = high`, инструментов 0, маркеров 0, фейковый ключ, иных данных нет; `parse_output` → ok, `actual_model = claude-opus-5-5` | `test_claude_pinned_invocation_is_isolated` |
| двойник без `--setting-sources ""` | срабатывают user и local hooks (SessionStart, UserPromptSubmit, Stop, SessionEnd), `apiKeyHelper`, запрос уходит по `env`-переадресации (`/local-settings-env/v1/messages`), оба маркера в теле — sentinels живые, флаг несущий | `test_claude_setting_sources_is_load_bearing` |
| контроль без всех флагов изоляции (разведка) | дополнительно: модель из настроек (`df-sentinel-model`), effort `low`, 12 инструментов, user и project MCP стартуют | — (журнал разведки) |
| проектный конфиг в родителе cwd | не влияет (входит в первый сценарий); `probe_cwd_problems` для нейтрального `probe_cwd` пуст | первый сценарий; `test_neutral_probe_cwd_has_no_problems` |
| `--tools ""` без `--` | промпт проглочен, запуск не состоялся | `test_claude_prompt_after_empty_tools_needs_separator` |

Наблюдение: claude переписывает некоторые model ID (запрошенный `claude-opus-4-1` ушёл как
`claude-opus-5-5`); `modelUsage` ответа называет фактически отправленный — `actual_model`
записи это и фиксирует.

### codex 0.163.0

| сценарий | результат | тест |
|---|---|---|
| закреплённый argv, посеяно (user `~/.codex/config.toml` с model/effort/provider/MCP/notify/`[hooks]`, `~/.codex/hooks.json`, `~/.codex/AGENTS.md`; project `.codex/config.toml`, `.codex/hooks.json`, `AGENTS.md`); ключ — в `~/.codex/auth.json` | sentinels: нет (hooks toml/json, MCP, notify); один `POST /v1/responses`, `model = gpt-5.5`, `reasoning.effort = high`, проектный маркер не дошёл, фейковый ключ из файла; `parse_output` → ok | `test_codex_pinned_invocation` |
| **провал:** user-инструкции и инструменты | маркер `~/.codex/AGENTS.md` роли **доходит до запроса** (флага, отключающего глобальные инструкции `CODEX_HOME`, в 0.163.0 нет; `--ignore-user-config` касается только `config.toml`); модели предлагаются встроенные инструменты (10; с `--disable` двадцати фич и `-c web_search="disabled"` — всё ещё 2) | `test_codex_user_instructions_and_tools_reach_the_request` |
| двойник без `--ignore-user-config` | стартуют user MCP и notify, запрос уходит к провайдеру из user-конфига (`/user-provider/v1/responses`) — sentinels живые, флаг несущий | `test_codex_ignore_user_config_is_load_bearing` |
| user hooks (разведка) | без `--ignore-user-config` и с `--dangerously-bypass-hook-trust` `hooks.json` и `[hooks]` из `CODEX_HOME` срабатывают; без `--ignore-user-config`, но без обхода доверия — не срабатывают (недоверенные hooks не исполняются), а user MCP и notify стартуют | — |
| `-c mcp_servers={}` (разведка) | **не** убирает MCP-серверы user-конфига (пустая таблица сливается, а не заменяет); user MCP отключает именно `--ignore-user-config`. Флаг оставлен в argv как безвредный, несущим он не является | — |
| подмена `auth.json` | `observe_auth` до/после запуска — `tracked`, файл запуском не изменён; после подмены содержимого отпечаток (sha256) другой | `test_codex_auth_file_is_the_fingerprinted_source` |
| нет `auth.json` | запрос уходит без учётных данных (ни фейковых, ни иных) | `test_codex_without_auth_file_presents_no_credential` |

Про keyring: хранилище закреплено `-c cli_auth_credentials_store="file"`; ключ при
наличии файла берётся из него, без файла запрос идёт без ключа. Доступ к связке ключей
в песочнице закрыт профилем, поэтому **отсутствие попытки обратиться к keyring
поведенчески не доказано** — доказано, что файл является источником, а связка ключей
этому запуску недоступна. Прогон без запрета связки ключей не делался намеренно: он мог
бы прочитать настоящие учётные данные владельца (R67).

### События codex (M1 из 11-A)

Последовательности сняты с бинаря и закреплены фикстурами
`tests/df/fixtures/probe/codex-{ok,http500,neterr}.jsonl` (и claude:
`claude-{ok,error,http500}.json`, ошибки — при `CLAUDE_CODE_MAX_RETRIES=0`):

- успех: `thread.started`, `turn.started`, **верхнеуровневые `error` «Reconnecting… n/5»**
  (неудачный WebSocket, откат на HTTPS), `item.completed` (`type: error` — предупреждение),
  `item.completed` (`agent_message`), `turn.completed` — последним;
- HTTP 500: ретраи `error`, финальный `error` и `turn.failed` последним;
- сеть недоступна: бесконечные `error` «Reconnecting… waiting for network», терминального
  события нет — проба упирается в таймаут (`доступ не подтверждён: таймаут`).

Прежнее правило 11-A «любое `error` — провал» признало бы реальный успех провалом.
Новое правило `_parse_codex`: ровно одно терминальное событие (`turn.completed` или
`turn.failed`), и оно последнее; успех — только `turn.completed`; без терминального
события — провал с последним сообщением `error` (или «ответ не разобран»). Это вывод из
снятых последовательностей, а не допущение «последнее событие — успех»: событие после
терминального или два терминальных — «не разобран» (тесты
`test_parse_codex_*` в `tests/df/test_probe.py`).

## Managed-источники: замкнутый список и способ

Создавать файлы в `/Library` и `/etc` нельзя (без root), поэтому наблюдаемость
установлена по коду бинаря (строки и встроенный JS/Rust-код), по debug-журналу claude и по
поведению; способ указан для каждого источника. На этой машине ни одного из них нет.

### claude 2.1.309, macOS — `probe.CLAUDE_MANAGED_MACOS`

| вид | локатор | наблюдаем | как установлено |
|---|---|---|---|
| file | `/Library/Application Support/ClaudeCode/managed-settings.json` | да | код: корень `Ey()` = этот каталог на macOS, `fe(N_(),"managed-settings.json")`; debug-журнал закреплённого запуска: чтение этого пути до запроса |
| file | `…/ClaudeCode/managed-settings.d` (каталог drop-in) | да (присутствие = отказ) | код: `yy(N_(),"managed-settings.d")` |
| file | `…/ClaudeCode/managed-mcp.json` | да | код: `ooe(N_(),"managed-mcp.json")` |
| file | `…/ClaudeCode/CLAUDE.md` | да | код: `et(N_(),"CLAUDE.md")` |
| file | `…/ClaudeCode/.claude` (skills, agents, commands, output-styles, rules) | да (присутствие = отказ) | код: `Np(N_(),".claude","skills")` и др.; debug-журнал: «Loading skills from: managed=…/.claude/skills», обход `.claude/{agents,commands,output-styles}` |
| mdm | `/Library/Managed Preferences/com.anthropic.claudecode.plist` | да | код: MDM читается **файлом** через `/usr/bin/plutil -convert json` (не CFPreferences); debug-журнал: «MDM settings load completed» |
| mdm | `/Library/Managed Preferences/df-exec/com.anthropic.claudecode.plist`, `…/df-review/…` | да | код: путь `/Library/Managed Preferences/${os.userInfo().username}/…` — имя эффективного пользователя, то есть роли |
| server | `api.anthropic.com/api/claude_code/settings` | нет | код: `${BASE_API_URL}/api/claude_code/settings`; поведение: см. ниже |
| server | `api.anthropic.com/api/claude_code/policy_limits` | нет | debug-журнал: «Policy limits: fetch failed (network) from api.anthropic.com» с повторами |

Каталоги (`managed-settings.d`, `.claude`) записаны источниками вида `file`: отсутствие
допускается, присутствие — отказ («не обычный файл»), пока профиль не научится их
подтверждать. «Policy helper» настраивается только из managed-источников и отдельным
источником не считается.

**Серверная доставка — исключения нет.** Правило допустимости в коде (`De()`):
неприменимо лишь при стороннем провайдере (Bedrock/Vertex/Foundry), нестандартном
`ANTHROPIC_BASE_URL`, точке входа-песочнице (`CLAUDE_CODE_ENTRYPOINT=local-agent` и т.п.)
или «герметичной удалённой сессии»; иначе применимо при OAuth-подписке Team/Enterprise и
**при наличии API-ключа**. Поведенчески (`test_claude_server_managed_settings_are_not_excluded`):
закреплённый argv с API-ключом и base URL, признанным first-party
(`_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL=1`, только в тесте), даёт в debug-журнале
«remote managed settings eligible» и попытки забрать policy limits. Кандидаты на
`Exclusion` отвергнуты: смена провайдера или base URL меняет сам проверяемый вызов; подмена
`CLAUDE_CODE_ENTRYPOINT` — выдача себя за другой продукт ради обхода политики организации,
а не исключение источника; `--bare` в списке условий не участвует. Значит, `excluded_by =
None`, и профиль не допускается даже при `isolation_verified=True`
(`test_verified_flag_alone_would_not_admit`).

### codex 0.163.0, macOS — `probe.CODEX_MANAGED_MACOS`

| вид | локатор | наблюдаем | как установлено |
|---|---|---|---|
| file | `/etc/codex/config.toml` (system-слой) | да | строки: `/etc/codex/config.toml`, слои `system`, «Overridden by managed config (system)» |
| file | `/etc/codex/requirements.toml` | да | строки: `/etc/codex/requirements.toml`, `config/src/loader/managed_requirements.rs` |
| file | `/etc/codex/managed_config.toml` (legacy) | да | строки: `/etc/codex/managed_config.toml`, «legacy managed_config.toml» |
| mdm | `cfprefs:com.openai.codex` (ключи `config_toml_base64`, `requirements_toml_base64`) | **нет** | строки + импорт `CFPreferencesCopyAppValue`/`CFPreferencesAppValueIsForced`: читается через cfprefsd по домену, а не одним файлом; какие домены поиска (managed, `/Library/Preferences`, пользовательские) принимаются, статически не установлено; проверять записью в настоящие настройки владельца нельзя |
| server | `chatgpt.com/backend-api/wham/config/bundle` | нет | поведение, ниже |

**Серверная доставка — исключения нет.** `test_codex_cloud_config_is_not_excluded`: с
фейковой ChatGPT-авторизацией (план `enterprise`, токены — фейковые, `access_token` =
фейковый ключ) и `-c chatgpt_base_url=<заглушка>` закреплённый argv до любого запроса к
модели запрашивает `GET /backend-api/wham/config/bundle` и без ответа отказывается
работать («Failed to load cloud config bundle (workspace-managed policies)»). Кандидат
`-c forced_login_method="api"` отвергнут: на ChatGPT-авторизации codex отвечает «API key
login is required, but ChatGPT is currently being used. Logging out.» — проба
разлогинила бы роль. Под API-ключом (разведка, `chatgpt_base_url` тоже на заглушку)
запроса к bundle не было — только `GET /backend-api/plugins/featured`; но это условие на
способ авторизации, которое `Exclusion` выразить не может.

## Вердикт по харнессам

| харнесс | изоляция (hooks, конфиг, MCP, модель, effort, провайдер) | managed: файлы/MDM | managed: сервер | `isolation_verified` |
|---|---|---|---|---|
| claude 2.1.309 | **прошла** (все сценарии, двойник подтверждает живость) | наблюдаемы | **не исключается** | `False` |
| codex 0.163.0 | **не прошла**: `~/.codex/AGENTS.md` роли и встроенные инструменты доходят до запроса | MDM ненаблюдаем | **не исключается** | `False` |

Отдельного коммита с `isolation_verified=True` нет.

## Пределы

- Наблюдаемость managed-файлов установлена по коду и debug-журналу, не созданием файлов в
  `/Library`/`/etc`: эффект присутствующего файла на запуск не проверен (недоступно без
  root). Список замкнут по коду этой версии; новая версия — новый SHA и новая проверка.
- Сценарии запускались от текущего пользователя, не от ролей `df-exec`/`df-review` (их нет до
  этапа 2) и не через `run_as`. Per-user MDM-путь claude берёт имя эффективного пользователя —
  у пробы это роль; здесь это проверено только по коду.
- claude: `auth_files = None` на macOS (связка ключей) — `auth_tracking` всегда
  `unavailable: backend_unknown`; поведение со связкой ключей не проверялось (закрыта
  песочницей).
- codex: отказ от keyring поведенчески не доказан (см. выше); запуск состоит из нескольких
  файлов пакета (`bin/codex-code-mode-host`, `codex-path/rg`, `codex-package.json`),
  SHA закрепляет только главный исполняемый файл.
- codex при недоступной сети ретраит бесконечно (`unbounded_connection_retries`) — исход
  пробы определяет таймаут `PROBE_TIMEOUT`.
- Linux-профили и SHA — Task 13.
