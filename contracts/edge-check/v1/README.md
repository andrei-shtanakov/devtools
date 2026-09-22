# edge-check v1 — каталог правил

Один файл на сочетание «тип узла + типы оснований». Порядок `items` — часть
identity: перестановка делает прежние результаты неприменимыми.

`severity` объявляет классы находок; отнесение находки к классу — суждение
модели, вычисление итога — механика инструмента.

`applicability` перечисляет основания, отсутствие которых разрешено, и id
правила, которым оно разрешено. Пустой список означает: все основания
обязательны, и отсутствие любого — `ERROR`, а не `N/A`.

Сочетание без файла правил — ошибка конфигурации (`unknown_edge`), не
молчаливый пропуск проверки.

## Состав каталога (срез 2 — координатор волны)

| Ребро | Subject | Основания | Отсутствие оснований |
|---|---|---|---|
| `charter-vs-customer-brief` | charter | customer-brief | `ERROR` (у W1 нет других оснований) |
| `charter-vs-engineer-brief` | charter | engineer-brief | `N/A` по `R0-no-engineer-brief` (customer-маршрут E2) |
| `requirements-vs-charter` | requirements | charter | `ERROR` |
| `behaviour-vs-requirements` | behaviour-spec | requirements | `ERROR` |
| `design-vs-requirements-behaviour` | design | requirements, behaviour-spec | `ERROR` |
| `acceptance-vs-requirements-behaviour` | acceptance | requirements, behaviour-spec | `ERROR` |
| `decomposition-vs-design-acceptance` | decomposition | design, acceptance | `ERROR` |
| `engineer-brief-vs-customer-brief` | engineer-brief | customer-brief | `N/A` по `A1` |

Ребро charter разбито на два (по одному основанию), а не одно с двумя:
загрузчик не поддерживает несколько оснований вместе с `applicability`
(`multi_basis_applicability_unsupported`), и обходится это составом каталога,
а не правкой загрузчика. Координатор (`governance/edge_check/coordinator.py`)
выводит рёбра из `upstream` профиля; для charter — оба ребра всегда.
