"""Логика импорта Excel-файлов из 1С в базу данных.

Файл 1С имеет кодировку cp1251 и фиксированную структуру колонок:
  1 — Номер (CЧКЗ-...)
  2 — Дата
  3 — Рейс
  4 — Дата рейса
  5 — Статус (Отгружен / К сборке / НЕТ НАКЛАДНОЙ / ...)
  6 — Сумма
  7 — Клиент
  8 — Торговая точка
  9 — Склад (Склад Внуково / Склад Брикета 31 / Склад Дроздово / Склад Алматы)
  10 — Реклама
  11 — Номер заказа

Строки со складом «Склад Алматы» игнорируются.
"""
import pandas as pd
from database import save_invoice_to_db


WAREHOUSE_MAP = {
    "склад внуково": "Внуково",
    "склад брикета": "Брикета",
    "склад брикета 31": "Брикета",
    "склад дроздово": "Дроздово",
}

STATUS_MAP = {
    "нет накладной": "Создан",
    "к сборке": "В сборке",
    "отгружен": "Отгружен",
    "обработан": "Обработан",
    "выгружен в wms": "Выгружен в WMS",
    "не обрабатывать": "Создан",
    "отменен": "Отменен",
}


def fix_encoding(text):
    """Исправление кодировки cp1251 через cp1252."""
    if pd.isna(text):
        return ""
    t_str = str(text).strip()
    try:
        return t_str.encode("cp1252").decode("cp1251")
    except Exception:
        return t_str


def normalize_warehouse(raw_value):
    """Склад Внуково → Внуково. Возвращает None для Алматы и неизвестных."""
    if not raw_value:
        return None
    val = str(raw_value).strip().lower()
    for key, mapped in WAREHOUSE_MAP.items():
        if key in val:
            return mapped
    return None


def map_status_1c(status_1c):
    """Преобразует статус из 1С во внутренний статус приложения."""
    if not status_1c:
        return None
    return STATUS_MAP.get(str(status_1c).strip().lower())


def parse_excel_1c(uploaded_file):
    """
    Читает загруженный Excel-файл 1С и возвращает DataFrame с распознанными
    счетами (склад Алматы отфильтрован).

    Возвращает: (df_parsed, stats_dict)
      df_parsed — DataFrame с колонками:
        doc_number, invoice_date, trip_name, trip_date, status_1c,
        client, warehouse, order_number
      stats_dict — словарь со статистикой загрузки.
    """
    raw_df = pd.read_excel(uploaded_file, header=None)
    raw_df = raw_df.dropna(how="all").reset_index(drop=True)

    if raw_df.empty:
        return pd.DataFrame(), {"error": "Файл пуст."}

    # Поиск строки заголовка: ищем «Номер» в первых 5 строках
    header_idx = 0
    for i in range(min(5, len(raw_df))):
        row_values = [str(v) for v in raw_df.iloc[i].tolist()]
        row_str = " ".join(row_values).lower()
        if "номер" in row_str and "дата" in row_str:
            header_idx = i
            break

    # Используем заголовок для проверки, но колонки нумеруем по порядку
    # (в 1С порядок колонок фиксирован)
    raw_df = raw_df.iloc[header_idx + 1:].reset_index(drop=True)
    raw_df.columns = list(range(1, len(raw_df.columns) + 1))

    # Фильтруем: колонка 1 (Номер) должна быть непустой
    if 1 in raw_df.columns:
        raw_df = raw_df[
            raw_df[1].notna()
            & (raw_df[1].astype(str).str.strip() != "")
        ].reset_index(drop=True)

    if raw_df.empty:
        return pd.DataFrame(), {"error": "В файле нет строк с номерами счетов."}

    # Применяем исправление кодировки к строковым колонкам
    string_cols = [c for c in [1, 2, 3, 4, 5, 7, 9, 11] if c in raw_df.columns]
    for col in string_cols:
        raw_df[col] = raw_df[col].apply(fix_encoding)

    # Нормализуем склад (колонка 9)
    if 9 not in raw_df.columns:
        return pd.DataFrame(), {"error": "В файле нет колонки «Склад» (ожидается колонка 9)."}

    raw_df["warehouse_norm"] = raw_df[9].apply(normalize_warehouse)

    # Статистика
    total_rows = len(raw_df)
    ignored_almaty = raw_df["warehouse_norm"].isna().sum()
    valid_mask = raw_df["warehouse_norm"].notna()
    valid_df = raw_df[valid_mask].reset_index(drop=True)

    stats = {
        "total_rows": int(total_rows),
        "ignored_almaty": int(ignored_almaty),
        "valid_rows": int(len(valid_df)),
        "by_warehouse": valid_df["warehouse_norm"].value_counts().to_dict() if not valid_df.empty else {},
        "by_status_1c": valid_df[5].value_counts().to_dict() if 5 in valid_df.columns else {},
    }

    # Формируем итоговый DataFrame
    result = pd.DataFrame({
        "doc_number": valid_df[1].astype(str).str.strip() if 1 in valid_df.columns else "",
        "invoice_date": valid_df[2].astype(str).str.strip() if 2 in valid_df.columns else "",
        "trip_name": valid_df[3].astype(str).str.strip() if 3 in valid_df.columns else "",
        "trip_date": valid_df[4].astype(str).str.strip() if 4 in valid_df.columns else "",
        "status_1c": valid_df[5].astype(str).str.strip() if 5 in valid_df.columns else "",
        "client": valid_df[7].astype(str).str.strip() if 7 in valid_df.columns else "",
        "warehouse": valid_df["warehouse_norm"],
        "order_number": valid_df[11].astype(str).str.strip() if 11 in valid_df.columns else "",
    })

    # Внутренний статус
    result["status"] = result["status_1c"].apply(map_status_1c)
    # Если статус не распознан — ставим «Создан» как безопасное значение по умолчанию
    result["status"] = result["status"].fillna("Создан")

    stats["by_internal_status"] = result["status"].value_counts().to_dict()

    return result, stats


