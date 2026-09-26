"""
Глобальный хук PreToolUse: работает во всех проектах.

Запрещает агенту:
  - читать и менять файлы с паролями и ключами (.env, *.pem, *.key, id_rsa);
  - выполнять в Bash или PowerShell разрушительные команды: массовое удаление (rm -rf),
    принудительную отправку в GitHub (push --force), сброс изменений (reset --hard),
    удаление базы или схемы (DROP DATABASE / DROP SCHEMA),
    обход проверки перед коммитом (--no-verify, commit -n),
    изменение данных или структуры базы разовой командой в терминале
    (psql/sqlite3/mysql/sqlcmd или python -c с UPDATE, DELETE, INSERT, ALTER, CREATE, DROP TABLE, TRUNCATE) —
    такие изменения делаются только файлом миграции в проекте.

Файл .env.example (образец без паролей) разрешён.
"""
import json
import re
import sys
from pathlib import PurePath

sys.stderr.reconfigure(encoding="utf-8")

DANGEROUS = [
    (re.compile(r"\bgit\s+push\b.*(--force|\s-f\b)", re.I), "принудительная отправка в GitHub (push --force)"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "сброс всех изменений (reset --hard)"),
    (re.compile(r"\bgit\b.*--no-verify\b", re.I), "обход проверки перед коммитом (--no-verify)"),
    (re.compile(r"\bgit\s+commit\b[^;&|]*\s-[avsqSm]*n[avsqSm]*(?=\s|$)", re.I), "обход проверки перед коммитом (commit -n)"),
    (re.compile(r"\bdrop\s+(database|schema)\b", re.I), "удаление базы данных или схемы"),
    (re.compile(r"\bRemove-Item\b[^;|&]*-Recurse", re.I), "массовое удаление (Remove-Item -Recurse)"),
    (re.compile(r"\b(rd|rmdir)\s+/s\b", re.I), "массовое удаление (rd /s)"),
]
# В PowerShell rm, del, ri, rd, rmdir, erase — псевдонимы Remove-Item; -r, -rec — сокращения -Recurse
PS_RECURSIVE_DELETE = re.compile(r"(^|[;|&({\s])(?<!git\s)(remove-item|rm|ri|del|erase|rd|rmdir)(\s[^;|&]*)?\s-r(e(c(u(r(se?)?)?)?)?)?\b", re.I)
SHELL_TOOLS = ("Bash", "PowerShell")
DB_CLIENT = re.compile(r"\b(psql|sqlite3|mysql|sqlcmd|pgcli)\b|\bpython[\w.]*\s+-c\b", re.I)
DB_WRITE = re.compile(r"\b(update\s+\S+\s+set|delete\s+from|insert\s+into|alter\s+table|create\s+(table|index|view|schema)|"
                      r"drop\s+(table|index|view)|truncate)\b", re.I)
SECRET_NAME = re.compile(r"(^\.env(\..+)?$|\.pem$|\.key$|^id_rsa)", re.I)
SECRET_IN_CMD = re.compile(r"(^|[\s/\\'\"=])\.env(?!\.example)(\.[\w-]+)?(?=$|[\s'\"])", re.I)


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


def is_secret_file(path):
    name = PurePath((path or "").replace("\\", "/")).name
    return bool(name) and name.lower() != ".env.example" and bool(SECRET_NAME.search(name))


def deny(reason):
    print(f"ЗАПРЕЩЕНО защитным хуком: {reason}. Если это действительно нужно — "
          "объясни пользователю зачем, и пусть он выполнит действие сам.", file=sys.stderr)
    sys.exit(2)


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool in ("Read", "Edit", "Write", "MultiEdit") and is_secret_file(inp.get("file_path")):
        deny("доступ к файлу с паролями или ключами")

    if tool in SHELL_TOOLS:
        cmd = inp.get("command", "")
        if is_rm_rf(cmd):
            deny("массовое удаление (rm -rf)")
        if tool == "PowerShell" and PS_RECURSIVE_DELETE.search(cmd):
            deny("массовое удаление (Remove-Item -Recurse)")
        for pattern, what in DANGEROUS:
            if pattern.search(cmd):
                deny(what)
        if DB_CLIENT.search(cmd) and DB_WRITE.search(cmd):
            deny("изменение базы разовой командой. Сделай файл миграции в migrations/ и запусти командой «бэкап + миграция»")
        if SECRET_IN_CMD.search(cmd):
            deny("доступ к файлу с паролями (.env)")

    sys.exit(0)


if __name__ == "__main__":
    main()
