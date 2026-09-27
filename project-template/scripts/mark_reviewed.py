"""
Отметка «ревью субагентом reviewer подтверждено для текущих изменений».

Запускать в конце скилла review — после того как reviewer проверил все изменения,
критичные замечания исправлены и python scripts/validate.py снова показал ПРОШЛО.

Замок .claude/hooks/stop_red.py не даст завершить задачу, а проверка перед коммитом
не даст закоммитить, если этой отметки нет или код и тесты изменились после неё.
Правка документов (docs/, *.md) отметку не сбивает.
Сама отметка не доказывает качество ревью — только то, что шаг не пропущен
и не устарел; за содержание ревью отвечает reviewer и итоговый отчёт.

Запуск:  python scripts/mark_reviewed.py
"""
import sys

import work_state

sys.stdout.reconfigure(encoding="utf-8")


def main():
    work_state.MARKER.parent.mkdir(exist_ok=True)
    work_state.MARKER.write_text(work_state.changes_hash(), encoding="utf-8")
    print("Отмечено: ревью подтверждено для текущих изменений.")


if __name__ == "__main__":
    main()
