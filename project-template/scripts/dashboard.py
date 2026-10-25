"""
Дашборд проекта: одна страница exports/dashboard.html (папка не попадает в git).

Открыть: двойной клик в браузере или в VS Code через расширение Live Preview
(правый клик по файлу → Show Preview). Страница сама обновляется раз в 5 секунд,
без сервера, библиотек и интернета; светлая или тёмная тема — как в системе.
Агент страницу не читает и не пересказывает — она для пользователя.

Что на странице и откуда:
  - общий цвет: серый — пауза (.claude/PAUSE); оранжевый — агент ждёт вашего решения
    (в ответе блок «Нужно от меня:» или замок отпустил его с неготовой работой);
    красный — последняя проверка НЕ ПРОШЛА; синий — агент работает;
    зелёный — агент закончил ответ или началась новая сессия, проверка не красная
    («работает» ставят замки: на ваше сообщение и protect_tests перед первой правкой или командой после ответа);
  - этапы: допрос (есть ТР) → устройство (design.md утверждён) → нарезка (есть задачи) →
    задачи (все текущего релиза готовы) → приёмка (docs/acceptance/release-N.md) → релиз (метка release-N);
  - цепочка проверок: бриф → ТР → задачи → тесты → приёмка, с числами;
  - задачи по релизам с цветом и счётчиком ремонтов 🔧, итог текущего релиза;
  - требования со статусами («в работе» и «сделано» — по задачам);
  - последние 20 событий: журнал exports/events.jsonl и коммиты git.

Журнал событий пишут замки и скрипты, которые уже есть:
  stop_red — конец ответа агента (и вопрос вам), model_router — запуск субагента с моделью
  (повторный запуск исполнителя по той же задаче — «ремонт»), mark_reviewed — итог ревью,
  validate — итог проверки, session_context — новая сессия. Каждый из них пересобирает страницу.
Ремонт считается по запускам исполнителя: перезапуск в новой сессии тоже попадает в счётчик.
Повторную попытку основного агента (Opus) замок не видит — агент записывает её сам командой
  python scripts/dashboard.py repair
Число 🔧 показывает лимит ремонта (~/.claude/CLAUDE.md, «Лимит ремонта»).

Любая ошибка здесь глушится (функции update, turn_end, subagent_started): страница
не обновится, но работа агента не остановится.

Запуск руками: python scripts/dashboard.py — пересобрать страницу.
"""
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import check_docs
import work_state

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "exports"
PAGE = OUT_DIR / "dashboard.html"
EVENTS = OUT_DIR / "events.jsonl"
TICKETS = ROOT / "docs" / "tickets"
MAX_EVENTS, KEEP_EVENTS = 1000, 500   # журнал длиннее — обрезаем до последних
FEED = 20
ASK_CHARS = 3000
ACTIVITY = ("ask", "turn", "work", "agent", "repair", "review", "session")  # проверку может запустить и пользователь
HIDDEN = ("turn", "work")              # служебное: только для общего цвета, в ленту не идёт

TICKET_HEAD_RE = re.compile(r"^#\s+([ТT]-\d{2,4})\.?\s*(.*)$", re.M)  # латинскую T check_docs считает ошибкой, но задачу видно
FIELD_RE = r"\*\*{}:\*\*\s*([^·\n*]*)"
ASK_MARK_RE = re.compile(r"^[\W]*нужно от меня:\**\s*", re.I)
TOTAL_RE = re.compile(r"(\d+)\s+из\s+(\d+)")


# ---------- журнал событий ----------

def read_events():
    if not EVENTS.is_file():
        return []
    result = []
    for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("kind"), str) and str(e.get("text") or "").strip():
            e["text"] = str(e["text"])
            if "ticket" in e:
                e["ticket"] = str(e["ticket"])
            result.append(e)
    return result


