"""
Отметка «ревью субагентом reviewer подтверждено для текущих изменений».

Ставится замком: хук PostToolUse на запуск субагента (.claude/settings.json).
Когда субагент reviewer закончил и последняя строка его ответа, начинающаяся с «ИТОГО:»,
содержит «критично 0», скрипт записывает отпечаток текущих изменений кода и тестов
в .claude/.review_ok. Остались критичные — отметка снимается: исправить и запустить
reviewer снова, чтобы он увидел и исправления.

Замок .claude/hooks/stop_red.py не даст завершить задачу, а проверка перед коммитом
не даст закоммитить, если отметки нет или код и тесты изменились после неё.
Правка документов (docs/, *.md) отметку не сбивает.

Руками — только по решению пользователя, принявшего оставшееся «критично»:
  python scripts/mark_reviewed.py --user-approved
Команда с этим файлом требует разрешения пользователя (замок protect_tests); в отчёте — 🟡.

Отметка — защита от пропуска шага, а не от умышленного обхода: агент, который может
запускать код, технически способен её подделать. Она не доказывает качество ревью —
только то, что reviewer видел именно эти изменения и не нашёл критичного.
"""
import json
import re
import sys

import work_state

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
sys.stdout.reconfigure(encoding="utf-8")

TOTAL_RE = re.compile(r"^\s*ИТОГО:.*?критично\s+(\d+)", re.I)
USER_FLAG = "--user-approved"


def response_text(response):
    """Только текст ответа reviewer — без задания и служебных полей инструмента."""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        response = response.get("content")
    if isinstance(response, list):
        return "\n".join(c.get("text", "") for c in response if isinstance(c, dict) and c.get("type") == "text")
    return ""


def verdict(text):
    """True — критичных нет, False — есть, None — в ответе нет строки «ИТОГО:» (например, запуск в фоне).
    Считается последняя строка, которая начинается с «ИТОГО:»."""
    totals = [m.group(1) for line in text.splitlines() if (m := TOTAL_RE.match(line.replace("*", "")))]
    if not totals:
        return None
    return int(totals[-1]) == 0


def mark():
    work_state.MARKER.parent.mkdir(exist_ok=True)
    work_state.MARKER.write_text(work_state.changes_hash(), encoding="utf-8")


def tell_agent(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}},
                     ensure_ascii=False))
    sys.exit(0)


def main():
    if USER_FLAG in sys.argv:
        mark()
        print("Отметка ревью поставлена по решению пользователя.")
        return
    if sys.stdin.isatty():
        print("Отметку ставит замок после ответа субагента reviewer. Запусти скилл review.")
        sys.exit(1)
    data = json.load(sys.stdin)
    inp = data.get("tool_input") or {}
    if data.get("hook_event_name") != "PostToolUse" or inp.get("subagent_type") != "reviewer":
        sys.exit(0)

    result = verdict(response_text(data.get("tool_response")))
    if result is None:
        tell_agent("Отметка ревью НЕ поставлена: в ответе reviewer нет строки «ИТОГО:». "
                   "Запускай reviewer не в фоне и дождись его итога.")
    if not result:
        work_state.MARKER.unlink(missing_ok=True)
        tell_agent("Отметка ревью НЕ поставлена: есть критичные замечания. Исправь их, "
                   "проверь validate и запусти reviewer снова — он должен увидеть исправления. "
                   "Если пользователь решил принять замечание — спроси его (блок «Нужно от меня:»).")
    mark()
    tell_agent("Отметка ревью поставлена для текущих изменений. Изменишь код или тесты — "
               "понадобится новое ревью.")


if __name__ == "__main__":
    main()
