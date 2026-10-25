"""
Тесты самого комплекта (не проекта): исправления по находкам пилота akt-sverki (шаг 8 плана доработки).
Номер в имени теста — номер находки в разделе 7 docs/upgrade-plan.md:
  1 — отметка ревью, когда фоновый reviewer сдаёт отчёт через SubagentHandback;
  2 — guard не запрещает запись файла, в тексте которого есть SQL или «.env»;
  3 — 🔧 и журнал попыток задачи совпадают;
  4 — «у ПР нет теста» не выдаётся до нарезки;
  5 — conftest ставит временную базу SQLite, когда адрес базы не задан;
  6 — .gitattributes хранит эталон байт в байт;
а также одно место поиска текущей задачи (work_state.active_tickets).
Запуск из корня комплекта: python -m pytest tests
"""
import json
import os
import stat
import subprocess
import sys

import pytest

from test_hooks import GIT, TEMPLATE, change_code, guard, hook, project, review_check, router, run

JOURNAL_TICKET = ("# Т-001. Сверка\n\n**Вид:** обычная · **Сложность:** обычная · **Сценарии:** утверждены\n"
                  "**Статус:** в работе · **Дата:**\n**Требования:** ТР-01\n\n"
                  "## Где остановились (журнал: после каждого шага — шаг · решение и почему · что дальше)\n\n"
                  "- попытка 1 из 4 · implementer на Sonnet\n\n"
                  "## Итог сессии (агент)\n\nЕщё не готово.\n")


# ---------- 1. отметка ревью при фоновом ревьюере ----------

def handback(root, text, agent_type="reviewer", agent_id="a1"):
    return hook(root, "scripts/mark_reviewed.py",
                {"hook_event_name": "PostToolUse", "tool_name": "SubagentHandback", "agent_type": agent_type,
                 "agent_id": agent_id, "tool_input": {"message": text}})


def subagent_stop(root, text, agent_id="a1"):
    return hook(root, "scripts/mark_reviewed.py",
                {"hook_event_name": "SubagentStop", "agent_type": "reviewer", "agent_id": agent_id,
                 "last_assistant_message": text})


