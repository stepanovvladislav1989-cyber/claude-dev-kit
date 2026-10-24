"""
Тесты самого комплекта (не проекта): слепая приёмка — проверки приёмщика tests/blind/release-N,
отчёт docs/acceptance/release-N.md и замки вокруг них. До метки release-N проверки приёмщика
в общую проверку не входят и не защищены; после метки — входят и защищены, как tests/acceptance.
Каждый тест копирует шаблон во временную папку. Запуск из корня комплекта: python -m pytest tests
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "project-template"
GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
FAILING = "def test_tr01():\n    assert 1 == 2\n"


def project(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns("__pycache__", "exports"))
    return root


def run(root, args, stdin=None):
    res = subprocess.run(args, cwd=root, input=stdin, capture_output=True, text=True, encoding="utf-8")
    return res.returncode, res.stdout + res.stderr


def blind(root, release, text=FAILING):
    folder = root / "tests" / "blind" / f"release-{release}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"test_blind_r{release}_tr01.py"
    path.write_text(text, encoding="utf-8")
    return path


def commit_and_tag(root, tag=None):
    run(root, ["git", "init", "-q"])
    run(root, [*GIT, "add", "-A"])
    run(root, [*GIT, "commit", "-q", "-m", "init"])
    if tag:
        run(root, [*GIT, "tag", tag])


def validate(root):
    return run(root, [sys.executable, "scripts/validate.py"])


def hook(root, tool, tool_input):
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    return run(root, [sys.executable, ".claude/hooks/protect_tests.py"], json.dumps(data))


def test_do_metki_proverki_priemshchika_ne_v_obshchey_proverke(tmp_path):
    root = project(tmp_path)
    blind(root, 1)
    commit_and_tag(root)
    code, out = validate(root)
    assert code == 0, out
    assert "ИТОГ: ПРОШЛО" in out


def test_posle_metki_proverki_priemshchika_v_obshchey_proverke(tmp_path):
    root = project(tmp_path)
    blind(root, 1)
    commit_and_tag(root, "release-1")
    code, out = validate(root)
    assert code == 1, out
    assert "ИТОГ: НЕ ПРОШЛО" in out
    assert "test_blind_r1_tr01" in out


def test_prinyatyy_reliz_zashchishchen_neprinyatyy_net(tmp_path):
    root = project(tmp_path)
    accepted = blind(root, 1, "def test_tr01():\n    assert True\n")
    fresh = blind(root, 2, "def test_tr01():\n    assert True\n")
    commit_and_tag(root, "release-1")
    fresh.write_text("def test_tr01():\n    assert 1\n", encoding="utf-8")
    assert run(root, [sys.executable, "scripts/work_state.py", "protected"])[0] == 0
    accepted.write_text("def test_tr01():\n    assert 1\n", encoding="utf-8")
    code, out = run(root, [sys.executable, "scripts/work_state.py", "protected"])
    assert code == 1, out
    assert "tests/blind/release-1/test_blind_r1_tr01.py" in out
    assert "release-2" not in out


def test_zamok_sprashivaet_tolko_pro_prinyatyy_reliz(tmp_path):
    root = project(tmp_path)
    accepted = blind(root, 1, "def test_tr01():\n    assert True\n")
    fresh = blind(root, 2, "def test_tr01():\n    assert True\n")
    commit_and_tag(root, "release-1")
    code, out = hook(root, "Edit", {"file_path": str(accepted)})
    assert code == 0 and '"ask"' in out, out
    code, out = hook(root, "Edit", {"file_path": str(fresh)})
    assert code == 0 and '"ask"' not in out, out
    new_file = accepted.parent / "test_blind_r1_tr02.py"
    code, out = hook(root, "Write", {"file_path": str(new_file)})
    assert code == 0 and '"ask"' not in out, out      # новые проверки в принятом релизе — без вопросов
    code, out = hook(root, "PowerShell", {"command": "Remove-Item tests/blind/release-1/test_blind_r1_tr01.py"})
    assert '"ask"' in out, out
    for cmd in ("Remove-Item -Recurse tests/blind/release-1", "rm -rf tests/blind"):
        assert '"ask"' in hook(root, "PowerShell", {"command": cmd})[1], cmd
    assert '"ask"' not in hook(root, "PowerShell", {"command": "Remove-Item -Recurse tests/blind/release-2"})[1]


def test_priemshchik_tolko_na_opus(tmp_path):
    root = project(tmp_path)
    for model, expected in (("sonnet", 2), ("haiku", 2), ("opus", 0), ("", 0)):
        data = {"tool_input": {"subagent_type": "acceptor", "model": model}}
        code, out = run(root, [sys.executable, ".claude/hooks/model_router.py"], json.dumps(data))
        assert code == expected, (model, out)


def test_shablon_otcheta_chitaet_dashbord(tmp_path):
    root = project(tmp_path)
    template = (root / "docs" / "acceptance" / "_template.md").read_text(encoding="utf-8")
    head = "\n".join(template.splitlines()[:5])
    assert re.search(r"подтверждено X из Y", head), head
    report = template.replace("релиза N", "релиза 1").replace("X из Y", "2 из 3", 1)
    (root / "docs" / "acceptance" / "release-1.md").write_text(report, encoding="utf-8")
    (root / "docs" / "tickets" / "Т-001.md").write_text(
        "# Т-001. Задача\n\n**Статус:** готово · **Дата:**\n**Требования:** ТР-01  **Правила:** —\n"
        "**Зависит от:** —  **Релиз:** 1. Базовый\n", encoding="utf-8")
    code, out = run(root, [sys.executable, "scripts/dashboard.py"])
    assert code == 0, out
    html = (root / "exports" / "dashboard.html").read_text(encoding="utf-8")
    assert "2 из 3" in html


def test_na_github_pravka_prinyatoy_proverki_bez_razresheniya(tmp_path):
    root = project(tmp_path)
    accepted = blind(root, 1, "def test_tr01():\n    assert True\n")
    fresh = blind(root, 2, "def test_tr01():\n    assert True\n")
    commit_and_tag(root, "release-1")
    base = run(root, ["git", "rev-parse", "HEAD"])[1].strip()
    fresh.write_text("def test_tr01():\n    assert 1\n", encoding="utf-8")
    run(root, [*GIT, "commit", "-q", "-am", "непринятый релиз"])
    assert run(root, [sys.executable, "scripts/work_state.py", "ci", base])[0] == 0
    accepted.write_text("def test_tr01():\n    assert 1\n", encoding="utf-8")
    run(root, [*GIT, "commit", "-q", "-am", "принятый релиз"])
    code, out = run(root, [sys.executable, "scripts/work_state.py", "ci", base])
    assert code == 1, out
    assert "tests/blind/release-1/" in out


def test_metka_release_1_ne_zashchishchaet_release_10(tmp_path):
    root = project(tmp_path)
    blind(root, 1, "def test_tr01():\n    assert True\n")
    tenth = blind(root, 10, "def test_tr01():\n    assert True\n")
    commit_and_tag(root, "release-1")
    code, out = hook(root, "Edit", {"file_path": str(tenth)})
    assert code == 0 and '"ask"' not in out, out
    tenth.write_text("def test_tr01():\n    assert 1\n", encoding="utf-8")
    assert run(root, [sys.executable, "scripts/work_state.py", "protected"])[0] == 0
