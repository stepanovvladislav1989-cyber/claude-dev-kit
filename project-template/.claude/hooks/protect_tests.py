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
    r"git\s+(diff|log|status|show))\b"
)
WRITE_SIGNS = re.compile(r"(>|\brm\b|\bdel\b|\bmv\b|\bmove\b|\bcp\b|\bcopy\b|sed\s+-i|\btee\b|git\s+(checkout|restore|rm)\b)")


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

    if tool == "Bash":
        cmd = inp.get("command", "")
        cleaned = cmd.replace("2>&1", "")  # перенаправление ошибок — не запись в файл
        if touches_protected(cmd) and (WRITE_SIGNS.search(cleaned) or not READ_ONLY.match(cmd)):
            ask("Команда затрагивает защищённые тесты или эталон. Проверьте, что она делает.")

    sys.exit(0)


if __name__ == "__main__":
    main()
