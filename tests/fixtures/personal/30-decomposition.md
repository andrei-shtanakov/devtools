## Проверки

#### BEH-01: add дописывает заметку
`notes add купить хлеб` дописывает строку в файл заметок и печатает «добавлено».
- **checked_by**: `kind: pytest` `target: tests/test_add.py::test_add_appends_line`
**Критерии:** FR-01/1

#### BEH-02: find печатает совпадения
При заметках «купить хлеб» и «позвонить» `notes find хлеб` печатает только первую.
- **checked_by**: `kind: pytest` `target: tests/test_find.py::test_find_prints_matches`
**Критерии:** FR-02/1

## Задачи

#### DT-01: Команда add · type: implement · owner: dev
scenarios: [BEH-01]
depends_on: []
parallel_group: solo
**Решения дизайна:** D-01
CLI `notes.py` с подкомандой `add`, дозапись в `~/.notes.txt`.

#### DT-02: Команда find · type: implement · owner: dev
scenarios: [BEH-02]
depends_on: [DT-01]
parallel_group: solo
**Решения дизайна:** D-02
Подкоманда `find`: построчный фильтр файла заметок.
