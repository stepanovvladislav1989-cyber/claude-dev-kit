"""
Выгрузка всего кода задачи в один файл для разбора в чате.

Запуск:
    python scripts/export_ticket.py Т-012     — код задачи по её коммитам
    python scripts/export_ticket.py           — незакоммиченные изменения (если сессия закончилась без коммита)

Результат: exports/Т-012.md (папка не попадает в git). Файл можно перетащить в чат
или открыть и скопировать целиком (Ctrl+A, Ctrl+C).

В файле:
  - что сделано за сессию и итог проверок (раздел «Итог сессии» из файла задачи);
  - новые файлы — целиком;
  - изменённые файлы — «было → стало» (diff: строки с «-» удалены, с «+» добавлены);
  - список удалённых файлов.
Эталон (tests/golden), данные, пароли и служебные файлы не выгружаются.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "exports"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # «пустое состояние» git — для самого первого коммита
SKIP_PREFIXES = ("tests/golden/", "data/", "backups/", "exports/", ".claude/", ".github/")
SKIP_NAMES = ("requirements.txt", ".gitignore", ".pre-commit-config.yaml", ".env.example")
LANG = {".py": "python", ".sql": "sql", ".js": "javascript", ".ts": "typescript", ".md": "markdown",
        ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".html": "html", ".css": "css"}


def git(*args):
    res = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        sys.exit(f"Ошибка git: {res.stderr.strip()}")
    return res.stdout


def skip(path):
    name = Path(path).name
    return (path.startswith(SKIP_PREFIXES) or name in SKIP_NAMES
            or name.startswith(".env") or name.endswith((".pem", ".key")))


def ticket_range(ticket):
    """Диапазон коммитов задачи: от родителя первого до последнего. Ищет «Т-012» (кириллица) и «T-012» (латиница)."""
    num = ticket.split("-", 1)[-1]
    commits = git("log", "--format=%H", f"--grep=Т-{num}", f"--grep=T-{num}").split()
    if not commits:
        sys.exit(f"Коммиты задачи {ticket} не найдены. Номер задачи должен быть в сообщении коммита.")
    first, last = commits[-1], commits[0]
    parents = git("rev-list", "--parents", "-n", "1", first).split()
    base = parents[1] if len(parents) > 1 else EMPTY_TREE
    return base, last, commits


def changed_files(base, head):
    """Список (статус, путь): A — новый, M — изменён, D — удалён, R — переименован."""
    files = []
    for line in git("diff", "--name-status", "-M", base, *([head] if head else [])).splitlines():
        parts = line.split("\t")
        files.append((parts[0][0], parts[-1]))
    if not head:  # незакоммиченные: добавить новые файлы, которые git ещё не отслеживает
        for path in git("ls-files", "--others", "--exclude-standard").splitlines():
            files.append(("A", path))
    return [(s, p) for s, p in files if not skip(p)]


def file_content(path, head):
    if head:
        return git("show", f"{head}:{path}")
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def session_summary(ticket):
    """Раздел «Итог сессии» из файла задачи docs/tickets/Т-012….md."""
    num = ticket.split("-", 1)[-1]
    for f in sorted((ROOT / "docs" / "tickets").glob("*.md")):
        if f.name.startswith(("Т-" + num, "T-" + num)):
            text = f.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip() if text else ticket
            part = text.split("## Итог сессии", 1)
            body = part[1].split("\n", 1)[1] if len(part) > 1 and "\n" in part[1] else ""
            body = body.split("\n## ", 1)[0].strip()
            return title, body
    return ticket, ""


def fence(text, lang=""):
    return f"````{lang}\n{text.rstrip()}\n````\n"


def main():
    ticket = sys.argv[1] if len(sys.argv) > 1 else None
    if ticket:
        base, head, commits = ticket_range(ticket)
        title, name = f"Код задачи {ticket}", ticket
        source = f"Коммитов: {len(commits)} (последний {head[:8]})"
    else:
        base, head = "HEAD", None
        title, name = "Незакоммиченные изменения", "uncommitted"
        source = "Изменения после последнего коммита"

    files = changed_files(base, head)
    new = [p for s, p in files if s == "A"]
    mod = [p for s, p in files if s in ("M", "R")]
    deleted = [p for s, p in files if s == "D"]

    out = [f"# {title}\n\n", f"{source} · выгружено {datetime.now():%d.%m.%Y %H:%M}\n\n",
           f"Новых файлов: {len(new)} · изменённых: {len(mod)} · удалённых: {len(deleted)}\n"]
    if ticket:
        t_title, summary = session_summary(ticket)
        out.append(f"\n## Что сделано — {t_title}\n\n")
        out.append((summary or "Итог сессии в файле задачи не заполнен.") + "\n")
    if new:
        out.append("\n## Новые файлы — целиком\n")
        for p in new:
            out += [f"\n### {p}\n\n", fence(file_content(p, head), LANG.get(Path(p).suffix, ""))]
    if mod:
        out.append("\n## Изменённые файлы — было → стало\n\n")
        out.append("Строки с «-» удалены, с «+» добавлены, остальные — для контекста.\n")
        for p in mod:
            diff = git("diff", "-U5", "-M", base, *([head] if head else []), "--", p)
            out += [f"\n### {p}\n\n", fence(diff, "diff")]
    if deleted:
        out.append("\n## Удалённые файлы\n\n")
        out += [f"- {p}\n" for p in deleted]
    if not files:
        out.append("\nИзменений кода нет.\n")

    OUT_DIR.mkdir(exist_ok=True)
    target = OUT_DIR / f"{name}.md"
    target.write_text("".join(out), encoding="utf-8")
    print(f"Готово: {target.relative_to(ROOT).as_posix()} — новых {len(new)}, изменённых {len(mod)}, удалённых {len(deleted)}")


if __name__ == "__main__":
    main()
