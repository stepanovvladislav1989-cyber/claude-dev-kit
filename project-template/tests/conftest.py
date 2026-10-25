"""
Защита рабочей базы: тесты работают только с тестовой базой.

Перед любыми тестами:
  - APP_ENV=test — приложение знает, что его запускают тесты;
  - адрес тестовой базы TEST_DATABASE_URL (из окружения или из .env) подставляется
    в DATABASE_URL — приложение, читающее .env без перезаписи, получит тестовую базу;
  - если адрес базы есть, а тестового нет, или в тестовом адресе нет слова «test»
    (отдельно: app_test, test.db; «latest» не считается) — тесты не запускаются вовсе, проверка НЕ ПРОШЛА.

Не задан ни DATABASE_URL, ни TEST_DATABASE_URL (нет .env, у программы адрес по умолчанию, например
SQLite в data/) — DATABASE_URL ставится на пустой временный файл SQLite (sqlite:///…/test.db),
который удаляется после тестов. Так программа с адресом по умолчанию в тестах не тронет рабочую базу,
а программа на другой базе (PostgreSQL) упадёт на чужом адресе, а не тихо возьмёт рабочую.
Программе без базы переменная не мешает.
Файл — часть настроек проверки: меняется только с разрешения пользователя.
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_WORD = re.compile(r"(?<![a-z])test", re.I)


def env_file_values():
    """Значения из .env без сторонних библиотек: строки КЛЮЧ=значение (и «export КЛЮЧ=значение»)."""
    path = ROOT / ".env"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


def setting(name, file_values):
    return os.environ.get(name) or file_values.get(name, "")


def pytest_configure(config):
    file_values = env_file_values()
    db_url = setting("DATABASE_URL", file_values)
    test_url = setting("TEST_DATABASE_URL", file_values)
    if not db_url and not test_url:
        config.temp_db_dir = tempfile.mkdtemp(prefix="test-db-")
        test_url = "sqlite:///" + (Path(config.temp_db_dir) / "test.db").as_posix()
    if not test_url:
        pytest.exit("Тесты остановлены: задан адрес рабочей базы DATABASE_URL, но нет TEST_DATABASE_URL. "
                    "Тесты могли бы изменить настоящие данные.", returncode=3)
    if not TEST_WORD.search(test_url):
        pytest.exit("Тесты остановлены: в адресе TEST_DATABASE_URL нет слова «test» — "
                    "похоже на рабочую базу.", returncode=3)
    if db_url and test_url == db_url:
        pytest.exit("Тесты остановлены: тестовая и рабочая база совпадают.", returncode=3)
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = test_url


def pytest_unconfigure(config):
    folder = getattr(config, "temp_db_dir", None)
    if folder:
        shutil.rmtree(folder, ignore_errors=True)
