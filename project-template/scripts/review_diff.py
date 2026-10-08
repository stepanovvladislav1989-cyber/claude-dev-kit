"""
Все изменения одним файлом для субагента reviewer — чтобы основной агент не переписывал diff
в задание (это самые дорогие токены, и ревьюер видел бы пересказ, а не сами изменения).

Запуск:
    python scripts/review_diff.py            — незакоммиченные изменения (ревью задачи)
    python scripts/review_diff.py release-1  — всё, что изменилось с метки или коммита (ревью релиза)
    python scripts/review_diff.py --all      — весь код проекта (первый релиз)

Результат: exports/review.diff (папка не попадает в git). Скрипт печатает путь и размер —
путь передаётся ревьюеру, сам файл основной агент не читает.

В файле: коммит и время, список изменённых файлов, затем все изменения (строки с «-» удалены,
с «+» добавлены); новые файлы — целиком. Ничего не отбирается, кроме файлов из .gitignore
(.env с паролями, данные, бэкапы) и двоичных файлов (для них — только размер).
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "exports" / "review.diff"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # «пустое состояние» git


def git(*args):
    res = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        sys.exit(f"Ошибка git: {res.stderr.strip()}")
    return res.stdout


def last_commit():
    res = subprocess.run(["git", "rev-parse", "--verify", "-q", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def base_ref(arg):
    if arg == "--all":
        return EMPTY_TREE
    if arg:
        return arg
    return "HEAD" if last_commit() else EMPTY_TREE


def new_file_text(path):
    data = (ROOT / path).read_bytes()
    if b"\0" in data:
        return f"(двоичный файл, {len(data)} байт)\n"
    lines = data.decode("utf-8", errors="replace").splitlines()
    return "".join(f"+{line}\n" for line in lines)


def main():
    OUT.unlink(missing_ok=True)  # упадёт скрипт — ревьюер не прочтёт старые изменения
    base = base_ref(sys.argv[1] if len(sys.argv) > 1 else "")
    stat = git("diff", base, "--stat=200", "--no-color")
    diff = git("diff", base, "--no-color", "--no-ext-diff", "-M")
    untracked = [p for p in git("ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
    parts = [f"# Изменения относительно {'пустого проекта' if base == EMPTY_TREE else base}\n",
             f"Последний коммит: {last_commit() or 'нет'} · собрано {datetime.now():%d.%m.%Y %H:%M}\n\n",
             "## Изменённые файлы\n", stat or "(нет)\n"]
    if untracked:
        parts.append("Новые файлы (ещё не в git): " + ", ".join(untracked) + "\n")
    parts += ["\n## Изменения\n", diff]
    for path in untracked:
        parts.append(f"\n--- новый файл: {path}\n{new_file_text(path)}")

    OUT.parent.mkdir(exist_ok=True)
    text = "".join(parts)
    OUT.write_text(text, encoding="utf-8")
    print(f"Изменения для ревью: {OUT.relative_to(ROOT).as_posix()} — строк {text.count(chr(10))}")


if __name__ == "__main__":
    main()
