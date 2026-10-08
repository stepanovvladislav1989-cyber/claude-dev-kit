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


def last_assistant_text(data):
    """Текст последнего ответа агента: из поля события или из журнала сессии."""
    if isinstance(data.get("last_assistant_message"), str):
        return data["last_assistant_message"]
    path = data.get("transcript_path")
    if not path or not Path(path).is_file():
        return ""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines[-200:]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            texts = [content]
        else:
            texts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
        if entry.get("type") == "user" and texts:
            return ""  # дошли до сообщения пользователя — в этом ходе агент вопроса не задал
        if entry.get("type") == "assistant" and texts:
            return "\n".join(texts)
    return ""


def asks_user(data):
    """Агент задал вопрос по стоп-листу: строка ответа начинается с «Нужно от меня:»."""
    return any(line.replace("*", "").strip().lstrip("#>-• ").lower().startswith(QUESTION_MARK)
               for line in last_assistant_text(data).splitlines())


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

    res = subprocess.run([sys.executable, "scripts/validate.py"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    validate_ok = res.returncode == 0
    review_ok = work_state.review_confirmed() or not work_state.code_changed()
    if validate_ok and review_ok:
        counter.unlink(missing_ok=True)
        sys.exit(0)

    if asks_user(data):
        counter.unlink(missing_ok=True)
        reason = "проверка НЕ ПРОШЛА" if not validate_ok else "ревью не подтверждено"
        notify_user(f"Агент ждёт вашего решения (блок «Нужно от меня»). Работа не готова: {reason}.")

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
