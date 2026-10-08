"""
Хук SessionStart: при старте сессии, после /clear и после сжатия контекста
подгружает агенту журнал текущей задачи — чтобы он продолжал по записям, а не по памяти.

Текущая задача — файл в docs/tickets/ со статусом «в работе», а если такой нет — «пауза».
Агенту передаются: путь к файлу задачи, статус и раздел «Где остановились».
Нет такой задачи — хук молчит.
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TICKETS = ROOT / "docs" / "tickets"
STATUS_RE = re.compile(r"\*\*Статус:\*\*\s*([^·\n]+)")
MAX_CHARS = 4000  # журнал длиннее — показываем последние записи


def journal(text):
    part = text.split("## Где остановились", 1)
    if len(part) < 2:
        return ""
    body = part[1].split("\n", 1)[1] if "\n" in part[1] else ""
    return body.split("\n## ", 1)[0].strip()[-MAX_CHARS:]


def main():
    if not TICKETS.is_dir():
        return
    files = []
    for f in sorted(TICKETS.glob("*.md")):
        if not f.name.startswith("_"):
            text = f.read_text(encoding="utf-8")
            m = STATUS_RE.search(text)
            files.append((f, text, m.group(1).strip().lower() if m else ""))
    for wanted in ("в работе", "пауза"):
        for f, text, status in files:
            if status.startswith(wanted):
                show(f, text, status)
                return


def show(f, text, status):
    rel = f.relative_to(ROOT).as_posix()
    if status.startswith("пауза"):
        print(f"Задача на паузе: {rel}. Сам не продолжай — жди команды пользователя «продолжить».")
    else:
        print(f"Текущая задача: {rel}. Продолжай по её журналу и git log, а не по памяти.")
    print("Журнал «Где остановились»:")
    print(journal(text) or "(пока пуст)")


if __name__ == "__main__":
    main()
