"""
Проверка документов: «один факт — одно место».

Ошибки (проверка НЕ ПРОШЛА):
  1. Битая ссылка на файл в документах.
  2. Номер (ТР, ПР, ИНВ, Т, Р, Б) определён больше одного раза.
  3. Упомянут номер, который нигде не определён (для Б — абзаца с таким номером нет в брифе;
     пока файла брифа нет, номера Б не проверяются).
  4. Когда задачи уже нарезаны: ТР нет в поле «Требования» ни одной задачи (кроме «Статус: отложено» и «снято»).
  5. В поле «Требования» задачи нет ни одного ТР.

Предупреждения (проверку не валят):
  6. У правила ПР или инварианта ИНВ пока нет ни одного теста.
  7. У ТР нет строки «Из брифа: …» или «Источник: …», нет строки «Статус: …»
     статус не из списка: открыто, отложено, снято («в работе» и «сделано» видны по задачам)
     или у «отложено» / «снято» нет причины.
  8. Абзац брифа (Б-…) не упомянут ни в одном документе — мог потеряться.
  9. В документе остался маркер [УТОЧНИТЬ: …] — открытый вопрос к пользователю.

Номер считается определённым, если он стоит в начале заголовка:
  ### ПР-07. Выручка по дате подписания акта
В тестах номер указывают в имени теста: test_pr07_... (Python, имя функции или файла)
или в названии теста JS/TS: test("ПР-07 …"). Упоминание в комментарии тестом не считается.
Абзацы брифа docs/brief.md нумерует агент: строка абзаца начинается с «Б-1.».
Бриф — дословный текст заказчика, поэтому ссылки и номера внутри него не проверяются.
Файлы, имя которых начинается с «_» (шаблоны), не проверяются.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PREFIXES = {"ТР": "tr", "ПР": "pr", "ИНВ": "inv", "Т": "t", "Р": "r"}
ID_RE = re.compile(r"(?<![\w-])(ТР|ПР|ИНВ|Т|Р)-(\d{2,4})(?![\w])")
HEAD_RE = re.compile(r"^#{1,6}\s+(ТР|ПР|ИНВ|Т|Р)-(\d{2,4})\b")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE_RE = re.compile(r"```.*?```", re.S)
CLARIFY_RE = re.compile(r"\[УТОЧНИТЬ:")
BRIEF = "docs/brief.md"
BRIEF_HEAD_RE = re.compile(r"^\W*Б-(\d{1,3})\.")  # «Б-1.» или «**Б-1.**» в начале абзаца
BRIEF_RE = re.compile(r"(?<![\w-])Б-(\d{1,3})(?!\d)")
SOURCE_RE = re.compile(r"^\W*(Из брифа|Источник):", re.M)
STATUS_RE = re.compile(r"^\W*Статус:\W*(\w+)(.*)$", re.M)
TR_STATUSES = ("открыто", "отложено", "снято")
TICKET_TR_RE = re.compile(r"Требования\**:\**([^*\n]*)")


def doc_files():
    files = [ROOT / "CLAUDE.md", ROOT / "README.md"]
    files += sorted((ROOT / "docs").rglob("*.md"))
    files += sorted((ROOT / "tests").rglob("*.md"))
    return [f for f in files if f.exists() and not f.name.startswith("_")]


def test_files():
    tests = ROOT / "tests"
    if not tests.exists():
        return []
    return [f for f in tests.rglob("*") if f.is_file() and f.suffix in {".py", ".js", ".ts", ".sql"}]


TEST_NAME_RE = re.compile(r"\b(?:def|class)\s+(test\w*)|\b(?:test|it|describe)\s*\(\s*[\"'`]([^\"'`]+)", re.I)


def test_names():
    """Имена тестов и файлов тестов в нижнем регистре — только там номер правила считается покрытым."""
    names = []
    for f in test_files():
        names.append(f.stem)
        text = f.read_text(encoding="utf-8", errors="replace")
        names += [a or b for a, b in TEST_NAME_RE.findall(text)]
    return "\n".join(names).lower()


def tr_sections_of(body):
    """ТР документа -> текст его раздела: до заголовка того же или старшего уровня
    (подзаголовок внутри ТР раздел не обрывает). Общая функция для check_docs и дашборда."""
    sections, current, tr_level = {}, None, 0
    for line in body.splitlines():
        m = HEAD_RE.match(line)
        level = len(line) - len(line.lstrip("#"))
        if m and m.group(1) == "ТР":
            current, tr_level = f"ТР-{m.group(2)}", level
            sections[current] = ""
        elif level and level <= tr_level:
            current, tr_level = None, 0
        elif current:
            sections[current] += line + "\n"
    return sections


def ticket_trs(body):
    """ТР из поля «Требования» задачи."""
    field = TICKET_TR_RE.search(body)
    return [f"ТР-{n}" for p, n in ID_RE.findall(field.group(1)) if p == "ТР"] if field else []


def brief_ids(body):
    """Номера абзацев брифа («Б-1», …)."""
    return [f"Б-{m.group(1)}" for line in body.splitlines() if (m := BRIEF_HEAD_RE.match(line))]


def brief_refs(body):
    """Упомянутые в документе абзацы брифа."""
    return {f"Б-{n}" for n in BRIEF_RE.findall(body)}


def main():
    errors, warnings = [], []
    defined = defaultdict(list)   # номер -> где определён
    referenced = defaultdict(set) # номер -> где упомянут
    in_tickets = set()            # ТР из поля «Требования» задач
    tr_sections = {}              # ТР -> текст его раздела (до заголовка того же или старшего уровня)
    tickets_without_tr = []       # номера задач с пустым полем «Требования»
    has_brief = (ROOT / BRIEF).exists()

    for f in doc_files():
        rel = f.relative_to(ROOT).as_posix()
        text = f.read_text(encoding="utf-8")
        body = FENCE_RE.sub("", text)  # примеры в блоках кода не считаем

        if rel == BRIEF:
            for id_ in brief_ids(body):
                defined[id_].append(rel)
            continue

        if has_brief:  # без брифа «Б-2» — скорее корпус или форма, а не абзац
            for id_ in brief_refs(body):
                referenced[id_].add(rel)

        ticket_id = None
        for line in body.splitlines():
            m = HEAD_RE.match(line)
            if m:
                defined[f"{m.group(1)}-{m.group(2)}"].append(rel)
                if m.group(1) == "Т" and not ticket_id:
                    ticket_id = f"Т-{m.group(2)}"
        tr_sections.update(tr_sections_of(body))

        if rel.startswith("docs/tickets/") and ticket_id:
            trs = ticket_trs(body)
            in_tickets.update(trs)
            if not trs:
                tickets_without_tr.append(ticket_id)

        for m in ID_RE.finditer(body):
            referenced[f"{m.group(1)}-{m.group(2)}"].add(rel)

        clarify = len(CLARIFY_RE.findall(body))
        if clarify:
            warnings.append(f"В {rel} открытых вопросов [УТОЧНИТЬ]: {clarify}")

        for target in LINK_RE.findall(body):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if path and not (f.parent / path).resolve().exists():
                errors.append(f"Битая ссылка в {rel}: {target}")

    for id_, places in defined.items():
        if len(places) > 1:
            errors.append(f"{id_} определён несколько раз: {', '.join(places)}")

    for id_, places in referenced.items():
        if id_ in defined:
            continue
        if id_.startswith("Б-"):
            errors.append(f"{id_} упомянут, но такого абзаца в брифе нет: {', '.join(sorted(places))}")
        else:
            errors.append(f"{id_} упомянут, но нигде не определён: {', '.join(sorted(places))}")

    for id_ in sorted((d for d in defined if d.startswith("Б-")), key=lambda x: int(x[2:])):
        if id_ not in referenced:
            warnings.append(f"Абзац брифа {id_} не упомянут ни в одном документе (ТР, нецели, Р) — "
                            f"проверьте, не потерялся ли он")

    statuses = {}
    for id_, section in sorted(tr_sections.items()):
        if not SOURCE_RE.search(section):
            warnings.append(f"У {id_} нет строки «Из брифа: …» или «Источник: …» — непонятно, откуда требование")
        m = STATUS_RE.search(section)
        if not m:
            warnings.append(f"У {id_} нет строки «Статус: …» (открыто, отложено или снято)")
            continue
        statuses[id_] = m.group(1).lower()
        if statuses[id_] not in TR_STATUSES:
            warnings.append(f"{id_}: статус «{m.group(1)}» не из списка: {', '.join(TR_STATUSES)} "
                            f"(«в работе» и «сделано» видны по задачам)")
        elif statuses[id_] != "открыто" and not re.search(r"\w", m.group(2)):
            warnings.append(f"{id_}: у статуса «{m.group(1)}» нет причины — нужно «{m.group(1)} — почему»")

    names = test_names()
    for id_ in sorted(defined):
        prefix, num = id_.split("-")
        if prefix not in ("ПР", "ИНВ"):
            continue
        latin = f"{PREFIXES[prefix]}{num}"
        if not re.search(rf"({re.escape(id_.lower())}|{latin})(?!\d)", names):
            warnings.append(f"У {id_} пока нет теста")

    has_tickets = any(t.startswith("Т-") for t in defined)
    for id_ in sorted(defined):
        if has_tickets and id_.startswith("ТР-") and id_ not in in_tickets \
                and statuses.get(id_) not in ("отложено", "снято"):
            errors.append(f"На {id_} не ссылается ни одна задача — добавьте задачу "
                          f"или поставьте у требования «Статус: отложено — причина»")

    for id_ in tickets_without_tr:
        errors.append(f"Задача {id_} не ссылается ни на одно ТР (поле «Требования») — "
                      f"непонятно, какое требование она выполняет")

    for e in errors:
        print(f"ОШИБКА: {e}")
    for w in warnings:
        print(f"Предупреждение: {w}")
    print(f"Документы: номеров определено {len(defined)}, ошибок {len(errors)}, предупреждений {len(warnings)}.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
