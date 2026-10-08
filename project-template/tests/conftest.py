"""
Защита рабочей базы: тесты работают только с тестовой базой.

Перед любыми тестами:
  - APP_ENV=test — приложение знает, что его запускают тесты;
  - адрес тестовой базы TEST_DATABASE_URL (из окружения или из .env) подставляется
    в DATABASE_URL — приложение, читающее .env без перезаписи, получит тестовую базу;
  - если адрес базы есть, а тестового нет, или в тестовом адресе нет слова «test» —
    тесты не запускаются вовсе, проверка НЕ ПРОШЛА.

Нет базы в проекте (нет ни DATABASE_URL, ни TEST_DATABASE_URL) — ничего не делает.
Файл — часть настроек проверки: меняется только с разрешения пользователя.
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def env_file_values():
    """Значения из .env без сторонних библиотек: строки КЛЮЧ=значение."""
    path = ROOT / ".env"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().partition("=")
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
        return
    if not test_url:
        pytest.exit("Тесты остановлены: задан адрес рабочей базы DATABASE_URL, но нет TEST_DATABASE_URL. "
                    "Тесты могли бы изменить настоящие данные.", returncode=3)
    if "test" not in test_url.lower():
        pytest.exit("Тесты остановлены: в адресе TEST_DATABASE_URL нет слова «test» — "
                    "похоже на рабочую базу.", returncode=3)
    if db_url and test_url == db_url:
        pytest.exit("Тесты остановлены: тестовая и рабочая база совпадают.", returncode=3)
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = test_url