def record(kind, text, **fields):
    """Добавляет событие. Повтор того же, что уже было последним (или того же итога проверки), не пишется."""
    events = read_events()
    if events and events[-1]["kind"] == kind and events[-1]["text"] == text:
        return
    if kind == "validate":
        last = next((e for e in reversed(events) if e["kind"] == "validate"), None)
        if last and last["text"] == text:
            return
    event = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text}
    event.update({k: v for k, v in fields.items() if v is not None})
    if "ticket" not in event and (active := active_ticket()):
        event["ticket"] = active["id"]
    OUT_DIR.mkdir(exist_ok=True)
    with EVENTS.open("a", encoding="utf-8") as f:  # дозапись: замки, сработавшие одновременно, не стирают чужое
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    if len(events) >= MAX_EVENTS:  # редкая обрезка до последних событий
        lines = EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()
        EVENTS.write_text("\n".join(lines[-KEEP_EVENTS:]) + "\n", encoding="utf-8")


def update(kind, text, **fields):
    """Записать событие и пересобрать страницу. Для замков: ошибки глушатся."""
    try:
        record(kind, text, **fields)
        build()
    except Exception:
        pass


def turn_end(ask):
    """Ответ агента закончен. ask — текст вопроса вам (от строки «Нужно от меня:»), '' — вопроса нет."""
    if ask:
        update("ask", ASK_MARK_RE.sub("", ask.strip(), count=1)[:ASK_CHARS] or "Агент ждёт вашего решения")
    else:
        update("turn", "Агент закончил ответ")


def agent_working():
    """Агент снова действует после конца ответа или старта сессии — «работает».
    Вызывается часто (сообщение пользователя, каждая правка и команда), поэтому пишет только при смене состояния."""
    try:
        activity = [e for e in read_events() if e["kind"] in ACTIVITY]
        if activity and activity[-1]["kind"] in ("ask", "turn", "session"):
            update("work", "Агент работает")
    except Exception:
        pass


def agent_model(agent):
    """Модель из описания агента (.claude/agents/<имя>.md проекта или ~/.claude/agents/)."""
    for folder in (ROOT / ".claude" / "agents", Path.home() / ".claude" / "agents"):
        f = folder / f"{agent}.md"
        if f.is_file():
            m = re.search(r"^model:\s*(\w+)", f.read_text(encoding="utf-8", errors="replace"), re.M)
            if m and m.group(1).lower() != "inherit":
                return m.group(1)
    return ""


def subagent_started(agent, model):
    """Запуск субагента. Повторный запуск исполнителя по той же задаче — ремонт."""
    try:
        agent = agent or "general-purpose"
        model = (model or agent_model(agent)).capitalize() or "модели основной сессии"
        active = active_ticket()
        ticket = active["id"] if active else None
        if agent == "implementer" and ticket and any(
                e.get("ticket") == ticket and e.get("agent") == "implementer" for e in read_events()):
            update("repair", f"Ремонт 🔧{repairs_of(ticket) + 1}: исполнитель снова запущен на {model}", agent=agent)
        else:
            update("agent", f"Запущен {agent} на {model}", agent=agent)
    except Exception:
        pass


def repairs_of(ticket):
    return sum(1 for e in read_events() if e["kind"] == "repair" and e.get("ticket") == ticket)


def opus_repair():
    """Повторная попытка основного агента (Opus): замок её не видит, агент записывает её сам.
    Номер в тексте — чтобы два ремонта подряд не склеились в одно событие."""
    active = active_ticket()
    if not active:
        print("Нет задачи «в работе» или на «паузе» — ремонт не записан.")
        return 1
    n = repairs_of(active["id"]) + 1
    record("repair", f"Ремонт 🔧{n}: основной агент снова пишет код на Opus", agent="main", ticket=active["id"])
    build()
    print(f"Ремонт №{n} записан по {active['id']}.")
    return 0


# ---------- документы ----------

def read(path):
    return work_state.read_doc(path) if path.is_file() else ""


def field(name, text):
    """Значение поля «**Имя:** …»; '' — нет или не заполнено (остались варианты шаблона через «|»)."""
    m = re.search(FIELD_RE.format(name), text)
    value = m.group(1).strip() if m else ""
    return "" if "|" in value else value


def tickets():
    result = []
    for f in sorted(TICKETS.glob("*.md")) if TICKETS.is_dir() else []:
        if f.name.startswith("_"):
            continue
        text = check_docs.FENCE_RE.sub("", read(f))
        m = TICKET_HEAD_RE.search(text)
        if not m:
            continue
        result.append({
            "id": "Т-" + m.group(1)[2:], "title": m.group(2).strip(),
            "status": field("Статус", text).lower(),
            "release": field("Релиз", text),
            "trs": check_docs.ticket_trs(text),
        })
    return result


