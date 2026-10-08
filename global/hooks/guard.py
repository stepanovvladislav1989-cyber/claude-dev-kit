"""
Глобальный хук PreToolUse: работает во всех проектах.

Запрещает агенту:
  - читать и менять файлы с паролями и ключами (.env, *.pem, *.key, id_rsa) и искать в них (Grep);
  - выполнять в Bash или PowerShell разрушительные команды: массовое удаление (rm -rf),
    принудительную отправку в GitHub (push --force), сброс изменений (reset --hard,
    git checkout/restore всей папки, git checkout/switch --force или --discard-changes, git clean -f;
    git restore --staged . разрешён — он рабочие файлы не трогает),
    удаление базы или схемы (DROP DATABASE / DROP SCHEMA),
    удаление или перенос файлов базы (*.db, *.sqlite) и папок data/ и backups/ в корне проекта
    (файлы со словом «test» в имени — можно: это тестовая база);
  - обходить проверку перед коммитом: --no-verify, commit -n, SKIP=…, установка core.hooksPath,
    запись в .git/hooks (командой или правкой файла), pre-commit uninstall;
  - менять данные или структуру базы разовой командой (psql/sqlite3/mysql/sqlcmd, python/py -c,
    python - <<) или разовым SQL-файлом вне migrations/ (UPDATE, DELETE, INSERT, ALTER, CREATE,
    DROP TABLE, TRUNCATE) — такие изменения делаются только файлом миграции в проекте.

Спрашивает разрешение пользователя, если запускается python-скрипт с такими командами,
который не входит в проект (вне src/, scripts/, migrations/, tests/ и не сохранён в git),
или скрипт не удалось прочитать.

Сообщения коммитов (-m "…" и текст после <<) при поиске обходов и удалений не учитываются.

Это защита от случайной ошибки агента, а не от умысла: команду, которая прячет действие
(запись файла из python -c, обход внутри << …, переменная $env:skip в нижнем регистре),
замок не распознает.
Файл .env.example (образец без паролей) разрешён.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePath

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DANGEROUS = [
    (re.compile(r"\bgit\s+push\b.*(--force|\s-f\b)", re.I), "принудительная отправка в GitHub (push --force)"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "сброс всех изменений (reset --hard)"),
    (re.compile(r"\bdrop\s+(database|schema)\b", re.I), "удаление базы данных или схемы"),
    (re.compile(r"\bRemove-Item\b[^;|&]*-Recurse", re.I), "массовое удаление (Remove-Item -Recurse)"),
    (re.compile(r"\b(rd|rmdir)\s+/s\b", re.I), "массовое удаление (rd /s)"),
]
# Обходы проверки перед коммитом — ищутся в команде без текста сообщений коммита
BYPASS = [
    (re.compile(r"\bgit\b.*--no-verify\b", re.I), "обход проверки перед коммитом (--no-verify)"),
    (re.compile(r"\bgit\s+commit\b[^;&|]*\s-[avsqSm]*n[avsqSm]*(?=\s|$)", re.I), "обход проверки перед коммитом (commit -n)"),
    # pre-commit читает именно SKIP заглавными
    (re.compile(r"(\$env:|\bexport\s+|\bset\s+|^|[\s;&|(])SKIP\s*="), "обход проверки перед коммитом (SKIP=)"),
    (re.compile(r"core\.hookspath\s*=|\bconfig\b(?![^;&|\n]*--get)[^;&|\n]*\bcore\.hookspath\s+\S", re.I),
     "обход проверки перед коммитом (core.hooksPath)"),
    (re.compile(r"\bpre-commit\s+uninstall\b", re.I), "отключение проверки перед коммитом (pre-commit uninstall)"),
]
GIT_HOOKS = re.compile(r"\.git[/\\]hooks", re.I)
WRITE_CMD = re.compile(r"(>|\b(rm|del|erase|cp|copy|mv|move|ln|tee|chmod|set-content|add-content|out-file|"
                       r"remove-item|copy-item|move-item|new-item|ri|mi|cpi|ni)\b|sed\s+-i)", re.I)
# В PowerShell rm, del, ri, rd, rmdir, erase — псевдонимы Remove-Item; -r, -rec — сокращения -Recurse
PS_RECURSIVE_DELETE = re.compile(r"(^|[;|&({\s])(?<!git\s)(remove-item|rm|ri|del|erase|rd|rmdir)(\s[^;|&]*)?\s-r(e(c(u(r(se?)?)?)?)?)?\b", re.I)
SHELL_TOOLS = ("Bash", "PowerShell")
PYTHON = r"(?:python[\w.]*|py)(?:\.exe)?"
DB_CLIENT = re.compile(r"\b(psql|sqlite3|mysql|sqlcmd|pgcli)\b|\b" + PYTHON + r"\s+(-c\b|-(\s|$)|<<)", re.I)
SQL_CLIENT = re.compile(r"\b(psql|sqlite3|mysql|sqlcmd|pgcli)\b", re.I)
SQL_INPUT = re.compile(r"(?:-f\s*|-i\s+|--file[=\s]|(?<!<)<\s*|\.read\s+|\b(?:cat|type|get-content|gc)\s+)"
                       r"['\"]?([^\s'\"<>|;&]+\.sql)\b", re.I)
DB_WRITE = re.compile(r"\b(update\s+\S+\s+set|delete\s+from|insert\s+into|alter\s+table|create\s+(table|index|view|schema)|"
                      r"drop\s+(table|index|view)|truncate)\b", re.I)
DELETE_CMD = re.compile(r"(?:^|[;&|(\n]|\s)(rm|del|erase|remove-item|ri|unlink|rmdir|rd|mv|move|move-item|mi)\s+([^;&|\n]*)", re.I)
FIND_DELETE = re.compile(r"\bfind\s+([^\s;&|]+)[^;&|\n]*\s-delete\b", re.I)
DB_FILE = re.compile(r"\.(db|sqlite3?|duckdb)$", re.I)
REDIRECT_TO_DB = re.compile(r">\s*['\"]?([^\s'\";&|]+\.(?:db|sqlite3?|duckdb))\b", re.I)
DATA_DIRS = ("data", "backups")
SECRET_NAME = re.compile(r"(^\.env(\..+)?$|\.pem$|\.key$|^id_rsa)", re.I)
SECRET_IN_CMD = re.compile(r"(^|[\s/\\'\"=])\.env(?!\.example)(\.[\w-]+)?(?=$|[\s'\"])", re.I)
# Папки, где скрипты с SQL — норма: приложение, команды проекта (бэкап + миграция), миграции, тесты
PROJECT_CODE = ("src", "scripts", "migrations", "tests")
PY_FLAGS_WITH_VALUE = {"-X", "-W", "-Q"}


def is_rm_rf(cmd):
    """rm с флагами «рекурсивно» и «принудительно» в любой записи: -rf, -r -f, --recursive --force."""
    for part in re.split(r"[;&|]+", cmd):
        tokens = part.split()
        if not tokens or tokens[0] != "rm":
            continue
        flags = "".join(t.lstrip("-") for t in tokens[1:] if t.startswith("-") and not t.startswith("--"))
        long_flags = {t for t in tokens[1:] if t.startswith("--")}
        recursive = "r" in flags.lower() or "--recursive" in long_flags
        force = "f" in flags or "--force" in long_flags
        if recursive and force:
            return True
    return False


WHOLE_TREE = {".", "./", ":/", ":/.", "*"}


def git_discard(cmd):
    """Команда git, стирающая несохранённую работу целиком: clean -f, checkout/restore всей папки,
    checkout/switch с -f, --force, --discard-changes. Возвращает описание или None."""
    for part in re.split(r"[;&|\n]+", cmd):
        tokens = [t.strip("'\"") for t in part.split()]
        # git, git.exe или полный путь к нему
        start = next((i for i, t in enumerate(tokens) if re.search(r"(^|[/\\])git(\.exe)?$", t, re.I)), None)
        if start is None:
            continue
        rest = tokens[start + 1:]
        while rest and rest[0].startswith("-"):  # общие флаги до команды: -C <папка>, -c ключ=значение, --no-pager …
            rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
        if not rest:
            continue
        sub, args = rest[0].lower(), rest[1:]
        if sub == "clean" and any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a)
                                  for a in args):
            return "удаление всех новых несохранённых файлов (git clean --force)"
        if sub in ("checkout", "switch") and any(a in ("--force", "--discard-changes") or
                                                 (a.startswith("-") and not a.startswith("--") and "f" in a)
                                                 for a in args):
            return "отмена всех несохранённых изменений (git checkout/switch --force)"
        if sub in ("checkout", "restore") and WHOLE_TREE & set(args):
            if sub == "restore" and ("--staged" in args or "-S" in args) and not ("--worktree" in args or "-W" in args):
                continue  # убирает файлы из подготовки к коммиту, рабочие файлы не трогает
            return "отмена всех несохранённых изменений (git checkout/restore .) — возвращай файлы по одному"
    return None


def without_messages(cmd):
    """Команда без текста сообщений коммита: «-m "…"» и тела после <<МЕТКА … МЕТКА."""
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", " ", cmd, flags=re.S | re.M)
    return re.sub(r"(\s-m|--message)(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)", " ", cmd)


def clean_path(token):
    return token.strip("'\"").replace("\\", "/").removeprefix("./")


TEST_WORD = re.compile(r"(^|[^a-z])test", re.I)


def in_data_dir(path, data):
    rel = relative_to_project(path, data)
    return rel is not None and bool(rel.parts) and rel.parts[0].lower() in DATA_DIRS


def is_data_target(token, data):
    """Файл базы или что угодно в папках data/, backups/ проекта (полный путь тоже).
    Тестовая база (слово test отдельно: test.db, app_test.db) — можно, но не в backups/."""
    path = clean_path(token)
    if not path or path.startswith("-"):
        return False
    rel = relative_to_project(path, data)
    if rel is not None and rel.parts and rel.parts[0].lower() == "backups":
        return True
    if TEST_WORD.search(PurePath(path).name):
        return False
    return bool(DB_FILE.search(path)) or in_data_dir(path, data)


def deletes_data(cmd, data):
    for match in DELETE_CMD.finditer(cmd):
        verb, args = match.group(1).lower(), match.group(2).split()
        if verb in ("mv", "move", "move-item", "mi"):
            args = args[:-1]  # куда переносим — не трогаем
        if any(is_data_target(a, data) for a in args):
            return True
    for match in FIND_DELETE.finditer(cmd):
        if in_data_dir(clean_path(match.group(1)), data) or DB_FILE.search(match.group(0)):
            return True
    return any(is_data_target(m.group(1), data) for m in REDIRECT_TO_DB.finditer(cmd))


def project_root(data):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())


def relative_to_project(path, data):
    """Путь относительно проекта или None, если файл вне проекта."""
    root = project_root(data)
    path = re.sub(r"^/([a-zA-Z])(?=/)", r"\1:", path)  # путь Git Bash /d/proj → D:/proj
    full = Path(path) if Path(path).is_absolute() else Path(data.get("cwd") or os.getcwd()) / path
    try:
        return full.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def tracked_in_git(rel, data):
    res = subprocess.run(["git", "ls-files", "--error-unmatch", rel.as_posix()], cwd=project_root(data),
                         capture_output=True)
    return res.returncode == 0


def python_scripts(cmd):
    """Пути .py, которые команда запускает интерпретатором (python, py, по полному пути)."""
    for part in re.split(r"[;&|\n]+", cmd):
        tokens = [t.strip("'\"") for t in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", part)]
        for i, token in enumerate(tokens):
            if not re.fullmatch(r"(.*[/\\])?" + PYTHON, token, re.I):
                continue
            rest = tokens[i + 1:]
            while rest and rest[0].startswith("-"):
                if rest[0] in ("-m", "-c"):
                    rest = []
                    break
                rest = rest[2:] if rest[0] in PY_FLAGS_WITH_VALUE else rest[1:]
            if rest and rest[0].lower().endswith(".py"):
                yield rest[0]
            break


def adhoc_sql_script(cmd, data):
    """(путь, причина) для скрипта, о котором надо спросить пользователя, или None."""
    for path in python_scripts(cmd):
        rel = relative_to_project(path, data)
        if rel is not None and rel.parts and (rel.parts[0] in PROJECT_CODE or tracked_in_git(rel, data)):
            continue
        full = Path(path) if Path(path).is_absolute() else Path(data.get("cwd") or os.getcwd()) / path
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return path, "скрипт не удалось прочитать и проверить"
        if DB_WRITE.search(text):
            return path, "разовый скрипт меняет базу. Обычно это делают файлом миграции"
    return None


def adhoc_sql_file(cmd, data):
    """Входной SQL-файл (-f, -i, <, .read, cat/Get-Content … |) вне migrations/ и tests/ для клиента базы:
    (путь, можно ли прочитать и он меняет базу). Файлы, куда пишется результат (> файл.sql), не проверяются."""
    if not SQL_CLIENT.search(cmd):
        return None
    unreadable = None
    for path in SQL_INPUT.findall(cmd):
        rel = relative_to_project(clean_path(path), data)
        if rel is not None and rel.parts and rel.parts[0] in ("migrations", "tests"):
            continue
        full = Path(clean_path(path))
        full = full if full.is_absolute() else Path(data.get("cwd") or os.getcwd()) / full
        try:
            if DB_WRITE.search(full.read_text(encoding="utf-8", errors="replace")):
                return path, True
        except OSError:
            unreadable = unreadable or (path, False)  # проверяем остальные файлы — запрет важнее вопроса
    return unreadable


def is_secret_file(path):
    name = PurePath((path or "").replace("\\", "/")).name
    return bool(name) and name.lower() != ".env.example" and bool(SECRET_NAME.search(name))


def is_secret_glob(glob):
    """Маска поиска, под которую попадают файлы с паролями: .env, *.env, *.pem, *.key, id_rsa*."""
    name = PurePath((glob or "").replace("\\", "/")).name
    return bool(name) and (is_secret_file(name.lstrip("*")) or is_secret_file(name.replace("*", "x")))


def deny(reason):
    print(f"ЗАПРЕЩЕНО защитным хуком: {reason}. Если это действительно нужно — "
          "объясни пользователю зачем, и пусть он выполнит действие сам.", file=sys.stderr)
    sys.exit(2)


def ask(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool in ("Read", "Edit", "Write", "MultiEdit") and is_secret_file(inp.get("file_path")):
        deny("доступ к файлу с паролями или ключами")

    if tool in ("Edit", "Write", "MultiEdit") and GIT_HOOKS.search(inp.get("file_path") or ""):
        deny("изменение проверки перед коммитом (.git/hooks)")

    if tool == "Grep" and (is_secret_file(inp.get("path")) or is_secret_glob(inp.get("glob"))):
        deny("поиск в файле с паролями или ключами")

    if tool in SHELL_TOOLS:
        cmd = inp.get("command", "")
        if is_rm_rf(cmd):
            deny("массовое удаление (rm -rf)")
        discard = git_discard(cmd)
        if discard:
            deny(discard)
        if tool == "PowerShell" and PS_RECURSIVE_DELETE.search(cmd):
            deny("массовое удаление (Remove-Item -Recurse)")
        for pattern, what in DANGEROUS:
            if pattern.search(cmd):
                deny(what)
        plain = without_messages(cmd)
        for pattern, what in BYPASS:
            if pattern.search(plain):
                deny(what)
        if GIT_HOOKS.search(plain) and WRITE_CMD.search(plain):
            deny("изменение проверки перед коммитом (.git/hooks)")
        if deletes_data(plain, data):
            deny("удаление или перенос файла базы, данных или бэкапов")
        if DB_CLIENT.search(cmd) and DB_WRITE.search(cmd):
            deny("изменение базы разовой командой. Сделай файл миграции в migrations/ и запусти командой «бэкап + миграция»")
        sql_file = adhoc_sql_file(cmd, data)
        if sql_file and sql_file[1]:
            deny(f"изменение базы разовым SQL-файлом {sql_file[0]}. Сделай файл миграции в migrations/ "
                 "и запусти командой «бэкап + миграция»")
        if SECRET_IN_CMD.search(cmd):
            deny("доступ к файлу с паролями (.env)")
        # Вопросы — только после всех запретов
        if sql_file:
            ask(f"Команда базы с файлом {sql_file[0]}, который не удалось прочитать и проверить. "
                "Разрешить можно, только если вы понимаете, что он делает.")
        script = adhoc_sql_script(cmd, data)
        if script:
            ask(f"Запуск {script[0]}: {script[1]}. Разрешить можно, только если вы понимаете, что он делает.")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # сбой замка не должен молча пропускать команду
        ask(f"Защитный замок не смог проверить действие ({type(error).__name__}: {error}). "
            "Разрешите, только если понимаете, что делает команда.")
