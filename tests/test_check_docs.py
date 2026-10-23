"""
Тесты самого комплекта (не проекта): проверка документов check_docs.py из project-template,
связь бриф → ТР → задачи. Каждый тест собирает маленький проект во временной папке
и запускает на нём копию скрипта. Запуск из корня комплекта: python -m pytest tests
"""
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "project-template"
SCRIPT = TEMPLATE / "scripts" / "check_docs.py"

BRIEF = "# Бриф\n\nБ-1. Хочу видеть акт сверки.\n\nБ-2. Копейки должны сходиться.\n"


def tr(num, status="открыто", source="Источник: допрос, тема 1"):
    lines = [f"### ТР-{num}. Требование {num}", "Что должно быть."]
    if source:
        lines.append(source)
    if status:
        lines.append(f"Статус: {status}")
    return "\n".join(lines) + "\n\n"


def ticket(num, field="ТР-01", extra=""):
    return f"# Т-{num}. Задача\n\n**Требования:** {field}  **Правила:** —\n{extra}"


def check(cwd, script):
    res = subprocess.run([sys.executable, str(script)], cwd=cwd,
                         capture_output=True, text=True, encoding="utf-8")
    return res.returncode, res.stdout


def run(tmp_path, requirements, tickets=None, brief=None):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "check_docs.py")
    docs = tmp_path / "docs"
    (docs / "tickets").mkdir(parents=True)
    (docs / "requirements.md").write_text("# Требования\n\n" + requirements, encoding="utf-8")
    if brief is not None:
        (docs / "brief.md").write_text(brief, encoding="utf-8")
    for name, text in (tickets or {}).items():
        (docs / "tickets" / name).write_text(text, encoding="utf-8")
    return check(tmp_path, tmp_path / "scripts" / "check_docs.py")


def test_chistyy_shablon_bez_oshibok_i_preduprezhdeniy():
    code, out = check(TEMPLATE, SCRIPT)
    assert code == 0, out
    assert "ошибок 0, предупреждений 0" in out


def test_vse_svyazano_proshlo(tmp_path):
    code, out = run(tmp_path, tr("01", source="Из брифа: «Хочу видеть акт сверки» (Б-1)")
                    + tr("02", source="Из брифа: «Копейки должны сходиться» (Б-2)"),
                    {"T-001.md": ticket("001", "ТР-01, ТР-02")}, BRIEF)
    assert code == 0, out
    assert "ошибок 0, предупреждений 0" in out


def test_tr_bez_zadachi_oshibka(tmp_path):
    code, out = run(tmp_path, tr("01") + tr("02"), {"T-001.md": ticket("001")})
    assert code == 1
    assert "ОШИБКА: На ТР-02 не ссылается ни одна задача" in out


def test_tr_tolko_v_ne_vkhodit_ne_schitaetsya(tmp_path):
    extra = "\n## Не входит в задачу\n\n- ТР-02 — позже\n"
    code, out = run(tmp_path, tr("01") + tr("02"), {"T-001.md": ticket("001", extra=extra)})
    assert code == 1
    assert "ОШИБКА: На ТР-02 не ссылается ни одна задача" in out


def test_tr_otlozheno_i_snyato_bez_zadachi_mozhno(tmp_path):
    code, out = run(tmp_path, tr("01") + tr("02", "отложено — во второй релиз") + tr("03", "снято — не нужно"),
                    {"T-001.md": ticket("001")})
    assert code == 0, out
    assert "ошибок 0, предупреждений 0" in out


def test_status_zhirnyy_s_bolshoy_bukvy(tmp_path):
    req = "### ТР-01. А\nИсточник: допрос\n**Статус:** Отложено — во второй релиз\n\n"
    code, out = run(tmp_path, req + tr("02"), {"T-001.md": ticket("001", "ТР-02")})
    assert code == 0, out
    assert "ошибок 0, предупреждений 0" in out


def test_otlozheno_bez_prichiny_preduprezhdenie(tmp_path):
    code, out = run(tmp_path, tr("01", "отложено"))
    assert code == 0, out
    assert "ТР-01: у статуса «отложено» нет причины" in out


def test_do_narezki_tr_bez_zadachi_ne_oshibka(tmp_path):
    code, out = run(tmp_path, tr("01"))
    assert code == 0, out


def test_zadacha_bez_tr_oshibka(tmp_path):
    code, out = run(tmp_path, tr("01"), {"T-001.md": ticket("001"), "T-002.md": ticket("002", "—")})
    assert code == 1
    assert "ОШИБКА: Задача Т-002 не ссылается ни на одно ТР" in out


def test_zadacha_bez_stroki_trebovaniya_oshibka(tmp_path):
    code, out = run(tmp_path, tr("01"), {"T-001.md": ticket("001"), "T-002.md": "# Т-002. Задача\n\nТекст.\n"})
    assert code == 1
    assert "ОШИБКА: Задача Т-002 не ссылается ни на одно ТР" in out


def test_tr_bez_istochnika_i_statusa_preduprezhdenie(tmp_path):
    code, out = run(tmp_path, tr("01", status="", source=""))
    assert code == 0, out
    assert "У ТР-01 нет строки «Из брифа: …» или «Источник: …»" in out
    assert "У ТР-01 нет строки «Статус: …»" in out


def test_status_sosednego_tr_ne_peretekaet(tmp_path):
    code, out = run(tmp_path, tr("01", status="") + tr("02", "отложено — позже") + tr("03"),
                    {"T-001.md": ticket("001", "ТР-03")})
    assert code == 1
    assert "У ТР-01 нет строки «Статус: …»" in out
    assert "ОШИБКА: На ТР-01 не ссылается ни одна задача" in out


def test_podzagolovok_vnutri_tr_ne_obryvaet_razdel(tmp_path):
    req = "### ТР-01. А\nЧто должно быть.\n\n#### Пример\n\nТекст.\n\nИсточник: допрос\nСтатус: открыто\n\n"
    code, out = run(tmp_path, req)
    assert code == 0, out
    assert "ошибок 0, предупреждений 0" in out


def test_status_sdelano_vruchnuyu_preduprezhdenie(tmp_path):
    code, out = run(tmp_path, tr("01", status="сделано"))
    assert code == 0, out
    assert "ТР-01: статус «сделано» не из списка: открыто, отложено, снято" in out


def test_abzac_brifa_bez_ssylki_preduprezhdenie(tmp_path):
    code, out = run(tmp_path, tr("01", source="Из брифа: «Хочу видеть акт сверки» (Б-1)"), brief=BRIEF)
    assert code == 0, out
    assert "Абзац брифа Б-2 не упомянут ни в одном документе" in out
    assert "Б-1 не упомянут" not in out


def test_ssylka_na_nesushchestvuyushchiy_abzac_oshibka(tmp_path):
    code, out = run(tmp_path, tr("01", source="Из брифа: «…» (Б-7)"), brief=BRIEF)
    assert code == 1
    assert "ОШИБКА: Б-7 упомянут, но такого абзаца в брифе нет" in out


def test_bez_brifa_nomera_b_ne_proveryayutsya(tmp_path):
    code, out = run(tmp_path, tr("01", source="Источник: допрос, склад в корпусе Б-2"))
    assert code == 0, out


def test_nomera_vnutri_brifa_ne_proveryayutsya(tmp_path):
    brief = "# Бриф\n\nБ-1. Как в старой программе, где правило ПР-99 и [ссылка](нет.md).\n"
    code, out = run(tmp_path, tr("01", source="Из брифа: «как в старой программе» (Б-1)"), brief=brief)
    assert code == 0, out
    assert "ошибок 0, предупреждений 0" in out
