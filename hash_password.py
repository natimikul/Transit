"""Утилита для генерации bcrypt-хэшей паролей.

Запуск:
    python hash_password.py

Программа запросит пароль (без эха в консоль), выведет готовый хэш
для вставки в .streamlit/secrets.toml.
"""
import getpass
import sys

try:
    import bcrypt
except ImportError:
    print("Библиотека bcrypt не установлена. Установите: pip install bcrypt")
    sys.exit(1)


def main():
    print("=== Генерация bcrypt-хэша пароля ===")
    password = getpass.getpass("Введите пароль: ")
    if not password:
        print("Пароль не может быть пустым.")
        sys.exit(1)
    confirm = getpass.getpass("Повторите пароль: ")
    if password != confirm:
        print("Пароли не совпадают.")
        sys.exit(1)
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    print("\nГотовый хэш для вставки в .streamlit/secrets.toml:")
    print(hashed.decode("utf-8"))


if __name__ == "__main__":
    main()
