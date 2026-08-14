import sqlite3
import pandas as pd
import base64
import json
import os

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transit_system.db")


def sync_db_to_github():
    """Сохраняет БД в GitHub репозиторий через API.
    Нужны st.secrets: github_token, github_repo (owner/repo), github_branch (по умолчанию main).
    Возвращает True при успехе, False при ошибке/невозможности.
    """
    try:
        import streamlit as st
        token = st.secrets.get("github", {}).get("token", "")
        repo = st.secrets.get("github", {}).get("repo", "natimikul/Transit")
        branch = st.secrets.get("github", {}).get("branch", "main")
        if not token:
            return False
    except Exception:
        return False

    import urllib.request
    import urllib.error

    if not os.path.exists(DB_NAME):
        return False

    with open(DB_NAME, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    api_url = f"https://api.github.com/repos/{repo}/contents/{DB_NAME}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        req = urllib.request.Request(api_url + f"?ref={branch}", headers=headers)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            sha = data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sha = None
        else:
            return False
    except Exception:
        return False

    payload = json.dumps({
        "message": "Auto-sync DB",
        "content": content,
        "branch": branch,
        **({"sha": sha} if sha else {}),
    }).encode("utf-8")

    try:
        req = urllib.request.Request(api_url, data=payload, headers=headers, method="PUT")
        with urllib.request.urlopen(req) as resp:
            return resp.status in (200, 201)
    except Exception:
        return False


def _add_column_if_missing(cursor, table, column, col_type):
    """Безопасно добавляет колонку, если её нет (миграция существующей БД)."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db():
    """Создаёт таблицы и применяет миграции для новых колонок."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Таблица авто в пути (Пополн)
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
    # Миграции для auto_in_transit
    _add_column_if_missing(cursor, "auto_in_transit", "fact_arrival_date", "TEXT")
    _add_column_if_missing(cursor, "auto_in_transit", "is_arrived", "INTEGER DEFAULT 0")

    # Таблица счетов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_number TEXT,
        invoice_date TEXT,
        client TEXT,
        pkcb TEXT,
        warehouse TEXT,
        perm_rb INTEGER DEFAULT 0,
        perm_kz INTEGER DEFAULT 0,
        note TEXT,
        plan_ship_date TEXT,
        fact_ship_date TEXT,
        transit_days TEXT,
        plan_arrival TEXT,
        fact_arrival TEXT,
        status TEXT,
        status_1c TEXT,
        perm_send_date TEXT,
        trip_name TEXT,
        source_sheet TEXT,
        added_by TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    # Миграции для invoices (новые колонки)
    _add_column_if_missing(cursor, "invoices", "order_number", "TEXT")
    _add_column_if_missing(cursor, "invoices", "rated_date", "TEXT")
    _add_column_if_missing(cursor, "invoices", "trip_date", "TEXT")
    _add_column_if_missing(cursor, "invoices", "delivery_date_to_client", "TEXT")
    _add_column_if_missing(cursor, "invoices", "rzc_number", "TEXT")
    _add_column_if_missing(cursor, "invoices", "auto_id", "INTEGER")
    _add_column_if_missing(cursor, "invoices", "rated_by", "TEXT")
    _add_column_if_missing(cursor, "invoices", "final_trip_name", "TEXT")
    _add_column_if_missing(cursor, "invoices", "final_trip_date", "TEXT")
    _add_column_if_missing(cursor, "invoices", "reject_date", "TEXT")

    conn.commit()
    conn.close()
    sync_db_to_github()


# ========== ФУНКЦИИ ДЛЯ РАБОТЫ СО СЧЕТАМИ ==========

def save_invoice_to_db(data_dict):
    """Сохраняет один счет в таблицу invoices."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO invoices (
        doc_number, invoice_date, client, pkcb, warehouse,
        perm_rb, perm_kz, note, plan_ship_date, fact_ship_date,
        transit_days, plan_arrival, fact_arrival, status, status_1c,
        perm_send_date, trip_name, source_sheet, added_by,
        order_number, rated_date, trip_date, delivery_date_to_client, rzc_number, auto_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        data_dict.get('added_by', 'admin'),
        data_dict.get('order_number'),
        data_dict.get('rated_date'),
        data_dict.get('trip_date'),
        data_dict.get('delivery_date_to_client'),
        data_dict.get('rzc_number'),
        data_dict.get('auto_id'),
    ))
    conn.commit()
    conn.close()
    sync_db_to_github()


def get_invoices_by_status(status):
    """Возвращает счета по статусу."""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        "SELECT * FROM invoices WHERE status = ? ORDER BY timestamp DESC",
        conn, params=(status,)
    )
    conn.close()
    return df


def get_invoices_by_filters(status_list=None, warehouse_list=None, perm_rb=None, perm_kz=None,
                            has_auto=None):
    """Гибкая фильтрация счетов."""
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

    if has_auto is True:
        query += " AND auto_id IS NOT NULL"
    elif has_auto is False:
        query += " AND auto_id IS NULL"

    query += " ORDER BY timestamp DESC"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_all_invoices():
    """Все счета из базы."""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM invoices ORDER BY timestamp DESC", conn)
    conn.close()
    return df


def update_invoices_batch(df_updates):
    """
    Массовое обновление счетов по id.
    Обновляет ТОЛЬКО поля, присутствующие в df_updates.
    Значение None (или NaN) трактуется как «не обновлять» (пропускается),
    чтобы не затирать существующие данные. Для обнуления используйте пустую строку.
    """
    if df_updates is None or df_updates.empty:
        return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    allowed_fields = [
        'pkcb', 'perm_rb', 'perm_kz', 'note',
        'plan_ship_date', 'fact_ship_date', 'transit_days',
        'plan_arrival', 'fact_arrival', 'status',
        'perm_send_date', 'trip_name',
        'order_number', 'rated_date', 'trip_date',
        'delivery_date_to_client', 'rzc_number', 'auto_id',
        'final_trip_name', 'final_trip_date', 'reject_date',
    ]
    present_fields = [f for f in allowed_fields if f in df_updates.columns]
    if not present_fields:
        conn.close()
        return

    for _, row in df_updates.iterrows():
        # Собираем только поля с непустыми значениями для этой строки
        set_parts = []
        values = []
        for f in present_fields:
            v = row.get(f)
            # NaN → None
            if v is not None and isinstance(v, float) and pd.isna(v):
                v = None
            # None — пропускаем (не обновляем)
            if v is None:
                continue
            # Пустая строка → None (для обнуления поля)
            if v == "":
                v = None
            if f in ('perm_rb', 'perm_kz') and v is not None:
                try:
                    v = int(v)
                except (ValueError, TypeError):
                    v = 0
            if f == 'auto_id' and v is not None:
                try:
                    v = int(v)
                except (ValueError, TypeError):
                    v = None
                    if v is None:
                        continue
            set_parts.append(f"{f} = ?")
            values.append(v)
        if not set_parts:
            continue
        sql = f"UPDATE invoices SET {', '.join(set_parts)} WHERE id = ?"
        values.append(int(row.get('id') or 0))
        cursor.execute(sql, values)
    conn.commit()
    conn.close()
    sync_db_to_github()


def update_invoice_status(invoice_id, status):
    """Смена статуса одного счета."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    conn.commit()
    conn.close()
    sync_db_to_github()


def link_invoice_to_auto(invoice_id, auto_id):
    """Привязка счета к авто."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE invoices SET auto_id = ? WHERE id = ?",
        (auto_id, invoice_id)
    )
    conn.commit()
    conn.close()
    sync_db_to_github()


def delete_invoices_by_status(status):
    """Удаляет счета по статусу (для перезагрузки)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM invoices WHERE status = ?", (status,))
    conn.commit()
    conn.close()
    sync_db_to_github()


def delete_invoice_by_id(invoice_id):
    """Удаляет один счет по id."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    sync_db_to_github()


def get_invoices_by_auto_id(auto_id):
    """Все счета, привязанные к конкретному авто."""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        "SELECT * FROM invoices WHERE auto_id = ? ORDER BY timestamp DESC",
        conn, params=(auto_id,)
    )
    conn.close()
    return df


# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С АВТО ==========

def save_car_to_db(dispatch_date, country, location, doc_number, rkz_number,
                    estimated_arrival, user="admin"):
    """Сохраняет авто в таблицу auto_in_transit, возвращает id."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO auto_in_transit (dispatch_date, country, location, doc_number,
                                  rkz_number, estimated_arrival, added_by, log_status)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'Создан')
    ''', (dispatch_date, country, location, doc_number, rkz_number,
          estimated_arrival, user))
    conn.commit()
    auto_id = cursor.lastrowid
    conn.close()
    return auto_id


def get_all_cars_from_db():
    """Возвращает все авто."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, dispatch_date, country, location, doc_number, rkz_number,
               estimated_arrival, fact_arrival_date, is_arrived
        FROM auto_in_transit ORDER BY timestamp DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_active_cars():
    """Только авто, которые ещё не прибыли (для привязки счетов)."""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        "SELECT * FROM auto_in_transit WHERE is_arrived = 0 ORDER BY timestamp DESC",
        conn
    )
    conn.close()
    return df


def get_arrived_cars():
    """Только авто, которые уже прибыли (для вкладки Алматы)."""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        "SELECT * FROM auto_in_transit WHERE is_arrived = 1 ORDER BY fact_arrival_date DESC, id DESC",
        conn
    )
    conn.close()
    return df


def mark_car_arrived(car_id, fact_arrival_date):
    """Отмечает авто как прибывшее и переносит связанные счета в 'Прибыл на склад Алматы'."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    link_statuses = ('В пути', 'В сборке', 'Прибыл на склад Алматы', 'Готов к отгрузке клиенту')
    status_ph = ",".join("?" * len(link_statuses))
    cursor.execute("SELECT rkz_number, doc_number FROM auto_in_transit WHERE id = ?", (car_id,))
    row = cursor.fetchone()
    if row:
        rkz_str, pkcb_str = row
        if rkz_str:
            rkz_list = [r.strip() for r in rkz_str.split("\n") if r.strip()]
            for rkz in rkz_list:
                cursor.execute(
                    f"UPDATE invoices SET auto_id = ? WHERE doc_number = ? "
                    f"AND status IN ({status_ph}) AND (auto_id IS NULL OR auto_id = '' OR auto_id = 0)",
                    [car_id, rkz] + list(link_statuses)
                )
        if pkcb_str:
            pkcb_list = [p.strip() for p in pkcb_str.split("\n") if p.strip()]
            for pkcb in pkcb_list:
                cursor.execute(
                    f"UPDATE invoices SET auto_id = ? WHERE pkcb = ? "
                    f"AND status IN ({status_ph}) AND (auto_id IS NULL OR auto_id = '' OR auto_id = 0)",
                    [car_id, pkcb] + list(link_statuses)
                )
    # Отмечаем авто
    cursor.execute(
        "UPDATE auto_in_transit SET is_arrived = 1, fact_arrival_date = ?, location = 'Алматы', log_status = 'Прибыл' WHERE id = ?",
        (fact_arrival_date, car_id)
    )
    # Переносим связанные счета в статус "Прибыл на склад Алматы"
    cursor.execute(
        "UPDATE invoices SET status = 'Прибыл на склад Алматы', fact_arrival = ? WHERE auto_id = ?",
        (fact_arrival_date, car_id)
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    sync_db_to_github()
    return affected


def delete_car_by_id(car_id):
    """Удаляет авто (и отвязывает счета)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE invoices SET auto_id = NULL WHERE auto_id = ?", (car_id,))
    cursor.execute("DELETE FROM auto_in_transit WHERE id = ?", (car_id,))
    conn.commit()
    conn.close()
    sync_db_to_github()


def update_car(car_id, dispatch_date, country, location, doc_number,
               rkz_number, estimated_arrival):
    """Обновление параметров авто."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE auto_in_transit SET
            dispatch_date = ?, country = ?, location = ?,
            doc_number = ?, rkz_number = ?, estimated_arrival = ?
        WHERE id = ?
    ''', (dispatch_date, country, location, doc_number, rkz_number,
          estimated_arrival, car_id))
    conn.commit()
    conn.close()
    sync_db_to_github()


