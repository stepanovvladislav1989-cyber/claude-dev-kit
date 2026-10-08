"""
Единая проверка проекта. Одна команда — один вердикт: ПРОШЛО или НЕ ПРОШЛО.

Запуск:  python scripts/validate.py

Что проверяется — список CHECKS ниже. Его настраивает скилл design под стек проекта
(например, добавляет линтер). Порядок работы от стека не зависит.

Тесты считаются отдельно: логика (всё в tests/, кроме e2e) и сквозные e2e (tests/e2e/),
с числом зелёных тестов — чтобы в отчёте было видно «сколько из скольких».
"""
import os
import re
import subprocess
import sys
from pathlib import Path

# Вывод русских букв в консоль Windows без ошибок кодировки
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
# Проверки пишут по-русски в UTF-8 — иначе в консоли Windows сообщения превращаются в «кракозябры»
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

# Проверки: (название для человека, команда, можно ли «тестов нет»).
# Команда — список (запуск без оболочки) или строка (запуск через оболочку).
CHECKS = [
    ("Тесты логики (сценарии, эталон, инварианты)",
     [PY, "-m", "pytest", "tests", "--ignore=tests/e2e", "-q", "--no-header", "-p", "no:cacheprovider"], False),
    ("Сквозные тесты e2e (от данных до результата)",
     [PY, "-m", "pytest", "tests/e2e", "-q", "--no-header", "-p", "no:cacheprovider"], True),
    ("Документы: ссылки и номера", [PY, "scripts/check_docs.py"], False),
    ("Тесты-приёмки и эталон: изменены только с разрешения", [PY, "scripts/work_state.py", "protected"], False),
]

TAIL_LINES = 30  # сколько последних строк вывода показывать при ошибке


def count_tests(out):
    """Из итоговой строки pytest (последней строки с числами) достаёт «зелёных X из Y»."""
    summary = next((line for line in reversed(out.splitlines())
                    if re.search(r"\d+ (passed|failed|error|errors|skipped)", line)), "")
    nums = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|error|errors|skipped)", summary)}
    passed = nums.get("passed", 0)
    total = passed + nums.get("failed", 0) + nums.get("error", 0) + nums.get("errors", 0) + nums.get("skipped", 0)
    return passed, total


def run(cmd, allow_empty):
    """Запускает одну проверку. Возвращает (прошла ли, пояснение, вывод)."""
    try:
        res = subprocess.run(cmd, cwd=ROOT, shell=isinstance(cmd, str), capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=1800, env=ENV)
    except FileNotFoundError as e:
        return False, "команда не найдена", str(e)
    except subprocess.TimeoutExpired:
        return False, "не уложилась в 30 минут", ""
    out = (res.stdout or "") + (res.stderr or "")
    is_pytest = "pytest" in str(cmd)
    # Код 5 — pytest не нашёл ни одного теста; код 4 — ошибка запуска, это НЕ «тестов нет»
    folder_missing = res.returncode == 4 and "file or directory not found" in out
    if is_pytest and (res.returncode == 5 or folder_missing):
        if allow_empty:
            return True, "тестов пока нет", out
        return False, "тестов не найдено — проверка без тестов не считается пройденной", out
    note = ""
    if is_pytest:
        passed, total = count_tests(res.stdout or "")
        note = f"зелёных {passed} из {total}"
        if res.returncode == 0 and "skipped" in out:
            note += " (есть пропущенные — проверить почему)"
    return res.returncode == 0, note, out


def main():
    results = [(name, *run(cmd, allow_empty)) for name, cmd, allow_empty in CHECKS]
    ok_all = all(ok for _, ok, _, _ in results)

    print(f"ИТОГ: {'ПРОШЛО' if ok_all else 'НЕ ПРОШЛО'}")
    for name, ok, note, _ in results:
        print(f"  - {name}: {'ПРОШЛО' if ok else 'НЕ ПРОШЛО'}" + (f" — {note}" if note else ""))

    for name, ok, _, out in results:
        if not ok:
            print(f"\n--- Подробности: {name} (последние строки) ---")
            print("\n".join(out.strip().splitlines()[-TAIL_LINES:]))

    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
