"""
Общая логика замков — одно место для stop_red, protect_tests, validate и pre-commit.

  - что изменено с последнего коммита (от git add не зависит);
  - отпечаток изменений для отметки ревью (только код и тесты — правка документов его не сбивает);
  - отпечаток состояния, чтобы validate --if-changed не прогонял тесты повторно, если код и тесты не менялись;
  - какие существующие тесты-приёмки и эталон изменены и разрешил ли их менять пользователь.

Запуск из командной строки:
  python scripts/work_state.py protected  — НЕ ПРОШЛО, если существующий защищённый тест или эталон
                                            изменён без разрешения пользователя (любым способом);
  python scripts/work_state.py review     — НЕ ПРОШЛО, если изменён код, а ревью для него не отмечено
                                            (проверка перед коммитом).
  python scripts/work_state.py ci <коммит> — для проверки на GitHub: НЕ ПРОШЛО, если с указанного коммита
                                            изменены существующие тесты-приёмки или эталон, а ни в одном
                                            сообщении коммита нет строки APPROVAL_LINE. Ловит обход
                                            проверки на компьютере: правка теста видна на GitHub.
                                            Строку пишет агент — она не доказывает разрешение,
                                            но делает правку заявленной, а не тихой.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ("tests/acceptance", "tests/e2e", "tests/golden")
REVIEW_FREE = ("docs/", ".claude/")
MARKER = ROOT / ".claude" / ".review_ok"
VALIDATED = ROOT / ".claude" / ".validate_ok"  # отпечаток состояния и итог последней проверки ПРОШЛО
APPROVED = ROOT / ".claude" / ".protected_approved"
APPROVAL_LINE = "Разрешено пользователем: защищённые тесты"


def git(*args):
    res = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return res.stdout


def head():
    return git("rev-parse", "--verify", "-q", "HEAD").strip()


def changed_paths():
    """Все пути, отличающиеся от последнего коммита, включая новые и удалённые файлы."""
    entries = git("status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    paths = set()
    i = 0
    while i < len(entries):
        entry = entries[i]
        if entry:
            paths.add(entry[3:])
            if ("R" in entry[:2] or "C" in entry[:2]) and i + 1 < len(entries):
                i += 1
                paths.add(entries[i])
        i += 1
    return sorted(paths)


def needs_review(path):
    return not path.startswith(REVIEW_FREE) and not path.endswith(".md")


def code_changed():
    """Изменён код (не документы, не служебное, не только тесты) — нужны проверка и ревью."""
    return any(needs_review(p) and not p.startswith("tests/") for p in changed_paths())


def changes_hash():
    """Отпечаток изменённого кода и тестов. Концы строк не учитываются — их меняет git, а не человек."""
    digest = hashlib.sha256()
    for path in changed_paths():
        if not needs_review(path):
            continue
        file_path = ROOT / path
        content = file_path.read_bytes().replace(b"\r\n", b"\n") if file_path.is_file() else b"<no file>"
        digest.update(path.encode("utf-8") + b"\0" + content + b"\0")
    return digest.hexdigest()


def is_doc(path):
    """Документ (.md в docs/ или в корне): правка не влияет на тесты, документы validate проверяет всегда."""
    return path.endswith(".md") and (path.startswith("docs/") or "/" not in path)


def state_hash():
    """Отпечаток состояния для запоминания ПРОШЛО: последний коммит, все изменённые файлы, кроме документов,
    файл .env и адреса баз. None — git не сработал: тогда запоминать и сравнивать нельзя."""
    res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True)
    if res.returncode != 0:
        return None
    digest = hashlib.sha256(head().encode())
    env_file = ROOT / ".env"
    digest.update(env_file.read_bytes() if env_file.is_file() else b"<no .env>")
    for name in ("DATABASE_URL", "TEST_DATABASE_URL"):
        digest.update(f"{name}={os.environ.get(name, '')}\0".encode("utf-8"))
    for path in changed_paths():
        if is_doc(path):
            continue
        file_path = ROOT / path
        content = file_path.read_bytes().replace(b"\r\n", b"\n") if file_path.is_file() else b"<no file>"
        digest.update(path.encode("utf-8") + b"\0" + content + b"\0")
    return digest.hexdigest()


def review_confirmed():
    return MARKER.exists() and MARKER.read_text(encoding="utf-8").strip() == changes_hash()


def protected_modified():
    """Файлы тестов-приёмки и эталона, которые есть в последнем коммите и сейчас изменены или удалены."""
    if not head():
        return []
    out = git("diff", "HEAD", "--name-only", "--no-renames", "--diff-filter=MDT", "-z", "--", *PROTECTED)
    return [p for p in out.split("\0") if p]


def approve(prefixes):
    """Запоминает разрешённые пользователем правки. Действуют до следующего коммита."""
    if not prefixes:
        return
    sha = head()
    APPROVED.parent.mkdir(exist_ok=True)
    with APPROVED.open("a", encoding="utf-8") as f:
        for prefix in prefixes:
            f.write(f"{sha}\t{prefix.lower()}\n")


def approved_prefixes():
    if not APPROVED.exists():
        return []
    sha = head()
    result = []
    for line in APPROVED.read_text(encoding="utf-8").splitlines():
        line_sha, _, prefix = line.partition("\t")
        if line_sha == sha and prefix:
            result.append(prefix)
    return result


def protected_unapproved():
    allowed = approved_prefixes()
    def is_allowed(path):
        p = path.lower()
        return any(p == a or p.startswith(a.rstrip("/") + "/") for a in allowed)
    return [p for p in protected_modified() if not is_allowed(p)]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "protected":
        bad = protected_unapproved()
        if bad:
            print("Изменены существующие тесты-приёмки или эталон без разрешения пользователя:")
            for path in bad:
                print(f"  - {path}")
            print("Верни файлы как было (git checkout -- <файл>) или попроси разрешение у пользователя.")
            sys.exit(1)
        print("Защищённые тесты и эталон не тронуты без разрешения.")
    elif mode == "review":
        if code_changed() and not review_confirmed():
            print("Коммит остановлен: код изменён, а ревью субагентом reviewer для этих изменений не отмечено.")
            print("Запусти скилл review: отметку ставит замок, когда reviewer ответил без «критично».")
            print("Если ревью было: закоммить все изменения задачи целиком (git add -A) — частичный коммит не совпадёт с отметкой.")
            sys.exit(1)
    elif mode == "ci":
        base = sys.argv[2] if len(sys.argv) > 2 else ""
        if not base or set(base) == {"0"}:
            print("Нет предыдущего коммита для сравнения — проверка защищённых тестов пропущена.")
            return
        if subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=ROOT).returncode != 0:
            print(f"Не удалось проверить защищённые тесты: коммит {base} не найден в истории "
                  "(например, после принудительной отправки).")
            sys.exit(1)
        changed = [p for p in git("diff", base, "HEAD", "--name-only", "--no-renames", "--diff-filter=MDT",
                                  "-z", "--", *PROTECTED).split("\0") if p]
        if changed and APPROVAL_LINE not in git("log", "--format=%B", f"{base}..HEAD"):
            print("Изменены существующие тесты-приёмки или эталон, а в сообщении коммита нет строки")
            print(f"«{APPROVAL_LINE}»:")
            for path in changed:
                print(f"  - {path}")
            sys.exit(1)
        print("Защищённые тесты и эталон не тронуты или изменены с отметкой о разрешении.")
    else:
        print("Использование: python scripts/work_state.py protected | review | ci <коммит>")
        sys.exit(2)


if __name__ == "__main__":
    main()
