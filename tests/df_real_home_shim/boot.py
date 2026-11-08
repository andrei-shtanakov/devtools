"""ТОЛЬКО ДЛЯ ТЕСТОВ: исполнить `-I <скрипт> …` с `pw_dir` = DF_TEST_REAL_HOME.

Зовётся шимом `tests/df_real_home_shim/python3` (см. его шапку): argv —
`boot.py -I <скрипт> <аргументы…>`. Подменяется только `pwd.getpwuid` в этом
дочернем процессе; скрипт исполняется как `__main__`, как под `python3 -I`.
"""

import os
import pwd
import runpy
import sys

_HOME = os.environ["DF_TEST_REAL_HOME"]
_getpwuid = pwd.getpwuid


def _fake_getpwuid(uid: int) -> pwd.struct_passwd:
    entry = _getpwuid(uid)
    return pwd.struct_passwd((*entry[:5], _HOME, entry[6]))


pwd.getpwuid = _fake_getpwuid
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
