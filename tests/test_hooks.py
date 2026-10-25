"""
Тесты самого комплекта (не проекта): замки после проверки комплекта четырьмя агентами (2026-10-25).
Каждый тест — одна найденная дыра или ложное срабатывание: защита тестов (protect_tests), выбор модели
(model_router), отметка ревью (mark_reviewed, review_diff, work_state), конец ответа (stop_red),
глобальный замок паролей и опасных команд (guard), счёт тестов (validate), номера задач (check_docs, dashboard).
Каждый тест копирует шаблон во временную папку. Запуск из корня комплекта: python -m pytest tests
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
TEMPLATE = KIT / "project-template"
GUARD = KIT / "global" / "hooks" / "guard.py"
GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
MONEY_TICKET = "# Т-001. Сверка\n\n**Вид:** {kind} · **Сложность:** {level} · **Сценарии:** утверждены\n" \
               "**Статус:** в работе · **Дата:**\n**Требования:** ТР-01\n"


def project(tmp_path, commit=True):
    root = tmp_path / "proj"
    shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns("__pycache__", "exports"))
    if commit:
        run(root, ["git", "init", "-q"])
        run(root, [*GIT, "add", "-A"])
        run(root, [*GIT, "commit", "-q", "-m", "init"])
    return root


def run(root, args, stdin=None, env=None):
    res = subprocess.run(args, cwd=root, input=stdin, capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, **(env or {})})
    return res.returncode, res.stdout + res.stderr


def hook(root, script, data):
    return run(root, [sys.executable, script], json.dumps(data, ensure_ascii=False))


def protect(root, tool, tool_input):
    """'ask' — замок спросит пользователя, 'ok' — пропустит."""
    code, out = hook(root, ".claude/hooks/protect_tests.py",
                     {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})
    assert code == 0, out
    return "ask" if '"ask"' in out else "ok"


def guard(root, command, tool="Bash"):
    """'deny', 'ask' или 'ok' глобального замка."""
    code, out = run(root, [sys.executable, str(GUARD)],
                    json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(root)}),
                    env={"CLAUDE_PROJECT_DIR": str(root)})
    if code == 2:
        return "deny"
    return "ask" if '"ask"' in out else "ok"


def router(root, agent, model=""):
    data = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
            "tool_input": {"subagent_type": agent, "model": model, "prompt": "x"}}
    code, out = hook(root, ".claude/hooks/model_router.py", data)
    return "deny" if code == 2 else ("ask" if '"ask"' in out else "ok")


def ticket(root, name="Т-001.md", kind="💰 деньги и правила", level="обычная", encoding="utf-8"):
    path = root / "docs" / "tickets" / name
    path.write_text(MONEY_TICKET.format(kind=kind, level=level), encoding=encoding)
    return path


def review_stop(root, text):
    return hook(root, "scripts/mark_reviewed.py",
                {"hook_event_name": "SubagentStop", "agent_type": "reviewer", "last_assistant_message": text})


# ---------- защита тестов и замков ----------

@pytest.mark.parametrize("command", [
    "wc -l tests/acceptance/*.py",
    "sed -n 1,40p tests/acceptance/README.md",
    "cd tests && python -m pytest tests/acceptance -q",
    "git grep foo -- tests/e2e",
    "cat .git/hooks/pre-commit 2>/dev/null; ls tests/golden",
    "sed -n '1,40p' tests/acceptance/README.md",
    "git commit -m \"$(cat <<'EOF'\nfix: scripts/validate.py и tests/acceptance\nEOF\n)\"",
    "git add -A && git commit -m \"$(cat <<'EOF'\nfix: scripts/validate.py\nEOF\n)\"",
])
def test_chtenie_zashchishchyonnyh_bez_voprosa(tmp_path, command):
    assert protect(project(tmp_path), "Bash", {"command": command}) == "ok"


@pytest.mark.parametrize("command", [
    "ls && python -c \"open('.claude/settings.json','w').write('{}')\"",
    "git status; python -c \"open('.claude/PAUSE','w')\"",
    "ls; truncate -s 0 tests/acceptance/README.md",
    "ls && perl -pi -e s/a/b/ tests/golden/README.md",
    "cd scripts && python mark_reviewed.py --user-approved",
    "python scripts/mark_reviewed.py --user-approved",
    "cp evil.yml .github/workflows/",
    "rm -r .claude/hooks/",
    "rm tests/golden/a.json b/../../c",
    "sed -n -i 's/a/b/' .claude/hooks/stop_red.py",
    "sed -ni 's/a/b/' tests/acceptance/README.md",
    "ls & python -c \"open('.claude/PAUSE','w')\"",
    "echo $(python -c \"open('.claude/PAUSE','w')\")",
    "sed -n 1p --in-place tests/acceptance/README.md",
    "git commit -m x && echo $(python -c \"open('.claude/PAUSE','w')\")",
])
def test_zapis_v_cepochke_komand_sprashivaet(tmp_path, command):
    assert protect(project(tmp_path), "Bash", {"command": command}) == "ask"


@pytest.mark.parametrize("path", [".claude/settings.local.json", ".claude/./hooks/stop_red.py",
                                  "tests/./golden/README.md", "tests/acceptance/../acceptance/README.md"])
def test_obhodnye_puti_k_zamkam_i_etalonu_sprashivayut(tmp_path, path):
    root = project(tmp_path)
    assert protect(root, "Edit", {"file_path": str(root / path)}) == "ask"


def test_novyy_test_zadachi_mozhno_popravit_bez_voprosa(tmp_path):
    root = project(tmp_path)
    new = root / "tests" / "acceptance" / "test_pr07_novyy.py"
    new.write_text("def test_pr07():\n    assert 1\n", encoding="utf-8")
    assert protect(root, "Edit", {"file_path": str(new)}) == "ok"            # ещё не в коммите
    old = root / "tests" / "acceptance" / "README.md"
    assert protect(root, "Write", {"file_path": str(old)}) == "ask"          # есть в коммите
    assert protect(root, "NotebookEdit", {"notebook_path": str(old)}) == "ask"
    upper = root / "tests" / "ACCEPTANCE" / "README.md"                      # Windows: тот же файл
    assert protect(root, "Edit", {"file_path": str(upper)}) == "ask"


def test_sboy_zamka_zashchity_sprashivaet(tmp_path):
    root = project(tmp_path)
    code, out = hook(root, ".claude/hooks/protect_tests.py",
                     {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": "не словарь"})
    assert code == 0 and '"ask"' in out, out


def test_zamki_zapuskayutsya_bez_obolochki_i_blokiruyut_pri_sboe():
    """Без Git Bash путь $CLAUDE_PROJECT_DIR в оболочке PowerShell пустой — поэтому запуск без оболочки."""
    settings = json.loads((TEMPLATE / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = [h for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert all(h["command"] == "python" and h["args"][0].startswith("${CLAUDE_PROJECT_DIR}/") for h in hooks)
    assert all(h.get("onFailure") == "block" for g in settings["hooks"]["PreToolUse"] for h in g["hooks"])
    assert settings["hooks"]["SubagentStop"][0]["hooks"][0]["args"][0].endswith("scripts/mark_reviewed.py")
    # вопрос пользователю и запуск субагента не должны переводить дашборд в «работает»
    protect_matchers = [g["matcher"] for g in settings["hooks"]["PreToolUse"]
                        if "protect_tests" in g["hooks"][0]["args"][0]]
    assert protect_matchers and all(m != "*" and "AskUserQuestion" not in m and "Agent" not in m
                                    for m in protect_matchers)


# ---------- выбор модели ----------

def test_obhod_vybora_modeli_drugim_agentom_zapreshchyon(tmp_path):
    root = project(tmp_path)
    ticket(root)
    assert router(root, "general-purpose", "haiku") == "deny"
    assert router(root, "", "sonnet") == "deny"
    assert router(root, "Explore", "haiku") == "ok"        # только поиск, файлы не правит
    assert router(root, "general-purpose") == "ok"         # модель основной сессии


def test_dengi_bez_znachka_i_polnye_imena_modeley(tmp_path):
    root = project(tmp_path)
    ticket(root, kind="деньги и правила")
    assert router(root, "implementer", "sonnet") == "deny"
    assert router(root, "reviewer", "claude-sonnet-5-5") == "deny"
    assert router(root, "reviewer", "fable") == "ok"
    assert router(root, "acceptor", "claude-opus-5-5") == "ok"


def test_neskolko_zadach_v_rabote_po_samoy_strogoy(tmp_path):
    root = project(tmp_path)
    ticket(root, "Т-001.md", kind="обычная", level="обычная")
    ticket(root, "Т-002.md", kind="💰 деньги", level="обычная")
    assert router(root, "implementer", "sonnet") == "deny"
    code, out = run(root, [sys.executable, ".claude/hooks/session_context.py"], "{}")
    assert code == 0 and "несколько задач" in out


def test_otlozhennaya_zadacha_ne_meshaet(tmp_path):
    root = project(tmp_path)
    ticket(root, "Т-001.md").write_text(
        MONEY_TICKET.format(kind="💰", level="высокая").replace("в работе", "отложена"), encoding="utf-8")
    ticket(root, "Т-002.md", kind="обычная", level="обычная")
    assert router(root, "implementer", "sonnet") == "ok"


def test_fayl_zadachi_v_kodirovke_windows_ne_otklyuchaet_zamok(tmp_path):
    root = project(tmp_path)
    ticket(root, kind="💰 деньги и правила".replace("💰 ", ""), encoding="cp1251")
    assert router(root, "implementer", "sonnet") == "deny"


# ---------- отметка ревью ----------

def change_code(root, text="x = 1\n"):
    (root / "src" / "calc.py").write_text(text, encoding="utf-8")


def review_check(root):
    return run(root, [sys.executable, "scripts/work_state.py", "review"])[0]


def test_otmetka_po_zaversheniyu_subagenta_i_ne_za_plan(tmp_path):
    root = project(tmp_path)
    change_code(root)
    assert review_stop(root, "Замечаний нет.\nИТОГО ПЛАНА: замечаний 0 (критично 0)")[0] == 0
    assert review_check(root) == 1                       # ревью плана — не ревью кода
    assert review_stop(root, "ИТОГО: замечаний 0 (критично 0, важно 0, мелочь 0)")[0] == 0
    assert review_check(root) == 0
    hook(root, "scripts/mark_reviewed.py",
         {"hook_event_name": "SubagentStop", "agent_type": "implementer", "last_assistant_message": "ИТОГО: критично 1"})
    assert review_check(root) == 0                       # чужой субагент отметку не трогает


def test_otmetka_za_to_chto_videl_reviewer(tmp_path):
    root = project(tmp_path)
    change_code(root)
    assert run(root, [sys.executable, "scripts/review_diff.py"])[0] == 0
    change_code(root, "x = 2  # правка после сбора файла изменений\n")
    review_stop(root, "ИТОГО: замечаний 0 (критично 0, важно 0, мелочь 0)")
    assert review_check(root) == 1


def test_kommit_tolko_s_testami_trebuet_revyu(tmp_path):
    root = project(tmp_path)
    (root / "tests" / "agent" / "test_weak.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert review_check(root) == 1


def test_ruchnaya_otmetka_vidna_na_dashborde(tmp_path):
    root = project(tmp_path)
    change_code(root)
    assert run(root, [sys.executable, "scripts/mark_reviewed.py", "--user-approved"])[0] == 0
    events = (root / "exports" / "events.jsonl").read_text(encoding="utf-8")
    assert "вручную" in events


# ---------- конец ответа ----------

def stop(root, data):
    return hook(root, ".claude/hooks/stop_red.py", {"hook_event_name": "Stop", **data})


def test_fonovaya_zadacha_ne_gonyaet_po_krugu(tmp_path):
    root = project(tmp_path)
    change_code(root)
    assert stop(root, {"background_tasks": [{"id": "a1", "type": "subagent", "agent_type": "reviewer"}]})[0] == 0
    assert stop(root, {"last_assistant_message": "Готово."})[0] == 2   # без фоновых — ревью не подтверждено
    server = [{"id": "s1", "type": "shell", "command": "npm run dev"}]    # фоновый сервер — не повод отпустить
    assert stop(root, {"background_tasks": server, "last_assistant_message": "Готово."})[0] == 2


def test_otvet_na_vopros_s_chteniem_komandoy(tmp_path):
    root = project(tmp_path)
    change_code(root)
    transcript = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "почему упал тест? поясни"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "git diff && python -m pytest tests -q"}},
            {"type": "text", "text": "Потому что…"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    code, out = stop(root, {"transcript_path": str(transcript), "last_assistant_message": "Потому что…"})
    assert code == 0 and "ответил на ваш вопрос" in json.loads(out)["systemMessage"], out


# ---------- глобальный замок ----------

@pytest.mark.parametrize("command", [
    'git commit -m "docs: .env в .gitignore"',
    'git commit -m "docs: запрет git reset --hard в правилах"',
    "git push && rm -f build.log",
    'echo ".env" >> .gitignore',
    "git check-ignore .env",
    "cat .git/hooks/pre-commit 2>/dev/null",
    'sqlite3 :memory: "create table t(a int); select 1"',
    "git commit -F - <<'EOF'\ndocs: про git reset --hard и .env\nEOF",
    "if (Test-Path .env) { Write-Output 'есть' }",
])
def test_guard_ne_meshaet_obychnoy_rabote(tmp_path, command):
    assert guard(tmp_path, command) == "ok"


@pytest.mark.parametrize("command,tool", [
    ("git commit --no-veri -m x", "Bash"),
    ("git push origin +main", "Bash"),
    ('cmd /c "rd /q /s tests"', "Bash"),
    ("Get-ChildItem data | Remove-Item", "PowerShell"),
    ("cat .env", "Bash"),
    ('git commit -m "x" && cat .env', "Bash"),
    ('psql -c "UPDATE accounts SET a = 1"', "Bash"),
    ("python -c \"import sqlite3; c=sqlite3.connect('app.db'); c.execute('UPDATE t SET a=1')\"", "Bash"),
    ('sqlite3 app.db "BEGIN; UPDATE t SET a=1; COMMIT;"', "Bash"),
    ("psql <<EOF\nUPDATE t SET a = 1;\nEOF", "Bash"),
    ("python - <<EOF\nimport sqlite3\nsqlite3.connect('app.db').execute('DELETE FROM t')\nEOF", "Bash"),
    ("bash <<'EOF'\ngit reset --hard\nEOF", "Bash"),
    ("python - <<EOF\nprint(open('.env').read())\nEOF", "Bash"),
    ("Get-ChildItem data | Remove-Item -Force", "PowerShell"),
    ('find data -name "*.db" | xargs rm', "Bash"),
    ('find data -name "*.db" -print0 | xargs -0 rm', "Bash"),
    ("Get-ChildItem data | ForEach-Object { Remove-Item $_ }", "PowerShell"),
    ("Get-ChildItem data | ForEach-Object -Process { Remove-Item $_ }", "PowerShell"),
    ("ls data | foreach { rm $_ }", "PowerShell"),
    ('find data -name "*.db" | xargs -n 1 rm', "Bash"),
    ('find data -name "*.db" | xargs -I {} rm {}', "Bash"),
    ("echo $(cat .env) >> .gitignore", "Bash"),
    ("git commit -m x && bash <<'EOF'\ngit reset --hard\nEOF", "Bash"),
])
def test_guard_zakryvaet_lazeyki(tmp_path, command, tool):
    (tmp_path / "data").mkdir()
    assert guard(tmp_path, command, tool) == "deny"


# ---------- счёт тестов и номера задач ----------

def test_ozhidaemo_padayushchiy_test_ne_zelyonyy(tmp_path):
    root = project(tmp_path)
    (root / "tests" / "agent" / "test_xf.py").write_text(
        "import pytest\n\n@pytest.mark.xfail\ndef test_x():\n    assert 0\n", encoding="utf-8")
    code, out = run(root, [sys.executable, "scripts/validate.py"])
    assert "зелёных 1 из 2" in out and "ожидаемо падает" in out, out


def test_latinskaya_T_i_metka_BOM(tmp_path):
    root = project(tmp_path)
    (root / "docs" / "tickets" / "T-002.md").write_text(
        "# T-002. Латинская\n\n**Статус:** новый\n**Требования:** ТР-01\n", encoding="utf-8")
    (root / "docs" / "tickets" / "Т-003.md").write_text(
        "# Т-003. С меткой\n\n**Статус:** в работе\n**Требования:** ТР-01\n", encoding="utf-8-sig")
    code, out = run(root, [sys.executable, "scripts/check_docs.py"])
    assert "латинской «T»" in out
    assert "Т-003 упомянут, но нигде не определён" not in out
    sys.path.insert(0, str(root / "scripts"))
    try:
        import dashboard
        ids = [t["id"] for t in dashboard.tickets()]
    finally:
        sys.path.remove(str(root / "scripts"))
        sys.modules.pop("dashboard", None)
        sys.modules.pop("check_docs", None)
    assert ids == ["Т-002", "Т-003"]
