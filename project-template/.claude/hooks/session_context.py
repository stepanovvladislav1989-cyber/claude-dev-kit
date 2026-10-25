"""
Хук SessionStart: при старте сессии, после /clear и после сжатия контекста
подгружает агенту журнал текущей задачи — чтобы он продолжал по записям, а не по памяти.

Текущая задача — файл в docs/tickets/ со статусом «в работе», а если такой нет — «пауза».
Задач «в работе» несколько — агент получает их список и просьбу оставить в работе одну
(остальные — «отложена» или «пауза»): иначе замок выбора модели берёт самую строгую.
Агенту передаются: путь к файлу задачи, статус и раздел «Где остановились».
Нет такой задачи — хук молчит.

Заодно пересобирает дашборд (scripts/dashboard.py) с событием «новая сессия» — кроме сжатия
контекста, это продолжение работы; ошибка дашборда глушится.
"""
import json
import re
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TICKETS = ROOT / "docs" / "tickets"
STATUS_RE = re.compile(r"\*\*Статус:\*\*\s*([^·\n]+)")
sys.path.insert(0, str(ROOT / "scripts"))
import work_state  # noqa: E402

MAX_CHARS = 4000  # журнал длиннее — показываем последние записи


def journal(text):
    part = text.split("## Где остановились", 1)
    if len(part) < 2:
        return ""
    body = part[1].split("\n", 1)[1] if "\n" in part[1] else ""
    return body.split("\n## ", 1)[0].strip()[-MAX_CHARS:]


def show_on_dashboard():
    try:
        import dashboard
        dashboard.update("session", "Новая сессия")
    except Exception:
        pass


def main():
    try:
        source = "" if sys.stdin.isatty() else json.load(sys.stdin).get("source", "")
    except (ValueError, AttributeError, OSError):
        source = ""
    if source != "compact":  # сжатие контекста посреди работы — не новая сессия
        show_on_dashboard()
    if not TICKETS.is_dir():
        return
    files = []
    for f in sorted(TICKETS.glob("*.md")):
        if not f.name.startswith("_"):
            text = work_state.read_doc(f)
            m = STATUS_RE.search(text)
            files.append((f, text, m.group(1).strip().lower() if m else ""))
    for wanted in ("в работе", "пауза"):
        found = [(f, text, status) for f, text, status in files if status.startswith(wanted)]
        if found:
            show(*found[0])
            if len(found) > 1:
                names = ", ".join(f.stem for f, _, _ in found)
                print(f"Внимание: со статусом «{wanted}» несколько задач: {names}. Оставь одну, "
                      "остальным поставь «отложена» или «пауза» (замок выбора модели берёт самую строгую).")
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
