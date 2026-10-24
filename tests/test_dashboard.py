"""
Тесты самого комплекта (не проекта): дашборд scripts/dashboard.py из project-template
и его вызовы из замков. Каждый тест копирует шаблон во временную папку и запускает
скрипты на копии. Запуск из корня комплекта: python -m pytest tests
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "project-template"

REQUIREMENTS = """# Требования

### ТР-01. Акт сверки
Из брифа: «Хочу видеть акт сверки» (Б-1)
Статус: открыто

### ТР-02. Валюта
Источник: допрос, тема 2
Статус: отложено — во втором релизе
"""
BRIEF = "# Бриф\n\nБ-1. Хочу видеть акт сверки.\n"


def ticket(num, status, release="1. Базовый акт", trs="ТР-01"):
    return (f"# Т-{num}. Задача {num}\n\n"
            f"**Вид:** обычная · **Сложность:** обычная · **Сценарии:** утверждены\n"
            f"**Статус:** {status} · **Дата:**\n"
            f"**Требования:** {trs}  **Правила:** —\n"
            f"**Зависит от:** —  **Релиз:** {release}\n")


def project(tmp_path, tickets=None, requirements=None, brief=None):
    root = tmp_path / "proj"
    shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns("__pycache__", "exports"))
    docs = root / "docs"
    if requirements is not None:
        (docs / "requirements.md").write_text(requirements, encoding="utf-8")
    if brief is not None:
        (docs / "brief.md").write_text(brief, encoding="utf-8")
    for name, text in (tickets or {}).items():
        (docs / "tickets" / name).write_text(text, encoding="utf-8")
    return root


def run(root, script, stdin=None):
    res = subprocess.run([sys.executable, str(root / script)], cwd=root, input=stdin,
                         capture_output=True, text=True, encoding="utf-8")
    return res.returncode, res.stdout + res.stderr


def page(root):
    code, out = run(root, "scripts/dashboard.py")
    assert code == 0, out
    return (root / "exports" / "dashboard.html").read_text(encoding="utf-8")


def events(root):
    path = root / "exports" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def subagent(root, agent, model=""):
    data = {"tool_input": {"subagent_type": agent, "model": model}}
    return run(root, ".claude/hooks/model_router.py", json.dumps(data))


def stop(root, agent_text):
    return run(root, ".claude/hooks/stop_red.py", json.dumps({"last_assistant_message": agent_text}))


def test_pustoy_proekt_stranica_sobiraetsya(tmp_path):
    html = page(project(tmp_path))
    assert '<meta http-equiv="refresh" content="5">' in html
    assert "Работа ещё не начиналась" in html
    assert "ещё не было" in html            # звено «приёмка» в цепочке проверок
    assert 'class="stage now">допрос' in html


def test_zadachi_po_relizam_trebovaniya_i_remont(tmp_path):
    root = project(tmp_path, {"Т-001.md": ticket("001", "готово"),
                              "Т-002.md": ticket("002", "в работе")}, REQUIREMENTS, BRIEF)
    approve_design(root)
    assert subagent(root, "implementer", "sonnet")[0] == 0
    assert subagent(root, "implementer", "sonnet")[0] == 0   # второй запуск по той же задаче — ремонт
    html = page(root)
    assert "Релиз 1. Базовый акт" in html
    assert "🔧1" in html
    assert 'class="task repair"' in html
    assert 'class="task done"' in html
    assert "на Sonnet" in html                # модель в ленте событий
    assert 'ТР в задачах<br><span class="muted">1 из 1</span>' in html   # отложенное ТР-02 не считается
    assert "отложено" in html
    assert 'class="stage now">задачи' in html


def test_remont_opus_zapisyvaet_agent_i_ne_skleivaetsya(tmp_path):
    # лимит ремонта: попытки основного агента (Opus) тоже идут в 🔧, два ремонта подряд — два
    root = project(tmp_path, {"Т-001.md": ticket("001", "в работе")}, REQUIREMENTS, BRIEF)
    for _ in range(3):   # три запуска исполнителя подряд — два ремонта, не склеиваются
        assert subagent(root, "implementer", "sonnet")[0] == 0
    res = subprocess.run([sys.executable, str(root / "scripts/dashboard.py"), "repair"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout + res.stderr
    res = subprocess.run([sys.executable, str(root / "scripts/dashboard.py"), "repair"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout + res.stderr
    assert [e["text"][:9] for e in events(root) if e["kind"] == "repair"] == ["Ремонт 🔧1", "Ремонт 🔧2", "Ремонт 🔧3", "Ремонт 🔧4"]
    assert "🔧4" in page(root)


def test_remont_bez_aktivnoy_zadachi_ne_zapisyvaetsya(tmp_path):
    root = project(tmp_path, {"Т-001.md": ticket("001", "готово")}, REQUIREMENTS, BRIEF)
    res = subprocess.run([sys.executable, str(root / "scripts/dashboard.py"), "repair"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1
    assert not (root / "exports" / "events.jsonl").exists()
    res = subprocess.run([sys.executable, str(root / "scripts/dashboard.py"), "repiar"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 2   # опечатка в команде не проходит молча


def test_reviewer_zapushchen_zadacha_na_proverke(tmp_path):
    root = project(tmp_path, {"Т-001.md": ticket("001", "в работе")}, REQUIREMENTS, BRIEF)
    assert subagent(root, "reviewer")[0] == 0
    html = page(root)
    assert 'class="task check"' in html
    assert "Агент работает" in html


def test_vopros_polzovatelyu_oranzhevyy_blok(tmp_path):
    root = project(tmp_path)
    code, out = stop(root, "Макет готов.\n\nНужно от меня:\n1. Выбрать вариант А или Б.")
    assert code == 0, out
    html = (root / "exports" / "dashboard.html").read_text(encoding="utf-8")
    assert "Что нужно от вас" in html
    assert "Выбрать вариант А или Б." in html
    assert 'class="banner ask"' in html


def test_konec_otveta_bez_voprosa_zelenyy(tmp_path):
    root = project(tmp_path)
    assert stop(root, "Нужно от меня:\nрешение")[0] == 0
    assert stop(root, "Готово, всё сделано.")[0] == 0
    html = (root / "exports" / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="banner ok"' in html
    assert "Что нужно от вас" not in html


def test_proverka_ne_proshla_krasnyy(tmp_path):
    root = project(tmp_path)
    (root / "exports").mkdir()
    (root / "exports" / "events.jsonl").write_text(
        json.dumps({"time": "2026-10-23T10:00:00", "kind": "validate", "text": "Проверка НЕ ПРОШЛО",
                    "ok": False, "passed": 3, "total": 5}, ensure_ascii=False) + "\n", encoding="utf-8")
    html = page(root)
    assert 'class="banner fail"' in html
    assert "3 из 5" in html


def test_validate_i_review_pishut_sobytiya(tmp_path):
    root = project(tmp_path)
    code, out = run(root, "scripts/validate.py")
    assert code == 0, out
    review = {"hook_event_name": "PostToolUse", "tool_input": {"subagent_type": "reviewer"},
              "tool_response": "Замечаний нет.\nИТОГО: критично 0, важно 0, мелочей 0"}
    assert run(root, "scripts/mark_reviewed.py", json.dumps(review))[0] == 0
    kinds = [e["kind"] for e in events(root)]
    assert "validate" in kinds and "review" in kinds
    validate = next(e for e in events(root) if e["kind"] == "validate")
    assert validate["ok"] is True and validate["total"] > 0


def test_pauza_seryy(tmp_path):
    root = project(tmp_path, {"Т-001.md": ticket("001", "пауза")}, REQUIREMENTS, BRIEF)
    (root / ".claude" / "PAUSE").write_text("", encoding="utf-8")
    html = page(root)
    assert 'class="banner pause"' in html
    assert 'class="task pause"' in html


def test_bitye_dannye_ne_lomayut_stranicu(tmp_path):
    root = project(tmp_path, {"Т-001.md": "мусор без заголовка", "Т-002.md": ticket("002", "в работе", release="")},
                   "# Требования\n\n### ТР-01. Без статуса\n")
    (root / "exports").mkdir()
    (root / "exports" / "events.jsonl").write_text(
        'не json\n{"kind": 5}\n{"kind": "agent", "text": ""}\n{"kind": "agent", "text": "x", "ticket": 7}\n',
        encoding="utf-8")
    html = page(root)
    assert "Без релиза" in html


def test_oshibka_dashborda_ne_lomaet_zamki(tmp_path):
    root = project(tmp_path, {"Т-001.md": ticket("001", "в работе")}, REQUIREMENTS, BRIEF)
    (root / "scripts" / "dashboard.py").write_text("raise RuntimeError('сломан')\n", encoding="utf-8")
    assert stop(root, "Нужно от меня: ответ")[0] == 0
    assert subagent(root, "implementer", "sonnet")[0] == 0
    assert session(root)[0] == 0
    assert pre_tool(root)[0] == 0
    assert run(root, "scripts/validate.py")[0] == 0
    review = {"hook_event_name": "PostToolUse", "tool_input": {"subagent_type": "reviewer"},
              "tool_response": "ИТОГО: критично 0"}
    code, out = run(root, "scripts/mark_reviewed.py", json.dumps(review))
    assert code == 0 and "отметка ревью поставлена" in out.lower()


def session(root, source="startup"):
    return run(root, ".claude/hooks/session_context.py", json.dumps({"source": source}))


def pre_tool(root):
    data = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "x"}}
    return run(root, ".claude/hooks/protect_tests.py", json.dumps(data))


def git_project(tmp_path):
    """Копия шаблона в git с начальным коммитом и изменённым кодом — замок stop_red проверяет по-настоящему."""
    root = project(tmp_path)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    return root


def test_stop_red_vernul_agenta_dashbord_ne_menyaetsya(tmp_path):
    root = git_project(tmp_path)
    code, out = stop(root, "Готово.")
    assert code == 2, out                      # ревью нет — агент доделывает, ответ не закончен
    assert "ревью" in out
    assert not [e for e in events(root) if e["kind"] in ("turn", "ask")]


def test_stop_red_otpustil_posle_popytok_oranzhevyy(tmp_path):
    root = git_project(tmp_path)
    (root / ".claude" / ".stop_attempts").write_text("3", encoding="utf-8")
    res = subprocess.run([sys.executable, str(root / ".claude/hooks/stop_red.py")], cwd=root,
                         input=json.dumps({"stop_hook_active": True, "last_assistant_message": "Готово."}),
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert "ВНИМАНИЕ" in json.loads(res.stdout)["systemMessage"]   # вывод замка — целый JSON
    html = (root / "exports" / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="banner ask"' in html and "ВНИМАНИЕ: агент остановился" in html


def test_vopros_vazhnee_krasnogo_krasnyy_vazhnee_konca_otveta(tmp_path):
    root = project(tmp_path)
    (root / "exports").mkdir()
    (root / "exports" / "events.jsonl").write_text(
        json.dumps({"time": "2026-10-23T10:00:00", "kind": "validate", "text": "Проверка НЕ ПРОШЛО",
                    "ok": False, "passed": 0, "total": 1}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert stop(root, "Закончил.")[0] == 0
    assert 'class="banner fail"' in (root / "exports" / "dashboard.html").read_text(encoding="utf-8")
    assert stop(root, "Нужно от меня: решение")[0] == 0
    assert 'class="banner ask"' in (root / "exports" / "dashboard.html").read_text(encoding="utf-8")


def test_validate_ne_proshlo_pishet_krasnoe_sobytie(tmp_path):
    root = project(tmp_path)
    (root / "tests" / "agent" / "test_padaet.py").write_text("def test_padaet():\n    assert False\n",
                                                            encoding="utf-8")
    assert run(root, "scripts/validate.py")[0] == 1
    last = [e for e in events(root) if e["kind"] == "validate"][-1]
    assert last["ok"] is False
    assert 'class="banner fail"' in (root / "exports" / "dashboard.html").read_text(encoding="utf-8")


def test_novaya_sessiya_zhdyot_a_deystvie_agenta_siniy(tmp_path):
    root = project(tmp_path)
    assert session(root)[0] == 0
    assert 'class="banner ok"' in page(root)
    assert pre_tool(root)[0] == 0
    html = (root / "exports" / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="banner work"' in html
    assert pre_tool(root)[0] == 0
    assert [e["kind"] for e in events(root)].count("work") == 1   # пишется только при смене состояния
    assert session(root, "compact")[0] == 0                         # сжатие контекста — не новая сессия
    assert 'class="banner work"' in page(root)


def test_zapusk_subagenta_i_vopros_ne_pishut_rabotaet(tmp_path):
    root = project(tmp_path)
    assert stop(root, "Готово.")[0] == 0
    for tool in ("Agent", "AskUserQuestion"):
        data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {}}
        assert run(root, ".claude/hooks/protect_tests.py", json.dumps(data))[0] == 0
    assert "work" not in [e["kind"] for e in events(root)]


def test_zamok_pered_deystviem_podklyuchen_ko_vsem_instrumentam():
    settings = json.loads((TEMPLATE / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = [h for h in settings["hooks"]["PreToolUse"] if "protect_tests" in h["hooks"][0]["command"]]
    assert pre[0]["matcher"] == "*"     # иначе чтение файлов не переводит дашборд в «работает»


def test_proverka_polzovatelya_ne_delaet_sinim(tmp_path):
    root = project(tmp_path)
    assert stop(root, "Готово.")[0] == 0
    assert run(root, "scripts/validate.py")[0] == 0
    assert 'class="banner ok"' in (root / "exports" / "dashboard.html").read_text(encoding="utf-8")


def test_priyomka_bez_itoga_ne_zelenaya(tmp_path):
    root = project(tmp_path, {"Т-001.md": ticket("001", "готово")}, REQUIREMENTS, BRIEF)
    (root / "docs" / "acceptance").mkdir(exist_ok=True)
    (root / "docs" / "acceptance" / "release-1.md").write_text("# Приёмка\n\nтекст без чисел\n", encoding="utf-8")
    approve_design(root)
    html = page(root)
    assert "нет итога в отчёте" in html
    assert 'class="stage now">приёмка' in html    # предыдущие этапы пройдены, а этот — нет
    (root / "docs" / "acceptance" / "release-1.md").write_text("# Приёмка: 0 из 0\n", encoding="utf-8")
    assert 'class="stage now">приёмка' in page(root)    # пустая приёмка — не пройдена
    (root / "docs" / "acceptance" / "release-1.md").write_text("# Приёмка: 2 из 3\n", encoding="utf-8")
    assert 'class="stage now">приёмка' in page(root)    # подтверждено не всё — этап не пройден
    (root / "docs" / "acceptance" / "release-1.md").write_text("# Приёмка: 3 из 3\n", encoding="utf-8")
    assert 'class="stage done">приёмка' in page(root)


def approve_design(root):
    design = root / "docs" / "design.md"
    design.write_text(design.read_text(encoding="utf-8").replace("**Статус:** черновик", "**Статус:** утверждено"),
                      encoding="utf-8")
