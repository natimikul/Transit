import sqlite3

DB_NAME = "transit_system.db"

def init_db():
    """Создает базу данных и таблицы, если они не существуют"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Таблица для хранения информации об автомобилях из листа Пополн
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_in_transit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispatch_date TEXT,          -- Дата отгрузки
            country TEXT,                -- Страна
            location TEXT,               -- Локация авто
            doc_number TEXT,             -- № документа (графа D)
            rkz_number TEXT,             -- № РКЗ (графа E)
            estimated_arrival TEXT,      -- Плановая дата прибытия
            added_by TEXT,               -- Кто добавил (для контроля)
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
        INSERT INTO auto_in_transit (dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, added_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
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
