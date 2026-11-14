import sqlite3
import pandas as pd
import base64
import json
import os
import datetime

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transit_system.db")


def _get_github_config():
    """Читает конфиг GitHub из st.secrets. Возвращает (token, repo, branch) или (None, None, None)."""
    try:
        import streamlit as st
        gh = dict(st.secrets["github"])
        token = gh.get("token", "")
        repo = gh.get("repo", "natimikul/Transit")
        branch = gh.get("branch", "main")
        return token, repo, branch
    except Exception:
        return None, None, None


def check_github_token():
    """Проверяет статус GitHub токена. Возвращает (status, message).
    status: 'ok' | 'missing' | 'invalid' | 'error'
    """
    token, repo, _ = _get_github_config()
    if not token:
        return "missing", "GitHub токен не настроен — автосохранение БД отключено."

    import urllib.request
    import urllib.error
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}",
            headers=headers,
        )
        with urllib.request.urlopen(req) as resp:
            return "ok", "GitHub токен активен — автосохранение БД работает."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "invalid", "GitHub токен истёк или недействителен — автосохранение БД не работает! Обновите токен."
        return "error", f"Ошибка проверки токена (HTTP {e.code})."
    except Exception as e:
        return "error", f"Ошибка проверки токена: {e}"


def sync_db_to_github():
    """Сохраняет БД в GitHub репозиторий через API.
    Возвращает True при успехе, False при ошибке/невозможности.
    """
    token, repo, branch = _get_github_config()
    if not token:
        return False

    import urllib.request
    import urllib.error

    if not os.path.exists(DB_NAME):
        return False

    with open(DB_NAME, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    db_filename = os.path.basename(DB_NAME)
    api_url = f"https://api.github.com/repos/{repo}/contents/{db_filename}"
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
    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            header = f.read(16)
        if header != b"SQLite format 3\x00":
            os.remove(DB_NAME)
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
    # Пометка «пополнение склада»: ПкЦБ без счетов (самостоятельный документ)
    _add_column_if_missing(cursor, "auto_in_transit", "is_replenishment", "INTEGER DEFAULT 0")

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
    _add_column_if_missing(cursor, "invoices", "advert", "TEXT")

    # Таблица архива — та же структура, что invoices (+ archived_at).
    # Создаётся динамически из фактического набора колонок invoices,
    # поэтому уже созданные миграции попадают в архив автоматически.
    cursor.execute("SELECT name, type FROM pragma_table_info('invoices')")
    col_defs = ", ".join(f"{c[0]} {c[1]}" for c in cursor.fetchall() if c[0] != "id")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS invoices_archive (
            id INTEGER PRIMARY KEY,
            {col_defs},
            archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


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
        'advert',
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


def update_invoice_status(invoice_id, status):
    """Смена статуса одного счета."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    conn.commit()
    conn.close()


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


def delete_invoices_by_status(status):
    """Удаляет счета по статусу (для перезагрузки)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM invoices WHERE status = ?", (status,))
    conn.commit()
    conn.close()


def delete_invoice_by_id(invoice_id):
    """Удаляет один счет по id."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()


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
                    estimated_arrival, user="admin", is_replenishment=0):
    """Сохраняет авто в таблицу auto_in_transit, возвращает id.

    is_replenishment=1 — ПкЦБ «пополнение склада»: самостоятельный документ
    без счетов СЧКЗ.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO auto_in_transit (dispatch_date, country, location, doc_number,
                                  rkz_number, estimated_arrival, added_by, log_status,
                                  is_replenishment)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'Создан', ?)
    ''', (dispatch_date, country, location, doc_number, rkz_number,
          estimated_arrival, user, int(is_replenishment)))
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
    return affected


def delete_car_by_id(car_id):
    """Удаляет авто (и отвязывает счета)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE invoices SET auto_id = NULL WHERE auto_id = ?", (car_id,))
    cursor.execute("DELETE FROM auto_in_transit WHERE id = ?", (car_id,))
    conn.commit()
    conn.close()


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


# ========== АРХИВ СЧЕТОВ (invoices_archive) ==========
#
# Цель: не держать в основной таблице «мёртвые» счета (статусы «Отгружено
# клиенту» и «Отказ»), чтобы фильтры, отчёты и импорт работали быстрее.
# Архив хранится в той же БД (таблица invoices_archive), всегда доступен
# для поиска и выгрузки, счета можно вернуть обратно.

ARCHIVE_STATUSES = ("Отгружено клиенту", "Отказ")

# Фактическая дата закрытия счёта: для «Отгружено клиенту» — дата отгрузки
# клиенту, для «Отказа» — дата отказа. Если поля пусты — берём iso-дату из
# invoice_date последней редакции (timestamp).
ARCHIVE_DATE_BY_STATUS = {
    "Отгружено клиенту": "delivery_date_to_client",
    "Отказ": "reject_date",
}


def _parse_date_flexible(*values):
    """Пытается распарсить дату в любом из используемых форматов.
    Возвращает datetime.date или None."""
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y",
                    "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.datetime.fromisoformat(s).date()
        except ValueError:
            continue
    return None


def _get_archive_columns(conn):
    """Список колонок таблицы invoices (для копирования в архив)."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM pragma_table_info('invoices')")
    return [r[0] for r in cursor.fetchall()]


def get_archive_candidates(days_threshold=7, statuses=None):
    """
    Возвращает DataFrame со счетами-кандидатами на архивацию:
    статусы «Отгружено клиенту» / «Отказ», у которых дата отгрузки клиенту
    (или дата отказа) старше days_threshold дней.
    Колонка 'archive_date' — дата, по которой принималось решение.
    """
    statuses = list(statuses or ARCHIVE_STATUSES)
    cutoff = datetime.date.today() - datetime.timedelta(days=int(days_threshold))

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    status_ph = ",".join("?" * len(statuses))
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM invoices WHERE status IN ({status_ph}) ORDER BY timestamp DESC",
        list(statuses),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    candidates = []
    for row in rows:
        status = row.get("status")
        primary_field = ARCHIVE_DATE_BY_STATUS.get(status)
        archive_date = _parse_date_flexible(
            row.get(primary_field), row.get("timestamp")
        )
        if archive_date is not None and archive_date <= cutoff:
            row["archive_date"] = archive_date.strftime("%d.%m.%Y")
            row["archive_basis"] = primary_field or "timestamp"
            candidates.append(row)
    return pd.DataFrame(candidates)


def archive_old_invoices(days_threshold=7, statuses=None):
    """
    Переносит в архив все счета-кандидаты (см. get_archive_candidates).
    Возвращает (archived_count, list_of_doc_numbers).
    """
    candidates = get_archive_candidates(days_threshold, statuses)
    if candidates.empty:
        return 0, []

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    invoice_cols = _get_archive_columns(conn)

    archived_docs = []
    for _, row in candidates.iterrows():
        inv_id = int(row["id"])
        placeholders = ",".join("?" * len(invoice_cols))
        cursor.execute(
            f"INSERT OR REPLACE INTO invoices_archive ({','.join(invoice_cols)}) "
            f"SELECT {','.join(invoice_cols)} FROM invoices WHERE id = ?",
            (inv_id,),
        )
        cursor.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
        archived_docs.append(row.get("doc_number") or f"#{inv_id}")

    conn.commit()
    conn.close()
    return len(archived_docs), archived_docs


def archive_invoice_ids(invoice_ids):
    """Ручная архивация произвольных счетов по списку id.
    Возвращает количество перенесённых счетов."""
    if not invoice_ids:
        return 0
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    invoice_cols = _get_archive_columns(conn)
    placeholders = ",".join("?" * len(invoice_cols))
    count = 0
    for inv_id in invoice_ids:
        cursor.execute(
            f"INSERT OR REPLACE INTO invoices_archive ({','.join(invoice_cols)}) "
            f"SELECT {','.join(invoice_cols)} FROM invoices WHERE id = ?",
            (inv_id,),
        )
        if cursor.rowcount > 0:
            cursor.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
            count += 1
    conn.commit()
    conn.close()
    return count


def restore_from_archive(invoice_ids):
    """Возвращает счета из архива в основную таблицу (по id архива, он равен исходному).
    Возвращает количество восстановленных счетов."""
    if not invoice_ids:
        return 0
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    invoice_cols = _get_archive_columns(conn)
    placeholders = ",".join("?" * len(invoice_cols))
    count = 0
    for inv_id in invoice_ids:
        cursor.execute("SELECT * FROM invoices_archive WHERE id = ?", (inv_id,))
        rows = cursor.fetchall()
        if not rows:
            continue
        names = [d[0] for d in cursor.description]
        data = dict(zip(names, rows[0]))
        data.pop("archived_at", None)
        try:
            cursor.execute(
                f"INSERT OR REPLACE INTO invoices ({','.join(invoice_cols)}) "
                f"VALUES ({placeholders})",
                [data.get(c) for c in invoice_cols],
            )
            cursor.execute("DELETE FROM invoices_archive WHERE id = ?", (inv_id,))
            count += 1
        except Exception:
            conn.rollback()
            conn.close()
            raise
    conn.commit()
    conn.close()
    return count


def delete_archive_rows(invoice_ids):
    """Безвозвратно удаляет записи из архива. Возвращает количество удалённых."""
    if not invoice_ids:
        return 0
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    ph = ",".join("?" * len(invoice_ids))
    cursor.execute(f"DELETE FROM invoices_archive WHERE id IN ({ph})", list(invoice_ids))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def get_archive_invoices(search=None, status_filter=None, start_dt=None, end_dt=None):
    """
    Счета из архива для просмотра: поиск по № счета/клиенту, фильтры по статусу
    и периоду (по archived_at). Сортировка по дате архивации (свежие сверху).
    """
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM invoices_archive ORDER BY archived_at DESC, id DESC", conn)
    conn.close()
    if df.empty:
        return df

    if search:
        s = search.strip().lower()
        mask = df["doc_number"].fillna("").astype(str).str.lower().str.contains(s) | \
               df["client"].fillna("").astype(str).str.lower().str.contains(s) | \
               df["advert"].fillna("").astype(str).str.lower().str.contains(s)
        df = df[mask]
    if status_filter:
        df = df[df["status"].isin(status_filter)]
    if start_dt is not None and end_dt is not None:
        # Фильтр по дате отгрузки клиенту / дате отказа
        start_d = start_dt if isinstance(start_dt, datetime.date) else _parse_date_flexible(start_dt)
        end_d = end_dt if isinstance(end_dt, datetime.date) else _parse_date_flexible(end_dt)
        if start_d is not None and end_d is not None:
            parsed = []
            for _, row in df.iterrows():
                f = ARCHIVE_DATE_BY_STATUS.get(row.get("status")) or "delivery_date_to_client"
                parsed.append(_parse_date_flexible(row.get(f)))
            parsed = pd.Series(parsed, index=df.index)
            mask = parsed.apply(lambda d: d is not None and start_d <= d <= end_d)
            df = df[mask]
    return df.reset_index(drop=True)


def get_archive_stats():
    """Сводка по архиву: количество записей и распределение по статусам."""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT status, COUNT(*) AS cnt FROM invoices_archive GROUP BY status", conn)
    conn.close()
    return df