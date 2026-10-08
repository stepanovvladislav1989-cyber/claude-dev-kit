"""
Хук Stop (строгий режим): агент не может закончить работу, пока проверка красная
или пока не подтверждено ревью субагентом reviewer для текущих изменений.

Когда срабатывает:
  - изменён код (что-то вне docs/, tests/, .claude/ и не .md-файлы) или изменён
    существующий тест-приёмки / эталон — иначе не мешаем: обсуждение, документы
    и этап «падающих тестов» проходят свободно;
  - в проекте есть scripts/validate.py.

Подтверждение ревью — файл .claude/.review_ok с отпечатком изменённого кода и тестов,
его ставит замок scripts/mark_reviewed.py, когда reviewer ответил без «критично».
Если файла нет или код изменился после отметки — считаем, что ревью для этих изменений не было,
и не даём закончить. Логика «что изменено» и отпечаток — в scripts/work_state.py.

Защита от бесконечного цикла: после MAX_ATTEMPTS попыток подряд агента отпускают,
а вам показывается предупреждение «работа НЕ готова».

Вопрос вам: если в последнем ответе агента есть строка «Нужно от меня:» (решение по стоп-листу),
хук не гоняет его по кругу, а отпускает и показывает вам, что работа не готова и ждёт решения.

Ваш вопрос: если ваше последнее сообщение — вопрос (кончается на «?»), а агент в этом ходе
только читал файлы (не правил, не запускал команд и субагентов), он просто ответил — хук отпускает
с напоминанием, что задача не доделана. Иначе агента заставляли бы чинить код вместо ответа.

Проверка запускается с --if-changed: если код и тесты не менялись с последнего ПРОШЛО,
тесты повторно не прогоняются.

Пауза: если по вашей команде создан файл .claude/PAUSE, хук не блокирует,
но показывает вам напоминание, что тесты могут быть красными и ревью не подтверждено.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import work_state  # noqa: E402

MAX_ATTEMPTS = 3
QUESTION_MARK = "нужно от меня:"


def notify_user(text):
    print(json.dumps({"systemMessage": text}))
    sys.exit(0)


# Только читают. Любой другой инструмент (правка, команда, субагент) — агент мог что-то изменить
READ_TOOLS = ("Read", "Grep", "Glob", "WebFetch", "WebSearch", "ToolSearch")


def last_turn(data):
    """Последний ход по журналу сессии: (сообщение пользователя или None — не найдено,
    последний текст агента в этом ходе, менял ли агент что-то в этом ходе)."""
    path = data.get("transcript_path")
    if not path or not Path(path).is_file():
        return None, "", True
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    agent_text, changed = None, False
    for line in reversed(lines[-500:]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("isSidechain"):  # записи субагентов — не ход пользователя и не ответ агента
            continue
        content = (entry.get("message") or {}).get("content")
        items = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
        items = [c for c in items if isinstance(c, dict)]
        texts = [c.get("text", "") for c in items if c.get("type") == "text"]
        if entry.get("type") == "assistant":
            if agent_text is None and texts:
                agent_text = "\n".join(texts)
            changed = changed or any(c.get("type") == "tool_use" and c.get("name") not in READ_TOOLS for c in items)
        elif entry.get("type") == "user" and texts and not entry.get("isMeta"):
            typed = [t for t in texts if not t.lstrip().startswith("<")]  # без служебных вставок редактора
            return "\n".join(typed), agent_text or "", changed  # дошли до сообщения пользователя
    return None, agent_text or "", True


def asks_user(agent_text):
    """Агент задал вопрос по стоп-листу: строка ответа начинается с «Нужно от меня:»."""
    return any(line.replace("*", "").strip().lstrip("#>-• ").lower().startswith(QUESTION_MARK)
               for line in agent_text.splitlines())


def answered_question(user_text, changed):
    """Пользователь спросил, агент только ответил и ничего не менял."""
    return user_text is not None and user_text.strip().endswith("?") and not changed


def main():
    data = json.load(sys.stdin)
    counter = ROOT / ".claude" / ".stop_attempts"

    if (ROOT / ".claude" / "PAUSE").exists():
        notify_user("Пауза: работа остановлена по вашей команде. Проверка могла остаться красной.")

    if not (ROOT / "scripts" / "validate.py").exists():
        sys.exit(0)
    if not work_state.code_changed() and not work_state.protected_modified():
        sys.exit(0)

    attempts = int(counter.read_text()) if data.get("stop_hook_active") and counter.exists() else 0

    user_text, agent_text, changed = last_turn(data)
    if isinstance(data.get("last_assistant_message"), str):
        agent_text = data["last_assistant_message"]
    # Вопрос вам или ответ на ваш вопрос — тесты не гоняем: работу доделают после вашего ответа
    if asks_user(agent_text):
        counter.unlink(missing_ok=True)
        notify_user("Агент ждёт вашего решения (блок «Нужно от меня»). Работа не готова: "
                    "проверка и ревью — после вашего ответа.")
    if answered_question(user_text, changed):
        counter.unlink(missing_ok=True)
        notify_user("Агент ответил на ваш вопрос. Задача не доделана — напишите «продолжить», когда будете готовы.")

    res = subprocess.run([sys.executable, "scripts/validate.py", "--if-changed"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    validate_ok = res.returncode == 0
    review_ok = work_state.review_confirmed() or not work_state.code_changed()
    if validate_ok and review_ok:
        counter.unlink(missing_ok=True)
        sys.exit(0)

    attempts += 1
    if attempts > MAX_ATTEMPTS:
        counter.unlink(missing_ok=True)
        reason = "Проверка НЕ ПРОШЛА" if not validate_ok else "ревью не подтверждено"
        notify_user(f"ВНИМАНИЕ: агент остановился после {MAX_ATTEMPTS} попыток. {reason} — работа НЕ готова.")

    counter.write_text(str(attempts))
    if not validate_ok:
        tail = "\n".join(res.stdout.strip().splitlines()[-30:])
        message = (
            f"Проверка НЕ ПРОШЛА (попытка {attempts} из {MAX_ATTEMPTS}). Завершать работу нельзя.\n"
            "Исправь код, а не тесты. Защищённые тесты меняются только с разрешения пользователя.\n"
            "Если нужно решение пользователя — ясно сформулируй вопрос в ответе.\n\n" + tail
        )
    else:
        message = (
            f"Проверка ПРОШЛА, но ревью субагентом reviewer для текущих изменений не подтверждено "
            f"(попытка {attempts} из {MAX_ATTEMPTS}). Завершать работу нельзя.\n"
            "Запусти скилл review (субагент reviewer, не в фоне). Отметку ставит замок, когда reviewer "
            "ответил без «критично»; после исправлений запусти reviewer снова."
        )
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
