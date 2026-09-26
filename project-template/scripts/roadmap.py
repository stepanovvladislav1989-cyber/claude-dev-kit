"""
Дорожная карта проекта: собирается автоматически из файлов задач.

Запуск:  python scripts/roadmap.py   → docs/roadmap.md

Единственный источник статусов — сами задачи (docs/tickets/Т-….md): строки
«**Статус:** …» и «**Релиз:** 2. Название». Отметка «внедрён» — метка версии в git
вида release-2 (ставится при приёмке релиза: git tag release-2).
docs/roadmap.md вручную не правится — он перезаписывается.
"""
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "docs" / "tickets"
OUT = ROOT / "docs" / "roadmap.md"

ICONS = {"готово": "✅", "в работе": "🔄", "пауза": "⏸", "новый": "⬜"}
TITLE_RE = re.compile(r"^#\s+([ТT]-\d+)\.?\s*(.*)$", re.M)
STATUS_RE = re.compile(r"\*\*Статус:\*\*\s*([^·\n]+)")
RELEASE_RE = re.compile(r"\*\*Релиз:\*\*\s*(\d+)\.?\s*([^·\n]*)")


def released():
    """Номер релиза → дата метки release-N (если метка стоит)."""
    try:
        out = subprocess.run(
            ["git", "tag", "-l", "release-*", "--format=%(refname:short) %(creatordate:short)"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    except Exception:
        return {}
    tags = {}
    for line in out.splitlines():
        m = re.match(r"release-(\d+)\s+(\S+)", line)
        if m:
            tags[int(m.group(1))] = m.group(2)
    return tags


def read_tickets():
    items = []
    for f in sorted(TICKETS.glob("*.md")):
        if f.name.startswith("_"):
            continue
        text = f.read_text(encoding="utf-8")
        t = TITLE_RE.search(text)
        if not t:
            continue
        s = STATUS_RE.search(text)
        status = s.group(1).strip().lower() if s else "новый"
        if "|" in status:  # статус не выбран из шаблона
            status = "новый"
        r = RELEASE_RE.search(text)
        rel_no = int(r.group(1)) if r else 0
        rel_name = r.group(2).strip() if r else ""
        items.append({"id": t.group(1), "title": t.group(2).strip(), "status": status,
                      "rel": rel_no, "rel_name": rel_name, "file": f.name})
    return items


def main():
    items = read_tickets()
    tags = released()
    done = sum(1 for i in items if i["status"] == "готово")
    total = len(items)
    pct = round(100 * done / total) if total else 0

    groups = OrderedDict()
    for i in sorted(items, key=lambda x: (x["rel"] or 999, x["id"])):
        g = groups.setdefault(i["rel"], {"name": "", "items": []})
        g["name"] = g["name"] or i["rel_name"]
        g["items"].append(i)

    out = [
        "# Дорожная карта\n\n",
        "> Файл собирается автоматически из `docs/tickets/` — вручную не править.\n\n",
        f"Обновлено {datetime.now():%d.%m.%Y %H:%M} · **готово {done} из {total} ({pct}%)**\n",
    ]
    for no, g in groups.items():
        g_done = sum(1 for i in g["items"] if i["status"] == "готово")
        head = f"Релиз {no}. {g['name']}".strip() if no else "Без релиза"
        mark = f" ✅ внедрён {tags[no]}" if no in tags else ""
        out.append(f"\n## {head} — готово {g_done} из {len(g['items'])}{mark}\n\n")
        for i in g["items"]:
            icon = ICONS.get(i["status"], "❔")
            tail = "" if i["status"] in ("готово", "новый") else f" — {i['status']}"
            out.append(f"- {icon} [{i['id']}](tickets/{i['file']}) {i['title']}{tail}\n")
    if not items:
        out.append("\nЗадач пока нет — они появятся после `/slice`.\n")

    OUT.write_text("".join(out), encoding="utf-8")
    print(f"Дорожная карта обновлена: готово {done} из {total} ({pct}%)")


if __name__ == "__main__":
    main()
