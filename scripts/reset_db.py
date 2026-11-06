"""Пересоздать тестовые данные перед показом: python scripts/reset_db.py

Трогает только тестовую компанию (код FIXLIST); компании, зарегистрированные через сайт, остаются.
Остановите приложение перед запуском и запустите снова после.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, seed  # noqa: E402

seed.reset_demo()
db.set_meta("seed_version", seed.SEED_VERSION)
print("Готово: тестовые данные пересозданы. Вход: код FIXLIST, boss / 1111, master / 2222.")
