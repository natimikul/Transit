import sqlite3
import pandas as pd

DB_NAME = "transit_system.db"

def init_db():
    """Создает базу данных и таблицы, если они не существуют"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Существующая таблица (оставляем для совместимости)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS auto_in_transit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dispatch_date TEXT,
        country TEXT,
        location TEXT,
        doc_number TEXT,
        rkz_number TEXT,
        estimated_arrival TEXT,
        status_1c TEXT,
        log_status TEXT,
        client TEXT,
        trip_name TEXT,
        order_number TEXT,
        added_by TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # === НОВАЯ ТАБЛИЦА: Полноценный учёт счетов ===
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_number TEXT,              -- № счета (колонка 1 из 1С)
        invoice_date TEXT,            -- Дата счета (колонка 2 из 1С)
        client TEXT,                  -- Клиент (колонка 7 из 1С)
        pkcb TEXT,                    -- ПкЦБ (ручное заполнение)
        warehouse TEXT,               -- Склад (автоопределение / колонка 9)
        perm_rb INTEGER DEFAULT 0,    -- Разрешение РБ (0/1)
        perm_kz INTEGER DEFAULT 0,    -- Разрешение КЗ (0/1)
        note TEXT,                    -- Примечание (ручное)
        plan_ship_date TEXT,          -- Плановая дата отгрузки
        fact_ship_date TEXT,          -- Дата отгрузки факт
        transit_days TEXT,            -- Транзит (дней)
        plan_arrival TEXT,            -- Плановая дата прибытия
        fact_arrival TEXT,            -- Прибыл факт
        status TEXT,                  -- Статус логистики / обработки
        status_1c TEXT,               -- Исходный статус из 1С
        perm_send_date TEXT,          -- Дата отправки на разрешение
        trip_name TEXT,               -- Рейс
        source_sheet TEXT,            -- Источник (Вну/Бри-Дро и т.д.)
        added_by TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()

# ========== НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ СО СЧЕТАМИ ==========

def save_invoice_to_db(data_dict):
    """Сохраняет один счет в таблицу invoices"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO invoices (
        doc_number, invoice_date, client, pkcb, warehouse,
        perm_rb, perm_kz, note, plan_ship_date, fact_ship_date,
        transit_days, plan_arrival, fact_arrival, status, status_1c,
        perm_send_date, trip_name, source_sheet, added_by
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data_dict.get('doc_number'),
        data_dict.get('invoice_date'),
        data_dict.get('client'),
        data_dict.get('pkcb'),
        data_dict.get('warehouse'),
        data_dict.get('perm_rb', 0),
        data_dict.get('perm_kz', 0),
        data_dict.get('note'),
        data_dict.get('plan_ship_date'),
        data_dict.get('fact_ship_date'),
        data_dict.get('transit_days'),
        data_dict.get('plan_arrival'),
        data_dict.get('fact_arrival'),
        data_dict.get('status'),
        data_dict.get('status_1c'),
        data_dict.get('perm_send_date'),
        data_dict.get('trip_name'),
        data_dict.get('source_sheet'),
        data_dict.get('added_by', 'admin')
    ))
    conn.commit()
    conn.close()

def get_invoices_by_status(status):
    """Возвращает счета по статусу"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        "SELECT * FROM invoices WHERE status = ? ORDER BY timestamp DESC",
        conn, params=(status,)
    )
    conn.close()
    return df

def get_invoices_by_filters(status_list=None, warehouse_list=None, perm_rb=None, perm_kz=None):
    """Гибкая фильтрация счетов по нескольким параметрам"""
    conn = sqlite3.connect(DB_NAME)
    query = "SELECT * FROM invoices WHERE 1=1"
    params = []

    if status_list:
        placeholders = ','.join('?' * len(status_list))
        query += f" AND status IN ({placeholders})"
        params.extend(status_list)

    if warehouse_list:
        placeholders = ','.join('?' * len(warehouse_list))
        query += f" AND warehouse IN ({placeholders})"
        params.extend(warehouse_list)

    if perm_rb is not None:
        query += " AND perm_rb = ?"
        params.append(int(perm_rb))

    if perm_kz is not None:
        query += " AND perm_kz = ?"
        params.append(int(perm_kz))

    query += " ORDER BY timestamp DESC"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df

def get_all_invoices():
    """Все счета из базы"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM invoices ORDER BY timestamp DESC", conn)
    conn.close()
    return df

def update_invoices_batch(df_updates):
    """Массовое обновление счетов по id"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for _, row in df_updates.iterrows():
        cursor.execute('''
        UPDATE invoices SET
            pkcb = ?, perm_rb = ?, perm_kz = ?, note = ?,
            plan_ship_date = ?, fact_ship_date = ?, transit_days = ?,
            plan_arrival = ?, fact_arrival = ?, status = ?,
            perm_send_date = ?, trip_name = ?
        WHERE id = ?
        ''', (
            row.get('pkcb'), int(row.get('perm_rb', 0)), int(row.get('perm_kz', 0)),
            row.get('note'), row.get('plan_ship_date'), row.get('fact_ship_date'),
            row.get('transit_days'), row.get('plan_arrival'), row.get('fact_arrival'),
            row.get('status'), row.get('perm_send_date'), row.get('trip_name'),
            int(row.get('id'))
        ))
    conn.commit()
    conn.close()

def delete_invoices_by_status(status):
    """Удаляет счета по статусу (для перезагрузки)"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM invoices WHERE status = ?", (status,))
    conn.commit()
    conn.close()

# ========== СТАРЫЕ ФУНКЦИИ (обратная совместимость) ==========

def save_car_to_db(dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, user="admin"):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO auto_in_transit (dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, added_by, log_status)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'Создан')
    ''', (dispatch_date, country, location, doc_number, rkz_number, estimated_arrival, user))
    conn.commit()
    conn.close()

def get_all_cars_from_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT dispatch_date, country, location, doc_number, rkz_number, estimated_arrival FROM auto_in_transit')
    rows = cursor.fetchall()
    conn.close()
    return rows