def active_ticket():
    found = tickets()
    for wanted in ("в работе", "пауза"):
        for t in found:
            if t["status"].startswith(wanted):
                return t
    return None


def requirements():
    """ТР из requirements.md: номер, название, статус, есть ли строка источника (разбор — как в check_docs)."""
    body = check_docs.FENCE_RE.sub("", read(ROOT / "docs" / "requirements.md"))
    titles = dict(re.findall(r"^#{1,6}\s+(ТР-\d{2,4})\.?[ \t]*(.*)$", body, re.M))
    result = []
    for id_, section in check_docs.tr_sections_of(body).items():
        s = check_docs.STATUS_RE.search(section)
        result.append({"id": id_, "title": titles.get(id_, "").strip(),
                       "status": (s.group(1) + s.group(2)).strip() if s else "",
                       "source": bool(check_docs.SOURCE_RE.search(section))})
    return result


def brief_coverage():
    """(абзацев брифа, из них упомянуто в других документах)."""
    ids = set(check_docs.brief_ids(check_docs.FENCE_RE.sub("", read(ROOT / check_docs.BRIEF))))
    used = set()
    for f in check_docs.doc_files():
        if f.relative_to(ROOT).as_posix() != check_docs.BRIEF:
            used |= check_docs.brief_refs(check_docs.FENCE_RE.sub("", read(f)))
    return len(ids), len(ids & used)


def git(*args):
    try:
        res = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=10)
        return res.stdout if res.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def commits():
    result = []
    for line in git("log", "-20", "--format=%ct\t%h %s", "--", ".").splitlines():
        stamp, _, text = line.partition("\t")
        if stamp.isdigit():
            result.append({"time": datetime.fromtimestamp(int(stamp)).isoformat(timespec="seconds"),
                           "kind": "commit", "text": f"Коммит {text}"})
    return result


# ---------- что показать ----------

def release_key(name):
    m = re.match(r"\d+", name)
    return (0, int(m.group()), name) if m else (1, 0, name)


def task_state(t, events):
    if t["status"].startswith("готово"):
        return "done", "готово"
    if t["status"].startswith("пауза"):
        return "pause", "пауза"
    if t["status"].startswith("отложен"):
        return "pause", "отложена"
    if t["status"].startswith("в работе"):
        own = [e for e in events if e.get("ticket") == t["id"] and e["kind"] in ("agent", "repair", "review")]
        last = own[-1] if own else {}
        if last.get("kind") == "repair":
            return "repair", "ремонт"
        if last.get("kind") == "agent" and last.get("agent") == "reviewer":
            return "check", "на проверке"
        return "work", "в работе"
    return "wait", "ожидает"


def last_validate(events):
    return next((e for e in reversed(events) if e["kind"] == "validate"), None)


def banner(events, active):
    """(класс цвета, заголовок, пояснение, текст вопроса вам)."""
    on = f" · {active['id']}" if active else ""
    if (ROOT / ".claude" / "PAUSE").exists():
        return "pause", "Пауза", "Работа остановлена по вашей команде", ""
    activity = [e for e in events if e["kind"] in ACTIVITY]
    last, check = (activity[-1] if activity else {}), last_validate(events)
    if last.get("kind") == "ask":
        return "ask", "Нужны вы", "Агент ждёт вашего решения", last["text"]
    if check and check.get("ok") is False:
        return "fail", "Проверка НЕ ПРОШЛА" + on, check["text"], ""
    if not activity:
        return "idle", "Работа ещё не начиналась", check["text"] if check else "Событий пока нет", ""
    if last["kind"] in ("turn", "session"):
        note = check["text"] if check else "Проверка ещё не запускалась"
        title = "Новая сессия" if last["kind"] == "session" else "Агент закончил ответ"
        return "ok", title + " — ждёт вашей команды", note, ""
    return "work", "Агент работает" + on, last["text"], ""


