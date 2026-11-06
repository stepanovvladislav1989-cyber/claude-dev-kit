"""
Хук PreToolUse на запуск субагента: следит, чтобы модель соответствовала сложности задачи.

Таблица назначений — в глобальных правилах («Выбор модели»): ~/.dsh/AGENTS.md в DeepSeek Harness,
~/.claude/CLAUDE.md в Claude Code.
Этот замок держит её автоматически, по полям задачи со статусом «в работе» или «пауза»:
  **Вид:** 💰 … (или слово «деньги») — деньги и правила;
  **Сложность:** низкая | обычная | высокая.
Значение читается по началу поля: «высокая (устройство)» — это «высокая».
Поле не заполнено (осталось из шаблона) — считаем по строгому варианту: 💰 и высокая.
Поля нет совсем (старые задачи) — не 💰, сложность обычная.
Есть задачи «в работе» — берутся они (несколько — по самой строгой); «пауза» — только если в работе
ничего нет. «Отложена», «готово», «новый» — не учитываются.

Запрещает:
  - ревьюера (reviewer) и приёмщика (acceptor) на любой модели, кроме сильной
    (Claude: Opus или сильнее — Fable; DeepSeek Harness: DeepSeek-V4-Pro),
    — проверка не понижается никогда. В DeepSeek Harness пустой model — это Flash, поэтому
    ревьюер, приёмщик и любой агент в 💰-задаче или задаче высокой сложности там обязаны
    указывать модель явно (плагин ставит метку harness = deepseek);
  - исполнителя (implementer) в 💰-задаче или задаче высокой сложности —
    такой код пишет основной агент на сильной модели;
  - исполнителя на Haiku, если сложность не «низкая» (в DeepSeek Harness пары к Haiku нет:
    Flash — это модель обычного кода, как Sonnet);
  - в 💰-задаче или задаче высокой сложности — любого другого агента, который может править файлы,
    на Sonnet, Haiku или DeepSeek Flash (обход через general-purpose). Агенты только для поиска (Explore и др.) — можно.
Повышать модель можно всегда. Нет задачи «в работе» — исполнителя не ограничиваем.
Сбой самого замка — вопрос пользователю, а не молчаливый пропуск.

Разрешённый запуск записывается в ленту дашборда с моделью (scripts/dashboard.py);
повторный запуск исполнителя по той же задаче — «ремонт». Ошибка дашборда запуск не останавливает.
"""
import json
import re
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import work_state  # noqa: E402

WEAK = ("sonnet", "haiku", "flash")  # по вхождению: короткое имя и полный ID (claude-sonnet-…, deepseek-flash)
# Сильные: у Claude — по вхождению (id бывают с версиями, claude-opus-5-5); у DeepSeek — точным именем:
# «pro» в куске имени ничего не значит, а выдуманный id («deepseek-v4-pro-free») не должен считаться сильным.
CLAUDE_STRONG = ("opus", "fable")
DEEPSEEK_STRONG = ("deepseek-v4-pro",)
SEARCH_AGENTS = ("explore", "plan", "claude-code-guide", "statusline-setup")  # не правят файлы
KIND_RE = re.compile(r"\*\*Вид:\*\*\s*([^·\n]+)")
LEVEL_RE = re.compile(r"\*\*Сложность:\*\*\s*([^·\n]+)")


def field(regex, text):
    """Значение поля в нижнем регистре; '' — поля нет; None — не заполнено (варианты через «|»)."""
    m = regex.search(text)
    if not m:
        return ""
    value = m.group(1).strip().lower()
    return None if "|" in value else value


def active_ticket():
    """(имя, 💰 ли задача, сложность) у задачи «в работе», иначе на «паузе»; None — такой нет.
    Несколько задач «в работе» — имена через запятую, 💰 и сложность — по самой строгой."""
    found = [describe(f, text) for f, text, _ in work_state.active_tickets()]
    if not found:
        return None
    order = ("низкая", "обычная", "высокая")
    return (", ".join(n for n, _, _ in found), any(m for _, m, _ in found),
            max((lv for _, _, lv in found), key=order.index))


def describe(f, text):
    kind = field(KIND_RE, text)
    level = field(LEVEL_RE, text)
    money = kind is None or "💰" in kind or "деньг" in kind
    if level is None or level.startswith("высок"):
        level = "высокая"
    elif level.startswith("низк"):
        level = "низкая"
    else:
        level = "обычная"
    return f.stem, money, level


def deny(reason):
    print(f"ЗАПРЕЩЕНО замком выбора модели: {reason}. "
          "Таблица назначений — «Выбор модели» в глобальных правилах "
          "(~/.dsh/AGENTS.md в DeepSeek Harness, ~/.claude/CLAUDE.md в Claude Code).", file=sys.stderr)
    sys.exit(2)


def is_weak(model):
    return any(w in model for w in WEAK)


def is_strong(model):
    return model in DEEPSEEK_STRONG or any(s in model for s in CLAUDE_STRONG)


def check(inp):
    agent = (inp.get("subagent_type") or "").strip().lower()
    model = (inp.get("model") or "").strip().lower()
    # Метку ставит плагин DeepSeek Harness. В Claude Code её нет: там пустой model — основная модель
    # (обычно сильная), а в DeepSeek Harness основной сессией правит Flash, то есть пусто = слабая.
    harness = (inp.get("harness") or "").strip().lower()
    no_model_is_weak = harness == "deepseek"

    if agent in ("reviewer", "acceptor"):
        if model and not is_strong(model):
            deny(f"{agent} на модели {model}. Ревью и приёмка — только сильная модель "
                 "(Opus, Fable, DeepSeek-V4-Pro): не передавай слабую")
        if not model and no_model_is_weak:
            deny(f"{agent} без указания model: в DeepSeek Harness это Flash, слабая модель. "
                 "Передай модель явно: deepseek-v4-pro")

    if agent != "reviewer" and agent != "acceptor" and agent not in SEARCH_AGENTS:
        ticket = active_ticket()
        if ticket:
            name, money, level = ticket
            strict = money or level == "высокая"
            why = "💰-задача" if money else "высокая сложность"
            if agent == "implementer" and strict:
                deny(f"{name}: {why} — код пишет основной агент на сильной модели, не implementer")
            if agent == "implementer" and "haiku" in model and level != "низкая":
                deny(f"{name}: Haiku только для низкой сложности, здесь «{level}». Используй сильную модель")
            if agent != "implementer" and strict and (is_weak(model) or (not model and no_model_is_weak)):
                deny(f"{name}: {why} — агент {agent or 'general-purpose'} на {model or 'модели по умолчанию (Flash)'} "
                     "может писать код; такой код пишет основной агент на сильной модели")
    return agent, model


def ask(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)


def main():
    data = json.load(sys.stdin)
    try:
        agent, model = check(data.get("tool_input", {}) or {})
    except SystemExit:
        raise
    except Exception as error:  # сбой замка не должен молча пропускать запуск
        ask(f"Замок выбора модели не смог проверить запуск ({type(error).__name__}: {error}). "
            "Разрешите, только если модель подходит задаче.")
    show_on_dashboard(agent, model)
    sys.exit(0)


def show_on_dashboard(agent, model):
    try:
        import dashboard
        dashboard.subagent_started(agent, model)
    except Exception:
        pass


if __name__ == "__main__":
    main()
