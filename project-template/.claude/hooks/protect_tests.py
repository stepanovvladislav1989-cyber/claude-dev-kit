"""
Хук PreToolUse + PostToolUse: защита тестов-приёмки, эталона и самих замков.

Создание НОВЫХ файлов в tests/acceptance/, tests/e2e/ и tests/golden/ — без вопросов
(новые тесты пишутся по уже утверждённым сценариям). Новый — значит, его нет в последнем коммите:
только что созданный тест задачи можно поправить без вопроса. Так же защищены проверки слепой приёмки
принятых релизов — tests/blind/release-N при метке release-N (scripts/work_state.py).
Изменение или удаление СУЩЕСТВУЮЩИХ — только с разрешения пользователя:
Claude Code покажет запрос, и решение за вами. Любая правка замков и настроек
проверки (CONFIG) — тоже только с разрешения; сюда же входят отметка ревью
(.claude/.review_ok, её ставит только замок по ответу reviewer), запомненный итог проверки (.claude/.validate_ok),
пауза (.claude/PAUSE) и запуск scripts/mark_reviewed.py командой из любой папки (ручная отметка ревью):
агент не может напрямую сам себе подтвердить ревью или выключить замок stop_red.
(Защита от пропуска шага, а не от умышленного обхода: обходной код замок не распознает.)
Чтение и запуск тестов разрешены без вопросов. Команда из нескольких частей (через ;, &&, ||, |,
перевод строки) считается только читающей, если читает каждая её часть.

После разрешённого действия (PostToolUse) запоминаем, какие защищённые пути
пользователь разрешил менять, — scripts/work_state.py (проверка в validate)
сверяет с этим списком всё, что реально изменилось. Так правка в обход этого
хука (командой, где путь не написан буквально) даёт НЕ ПРОШЛО.

Заодно перед правкой или командой агента отмечает на дашборде «агент работает», если перед этим
он закончил ответ (scripts/dashboard.py; ошибка глушится).
Сбой самого замка — вопрос пользователю, а не молчаливый пропуск.
"""
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PROTECTED = ("tests/acceptance", "tests/e2e", "tests/golden")
# Пути сравниваются в нижнем регистре: ".claude/pause" — это .claude/PAUSE; ".claude/settings" — и settings.local.json
CONFIG = (".claude/hooks/", ".claude/settings", ".claude/.protected_approved", ".claude/.review_ok", ".claude/.validate_ok",
          ".claude/pause", ".pre-commit-config.yaml", ".github/workflows/", "scripts/validate.py",
          "scripts/work_state.py", "scripts/mark_reviewed.py", "scripts/check_docs.py", "scripts/review_diff.py", "scripts/dashboard.py", "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg")
# Те же скрипты по одному имени — команда могла сначала перейти в папку (cd scripts && …)
CONFIG_NAMES = re.compile(r"(?<![\w.-])(validate|work_state|mark_reviewed|check_docs|review_diff|dashboard)\.py\b", re.I)
PROTECTED_PATH = re.compile(r"tests/(acceptance|e2e|golden|blind(/release-\d+)?)(/[^\s'\"`;|&()<>]*)?")
# Папка проверок приёмщика целиком или одного релиза — по границе: release-1 не захватывает release-10
BLIND_PATH = re.compile(r"tests/blind(/release-\d+)?(?![\w-])")
WILDCARD = re.compile(r"[*?\[{]")
# Команды, которые только читают или запускают — их не останавливаем
# Интерпретатор: python, python3, py — голый или по полному пути, в том числе в кавычках и через & в PowerShell
INTERPRETER = r"(&\s*)?(\"[^\"]*/|\S*/)?(python[\d.]*|py)(\.exe)?\"?\s+"
READ_ONLY = re.compile(
    r"^\s*(" + INTERPRETER + r"(-m\s+pytest|(scripts/)?(validate|work_state|check_docs|roadmap|review_diff|dashboard)\.py)|"
    # sed -n только с печатью строк: «sed -n 1,40p файл»
    r"pytest|cat|type|ls|dir|head|tail|grep|rg|find|wc|cd|pwd|echo|sed\s+-n\s+['\"]?[\d,$]*p['\"]?(?=\s)|"
    r"git\s+(diff|log|status|show|add|commit|push|grep|ls-files|blame)|"
    r"get-content|gc|get-childitem|gci|select-string|sls|test-path|set-location|write-output)(?!\w)",  # не \b: после кавычки sed -n '1p'
    re.I,
)
SPLIT = re.compile(r"&&|\|\||[;|&\n]")  # одиночный & в bash — тоже разделитель (запуск в фоне)
WRITE_SIGNS = re.compile(
    r"(>|\brm\b|\bdel\b|\bmv\b|\bmove\b|\bcp\b|\bcopy\b|\bsed\b[^;&|\n]*\s(-[a-z]*i|--in-place)|\btee\b|git\s+(checkout|restore|rm)\b|"
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
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def norm(text):
    """Команда или путь в одном виде: прямые слэши, нижний регистр, без «/./» внутри."""
    t = (text or "").replace("\\", "/").lower()
    while "/./" in t:
        t = t.replace("/./", "/")
    return t


def norm_path(path):
    """Путь файла: как norm, плюс «a/../b» → «b» (для команды так нельзя: «..» съест соседнее слово)."""
    t = norm(path)
    return posixpath.normpath(t) if t else ""


def touches(text, prefixes, path=False):
    t = norm_path(text) if path else norm(text)
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
    }, ensure_ascii=False))
    sys.exit(0)


