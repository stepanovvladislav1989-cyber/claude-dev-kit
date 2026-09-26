"""
Хук Stop (строгий режим): агент не может закончить работу, пока проверка красная
или пока не подтверждено ревью субагентом reviewer для текущих изменений.

Когда срабатывает:
  - изменён код (что-то вне docs/, tests/, .claude/ и не .md-файлы) — иначе не мешаем:
    обсуждение, документы и этап «падающих тестов» проходят свободно;
  - в проекте есть scripts/validate.py.

Подтверждение ревью — файл .claude/.review_ok с хэшем текущих изменений,
его создаёт scripts/mark_reviewed.py в конце скилла review. Если файла нет или
изменения выросли после отметки (хэш не совпал) — считаем, что ревью для этих
изменений не было, и не даём закончить. Сама отметка не проверяет качество
ревью — только то, что шаг не пропущен и не устарел.

Защита от бесконечного цикла: после MAX_ATTEMPTS попыток подряд агента отпускают,
а вам показывается предупреждение «работа НЕ готова».

Пауза: если по вашей команде создан файл .claude/PAUSE, хук не блокирует,
но показывает вам напоминание, что тесты могут быть красными и ревью не подтверждено.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

MAX_ATTEMPTS = 3
FREE_PREFIXES = ("docs/", "tests/", ".claude/")


def project_root():
    return Path(__file__).resolve().parent.parent.parent


def notify_user(text):
    print(json.dumps({"systemMessage": text}))
    sys.exit(0)


def code_changed(root):
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
    except Exception:
        return False
    for line in res.stdout.splitlines():
        path = line[3:].strip().strip('"').split(" -> ")[-1].replace("\\", "/")
        if path and not path.startswith(FREE_PREFIXES) and not path.endswith(".md"):
            return True
    return False


def changes_hash(root):
    """Хэш незакоммиченных изменений — должен совпадать с scripts/mark_reviewed.py."""
    def git(*args):
        res = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        return res.stdout
    parts = [git("diff", "HEAD")]
    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        file_path = root / path
        if file_path.is_file():
            parts.append(path)
            parts.append(file_path.read_text(encoding="utf-8", errors="replace"))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def review_confirmed(root):
    marker = root / ".claude" / ".review_ok"
    return marker.exists() and marker.read_text(encoding="utf-8").strip() == changes_hash(root)


def main():
    data = json.load(sys.stdin)
    root = project_root()
    counter = root / ".claude" / ".stop_attempts"

    if (root / ".claude" / "PAUSE").exists():
        notify_user("Пауза: работа остановлена по вашей команде. Проверка могла остаться красной.")

    if not (root / "scripts" / "validate.py").exists() or not code_changed(root):
        sys.exit(0)

    attempts = int(counter.read_text()) if data.get("stop_hook_active") and counter.exists() else 0

    res = subprocess.run([sys.executable, "scripts/validate.py"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    validate_ok = res.returncode == 0
    if validate_ok and review_confirmed(root):
        counter.unlink(missing_ok=True)
        sys.exit(0)

    attempts += 1
    if attempts > MAX_ATTEMPTS:
        counter.unlink(missing_ok=True)
        reason = "Проверка НЕ ПРОШЛА" if not validate_ok else "ревью не подтверждено"
        notify_user(f"ВНИМАНИЕ: агент остановился после {MAX_ATTEMPTS} попыток. {reason} — работа НЕ готова.")

    counter.write_text(str(attempts))
    if not validate_ok:
        tail = "\n".join(res.stdout.strip().splitlines()[-30:])
        message = (
            f"Проверка НЕ ПРОШЛА (попытка {attempts} из {MAX_ATTEMPTS}). Завершать работу нельзя.\n"
            "Исправь код, а не тесты. Защищённые тесты меняются только с разрешения пользователя.\n"
            "Если нужно решение пользователя — ясно сформулируй вопрос в ответе.\n\n" + tail
        )
    else:
        message = (
            f"Проверка ПРОШЛА, но ревью субагентом reviewer для текущих изменений не подтверждено "
            f"(попытка {attempts} из {MAX_ATTEMPTS}). Завершать работу нельзя.\n"
            "Запусти скилл review (субагент reviewer), исправь критичные замечания, затем "
            "python scripts/mark_reviewed.py — и только после этого заканчивай."
        )
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