def feed(root):
    path = root / "exports" / "events.jsonl"
    return [json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def test_1_otmetka_po_otchyotu_v_subagenthandback(tmp_path):
    root = project(tmp_path)
    change_code(root)
    assert handback(root, "Замечаний нет.\n**ИТОГО: замечаний 0 (критично 0, важно 0, мелочь 0)**")[0] == 0
    assert review_check(root) == 0
    # тот же ревьюер закончил: последнее сообщение — не отчёт, отметку не трогает и «без ИТОГО» не пишет
    assert subagent_stop(root, "Отчёт передан.")[0] == 0
    assert review_check(root) == 0
    assert not any("без строки" in text for text in feed(root))


def test_1_kritichnoe_v_subagenthandback_snimaet_otmetku(tmp_path):
    root = project(tmp_path)
    change_code(root)
    handback(root, "ИТОГО: замечаний 0 (критично 0, важно 0, мелочь 0)")
    handback(root, "ИТОГО: замечаний 1 (критично 1, важно 0, мелочь 0)", agent_id="a2")
    assert review_check(root) == 1


def test_1_chuzhoy_subagent_i_plan_ne_stavyat_otmetku(tmp_path):
    root = project(tmp_path)
    change_code(root)
    handback(root, "ИТОГО: замечаний 0 (критично 0)", agent_type="implementer")
    assert review_check(root) == 1
    handback(root, "ИТОГО ПЛАНА: замечаний 0 (критично 0)")
    assert review_check(root) == 1


def test_1_revyu_bez_peredachi_po_prezhnemu_po_subagentstop(tmp_path):
    root = project(tmp_path)
    change_code(root)
    subagent_stop(root, "ИТОГО: замечаний 0 (критично 0, важно 0, мелочь 0)", agent_id="b1")
    assert review_check(root) == 0


def test_1_hook_na_subagenthandback_v_nastroykah():
    settings = json.loads((TEMPLATE / ".claude" / "settings.json").read_text(encoding="utf-8"))
    post = [h for h in settings["hooks"]["PostToolUse"] if h.get("matcher") == "SubagentHandback"]
    assert post and post[0]["hooks"][0]["args"][0].endswith("scripts/mark_reviewed.py")


# ---------- 2. guard и запись файла с SQL или «.env» в тексте ----------

TEST_FILE_WITH_SQL = ("cat > tests/agent/test_t002.py <<'EOF'\nimport sqlite3\n\ndef test_x(tmp_path):\n"
                      "    c = sqlite3.connect(tmp_path / 'a.db')\n    c.execute('INSERT INTO t VALUES (1)')\nEOF")


@pytest.mark.parametrize("command,tool", [
    (TEST_FILE_WITH_SQL, "Bash"),
    (TEST_FILE_WITH_SQL + "\npython -m pytest tests/agent/test_t002.py -q", "Bash"),
    ("cat >> docs/decisions.md <<'EOF'\n### Р-10. Адрес базы\nБез файла .env — data/akt.db.\nEOF", "Bash"),
    ("cat <<EOF > docs/x.md\nВ .env хранится DELETE FROM — шутка\nEOF", "Bash"),
    ("tee docs/x.md <<'EOF' >/dev/null\nпро .env и sqlite3 UPDATE t SET a = 1\nEOF", "Bash"),
    ("Set-Content -Path docs\\x.md -Encoding utf8 -Value @'\nimport sqlite3; INSERT INTO t; .env\n'@", "PowerShell"),
    ("@'\nimport sqlite3\nDELETE FROM t\n'@ | Set-Content tests\\agent\\test_y.py", "PowerShell"),
])
def test_2_zapis_fayla_s_sql_i_env_v_tekste_mozhno(tmp_path, command, tool):
    assert guard(tmp_path, command, tool) == "ok"


@pytest.mark.parametrize("command,tool", [
    ("cat <<'EOF' | sqlite3 app.db\nINSERT INTO t VALUES (1);\nEOF", "Bash"),       # текст уходит клиенту базы
    ("sqlite3 app.db <<'EOF'\nUPDATE t SET a = 1;\nEOF", "Bash"),
    ("python - <<'EOF'\nimport sqlite3\nsqlite3.connect('app.db').execute('DELETE FROM t')\nEOF", "Bash"),
    ("cat > docs/x.md <<EOF\n$(cat .env)\nEOF", "Bash"),                             # без кавычек у метки — выполнится
    ("cat >> .env <<'EOF'\nKEY=1\nEOF", "Bash"),                                     # пишет в сам .env
    ("cat > docs/x.md <<'EOF'\nтекст\nEOF\ncat .env", "Bash"),                       # что после — проверяется
    ("cat > docs/x.md <<'EOF'\nтекст\nEOF\ngit reset --hard", "Bash"),
    ("Set-Content docs\\x.md @\"\n$(Get-Content .env)\n\"@", "PowerShell"),
    ("@'\nUPDATE t SET a = 1;\n'@ | sqlite3 app.db", "PowerShell"),
    # перед here-string стоит команда, которой он отдан, — вывод в файл ничего не меняет (замечание ревью)
    ("python -c @'\nimport sqlite3; sqlite3.connect('app.db').execute('DELETE FROM t')\n'@ | Out-File log.txt", "PowerShell"),
    ("sqlite3 data/app.db @'\nUPDATE t SET a = 1;\n'@ | Out-File x.txt", "PowerShell"),
    ("Invoke-Expression @'\nGet-Content .env\n'@ | Out-File x.txt", "PowerShell"),
    ("Set-Content x.txt (python -c @'\nimport sqlite3; sqlite3.connect('app.db').execute('DELETE FROM t')\n'@)", "PowerShell"),
    # записанный файл тут же запускается — его текст проверяется (замечание ревью)
    ("cat > run.sh <<'EOF'\nsqlite3 data/app.db \"DELETE FROM t\"\nEOF\nbash run.sh", "Bash"),
    ("cat > run.sh <<'EOF'\ngit reset --hard\nEOF\nbash run.sh", "Bash"),
    ("cat > fix.sql <<'EOF'\nDELETE FROM t;\nEOF\nsqlite3 data/app.db < fix.sql", "Bash"),
    ("Set-Content fix.ps1 @'\nRemove-Item data -Recurse\n'@; ./fix.ps1", "PowerShell"),
    ("@'\nDELETE FROM t;\n'@ | Set-Content fix.sql; Get-Content fix.sql | sqlite3 data/app.db", "PowerShell"),
    # тело достаётся первому heredoc строки (python), а не второму (мелочь ревью)
    ("python - <<'A' ; cat > f.txt <<'B'\nimport sqlite3; sqlite3.connect('app.db').execute('DELETE FROM t')\nB", "Bash"),
    ("cat <<'EOF' > >(sqlite3 data/app.db)\nDELETE FROM t;\nEOF", "Bash"),             # >(…) — команда, не файл
    # 2-й круг ревью: запуск после && в PowerShell, .sql и .sh в папках кода, вывод записи уходит команде
    ("@'\ngit reset --hard\n'@ | Set-Content run.ps1 && ./run.ps1", "PowerShell"),
    ("cat > scripts/fix.sql <<'EOF'\nDELETE FROM t;\nEOF\nsqlite3 data/app.db < scripts/fix.sql", "Bash"),
    ("cat > tests/fix.sql <<'EOF'\nDELETE FROM t;\nEOF\nsqlite3 data/app.db < tests/fix.sql", "Bash"),
    ("cat > scripts/x.sh <<'EOF'\ngit reset --hard\nEOF\nbash scripts/x.sh", "Bash"),
    ("cat > x.txt > >(sqlite3 data/app.db) <<'EOF'\nDELETE FROM t;\nEOF", "Bash"),
    ("tee x.txt >(bash) <<'EOF'\ngit reset --hard\nEOF", "Bash"),
    ("eval \"$(cat > /dev/stdout <<'EOF'\ngit reset --hard\nEOF\n)\"", "Bash"),
    ("iex (Set-Content x.txt -PassThru -Value @'\nGet-Content .env\n'@)", "PowerShell"),
    # 3-й круг ревью: продолжение строки «\», группа на несколько строк, /dev/stdout
    ("cat <<'EOF' > x.sh \\\n&& bash x.sh\ngit reset --hard\nEOF", "Bash"),
    ("(\ntee log.txt <<'EOF'\ngit reset --hard\nEOF\n) | bash", "Bash"),
    ("cat > /dev/stdout <<'EOF' | bash\ngit reset --hard\nEOF", "Bash"),
    ("eval \"$(\ncat > /dev/stdout <<'EOF'\ngit reset --hard\nEOF\n)\"", "Bash"),
])
def test_2_tekst_dlya_vypolneniya_po_prezhnemu_zapreshchyon(tmp_path, command, tool):
    assert guard(tmp_path, command, tool) == "deny"


def test_2_razovyy_skript_vne_proekta_zapuskaemyy_srazu_zapreshchyon(tmp_path):
    # записан и тут же запущен — текст проверяется, как будто он внутри команды (так было до шага 8)
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "fix.py").write_text("print('безобидно')\n", encoding="utf-8")   # старое содержимое не важно
    command = ("cat > ../fix.py <<'EOF'\nimport sqlite3\nsqlite3.connect('data/app.db').execute('UPDATE t SET a=1')\n"
               "EOF\npython ../fix.py")
    assert guard(root, command) == "deny"