def protected_for(text):
    """Защищённые папки, которые упомянуты в тексте; метки релизов спрашиваем у git, только если
    речь о tests/blind. Папка tests/blind целиком защищена, если принят хоть один релиз."""
    t = norm(text)
    if "tests/blind" not in t:
        return PROTECTED
    import work_state
    accepted = [p.rstrip("/") for p in work_state.accepted_blind()]
    hits = {m.group(0) for m in BLIND_PATH.finditer(t)}
    return (*PROTECTED, *(h for h in hits if h in accepted or (h == "tests/blind" and accepted)))


def in_last_commit(path):
    """Файл есть в последнем коммите — значит, существующий. Нет коммита или git — считаем новым."""
    full = Path(path) if Path(path).is_absolute() else ROOT / path
    try:
        rel = full.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return full.exists()  # вне проекта git не спросить
    if subprocess.run(["git", "cat-file", "-e", f"HEAD:{rel}"], cwd=ROOT, capture_output=True).returncode == 0:
        return True
    # Windows не различает регистр: tests/ACCEPTANCE/x.py — тот же файл, что в коммите
    res = subprocess.run(["git", "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return rel.lower() in res.stdout.lower().splitlines()


def read_only(cmd):
    """Каждая часть команды только читает или запускает проверки."""
    clean = HARMLESS_REDIRECTS.sub("", cmd)
    # многострочное сообщение коммита (git commit -m "$(cat <<'EOF' … EOF)") — текст, не команды
    clean = re.sub(r"(\bgit\s+commit\b[^\n;&|]*?)<<-?\s*(['\"]?)(\w+)\2.*?^\s*\3\s*$", r"\1 ", clean,
                   flags=re.S | re.M | re.I)
    if WRITE_SIGNS.search(clean):
        return False
    parts = [p for p in SPLIT.split(clean) if p.strip(" \t)\"'")]  # «)"» — хвост сообщения коммита после <<
    # подстановка команды: echo $(python -c …) — не чтение; в части с git commit — это сообщение коммита
    if any(re.search(r"\$\(|`", p) and not re.match(r"\s*git\s+commit\b", p, re.I) for p in parts):
        return False
    return all(READ_ONLY.match(norm(part)) for part in parts)


def check(tool, inp):
    """Возвращает (причина запроса или None, защищённые пути, которые действие меняет)."""
    if tool in EDIT_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if touches(path, CONFIG, path=True):
            return "Изменение замков или настроек проверки. Разрешить можно только после вашей проверки.", []
        protected = protected_for(norm_path(path))
        if touches(path, protected, path=True) and in_last_commit(path):
            t = norm_path(path)
            start = min(t.find(p) for p in protected if p in t)
            return ("Изменение существующего защищённого теста или эталона. "
                    "Разрешить можно только после вашей проверки."), [t[start:]]

    if tool in SHELL_TOOLS:
        cmd = inp.get("command", "")
        writes = not read_only(cmd)
        if (touches(cmd, CONFIG) or CONFIG_NAMES.search(cmd)) and writes:
            return "Команда может изменить замки или настройки проверки. Проверьте, что она делает.", []
        if touches(cmd, protected_for(cmd)) and writes:
            paths = [approved_part(m.group(0).rstrip(".,")) for m in PROTECTED_PATH.finditer(norm(cmd))]
            return "Команда затрагивает защищённые тесты или эталон. Проверьте, что она делает.", paths
    return None, []


def show_on_dashboard():
    """Агент снова что-то делает после ответа — дашборд переходит в «работает». Ошибка глушится."""
    try:
        import dashboard
        dashboard.agent_working()
    except Exception:
        pass


def main():
    data = json.load(sys.stdin)
    pre = data.get("hook_event_name") == "PreToolUse"
    if pre:
        show_on_dashboard()
    try:
        reason, paths = check(data.get("tool_name", ""), data.get("tool_input", {}) or {})
    except Exception as error:  # сбой замка не должен молча пропускать действие
        if pre:
            ask(f"Замок защиты тестов не смог проверить действие ({type(error).__name__}: {error}). "
                "Разрешите, только если понимаете, что оно делает.")
        sys.exit(0)
    if reason is None:
        sys.exit(0)
    if not pre:
        # Действие выполнилось — значит, пользователь его разрешил
        import work_state
        work_state.approve(paths)
        sys.exit(0)
    ask(reason)


if __name__ == "__main__":
    main()
