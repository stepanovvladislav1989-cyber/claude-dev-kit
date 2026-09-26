"""
Проверка документов: «один факт — одно место».

Ошибки (проверка НЕ ПРОШЛА):
  1. Битая ссылка на файл в документах.
  2. Номер (ТР, ПР, ИНВ, Т, Р) определён больше одного раза.
  3. Упомянут номер, который нигде не определён.

Предупреждения (проверку не валят):
  4. У правила ПР или инварианта ИНВ пока нет ни одного теста.

Номер считается определённым, если он стоит в начале заголовка:
  ### ПР-07. Выручка по дате подписания акта
В тестах номер указывают в имени теста или в описании: test_pr07_... или "ПР-07".
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


def main():
    errors, warnings = [], []
    defined = defaultdict(list)   # номер -> где определён
    referenced = defaultdict(set) # номер -> где упомянут

    for f in doc_files():
        rel = f.relative_to(ROOT).as_posix()
        text = f.read_text(encoding="utf-8")
        body = FENCE_RE.sub("", text)  # примеры в блоках кода не считаем

        for line in body.splitlines():
            m = HEAD_RE.match(line)
            if m:
                defined[f"{m.group(1)}-{m.group(2)}"].append(rel)

        for m in ID_RE.finditer(body):
            referenced[f"{m.group(1)}-{m.group(2)}"].add(rel)

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
        if id_ not in defined:
            errors.append(f"{id_} упомянут, но нигде не определён: {', '.join(sorted(places))}")

    tests_text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in test_files())
    tests_low = tests_text.lower()
    for id_ in sorted(defined):
        prefix, num = id_.split("-")
        if prefix not in ("ПР", "ИНВ"):
            continue
        latin = f"{PREFIXES[prefix]}{num}"
        if id_ not in tests_text and latin not in tests_low:
            warnings.append(f"У {id_} пока нет теста")

    for e in errors:
        print(f"ОШИБКА: {e}")
    for w in warnings:
        print(f"Предупреждение: {w}")
    print(f"Документы: номеров определено {len(defined)}, ошибок {len(errors)}, предупреждений {len(warnings)}.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
