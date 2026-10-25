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

Защита от бесконечного цикла: после MAX_ATTEMPTS возвратов подряд агента отпускают,
а вам показывается предупреждение «работа НЕ готова».

Вопрос вам: если в последнем ответе агента есть строка «Нужно от меня:» (решение по стоп-листу),
хук не гоняет его по кругу, а отпускает и показывает вам, что работа не готова и ждёт решения.

Ваш вопрос: если в вашем последнем сообщении есть «?», а агент в этом ходе только читал
(файлы, поиск, команды только для чтения и проверки — как их понимает protect_tests; не правил
и не запускал субагентов), он просто ответил — хук отпускает с напоминанием, что задача не доделана.
Иначе агента заставляли бы чинить код вместо ответа.

Фоновые задачи: если у агента ещё работает фоновый субагент (например, reviewer; поле background_tasks,
тип subagent), хук его отпускает — агент вернётся к работе, когда задача закончится, и хук сработает снова.

Проверка запускается с --if-changed: если код и тесты не менялись с последнего ПРОШЛО,
тесты повторно не прогоняются.

Пауза: если по вашей команде создан файл .claude/PAUSE, хук не блокирует,
но показывает вам напоминание, что тесты могут быть красными и ревью не подтверждено.

Дашборд: когда агент заканчивает ответ (хук его отпускает), страница exports/dashboard.html
пересобирается — с вопросом вам, если он есть (scripts/dashboard.py). Ошибка дашборда работу не ломает.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import work_state  # noqa: E402
from protect_tests import read_only  # noqa: E402

MAX_ATTEMPTS = 3  # возвраты замка — не попытки ремонта из «Лимита ремонта»
TRANSCRIPT_TAIL = 4_000_000  # байт с конца журнала сессии: журнал бывает в десятки МБ
QUESTION_MARK = "нужно от меня:"
released_unfinished = ""  # предупреждение, с которым агента отпустили после всех попыток — для дашборда


def notify_user(text):
    print(json.dumps({"systemMessage": text}))
    sys.exit(0)


# Только читают. Команды — по protect_tests.read_only; правка и субагент — агент мог что-то изменить
READ_TOOLS = ("Read", "Grep", "Glob", "WebFetch", "WebSearch", "ToolSearch")
SHELL_TOOLS = ("Bash", "PowerShell")


def changes_something(item):
    if item.get("type") != "tool_use" or item.get("name") in READ_TOOLS:
        return False
    if item.get("name") in SHELL_TOOLS:
        return not read_only((item.get("input") or {}).get("command", ""))
    return True


def transcript_lines(path):
    """Строки хвоста журнала сессии — без чтения всего файла."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TRANSCRIPT_TAIL))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > TRANSCRIPT_TAIL else lines  # первая строка хвоста обрезана


def last_turn(data):
    """Последний ход по журналу сессии: (сообщение пользователя или None — не найдено,
    последний текст агента в этом ходе, менял ли агент что-то в этом ходе)."""
    path = data.get("transcript_path")
    if not path or not Path(path).is_file():
        return None, "", True
    lines = transcript_lines(path)
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
            changed = changed or any(changes_something(c) for c in items)
        elif entry.get("type") == "user" and texts and not entry.get("isMeta"):
            typed = [t for t in texts if not t.lstrip().startswith("<")]  # без служебных вставок редактора
            return "\n".join(typed), agent_text or "", changed  # дошли до сообщения пользователя
    return None, agent_text or "", True


def ask_text(agent_text):
    """Вопрос по стоп-листу: текст ответа от строки, начинающейся с «Нужно от меня:», до конца; '' — вопроса нет."""
    lines = agent_text.splitlines()
    for i, line in enumerate(lines):
        if line.replace("*", "").strip().lstrip("#>-• ").lower().startswith(QUESTION_MARK):
            return "\n".join(lines[i:]).strip()
    return ""


def asks_user(agent_text):
    """Агент задал вопрос по стоп-листу: строка ответа начинается с «Нужно от меня:»."""
    return bool(ask_text(agent_text))


def show_on_dashboard(data):
    """Ответ агента закончен — пересобрать дашборд. Любая ошибка (журнал сессии, дашборд) глушится."""
    try:
        agent_text = data.get("last_assistant_message")
        if not isinstance(agent_text, str):
            agent_text = last_turn(data)[1]
        import dashboard
        dashboard.turn_end(released_unfinished or ask_text(agent_text))
    except Exception:
        pass


def answered_question(user_text, changed):
    """Пользователь спросил, агент только ответил и ничего не менял."""
    return user_text is not None and "?" in user_text and not changed


def main():
    data = json.load(sys.stdin)
    try:
        check(data)
    except SystemExit as e:
        if e.code != 2:  # 2 — агента вернули доделывать, ответ не закончен
            show_on_dashboard(data)
        raise


def check(data):
    global released_unfinished
    counter = ROOT / ".claude" / ".stop_attempts"

    if (ROOT / ".claude" / "PAUSE").exists():
        notify_user("Пауза: работа остановлена по вашей команде. Проверка могла остаться красной.")

    if not (ROOT / "scripts" / "validate.py").exists():
        sys.exit(0)
    if not work_state.code_changed() and not work_state.protected_modified():
        sys.exit(0)

    # Агент ждёт фонового субагента (например, reviewer) — вернётся сам, когда тот закончит.
    # Фоновый сервер (тип shell) работает всю сессию — его не ждём, проверка как обычно.
    if any(isinstance(t, dict) and t.get("type") == "subagent" for t in data.get("background_tasks") or []):
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
        released_unfinished = f"ВНИМАНИЕ: агент остановился после {MAX_ATTEMPTS} возвратов замка. {reason} — работа НЕ готова."
        notify_user(released_unfinished)

    counter.write_text(str(attempts))
    if not validate_ok:
        tail = "\n".join(res.stdout.strip().splitlines()[-30:])
        message = (
            f"Проверка НЕ ПРОШЛА (возврат замка {attempts} из {MAX_ATTEMPTS}; это не попытка из «Лимита ремонта»). Завершать работу нельзя.\n"
            "Исправь код, а не тесты. Защищённые тесты меняются только с разрешения пользователя.\n"
            "Если нужно решение пользователя — ясно сформулируй вопрос в ответе.\n\n" + tail
        )
    else:
        message = (
            f"Проверка ПРОШЛА, но ревью субагентом reviewer для текущих изменений не подтверждено "
            f"(возврат замка {attempts} из {MAX_ATTEMPTS}). Завершать работу нельзя.\n"
            "Запусти скилл review и дождись итога субагента reviewer. Отметку ставит замок, когда reviewer "
            "ответил без «критично»; после исправлений запусти reviewer снова."
        )
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
