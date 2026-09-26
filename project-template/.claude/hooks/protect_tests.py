"""
Хук PreToolUse: защита тестов-приёмки и эталона.

Создание НОВЫХ файлов в tests/acceptance/, tests/e2e/ и tests/golden/ — без вопросов
(новые тесты пишутся по уже утверждённым сценариям).
Изменение или удаление СУЩЕСТВУЮЩИХ — только с разрешения пользователя:
Claude Code покажет запрос, и решение за вами.
Чтение и запуск тестов разрешены без вопросов.
"""
import json
import os
import re
import sys

PROTECTED = ("tests/acceptance", "tests/e2e", "tests/golden")
# Команды, которые только читают или запускают — их не останавливаем
READ_ONLY = re.compile(
    r"^\s*(python\s+-m\s+pytest|pytest|python\s+scripts/validate\.py|cat|type|ls|dir|head|tail|grep|rg|find|"
    r"git\s+(diff|log|status|show)|get-content|gc|get-childitem|gci|select-string|sls|test-path)\b",
    re.I,
)
WRITE_SIGNS = re.compile(
    r"(>|\brm\b|\bdel\b|\bmv\b|\bmove\b|\bcp\b|\bcopy\b|sed\s+-i|\btee\b|git\s+(checkout|restore|rm)\b|"
    r"\b(set|add|clear)-content\b|\bout-file\b|\b(remove|move|copy|rename|new|set)-item\b|"
    # короткие псевдонимы PowerShell — только как команда, иначе ловятся флаги вроде grep -ri
    r"(^|[;|&({\n]\s*)(ri|mi|ni|ren|rni|erase|rd|rmdir|ac|clc|cpi|si)\b|"
    r"writealltext|appendalltext|io\.file\]|io\.directory\]|\.delete\()",
    re.I,
)
# Перенаправление ошибок и вывода «в никуда» — не запись в файл
HARMLESS_REDIRECTS = re.compile(r"[\d*]?>&\d|[\d*]?>\s*\$null", re.I)
SHELL_TOOLS = ("Bash", "PowerShell")


def norm(p):
    return (p or "").replace("\\", "/").lower()


def touches_protected(text):
    t = norm(text)
    return any(p in t for p in PROTECTED)


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool in ("Edit", "Write", "MultiEdit"):
        path = inp.get("file_path", "")
        is_new_file = tool == "Write" and not os.path.exists(path)
        if touches_protected(path) and not is_new_file:
            ask("Изменение существующего защищённого теста или эталона. Разрешить можно только после вашей проверки.")

    if tool in SHELL_TOOLS:
        cmd = inp.get("command", "")
        cleaned = HARMLESS_REDIRECTS.sub("", cmd)
        if touches_protected(cmd) and (WRITE_SIGNS.search(cleaned) or not READ_ONLY.match(cmd)):
            ask("Команда затрагивает защищённые тесты или эталон. Проверьте, что она делает.")

    sys.exit(0)


if __name__ == "__main__":
    main()