def stages(reqs, tasks, current):
    design = read(ROOT / "docs" / "design.md")
    in_release = [t for t in tasks if t["release"] == current] if current is not None else tasks
    done = [
        bool(reqs),
        bool(re.search(r"\*\*Статус:\*\*\s*утверждено", design[:1000])),
        bool(tasks),
        bool(in_release) and all(t["status"].startswith("готово") for t in in_release),
        isinstance(acc := acceptance(current), tuple) and 0 < acc[1] == acc[0],
        bool(release_number(current)) and bool(git("tag", "-l", f"release-{release_number(current)}").strip()),
    ]
    names = ["допрос", "устройство", "нарезка", "задачи", "приёмка", "релиз"]
    first_open = next((i for i, d in enumerate(done) if not d), len(done))
    return [(n, "done" if i < first_open else "now" if i == first_open else "next") for i, n in enumerate(names)]


def release_number(name):
    m = re.match(r"\d+", name or "")
    return m.group() if m else ""


def current_release(tasks):
    """Первый релиз, где есть неготовые задачи, иначе последний."""
    names = sorted({t["release"] for t in tasks}, key=release_key)
    for name in names:
        if any(not t["status"].startswith("готово") for t in tasks if t["release"] == name):
            return name
    return names[-1] if names else None


def acceptance(release):
    """(подтверждено, всего) из заголовка отчёта приёмки docs/acceptance/release-N.md;
    None — отчёта нет; False — отчёт есть, а чисел «N из M» в начале нет (не считается пройденным)."""
    num = release_number(release)
    text = read(ROOT / "docs" / "acceptance" / f"release-{num}.md") if num else ""
    if not text:
        return None
    m = TOTAL_RE.search("\n".join(text.splitlines()[:5]))
    return (int(m.group(1)), int(m.group(2))) if m else False


def req_state(r, tasks):
    """(класс, статус, задачи) для строки таблицы требований."""
    own = [t for t in tasks if r["id"] in t["trs"]]
    ids = ", ".join(t["id"] for t in own) or "—"
    word = r["status"].split()[0].lower() if r["status"] else ""
    if word in ("отложено", "снято"):
        return "pause", r["status"], ids
    if not own:
        return "fail" if tasks else "wait", "нет задачи" if tasks else "открыто", ids
    if all(t["status"].startswith("готово") for t in own):
        return "done", "сделано", ids
    if any(t["status"].startswith(("в работе", "пауза")) for t in own):
        return "work", "в работе", ids
    return "wait", "ожидает", ids


def chain(reqs, tasks, events, current):
    """Звенья цепочки проверок: (название, ✅/❌/○, подпись)."""
    def link(name, nums, empty, ok=None):
        if nums is None:
            return name, "○", empty
        if nums is False:
            return name, "❌", "нет итога в отчёте"
        x, y = nums
        return name, "✅" if (0 < y == x if ok is None else ok) else "❌", f"{x} из {y}"

    total_b, used_b = brief_coverage()
    open_reqs = [r for r in reqs if (r["status"].split() or [""])[0].lower() not in ("отложено", "снято")]
    covered = {tr for t in tasks for tr in t["trs"]}
    check = last_validate(events)
    return [
        link("бриф разобран", (used_b, total_b) if total_b else None, "брифа нет"),
        link("ТР с источником",(sum(r["source"] for r in reqs), len(reqs)) if reqs else None, "ТР нет"),
        link("ТР в задачах", (sum(r["id"] in covered for r in open_reqs), len(open_reqs))
             if tasks and open_reqs else None, "открытых ТР нет" if tasks else "задач нет"),
        link("тесты", (check.get("passed", 0), check.get("total", 0)) if check else None, "проверки не было",
             check.get("ok") is True if check else None),  # зелёные тесты при красной проверке — не ✅
        link("приёмка", acceptance(current), "ещё не было"),
    ]