# ========== СВЯЗЬ АВТО СО СЧЕТАМИ ПО № РКЗ ==========

def link_auto_to_invoices_by_rkz(car_id, rkz_numbers, status_list=None):
    """
    Привязывает счета к авто по совпадению № РКЗ (doc_number).
    rkz_numbers — список строк (СЧКЗ-...), по одному в элементе.
    status_list — список статусов для привязки (по умолчанию 'В пути', 'В сборке').
    Возвращает: количество привязанных счетов.
    """
    if not rkz_numbers:
        return 0
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cleaned = [str(r).strip() for r in rkz_numbers if r and str(r).strip()]
    if not cleaned:
        conn.close()
        return 0
    placeholders = ",".join("?" * len(cleaned))
    statuses = status_list if status_list else ['В пути', 'В сборке']
    status_ph = ",".join("?" * len(statuses))
    cursor.execute(
        f"UPDATE invoices SET auto_id = ? WHERE doc_number IN ({placeholders}) "
        f"AND status IN ({status_ph}) AND (auto_id IS NULL OR auto_id = '' OR auto_id = 0)",
        [car_id] + cleaned + statuses
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    sync_db_to_github()
    return affected


def link_auto_to_invoices_by_pkcb(car_id, pkcb_numbers, status_list=None):
    """
    Привязывает счета к авто по совпадению ПкЦБ.
    pkcb_numbers — список строк (ПкЦБ-...), по одному в элементе.
    status_list — список статусов для привязки (по умолчанию 'В пути', 'В сборке').
    Возвращает: количество привязанных счетов.
    """
    if not pkcb_numbers:
        return 0
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cleaned = [str(p).strip() for p in pkcb_numbers if p and str(p).strip()]
    if not cleaned:
        conn.close()
        return 0
    placeholders = ",".join("?" * len(cleaned))
    statuses = status_list if status_list else ['В пути', 'В сборке']
    status_ph = ",".join("?" * len(statuses))
    cursor.execute(
        f"UPDATE invoices SET auto_id = ? WHERE pkcb IN ({placeholders}) "
        f"AND status IN ({status_ph}) AND (auto_id IS NULL OR auto_id = '' OR auto_id = 0)",
        [car_id] + cleaned + statuses
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    sync_db_to_github()
    return affected


def get_invoices_by_status_list(status_list):
    """Возвращает счета по списку статусов (псевдоним get_invoices_by_filters)."""
    return get_invoices_by_filters(status_list=status_list)


def get_car_invoices_count(car_id):
    """Количество счетов, привязанных к авто."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM invoices WHERE auto_id = ?", (car_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_car_invoice_doc_numbers(car_id):
    """Возвращает список doc_number счетов, привязанных к авто."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT doc_number FROM invoices WHERE auto_id = ? AND doc_number IS NOT NULL AND doc_number != ''",
        (car_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def get_invoices_for_email(status):
    """
    Возвращает счета для email-рассылки по статусу.
    Используется при 'Прибыл на склад Алматы' и 'Готов к отгрузке клиенту'.
    """
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        """SELECT doc_number, invoice_date, client, warehouse, status,
                  fact_arrival, rated_date
           FROM invoices WHERE status = ? ORDER BY timestamp DESC""",
        conn, params=(status,)
    )
    conn.close()
    return df