# ---------- 3. 🔧 и журнал попыток ----------

def test_3_remont_sam_pishet_popytku_v_zhurnal(tmp_path):
    root = project(tmp_path, commit=False)
    path = root / "docs" / "tickets" / "Т-001.md"
    path.write_text(JOURNAL_TICKET, encoding="utf-8")
    assert router(root, "implementer", "sonnet") == "ok"
    assert router(root, "implementer", "sonnet") == "ok"         # перезапуск исполнителя — 🔧1
    res = subprocess.run([sys.executable, "scripts/dashboard.py", "repair"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout + res.stderr          # Opus после Sonnet — 🔧2
    text = path.read_text(encoding="utf-8")
    journal, rest = text.split("## Где остановились", 1)[1].split("## Итог сессии", 1)
    assert "попытка 2 из 4 (🔧1) · implementer на Sonnet" in journal
    assert "попытка 3 из 4 (🔧2) · основной агент на Opus" in journal
    assert journal.index("попытка 1") < journal.index("попытка 2") < journal.index("попытка 3")
    assert "Ещё не готово." in rest                              # следующий раздел не тронут
    html = run(root, [sys.executable, "scripts/dashboard.py"])
    assert html[0] == 0
    assert "🔧2" in (root / "exports" / "dashboard.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_3_perevody_strok_fayla_zadachi_ne_portyatsya(tmp_path, newline):
    root = project(tmp_path, commit=False)
    path = root / "docs" / "tickets" / "Т-001.md"
    path.write_bytes(JOURNAL_TICKET.encode("utf-8").replace(b"\n", newline))
    for _ in range(2):
        run(root, [sys.executable, "scripts/dashboard.py", "repair"])
    data = path.read_bytes()
    assert b"\r\r\n" not in data and "попытка 3 из 4".encode("utf-8") in data
    if newline == b"\n":
        assert b"\r" not in data
    else:
        assert data.count(b"\n") == data.count(b"\r\n")


def test_3_kodirovka_windows_sohranyaetsya(tmp_path):
    root = project(tmp_path, commit=False)
    path = root / "docs" / "tickets" / "Т-001.md"
    path.write_bytes(JOURNAL_TICKET.encode("cp1251", errors="replace"))
    code, out = run(root, [sys.executable, "scripts/dashboard.py", "repair"])
    assert code == 0 and "добавлена строка" in out, out
    path.read_bytes().decode("cp1251")                            # не переведён в UTF-8
    assert "попытка 2 из 4" in path.read_bytes().decode("cp1251")


def test_3_bityy_utf8_ne_perepisyvaetsya(tmp_path):
    # байт, которого нет ни в UTF-8, ни в cp1251 (0x98 — так в cp1251 видна половина буквы «И» из UTF-8):
    # без потерь файл не прочитать — не трогаем
    root = project(tmp_path, commit=False)
    path = root / "docs" / "tickets" / "Т-001.md"
    data = JOURNAL_TICKET.encode("cp1251", errors="replace") + b"\xd0\x98\n"
    path.write_bytes(data)
    code, out = run(root, [sys.executable, "scripts/dashboard.py", "repair"])
    assert path.read_bytes() == data and "записать не удалось" in out, out


def test_3_zhurnal_ne_zapisan_skazano_chestno(tmp_path):
    root = project(tmp_path, commit=False)
    path = root / "docs" / "tickets" / "Т-001.md"
    path.write_text(JOURNAL_TICKET, encoding="utf-8")
    os.chmod(path, stat.S_IREAD)                                  # файл только для чтения — записать нельзя
    try:
        code, out = run(root, [sys.executable, "scripts/dashboard.py", "repair"])
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    assert code == 0 and "записать не удалось" in out, out


def test_3_zhurnala_net_razdel_dobavlyaetsya(tmp_path):
    root = project(tmp_path, commit=False)
    path = root / "docs" / "tickets" / "Т-001.md"
    path.write_text(JOURNAL_TICKET.split("## Где остановились")[0], encoding="utf-8")
    run(root, [sys.executable, "scripts/dashboard.py", "repair"])
    assert "## Где остановились\n\n- " in path.read_text(encoding="utf-8")


# ---------- 4. «у ПР нет теста» до нарезки ----------

RULES = "# Правила\n\n### ПР-01. Сальдо\nТекст.\n\n### ИНВ-01. Баланс\nТекст.\n"


def check_docs(root):
    return run(root, [sys.executable, "scripts/check_docs.py"])


def test_4_do_narezki_net_testa_ne_shumit(tmp_path):
    root = project(tmp_path, commit=False)
    (root / "docs" / "rules.md").write_text(RULES, encoding="utf-8")
    code, out = check_docs(root)
    assert code == 0, out
    assert "нет теста" not in out


def test_4_posle_narezki_net_testa_vidno(tmp_path):
    root = project(tmp_path, commit=False)
    (root / "docs" / "rules.md").write_text(RULES, encoding="utf-8")
    (root / "docs" / "tickets" / "Т-001.md").write_text("# Т-001. Задача\n\n**Требования:** —\n", encoding="utf-8")
    out = check_docs(root)[1]
    assert "У ПР-01 пока нет теста" in out and "У ИНВ-01 пока нет теста" in out


# ---------- 5. conftest и база SQLite с адресом по умолчанию ----------

def test_5_bez_adresa_bazy_testy_poluchayut_vremennuyu(tmp_path):
    root = project(tmp_path, commit=False)
    seen = tmp_path / "seen.txt"
    (root / "tests" / "agent" / "test_db_url.py").write_text(
        "import os\nfrom pathlib import Path\n\n"
        "def test_db_url():\n"
        "    url = os.environ['DATABASE_URL']\n"
        "    assert url.startswith('sqlite:///') and os.environ['APP_ENV'] == 'test'\n"
        "    path = Path(url[len('sqlite:///'):])\n"
        "    assert path.parent.is_dir() and not path.exists()\n"
        f"    Path(r'{seen}').write_text(str(path), encoding='utf-8')\n", encoding="utf-8")
    env = {"DATABASE_URL": "", "TEST_DATABASE_URL": "", "PYTHONIOENCODING": "utf-8"}
    code, out = run(root, [sys.executable, "-m", "pytest", "tests/agent/test_db_url.py", "-q", "-p", "no:cacheprovider"],
                    env=env)
    assert code == 0, out
    db = seen.read_text(encoding="utf-8")
    assert "test" in db.lower()
    assert not (tmp_path / db).parent.exists()                   # временная папка удалена после тестов


def test_5_rabochiy_adres_bez_testovogo_po_prezhnemu_stop(tmp_path):
    root = project(tmp_path, commit=False)
    code, out = run(root, [sys.executable, "-m", "pytest", "tests/agent", "-q", "-p", "no:cacheprovider"],
                    env={"DATABASE_URL": "sqlite:///data/app.db", "TEST_DATABASE_URL": "", "PYTHONIOENCODING": "utf-8"})
    assert code == 3, out


# ---------- 6. эталон байт в байт ----------

def test_6_etalon_bez_zameny_perevodov_strok(tmp_path):
    root = project(tmp_path)
    data = b"a;b\r\n1;2\r\nlast-line-without-newline"
    (root / "tests" / "golden" / "expected.csv").write_bytes(data)
    out = run(root, ["git", "check-attr", "text", "--", "tests/golden/expected.csv"])[1]
    assert "text: unset" in out
    run(root, [*GIT, "-c", "core.autocrlf=true", "add", "tests/golden/expected.csv"])
    blob = subprocess.run(["git", "show", ":tests/golden/expected.csv"], cwd=root, capture_output=True).stdout
    assert blob == data                                           # без .gitattributes git сделал бы \r\n → \n


# ---------- одно место поиска текущей задачи ----------

def ticket_with(status):
    return JOURNAL_TICKET.replace("**Статус:** в работе", f"**Статус:** {status}")


def test_odno_mesto_poiska_tekushchey_zadachi(tmp_path):
    root = project(tmp_path, commit=False)
    tickets = root / "docs" / "tickets"
    (tickets / "Т-001.md").write_text(ticket_with("пауза"), encoding="utf-8")
    (tickets / "Т-002.md").write_text(ticket_with("отложена").replace("Т-001", "Т-002"), encoding="utf-8")
    code, out = run(root, [sys.executable, "-c", "import sys; sys.path.insert(0, 'scripts'); import work_state, dashboard; "
                                                 "print([f.stem for f, _, _ in work_state.active_tickets()], "
                                                 "dashboard.active_ticket()['id'])"])
    assert code == 0, out
    assert "['Т-001'] Т-001" in out                               # «пауза», когда в работе ничего нет
    (tickets / "Т-003.md").write_text(ticket_with("в работе").replace("Т-001", "Т-003"), encoding="utf-8")
    code, out = run(root, [sys.executable, ".claude/hooks/session_context.py"], "{}")
    assert "docs/tickets/Т-003.md" in out and "Т-001" not in out  # «в работе» важнее «паузы»