def import_invoices_to_db(parsed_df, added_by="admin"):
    """
    Сохраняет распознанные счета в БД.
    Если doc_number уже существует — обновляет (не дублирует).

    Возвращает: (saved_count, updated_count, skipped_count)
    """
    if parsed_df.empty:
        return 0, 0, 0

    import sqlite3
    from database import DB_NAME

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    saved_count = 0
    updated_count = 0
    skipped_count = 0

    for _, row in parsed_df.iterrows():
        doc_number = str(row.get("doc_number", "")).strip()
        if not doc_number:
            skipped_count += 1
            continue

        # Проверяем, существует ли уже такой счёт
        cursor.execute(
            "SELECT id FROM invoices WHERE doc_number = ?", (doc_number,)
        )
        existing = cursor.fetchone()

        if existing:
            # Обновляем статус и рейс (если счёт уже был загружен ранее)
            existing_id = existing[0]
            cursor.execute('''
                UPDATE invoices SET
                    status_1c = ?,
                    trip_name = ?,
                    trip_date = ?,
                    invoice_date = ?,
                    client = ?,
                    warehouse = ?,
                    order_number = ?
                WHERE id = ? AND (status NOT IN ('В пути', 'Прибыл на склад Алматы',
                                                  'Готов к отгрузке клиенту', 'Отгружено клиенту'))
            ''', (
                row.get("status_1c"),
                row.get("trip_name"),
                row.get("trip_date"),
                row.get("invoice_date"),
                row.get("client"),
                row.get("warehouse"),
                row.get("order_number"),
                existing_id,
            ))
            if cursor.rowcount > 0:
                updated_count += 1
            else:
                skipped_count += 1
        else:
            # Новый счёт — вставляем
            cursor.execute('''
                INSERT INTO invoices (
                    doc_number, invoice_date, client, warehouse, status, status_1c,
                    trip_name, trip_date, order_number, source_sheet, added_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                doc_number,
                row.get("invoice_date"),
                row.get("client"),
                row.get("warehouse"),
                row.get("status"),
                row.get("status_1c"),
                row.get("trip_name"),
                row.get("trip_date"),
                row.get("order_number"),
                "excel_1c",
                added_by,
            ))
            saved_count += 1

    conn.commit()
    conn.close()
    return saved_count, updated_count, skipped_count


def import_db_export(uploaded_file):
    """
    Импортирует счета из Excel-экспорта БД (формат db_export.xlsx).
    Если doc_number уже существует — обновляет.
    Возвращает: (saved_count, updated_count, skipped_count)
    """
    import sqlite3
    from database import DB_NAME

    df = pd.read_excel(uploaded_file, sheet_name='Счета')
    if df.empty:
        return 0, 0, 0

    rename = {
        '№ счета': 'doc_number', 'Дата счета': 'invoice_date', 'Клиент': 'client',
        'Склад': 'warehouse', 'ПкЦБ': 'pkcb', 'Статус': 'status',
        'Плановая дата отгрузки': 'plan_ship_date', 'Дата отгрузки (факт)': 'fact_ship_date',
        'Транзит (дней)': 'transit_days', 'Плановая дата прибытия': 'plan_arrival',
        'Дата прибытия (факт)': 'fact_arrival', 'Разрешение РБ': 'perm_rb',
        'Разрешение КЗ': 'perm_kz', 'Дата отправки на разрешение': 'perm_send_date',
        'Расценен': 'rated_date', 'Примечание': 'note', '№ заявки': 'order_number',
        'Источник': 'source_sheet', 'Рейс (отгрузка)': 'final_trip_name',
        'Дата рейса': 'final_trip_date', 'Дата отгрузки клиенту': 'delivery_date_to_client',
        'Дата отказа': 'reject_date',
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    saved_count = 0
    updated_count = 0
    skipped_count = 0

    for _, row in df.iterrows():
        doc_number = str(row.get('doc_number', '')).strip()
        if not doc_number or doc_number == 'nan':
            skipped_count += 1
            continue

        def clean(v):
            if pd.isna(v):
                return None
            s = str(v).strip()
            return s if s and s.lower() != 'nan' else None

        doc_number = clean(doc_number)
        invoice_date = clean(row.get('invoice_date'))
        client = clean(row.get('client'))
        warehouse = clean(row.get('warehouse')) or 'Внуково'
        pkcb = clean(row.get('pkcb'))
        status = clean(row.get('status')) or 'Создан'
        plan_ship_date = clean(row.get('plan_ship_date'))
        fact_ship_date = clean(row.get('fact_ship_date'))
        transit_days = clean(row.get('transit_days'))
        plan_arrival = clean(row.get('plan_arrival'))
        fact_arrival = clean(row.get('fact_arrival'))
        rated_date = clean(row.get('rated_date'))
        note = clean(row.get('note'))
        order_number = clean(row.get('order_number'))
        source_sheet = clean(row.get('source_sheet')) or 'ручной'
        final_trip_name = clean(row.get('final_trip_name'))
        final_trip_date = clean(row.get('final_trip_date'))
        delivery_date = clean(row.get('delivery_date_to_client'))
        reject_date = clean(row.get('reject_date'))
        perm_send_date = clean(row.get('perm_send_date'))
        try:
            perm_rb = int(row.get('perm_rb', 0) or 0)
        except (ValueError, TypeError):
            perm_rb = 0
        try:
            perm_kz = int(row.get('perm_kz', 0) or 0)
        except (ValueError, TypeError):
            perm_kz = 0

        cursor.execute("SELECT id FROM invoices WHERE doc_number = ?", (doc_number,))
        existing = cursor.fetchone()

        if existing:
            existing_id = existing[0]
            cursor.execute('''
                UPDATE invoices SET
                    status = ?, invoice_date = ?, client = ?, warehouse = ?, pkcb = ?,
                    plan_ship_date = ?, fact_ship_date = ?, transit_days = ?,
                    plan_arrival = ?, fact_arrival = ?, rated_date = ?,
                    perm_rb = ?, perm_kz = ?, perm_send_date = ?,
                    note = ?, order_number = ?, source_sheet = ?,
                    final_trip_name = ?, final_trip_date = ?,
                    delivery_date_to_client = ?, reject_date = ?
                WHERE id = ?
            ''', (status, invoice_date, client, warehouse, pkcb,
                  plan_ship_date, fact_ship_date, transit_days,
                  plan_arrival, fact_arrival, rated_date,
                  perm_rb, perm_kz, perm_send_date,
                  note, order_number, source_sheet,
                  final_trip_name, final_trip_date,
                  delivery_date, reject_date, existing_id))
            updated_count += 1
        else:
            cursor.execute('''
                INSERT INTO invoices (
                    doc_number, invoice_date, client, warehouse, pkcb, status,
                    plan_ship_date, fact_ship_date, transit_days,
                    plan_arrival, fact_arrival, rated_date,
                    perm_rb, perm_kz, perm_send_date,
                    note, order_number, source_sheet, added_by,
                    final_trip_name, final_trip_date,
                    delivery_date_to_client, reject_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (doc_number, invoice_date, client, warehouse, pkcb, status,
                  plan_ship_date, fact_ship_date, transit_days,
                  plan_arrival, fact_arrival, rated_date,
                  perm_rb, perm_kz, perm_send_date,
                  note, order_number, source_sheet, 'db_export',
                  final_trip_name, final_trip_date,
                  delivery_date, reject_date))
            saved_count += 1

    conn.commit()
    conn.close()
    return saved_count, updated_count, skipped_count


def import_cars_export(uploaded_file):
    """
    Импортирует авто из Excel-экспорта (cars_export.xlsx).
    Если авто с таким же dispatch_date + country + doc_number уже есть — обновляет.
    Также привязывает счета по РКЗ и ПкЦБ.
    Возвращает: (saved_count, updated_count, skipped_count)
    """
    import sqlite3
    from database import DB_NAME

    df = pd.read_excel(uploaded_file, sheet_name='Авто')
    if df.empty:
        return 0, 0, 0

    def clean(v):
        if pd.isna(v):
            return ''
        s = str(v).strip()
        return s if s and s.lower() != 'nan' else ''

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    saved_count = 0
    updated_count = 0
    skipped_count = 0

    for _, row in df.iterrows():
        dispatch_date = clean(row.get('Дата отгрузки'))
        country = clean(row.get('Страна'))
        location = clean(row.get('Локация'))
        doc_number = clean(row.get('ПкЦБ'))
        rkz_number = clean(row.get('№ РКЗ'))
        estimated_arrival = clean(row.get('Плановая дата прибытия'))
        is_arrived_str = clean(row.get('Прибыл'))
        is_arrived = 1 if is_arrived_str in ('1', '1.0', 'True', 'true') else 0
        fact_arrival_date = clean(row.get('Фактическая дата прибытия'))

        if not dispatch_date and not doc_number and not rkz_number:
            skipped_count += 1
            continue

        existing_id = None
        if dispatch_date and country and doc_number:
            cursor.execute(
                "SELECT id FROM auto_in_transit WHERE dispatch_date = ? AND country = ? AND doc_number = ?",
                (dispatch_date, country, doc_number)
            )
            res = cursor.fetchone()
            if res:
                existing_id = res[0]

        if existing_id:
            cursor.execute('''
                UPDATE auto_in_transit SET
                    dispatch_date = ?, country = ?, location = ?,
                    doc_number = ?, rkz_number = ?, estimated_arrival = ?,
                    is_arrived = ?, fact_arrival_date = ?
                WHERE id = ?
            ''', (dispatch_date, country, location,
                  doc_number, rkz_number, estimated_arrival,
                  is_arrived, fact_arrival_date, existing_id))
            auto_id = existing_id
            updated_count += 1
        else:
            cursor.execute('''
                INSERT INTO auto_in_transit (dispatch_date, country, location, doc_number,
                                              rkz_number, estimated_arrival, added_by, log_status,
                                              is_arrived, fact_arrival_date)
                VALUES (?, ?, ?, ?, ?, ?, 'cars_export', ?, ?, ?)
            ''', (dispatch_date, country, location, doc_number,
                  rkz_number, estimated_arrival,
                  'Прибыл' if is_arrived else 'Создан',
                  is_arrived, fact_arrival_date))
            auto_id = cursor.lastrowid
            saved_count += 1

        link_statuses = ('В пути', 'В сборке', 'Прибыл на склад Алматы', 'Готов к отгрузке клиенту')
        status_ph = ",".join("?" * len(link_statuses))
        if rkz_number:
            rkz_list = [r.strip() for r in rkz_number.split("\n") if r.strip()]
            for rkz in rkz_list:
                cursor.execute(
                    f"UPDATE invoices SET auto_id = ? WHERE doc_number = ? "
                    f"AND status IN ({status_ph}) AND (auto_id IS NULL OR auto_id = '' OR auto_id = 0)",
                    [auto_id, rkz] + list(link_statuses)
                )
        if doc_number:
            pkcb_list = [p.strip() for p in doc_number.split("\n") if p.strip()]
            for pkcb in pkcb_list:
                cursor.execute(
                    f"UPDATE invoices SET auto_id = ? WHERE pkcb = ? "
                    f"AND status IN ({status_ph}) AND (auto_id IS NULL OR auto_id = '' OR auto_id = 0)",
                    [auto_id, pkcb] + list(link_statuses)
                )

    conn.commit()
    conn.close()
    return saved_count, updated_count, skipped_count
