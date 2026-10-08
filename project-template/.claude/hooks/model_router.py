"""
Хук PreToolUse на запуск субагента: следит, чтобы модель соответствовала сложности задачи.

Таблица назначений — в глобальных правилах (~/.claude/CLAUDE.md, «Выбор модели»).
Этот замок держит её автоматически, по полям задачи со статусом «в работе» или «пауза»:
  **Вид:** 💰 …              — деньги и правила;
  **Сложность:** низкая | обычная | высокая.
Значение читается по началу поля: «высокая (устройство)» — это «высокая».
Поле не заполнено (осталось из шаблона) — считаем по строгому варианту: 💰 и высокая.
Поля нет совсем (старые задачи) — не 💰, сложность обычная.
Есть задача «в работе» — берётся она; «пауза» — только если в работе ничего нет.

Запрещает:
  - ревьюера (reviewer) на модели слабее Opus — проверка не понижается никогда;
  - исполнителя (implementer) в 💰-задаче или задаче высокой сложности —
    такой код пишет основной агент на Opus;
  - исполнителя на Haiku, если сложность не «низкая».
Повышать модель можно всегда. Нет задачи «в работе» — исполнителя не ограничиваем.
"""
import json
import re
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")  # Claude Code передаёт данные в UTF-8, Windows по умолчанию читает cp1251
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TICKETS = ROOT / "docs" / "tickets"
WEAK = ("sonnet", "haiku")
STATUS_RE = re.compile(r"\*\*Статус:\*\*\s*([^·\n]+)")
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
    """(имя, 💰 ли задача, сложность) у задачи «в работе», иначе на «паузе»; None — такой нет."""
    if not TICKETS.is_dir():
        return None
    files = [(f, f.read_text(encoding="utf-8")) for f in sorted(TICKETS.glob("*.md")) if not f.name.startswith("_")]
    for wanted in ("в работе", "пауза"):
        for f, text in files:
            if (field(STATUS_RE, text) or "").startswith(wanted):
                return describe(f, text)
    return None


def describe(f, text):
    kind = field(KIND_RE, text)
    level = field(LEVEL_RE, text)
    money = kind is None or "💰" in kind
    if level is None or level.startswith("высок"):
        level = "высокая"
    elif level.startswith("низк"):
        level = "низкая"
    else:
        level = "обычная"
    return f.stem, money, level


def deny(reason):
    print(f"ЗАПРЕЩЕНО замком выбора модели: {reason}. "
          "Таблица назначений — «Выбор модели» в ~/.claude/CLAUDE.md.", file=sys.stderr)
    sys.exit(2)


def main():
    data = json.load(sys.stdin)
    inp = data.get("tool_input", {}) or {}
    agent = (inp.get("subagent_type") or "").lower()
    model = (inp.get("model") or "").lower()

    if agent == "reviewer" and model in WEAK:
        deny(f"ревью на модели {model}. Ревью — только Opus: не передавай model или передай opus")

    if agent == "implementer":
        ticket = active_ticket()
        if ticket:
            name, money, level = ticket
            if money or level == "высокая":
                why = "💰-задача" if money else "высокая сложность"
                deny(f"{name}: {why} — код пишет основной агент на Opus, не implementer")
            if model == "haiku" and level != "низкая":
                deny(f"{name}: Haiku только для низкой сложности, здесь «{level}». Используй sonnet")

    sys.exit(0)


if __name__ == "__main__":
    main()
