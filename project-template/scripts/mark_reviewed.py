"""
Отметка «ревью субагентом reviewer подтверждено для текущих изменений».

Запускать в конце скилла review — после того как reviewer проверил все изменения,
критичные замечания исправлены и python scripts/validate.py снова показал ПРОШЛО.

Замок .claude/hooks/stop_red.py не даст завершить задачу, если этой отметки нет
или изменения в проекте выросли после неё (значит, нужно ревью снова).
Сама отметка не доказывает качество ревью — только то, что шаг не пропущен
и не устарел; за содержание ревью отвечает reviewer и итоговый отчёт.

Запуск:  python scripts/mark_reviewed.py
"""
import hashlib
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / ".claude" / ".review_ok"


def git(*args):
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return res.stdout


def changes_hash():
    """Хэш всех незакоммиченных изменений: diff отслеженных файлов + новые файлы целиком.

    Логика продублирована в .claude/hooks/stop_red.py — при правке менять в обоих местах.
    """
    parts = [git("diff", "HEAD")]
    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        file_path = ROOT / path
        if file_path.is_file():
            parts.append(path)
            parts.append(file_path.read_text(encoding="utf-8", errors="replace"))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def main():
    MARKER.parent.mkdir(exist_ok=True)
    MARKER.write_text(changes_hash(), encoding="utf-8")
    print("Отмечено: ревью подтверждено для текущих изменений.")


if __name__ == "__main__":
    main()
