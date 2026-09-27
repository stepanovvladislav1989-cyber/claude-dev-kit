"""
Хук PreToolUse + PostToolUse: защита тестов-приёмки, эталона и самих замков.

Создание НОВЫХ файлов в tests/acceptance/, tests/e2e/ и tests/golden/ — без вопросов
(новые тесты пишутся по уже утверждённым сценариям).
Изменение или удаление СУЩЕСТВУЮЩИХ — только с разрешения пользователя:
Claude Code покажет запрос, и решение за вами. Любая правка замков и настроек
проверки (CONFIG) — тоже только с разрешения.
Чтение и запуск тестов разрешены без вопросов.

После разрешённого действия (PostToolUse) запоминаем, какие защищённые пути
пользователь разрешил менять, — scripts/work_state.py (проверка в validate)
сверяет с этим списком всё, что реально изменилось. Так правка в обход этого
хука (командой, где путь не написан буквально) даёт НЕ ПРОШЛО.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PROTECTED = ("tests/acceptance", "tests/e2e", "tests/golden")
CONFIG = (".claude/hooks/", ".claude/settings.json", ".claude/.protected_approved", ".pre-commit-config.yaml",
          ".github/workflows/", "scripts/validate.py", "scripts/work_state.py", "scripts/mark_reviewed.py",
          "scripts/check_docs.py", "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg")
PROTECTED_PATH = re.compile(r"tests/(acceptance|e2e|golden)(/[^\s'\"`;|&()<>]*)?")
WILDCARD = re.compile(r"[*?\[{]")
# Команды, которые только читают или запускают — их не останавливаем
READ_ONLY = re.compile(
    r"^\s*(python\s+-m\s+pytest|pytest|python\s+scripts/(validate|mark_reviewed|work_state|check_docs|roadmap)\.py|"
    r"cat|type|ls|dir|head|tail|grep|rg|find|"
    r"git\s+(diff|log|status|show|add|commit|push)|get-content|gc|get-childitem|gci|select-string|sls|test-path)\b",
    re.I,
)
WRITE_SIGNS = re.compile(
    r"(>|\brm\b|\bdel\b|\bmv\b|\bmove\b|\bcp\b|\bcopy\b|sed\s+-i|\btee\b|git\s+(checkout|restore|rm)\b|"
    r"\s-(delete|exec|execdir)\b|"
    r"\b(set|add|clear)-content\b|\bout-file\b|\b(remove|move|copy|rename|new|set)-item\b|"
    # короткие псевдонимы PowerShell — только как команда, иначе ловятся флаги вроде grep -ri
    r"(^|[;|&({\n]\s*)(ri|mi|ni|ren|rni|erase|rd|rmdir|ac|clc|cpi|si)\b|"
    r"writealltext|appendalltext|io\.file\]|io\.directory\]|\.delete\()",
    re.I,
)
# Перенаправление ошибок и вывода «в никуда» — не запись в файл
HARMLESS_REDIRECTS = re.compile(r"[\d*]?>&\d|[\d*]?>\s*\$null|[\d*]?>\s*/dev/null", re.I)
SHELL_TOOLS = ("Bash", "PowerShell")


def norm(p):
    return (p or "").replace("\\", "/").lower()


def touches(text, prefixes):
    t = norm(text)
    return any(p in t for p in prefixes)


def approved_part(path):
    """tests/golden/*.json → tests/golden: со звёздочкой разрешается вся папка до неё."""
    if not WILDCARD.search(path):
        return path
    return WILDCARD.split(path)[0].rsplit("/", 1)[0]


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def check(tool, inp):
    """Возвращает (причина запроса или None, защищённые пути, которые действие меняет)."""
    if tool in ("Edit", "Write", "MultiEdit"):
        path = inp.get("file_path", "")
        if touches(path, CONFIG):
            return "Изменение замков или настроек проверки. Разрешить можно только после вашей проверки.", []
        is_new_file = tool == "Write" and not os.path.exists(path)
        if touches(path, PROTECTED) and not is_new_file:
            t = norm(path)
            start = min(t.find(p) for p in PROTECTED if p in t)
            return ("Изменение существующего защищённого теста или эталона. "
                    "Разрешить можно только после вашей проверки."), [t[start:]]

    if tool in SHELL_TOOLS:
        cmd = inp.get("command", "")
        writes = WRITE_SIGNS.search(HARMLESS_REDIRECTS.sub("", cmd)) or not READ_ONLY.match(cmd)
        if touches(cmd, CONFIG) and writes:
            return "Команда может изменить замки или настройки проверки. Проверьте, что она делает.", []
        if touches(cmd, PROTECTED) and writes:
            paths = [approved_part(m.group(0).rstrip(".,")) for m in PROTECTED_PATH.finditer(norm(cmd))]
            return "Команда затрагивает защищённые тесты или эталон. Проверьте, что она делает.", paths
    return None, []


def main():
    data = json.load(sys.stdin)
    reason, paths = check(data.get("tool_name", ""), data.get("tool_input", {}) or {})
    if reason is None:
        sys.exit(0)
    if data.get("hook_event_name") == "PostToolUse":
        # Действие выполнилось — значит, пользователь его разрешил
        import work_state
        work_state.approve(paths)
        sys.exit(0)
    ask(reason)


if __name__ == "__main__":
    main()
