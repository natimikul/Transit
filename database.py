import sqlite3

DB_NAME = "transit_system.db"

def init_db():
    """Создает базу данных и таблицы, если они не существуют"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Расширенная таблица для хранения информации по счетам из ежедневного Excel
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_in_transit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispatch_date TEXT,          -- Дата отгрузки / Дата рейса
            country TEXT,                -- Страна (Россия/Беларусь)
            location TEXT,               -- Локация / Склад отправления
            doc_number TEXT,             -- № документа (Колонка 1)
            rkz_number TEXT,             -- № РКЗ / Доп. инфо
            estimated_arrival TEXT,      -- Плановая дата прибытия
            status_1c TEXT,              -- Статус из 1С (Колонка 5)
            log_status TEXT,             -- Статус логистики (Создан, В сборке, В пути...)
            client TEXT,                 -- Клиент (Колонка 7)
            trip_name TEXT,              -- Рейс (Колонка 3)
            order_number TEXT,           -- Номер заказа (Колонка 11)
            added_by TEXT,               -- Кто добавил
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_car_to_db(dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, user="admin"):
    """Сохраняет одну запись об авто/документе в базу данных"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO auto_in_transit (dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, added_by, log_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Создан')
    ''', (dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, user))
    conn.commit()
    conn.close()

def get_all_cars_from_db():
    """Возвращает все записи из базы данных SQLite"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT dispatch_date, country, location, doc_number, rkz_number, estimated_arrival FROM auto_in_transit')
    rows = cursor.fetchall()
    conn.close()
    return rows