def short_time(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return ""


# ---------- страница ----------

STYLE = """
:root{--bg:#f6f6f4;--card:#fff;--text:#1d1d1f;--muted:#6b6b6b;--line:#e3e3e0;
--blue:#2563eb;--orange:#e07b00;--green:#16a34a;--red:#dc2626;--grey:#8a8a8a;--teal:#0d9488;--purple:#7c3aed}
@media (prefers-color-scheme:dark){:root{--bg:#151515;--card:#1f1f1f;--text:#ececec;--muted:#a0a0a0;--line:#333;
--blue:#5b8cff;--orange:#f59e0b;--green:#22c55e;--red:#f05252;--grey:#9a9a9a;--teal:#2dd4bf;--purple:#a78bfa}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;display:grid;gap:12px}
h1{font-size:18px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 8px}
.head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.banner{border-radius:10px;padding:14px 16px;color:#fff}.banner b{font-size:18px;display:block}
.banner.work{background:var(--blue)}.banner.ask{background:var(--orange)}.banner.ok{background:var(--green)}
.banner.fail{background:var(--red)}.banner.pause,.banner.idle{background:var(--grey)}
.need{border:3px solid var(--orange);font-size:16px;white-space:pre-wrap}
.need h2{color:var(--orange);font-size:15px}
.stages{display:flex;flex-wrap:wrap;gap:6px}
.stage{padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.stage.done{background:var(--green);border-color:var(--green);color:#fff}
.stage.now{border:2px solid var(--blue);color:var(--text);font-weight:600}
.chain{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;text-align:center}
.chain div{border:1px solid var(--line);border-radius:8px;padding:8px 4px}.chain .icon{font-size:20px}
.cols{display:grid;grid-template-columns:2fr 1fr;gap:12px}
.release{margin:4px 0 10px}.release h3{font-size:14px;margin:0 0 6px}
.task{display:flex;justify-content:space-between;gap:8px;padding:6px 10px;margin:4px 0;border-radius:6px;
border:1px solid var(--line);border-left:6px solid var(--line);background:var(--card)}
.task.work{border-left-color:var(--orange)}.task.check{border-left-color:var(--teal)}
.task.repair{border-left-color:var(--purple)}.task.done{border-left-color:var(--green)}
.task.pause{border-left-color:var(--grey);color:var(--muted)}
.tag{white-space:nowrap;font-size:12px;font-weight:600}
.task.work .tag{color:var(--orange)}.task.check .tag{color:var(--teal)}.task.repair .tag{color:var(--purple)}
.task.done .tag{color:var(--green)}.task.wait .tag,.task.pause .tag{color:var(--muted)}
dl{display:grid;grid-template-columns:1fr auto;gap:4px 12px;margin:0}dd{margin:0;font-weight:600;text-align:right}
table{width:100%;border-collapse:collapse}td{padding:5px 6px;border-top:1px solid var(--line);vertical-align:top}
td.work{color:var(--orange)}td.done{color:var(--green)}td.fail{color:var(--red)}td.pause,td.wait{color:var(--muted)}
.feed{list-style:none;margin:0;padding:0}.feed li{padding:3px 0;border-top:1px solid var(--line)}
.feed time{color:var(--muted);margin-right:8px;font-variant-numeric:tabular-nums}
@media (max-width:700px){.cols{grid-template-columns:1fr}.chain{grid-template-columns:repeat(2,1fr)}}
"""


def esc(text):
    return html.escape(str(text))


def project_name():
    m = re.search(r"^#\s*Проект:\s*(.+)$", read(ROOT / "CLAUDE.md"), re.M)
    name = m.group(1).strip() if m else ""
    return name if name and name != "НАЗВАНИЕ" else ROOT.name


def render():
    events = read_events()
    tasks, reqs = tickets(), requirements()
    active = active_ticket()
    current = current_release(tasks)
    color, title, note, ask = banner(events, active)
    for t in tasks:
        t["state"], t["label"] = task_state(t, events)
        t["repairs"] = sum(1 for e in events if e["kind"] == "repair" and e.get("ticket") == t["id"])

    parts = [f'<div class="head"><h1>{esc(project_name())}</h1>'
             f'<span class="muted">обновлено {datetime.now():%H:%M:%S}</span></div>',
             f'<div class="banner {color}"><b>{esc(title)}</b>{esc(note)}</div>']
    if ask:
        parts.append(f'<section class="card need"><h2>Что нужно от вас</h2>{esc(ask)}</section>')

    parts.append('<section class="card"><h2>Этапы</h2><div class="stages">' + "".join(
        f'<span class="stage {cls}">{esc(name)}</span>' for name, cls in stages(reqs, tasks, current))
        + "</div></section>")

    parts.append('<section class="card"><h2>Проверки</h2><div class="chain">' + "".join(
        f'<div><div class="icon">{icon}</div>{esc(name)}<br><span class="muted">{esc(caption)}</span></div>'
        for name, icon, caption in chain(reqs, tasks, events, current)) + "</div></section>")

    by_release = {}
    for t in tasks:
        by_release.setdefault(t["release"], []).append(t)
    groups = "".join(
        f'<div class="release"><h3>{esc("Релиз " + name if name else "Без релиза")}</h3>' + "".join(
            f'<div class="task {t["state"]}"><span>{esc(t["id"])} {esc(t["title"])}</span>'
            f'<span class="tag">{esc(t["label"])}{" 🔧" + str(t["repairs"]) if t["repairs"] else ""}</span></div>'
            for t in group) + "</div>"
        for name, group in sorted(by_release.items(), key=lambda kv: release_key(kv[0])))
    parts.append('<div class="cols"><section class="card"><h2>Задачи по релизам</h2>'
                 + (groups or '<span class="muted">Задач пока нет</span>') + "</section>"
                 + summary(current, tasks, reqs, events) + "</div>")

    rows = "".join(
        f'<tr><td>{esc(r["id"])} {esc(r["title"])}</td><td>{esc(ids)}</td><td class="{cls}">{esc(status)}</td></tr>'
        for r in reqs for cls, status, ids in [req_state(r, tasks)])
    parts.append('<section class="card"><h2>Требования</h2>'
                 + (f"<table>{rows}</table>" if rows else '<span class="muted">Требований пока нет</span>')
                 + "</section>")

    feed = sorted([e for e in events if e["kind"] not in HIDDEN] + commits(),
                  key=lambda e: str(e.get("time", "")), reverse=True)[:FEED]
    items = "".join(
        f'<li><time>{esc(short_time(e.get("time")))}</time>'
        f'{esc((e["text"].splitlines() or [""])[0] if e["kind"] != "ask" else "Агент ждёт вашего решения")}'
        f'{esc(" · " + e["ticket"]) if e.get("ticket") else ""}</li>' for e in feed)
    parts.append(f'<section class="card"><h2>Последние события</h2><ul class="feed">{items}</ul></section>'
                 if items else '<section class="card"><h2>Последние события</h2>'
                               '<span class="muted">Событий пока нет</span></section>')

    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="5">'
            f"<title>Дашборд проекта</title><style>{STYLE}</style></head>"
            f'<body><main>{"".join(parts)}</main></body></html>')


