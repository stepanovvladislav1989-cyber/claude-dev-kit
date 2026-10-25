"""
Отметка «ревью субагентом reviewer подтверждено для текущих изменений».

Ставится замком (.claude/settings.json) по ответу субагента reviewer — одним из двух хуков:
  - PostToolUse на SubagentHandback — так отчёт сдаёт фоновый субагент (Claude Code ≥ 2.1.271):
    отчёт приходит прямо во входе хука (tool_input.message), без чтения записи и ожидания;
  - SubagentStop — субагент закончил; ответ — его последнее сообщение (last_assistant_message).
    Если тот же субагент уже сдал отчёт через SubagentHandback, его последнее сообщение — не отчёт:
    хук ничего не делает (номер субагента запоминается в exports/review_handback.txt).
Когда последняя строка ответа, начинающаяся с «ИТОГО:», содержит «критично 0», скрипт записывает в .claude/.review_ok
отпечаток изменений кода и тестов — тот, что снял scripts/review_diff.py вместе с файлом
изменений (exports/review.hash). Так отметка относится к тому, что ревьюер видел: правка кода
между сбором файла изменений и ответом ревьюера отметку не получит. Остались критичные —
отметка снимается: исправить и запустить reviewer снова, чтобы он увидел и исправления.
Ревью плана (до кода) кончается строкой «ИТОГО ПЛАНА:» — отметку не ставит.

Замок .claude/hooks/stop_red.py не даст завершить задачу, а проверка перед коммитом
не даст закоммитить, если отметки нет или код и тесты изменились после неё.
Правка документов (docs/, *.md) отметку не сбивает.

Руками — только по решению пользователя, принявшего оставшееся «критично»:
  python scripts/mark_reviewed.py --user-approved
Команда с этим файлом (из любой папки) требует разрешения пользователя (замок protect_tests);
событие попадает в ленту дашборда; в отчёте — 🟡.

Отметка — защита от пропуска шага, а не от умышленного обхода: агент, который может
запускать код, технически способен её подделать. Она не доказывает качество ревью —
только то, что reviewer видел именно эти изменения и не нашёл критичного.
"""
import json
import re
import sys

import work_state

HANDBACK = work_state.ROOT / "exports" / "review_handback.txt"  # номера ревьюеров, сдавших отчёт через SubagentHandback
TOTAL_RE = re.compile(r"^\s*ИТОГО:.*?критично\s+(\d+)", re.I)
PLAN_RE = re.compile(r"^\s*ИТОГО ПЛАНА:", re.I)
USER_FLAG = "--user-approved"


def verdict(text):
    """True — критичных нет, False — есть, None — в ответе нет строки «ИТОГО:».
    Считается последняя строка, которая начинается с «ИТОГО:»."""
    totals = [m.group(1) for line in text.splitlines() if (m := TOTAL_RE.match(line.replace("*", "")))]
    if not totals:
        return None
    return int(totals[-1]) == 0


def reviewed_hash():
    """Отпечаток, снятый вместе с файлом изменений для ревьюера. Нет файла (review_diff не запускался
    или запускался для релиза — с аргументом) — текущий отпечаток: отметка за то, что есть сейчас."""
    try:
        saved = work_state.REVIEW_HASH.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    return saved or work_state.changes_hash()


def mark(fingerprint):
    work_state.MARKER.parent.mkdir(exist_ok=True)
    work_state.MARKER.write_text(fingerprint, encoding="utf-8")


def show_on_dashboard(text):
    """Итог ревью — в ленту дашборда. Ошибка дашборда глушится."""
    try:
        import dashboard
        dashboard.update("review", text)
    except Exception:
        pass


def summary(text):
    lines = [line.replace("*", "").strip() for line in text.splitlines()]
    plan = [line for line in lines if PLAN_RE.match(line)]
    if plan:
        return "Ревью плана: " + plan[-1].split(":", 1)[1].strip()
    totals = [line for line in lines if TOTAL_RE.match(line)]
    return "Ревью: " + (totals[-1] if totals else "ответ без строки «ИТОГО:»")


def handed_back(agent_id):
    try:
        return bool(agent_id) and agent_id in HANDBACK.read_text(encoding="utf-8").split()
    except OSError:
        return False


def remember_handback(agent_id):
    """Запомнить, что этот ревьюер сдал отчёт передачей (хранятся последние 20 номеров)."""
    if not agent_id:
        return
    try:
        known = HANDBACK.read_text(encoding="utf-8").split()
    except OSError:
        known = []
    HANDBACK.parent.mkdir(exist_ok=True)
    HANDBACK.write_text("\n".join((known + [agent_id])[-20:]) + "\n", encoding="utf-8")


def answer_text(data):
    """Текст отчёта ревьюера или None, если это событие не про отчёт reviewer."""
    if (data.get("agent_type") or "").lower() != "reviewer":
        return None
    event = data.get("hook_event_name")
    if event == "PostToolUse" and data.get("tool_name") == "SubagentHandback":
        remember_handback(data.get("agent_id"))
        return str((data.get("tool_input") or {}).get("message") or "")
    if event == "SubagentStop" and not handed_back(data.get("agent_id")):
        return data.get("last_assistant_message") or ""
    return None


def main():
    sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
    sys.stdout.reconfigure(encoding="utf-8")
    if USER_FLAG in sys.argv:
        mark(work_state.changes_hash())
        show_on_dashboard("Ревью: отметка поставлена вручную — пользователь принял оставшееся «критично»")
        print("Отметка ревью поставлена по решению пользователя.")
        return
    if sys.stdin.isatty():
        print("Отметку ставит замок после ответа субагента reviewer. Запусти скилл review.")
        sys.exit(1)
    text = answer_text(json.load(sys.stdin))
    if text is None:
        sys.exit(0)
    show_on_dashboard(summary(text))
    result = verdict(text)
    if result is None:  # ревью плана или ответ без итога — отметку не трогаем
        return
    if result:
        mark(reviewed_hash())
    else:
        work_state.MARKER.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