def summary(current, tasks, reqs, events):
    if current is None:
        return '<section class="card"><h2>Итог релиза</h2><span class="muted">Релизов пока нет</span></section>'
    own = [t for t in tasks if t["release"] == current]
    trs = sorted({tr for t in own for tr in t["trs"]})
    done_trs = [tr for tr in trs if all(t["status"].startswith("готово") for t in tasks if tr in t["trs"])]
    check = last_validate(events)
    acc = acceptance(current)
    rows = [
        ("ТР сделано", f"{len(done_trs)} из {len(trs)}"),
        ("задач готово", f"{sum(t['status'].startswith('готово') for t in own)} из {len(own)}"),
        ("тестов", f"{check.get('passed', 0)} из {check.get('total', 0)}" if check else "—"),
        ("ремонтов", str(sum(t["repairs"] for t in own))),
        ("приёмка", f"{acc[0]} из {acc[1]}" if acc else "нет итога" if acc is False else "—"),
    ]
    title = "Итог релиза " + (release_number(current) or current) if current else "Итог: задачи без релиза"
    return (f'<section class="card"><h2>{esc(title)}</h2><dl>'
            + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows) + "</dl></section>")


def build():
    OUT_DIR.mkdir(exist_ok=True)
    tmp = PAGE.with_suffix(".tmp")
    tmp.write_text(render(), encoding="utf-8")
    tmp.replace(PAGE)  # браузер не увидит недописанную страницу


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["repair"]:
        return opus_repair()
    if sys.argv[1:] == ["working"]:  # замок на сообщение пользователя: ничего не печатает — вывод ушёл бы агенту
        agent_working()
        return 0
    if sys.argv[1:]:
        print("Неизвестная команда. Можно: без аргументов — пересобрать страницу; repair — записать ремонт; "
              "working — отметить «агент работает».")
        return 2
    build()
    print(f"Дашборд: {PAGE.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
