# --- ДОПОЛНИТЕЛЬНЫЕ ИМПОРТЫ ДЛЯ ОТПРАВКИ ПОЧТЫ (Добавить в начало файла) ---
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# --- ИМПОРТЫ ДЛЯ БЕЗОПАСНОЙ АВТОРИЗАЦИИ ---
import bcrypt

import streamlit as st
import pandas as pd
import datetime
from io import BytesIO
from replenishment import show_replenishment_page
from excel_import import parse_excel_1c, import_invoices_to_db, import_db_export, import_cars_export
from database import (
    init_db, save_car_to_db, get_all_cars_from_db,
    save_invoice_to_db, get_all_invoices, get_invoices_by_filters,
    update_invoices_batch, delete_invoices_by_status,
    update_invoice_status, get_active_cars, mark_car_arrived,
    delete_car_by_id, update_car, delete_invoice_by_id,
    link_auto_to_invoices_by_rkz, link_auto_to_invoices_by_pkcb,
    get_car_invoices_count, get_invoices_for_email, get_car_invoice_doc_numbers,
    get_arrived_cars, sync_db_to_github, check_github_token
)

# Инициализируем базу данных при старте
init_db()

# --- НАСТРОЙКА СТРАНИЦЫ И СТИЛЕЙ КНОПОК ---
st.set_page_config(page_title="Статус транзитных счетов", layout="wide")
st.title("📦 Статус транзитных счетов")

# --- 1. ЗАЩИТА ПАРОЛЕМ (БЕЗ ХАРДКОДА: хэши в st.secrets) ---
# Пароли хранятся в st.secrets в виде bcrypt-хэшей. Сгенерировать хэш:
#   python -c "import bcrypt; print(bcrypt.hashpw('вашпароль'.encode(), bcrypt.gensalt()).decode())"

def _verify_password(password: str, password_hash: str) -> bool:
    """Проверка пароля через bcrypt."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _authenticate_user(password: str) -> bool:
    """Сопоставляет пароль с хэшами в st.secrets, выставляет admin_status."""
    try:
        user_hash = st.secrets["auth"]["user_password_hash"]
        admin_hash = st.secrets["auth"]["admin_password_hash"]
    except (KeyError, FileNotFoundError):
        st.error(
            "❌ Конфигурация авторизации не найдена. "
            "В Settings → Secrets на Streamlit Cloud добавьте раздел [auth] "
            "с полями user_password_hash и admin_password_hash."
        )
        st.stop()
    if _verify_password(password, admin_hash):
        st.session_state.authenticated = True
        st.session_state.admin_status = "admin"
        return True
    if _verify_password(password, user_hash):
        st.session_state.authenticated = True
        st.session_state.admin_status = "user"
        return True
    return False


if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "admin_status" not in st.session_state:
    st.session_state.admin_status = "guest"

if not st.session_state.authenticated:
    st.subheader("🔒 Вход в систему")
    user_password = st.text_input("Введите пароль для доступа к отчетам:", type="password")
    if st.button("Войти 🔑"):
        if _authenticate_user(user_password):
            st.rerun()
        else:
            st.error("❌ Неверный пароль! Доступ заблокирован.")
            st.stop()
    st.stop()

# --- ОПРЕДЕЛЕНИЕ АДМИН-СТАТУСА (ТОЛЬКО ЧЕРЕЗ session_state) ---
# Раньше админ определялся по ?admin=yes в URL — это позволяло любому пользователю
# дописать параметр и открыть админ-панель. Теперь используем только
# значение, выставленное при авторизации.
is_admin = st.session_state.get("admin_status") == "admin"

list_all_statuses = [
    "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути",
    "Задержка поставки", "Прибыл на склад Алматы", "Готов к отгрузке клиенту",
    "Отгружено клиенту", "Отказ"
]

# --- 3. ИНИЦИАЛИЗАЦИЯ ПАМЯТИ СОСТОЯНИЯ ---
if 'current_report' not in st.session_state: st.session_state.current_report = None
if 'report_name' not in st.session_state: st.session_state.report_name = ""
if 'show_email_modal' not in st.session_state: st.session_state.show_email_modal = False

# --- 5. ИНТЕРФЕЙС ПАРАМЕТРОВ ПОИСКА ---
st.subheader("🔍 Параметры поиска")
col_client, col_date = st.columns(2)

with col_client:
    client_input = st.text_input("Фильтр по Клиенту (можно через запятую):", "")
    invoice_input = st.text_input("Фильтр по Номеру счета (можно через запятую):", "")
    if client_input or invoice_input:
        st.session_state.current_report = None

with col_date:
    today_dt = datetime.date.today()
    default_start_dt = today_dt - datetime.timedelta(days=90)
    date_range = st.date_input("Период поиска (по Дате счета):", value=(default_start_dt, today_dt))
    selected_dropdown_statuses = st.multiselect("📊 Отфильтровать по статусу счетов:", list_all_statuses, key='selected_dropdown_statuses')

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_filter, end_filter = date_range[0], date_range[1]
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_filter, end_filter = date_range[0], today_dt
else:
    start_filter, end_filter = default_start_dt, today_dt

db_all_invoices = get_all_invoices()
total_rows = len(db_all_invoices)
st.write(f"📊 Всего счетов в базе: {total_rows}")

# --- 6. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ СБОРКИ И СТРОГОЙ ФИЛЬТРАЦИИ ---
def build_report(required_columns, status_list=None, source_sheet_list=None, warehouse_list=None,
                 filter_by_invoice=True, invoice_text="", client_text="",
                 start_dt=None, end_dt=None, sort_by=None, sort_ascending=True):
    df_all = get_all_invoices()
    if df_all.empty:
        return pd.DataFrame()

    if status_list:
        df_all = df_all[df_all['status'].isin(status_list)]

    if source_sheet_list and 'source_sheet' in df_all.columns:
        df_all = df_all[df_all['source_sheet'].isin(source_sheet_list)]

    dropdown_statuses = st.session_state.get('selected_dropdown_statuses', [])
    if dropdown_statuses and 'status' in df_all.columns:
        df_all = df_all[df_all['status'].isin(dropdown_statuses)]

    if warehouse_list:
        df_all = df_all[df_all['warehouse'].isin(warehouse_list)]

    is_invoice_empty = not invoice_text or not str(invoice_text).strip()

    if 'invoice_date' in df_all.columns and is_invoice_empty:
        df_all['_tmp_date'] = pd.to_datetime(df_all['invoice_date'], format='%d.%m.%Y', errors='coerce')
        mask_na = df_all['_tmp_date'].isna()
        df_all.loc[mask_na, '_tmp_date'] = pd.to_datetime(df_all.loc[mask_na, 'invoice_date'], format='%d.%m.%y', errors='coerce')
        mask_na2 = df_all['_tmp_date'].isna()
        df_all.loc[mask_na2, '_tmp_date'] = pd.to_datetime(df_all.loc[mask_na2, 'invoice_date'], format='%Y-%m-%d', errors='coerce')
        if start_dt is not None and end_dt is not None and not pd.isna(start_dt) and not pd.isna(end_dt):
            start_ts = pd.Timestamp(start_dt)
            end_ts = pd.Timestamp(end_dt)
            df_all = df_all[(df_all['_tmp_date'] >= start_ts) & (df_all['_tmp_date'] <= end_ts)]
        df_all.drop(columns=['_tmp_date'], inplace=True, errors='ignore')

    if filter_by_invoice and invoice_text:
        search_invoices = [inv.strip().lower() for inv in invoice_text.split(',') if inv.strip()]
        if search_invoices and 'doc_number' in df_all.columns:
            clean_series = df_all['doc_number'].fillna("").astype(str).str.lower().str.strip()
            df_all = df_all[clean_series.apply(lambda x: any(inv in x for inv in search_invoices))]

    if client_text and 'client' in df_all.columns:
        clean_text = lambda v: str(v).lower().replace(" ", "").replace(".", "").replace(",", "").replace('"', '').replace("'", "")
        search_words = [clean_text(w) for w in client_text.split(",") if w.strip()]
        if search_words:
            client_mask = df_all['client'].apply(lambda x: any(word in clean_text(x) for word in search_words))
            df_all = df_all[client_mask]

    rename = {
        'doc_number': '№ счета', 'invoice_date': 'Дата счета', 'client': 'Клиент',
        'warehouse': 'Склад', 'pkcb': 'ПкЦБ', 'status': 'Статус',
        'plan_ship_date': 'Плановая дата отгрузки', 'fact_ship_date': 'Дата отгрузки (факт)',
        'plan_arrival': 'Плановая дата прибытия', 'fact_arrival': 'Прибыл (факт)',
        'transit_days': 'Транзит (дней)', 'perm_send_date': 'Дата отправки на разрешение',
        'rated_date': 'Расценен',
        'note': 'Примечание', 'final_trip_name': 'Рейс', 'final_trip_date': 'Дата рейса',
        'delivery_date_to_client': 'Дата отгрузки клиенту', 'reject_date': 'Дата отказа',
    }
    df_all = df_all.rename(columns={k: v for k, v in rename.items() if k in df_all.columns})

    if sort_by and sort_by in df_all.columns and not df_all.empty:
        df_all['_sort_key'] = pd.to_datetime(df_all[sort_by], format='%d.%m.%Y', errors='coerce')
        mask_na = df_all['_sort_key'].isna()
        df_all.loc[mask_na, '_sort_key'] = pd.to_datetime(df_all.loc[mask_na, sort_by], format='%d.%m.%y', errors='coerce')
        mask_na2 = df_all['_sort_key'].isna()
        df_all.loc[mask_na2, '_sort_key'] = pd.to_datetime(df_all.loc[mask_na2, sort_by], errors='coerce')
        df_all = df_all.sort_values('_sort_key', ascending=sort_ascending, na_position='last')
        df_all.drop(columns=['_sort_key'], inplace=True)

    final_cols = [c for c in required_columns if c in df_all.columns]
    return df_all[final_cols].reset_index(drop=True) if not df_all.empty else pd.DataFrame()

# --- ФУНКЦИЯ ДЛЯ ФИЛЬТРАЦИИ И ОТПРАВКИ СВОДКИ НА EMAIL ---
def send_today_report_email(recipient_emails):
    try:
        sender_email = st.secrets["email"]["sender_email"]
        sender_password = st.secrets["email"]["sender_password"]
    except (KeyError, FileNotFoundError):
        st.error("❌ Не настроены параметры почты в st.secrets (раздел [email]).")
        return False

    today_str_1 = datetime.date.today().strftime('%d.%m.%Y')
    today_str_2 = datetime.date.today().strftime('%Y-%m-%d')

    df_all = get_all_invoices()
    if df_all.empty:
        st.warning("Нет данных в базе. Письмо не отправлено.")
        return False

    mask = df_all.astype(str).apply(
        lambda row: row.str.contains(today_str_1, na=False) | row.str.contains(today_str_2, na=False),
        axis=1
    ).any(axis=1)
    df_today = df_all[mask]

    if df_today.empty:
        st.warning("За сегодняшнее число записей не найдено. Письмо не отправлено.")
        return False

    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
        df_today.to_excel(writer, index=False, sheet_name='Сводка_Сегодня')
    excel_data = excel_buffer.getvalue()

    today_formatted = datetime.date.today().strftime('%d.%m.%Y')
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_emails
    msg['Subject'] = f"Мониторинг счетов — Сводка за {today_formatted}"

    body = f"""Добрый день!

Информируем Вас о смене статуса транзитных счетов на сегодня ({today_formatted}).
Файл во вложении.
"""
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    filename = f"Svodka_tranzitnyh_schetov_{today_formatted}.xlsx"
    part = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    part.set_payload(excel_data)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', 'attachment', filename=filename)
    msg.attach(part)

    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        if int(smtp_port) == 465:
            server = smtplib.SMTP_SSL(smtp_server, int(smtp_port))
        else:
            server = smtplib.SMTP(smtp_server, int(smtp_port))
            server.starttls()
        server.login(sender_email, sender_password)
        recipients_list = [email.strip() for email in recipient_emails.split(',') if email.strip()]
        server.sendmail(sender_email, recipients_list, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        st.error(f"Не удалось отправить письмо. Ошибка: {e}")
        return False


def send_status_email(status, recipient_emails):
    """
    Email-рассылка по счетам определённого статуса.
    Используется для «Прибыл на склад Алматы» и «Готов к отгрузке клиенту».
    Список адресов берётся из st.secrets['email']['notify_emails'] (если есть)
    или из аргумента recipient_emails.
    """
    try:
        sender_email = st.secrets["email"]["sender_email"]
        sender_password = st.secrets["email"]["sender_password"]
    except (KeyError, FileNotFoundError):
        st.error("❌ Не настроены параметры почты в st.secrets (раздел [email]).")
        return False

    # Список адресов: приоритет из аргумента, затем из secrets
    if not recipient_emails:
        try:
            recipient_emails = st.secrets["email"]["notify_emails"]
        except (KeyError, FileNotFoundError):
            st.error("❌ Не задан список адресов для рассылки (st.secrets['email']['notify_emails']).")
            return False

    df = get_invoices_for_email(status)
    if df.empty:
        st.warning(f"Нет счетов со статусом «{status}».")
        return False

    today_formatted = datetime.date.today().strftime('%d.%m.%Y')
    status_label = "Прибыл на склад Алматы" if "Прибыл" in status else "Готов к отгрузке клиенту"

    # Формируем тело письма
    rows_text = ""
    for _, r in df.iterrows():
        rows_text += (
            f"  • {r.get('doc_number', '-')} | {r.get('client', '-')} | "
            f"Склад: {r.get('warehouse', '-')} | Дата прибытия: {r.get('fact_arrival', '-')} | "
            f"Расценен: {r.get('rated_date', '-')}\n"
        )

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_emails
    msg['Subject'] = f"Транзит — {status_label} ({len(df)} шт.) — {today_formatted}"

    body = f"""Добрый день!

Информируем о счетах со статусом «{status_label}» на {today_formatted}.
Всего счетов: {len(df)}.

Список:
{rows_text}

С уважением,
Система мониторинга транзита.
"""
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Прикрепляем Excel-вложение
    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name=status_label[:31])
    part = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    part.set_payload(excel_buffer.getvalue())
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', 'attachment',
                    filename=f"{status_label}_{today_formatted}.xlsx".replace(' ', '_'))
    msg.attach(part)

    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        if int(smtp_port) == 465:
            server = smtplib.SMTP_SSL(smtp_server, int(smtp_port))
        else:
            server = smtplib.SMTP(smtp_server, int(smtp_port))
            server.starttls()
        server.login(sender_email, sender_password)
        recipients_list = [e.strip() for e in str(recipient_emails).split(',') if e.strip()]
        server.sendmail(sender_email, recipients_list, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        st.error(f"Не удалось отправить письмо. Ошибка: {e}")
        return False


# --- 7. ПАНЕЛЬ С КНОПКАМИ ОТЧЕТОВ ---
st.subheader("📋 Формирование отчетов")

if is_admin:
    c1, c2, c3_asm, c4_ship, c5_arr, c6_auto, c7_alm, c8_admin = st.columns(8)
else:
    c1, c2, c3_asm, c4_ship, c5_arr, c6_auto, c7_alm = st.columns(7)

if "active_report_mode" not in st.session_state:
    st.session_state.active_report_mode = "Поиск по Клиенту"

with c1:
    if st.button("🔵 Поиск по Клиенту"):
        st.session_state.active_report_mode = "Поиск по Клиенту"
        st.rerun()
with c2:
    if st.button("📄 Разрешения"):
        st.session_state.active_report_mode = "Разрешения"
        st.rerun()
with c3_asm:
    if st.button("🔧 В сборке"):
        st.session_state.active_report_mode = "В сборке"
        st.rerun()
with c4_ship:
    if st.button("🚚 Отгружено"):
        st.session_state.active_report_mode = "Отгружено"
        st.rerun()
with c5_arr:
    if st.button("🏢 Прибытие"):
        st.session_state.active_report_mode = "Прибытие"
        st.rerun()
with c6_auto:
    if st.button("🚀 Авто в пути"):
        st.session_state.active_report_mode = "Авто в пути"
        st.rerun()
with c7_alm:
    if st.button("🚛 Отгрузки Алматы"):
        st.session_state.active_report_mode = "Отгрузки Алматы"
        st.rerun()

if is_admin:
    with c8_admin:
        if st.button("⚙️ Админ-панель"):
            st.session_state.active_report_mode = "Админ-панель"
            st.rerun()

# --- 8. ВЫВОД РЕЗУЛЬТАТОВ ---
cols_all = ['№ счета', 'Дата счета', 'Клиент', 'ПкЦБ', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыл (факт)', 'Статус']
cols_no_finance = ['№ счета', 'Дата счета', 'Клиент', 'ПкЦБ', 'Плановая дата отгрузки', 'Плановая дата прибытия', 'Статус']

current_mode = st.session_state.get("active_report_mode", "Поиск по Клиенту")

# ==================== АДМИН-ПАНЕЛЬ (НОВАЯ ВЕРСИЯ) ====================
if current_mode == "Админ-панель" and is_admin:
    st.subheader("⚙️ Панель администратора")

    col_sync1, col_sync2, col_sync3 = st.columns([1, 1, 2])
    with col_sync1:
        if st.button("💾 Сохранить БД в GitHub", type="primary", key="btn_sync_db"):
            with st.spinner("Сохранение..."):
                ok = sync_db_to_github()
            if ok:
                st.success("✅ БД сохранена в GitHub.")
            else:
                st.error("❌ Не удалось сохранить. Проверьте токен GitHub в Secrets.")
    with col_sync2:
        import os as _os
        db_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "transit_system.db")
        if _os.path.exists(db_path):
            with open(db_path, "rb") as f:
                db_bytes = f.read()
            today_str_dl = datetime.date.today().strftime('%d.%m.%Y')
            st.download_button(
                label="📥 Скачать БД на компьютер",
                data=db_bytes,
                file_name=f"transit_system_backup_{today_str_dl}.db",
                mime="application/octet-stream",
                key="btn_download_db"
            )
        else:
            st.warning("БД не найдена.")
    with col_sync3:
        gh_status, gh_msg = check_github_token()
        if gh_status == "ok":
            st.caption(f"✅ {gh_msg}")
        elif gh_status == "missing":
            st.caption(f"⚠️ {gh_msg}")
        elif gh_status == "invalid":
            st.caption(f"🚨 {gh_msg}")
        else:
            st.caption(f"⚠️ {gh_msg}")

    # Вкладки по этапам логистики
    tab_import, tab_created, tab_permission, tab_assembly, tab_transit, tab_almaty, tab_shipped, tab_reject, tab_db = st.tabs([
        "📥 Импорт из 1С",
        "📋 Создан",
        "🛡️ Разрешения (ожидание)",
        "🔧 В сборке",
        "🚛 В пути (авто)",
        "🏢 Прибыл на склад Алматы",
        "📦 Отгружено клиенту",
        "❌ Отказ",
        "🗄️ Редактор БД",
    ])

    # ---------------- ВКЛАДКА: ИМПОРТ ----------------
    with tab_import:
        st.markdown("### 📥 Загрузка ежедневного отчёта из 1С")
        st.caption("Файл Excel (.xlsx, .xls) из 1С. Строки склада «Алматы» игнорируются автоматически.")

        uploaded_excel = st.file_uploader("Перетащите файл:", type=["xlsx", "xls"], key="uploader_1c")

        if uploaded_excel is not None:
            try:
                parsed_df, stats = parse_excel_1c(uploaded_excel)

                if "error" in stats:
                    st.error(stats["error"])
                else:
                    st.success(
                        f"✅ Файл обработан. Всего строк: {stats['total_rows']}, "
                        f"игнорировано (Алматы): {stats['ignored_almaty']}, "
                        f"целевых: {stats['valid_rows']}"
                    )
                    # Распределение по складам
                    st.markdown("**Распределение по складам:**")
                    wh_col1, wh_col2, wh_col3 = st.columns(3)
                    with wh_col1:
                        st.metric("🇷🇺 Внуково", stats["by_warehouse"].get("Внуково", 0))
                    with wh_col2:
                        st.metric("🇧🇾 Брикета", stats["by_warehouse"].get("Брикета", 0))
                    with wh_col3:
                        st.metric("🇧🇾 Дроздово", stats["by_warehouse"].get("Дроздово", 0))

                    # Распределение по статусам 1С
                    st.markdown("**Статусы из 1С:**")
                    for status_1c, count in stats.get("by_status_1c", {}).items():
                        st.caption(f"▪️ {status_1c}: {count} шт.")

                    # Кнопка импорта в БД
                    st.markdown("---")
                    col_imp1, col_imp2 = st.columns([1, 3])
                    with col_imp1:
                        if st.button("💾 Импортировать в базу", type="primary", key="btn_import"):
                            with st.spinner("Импорт в базу..."):
                                saved, updated, skipped = import_invoices_to_db(parsed_df)
                            st.success(
                                f"✅ Импорт завершён! Новых: {saved}, обновлено: {updated}, пропущено: {skipped}"
                            )
                            st.balloons()
                    with col_imp2:
                        st.caption("💡 Новые счета добавляются. Существующие обновляются, если их статус ещё не «В пути» / «Прибыл на склад Алматы» / «Готов к отгрузке» / «Отгружено клиенту».")

                    # Предпросмотр
                    with st.expander("👁️ Предпросмотр распознанных счетов", expanded=False):
                        preview_cols = ["doc_number", "invoice_date", "client", "warehouse", "status_1c", "status", "trip_name"]
                        st.dataframe(parsed_df[preview_cols], use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Не удалось обработать файл. Ошибка: {e}")

        st.markdown("---")
        st.markdown("#### 📦 Импорт из файла экспорта БД (db_export.xlsx)")
        st.caption("Загрузите файл db_export.xlsx со всеми счетами и их данными (статусы, даты, ПкЦБ и т.д.). Существующие счета обновляются, новые — добавляются.")
        uploaded_db = st.file_uploader("Перетащите db_export.xlsx:", type=["xlsx", "xls"], key="uploader_db_export")
        if uploaded_db is not None:
            try:
                with st.spinner("Импорт..."):
                    saved, updated, skipped = import_db_export(uploaded_db)
                st.success(f"✅ Импорт завершён! Новых: {saved}, обновлено: {updated}, пропущено: {skipped}")
                st.balloons()
            except Exception as e:
                st.error(f"Не удалось импортировать файл. Ошибка: {e}")

        st.markdown("---")
        st.markdown("#### 🚛 Импорт авто из файла экспорта (cars_export.xlsx)")
        st.caption("Загрузите файл cars_export.xlsx с авто (дата отгрузки, страна, ПкЦБ, РКЗ, статус прибытия). Счета привязываются по РКЗ/ПкЦБ автоматически.")
        uploaded_cars = st.file_uploader("Перетащите cars_export.xlsx:", type=["xlsx", "xls"], key="uploader_cars_export")
        if uploaded_cars is not None:
            try:
                with st.spinner("Импорт авто..."):
                    saved, updated, skipped = import_cars_export(uploaded_cars)
                st.success(f"✅ Импорт авто завершён! Новых: {saved}, обновлено: {updated}, пропущено: {skipped}")
                st.balloons()
            except Exception as e:
                st.error(f"Не удалось импортировать файл авто. Ошибка: {e}")

        st.markdown("---")
        st.markdown("#### 🗄️ Восстановление из резервной копии БД (.db)")
        st.caption("Загрузите файл transit_system_backup_ДД.ММ.ГГГГ.db (резервная копия). Текущая БД будет заменена.")
        uploaded_backup = st.file_uploader("Перетащите .db файл:", type=["db"], key="uploader_backup_db")
        if uploaded_backup is not None:
            try:
                db_bytes = uploaded_backup.read()
                if db_bytes[:16] != b"SQLite format 3\x00":
                    st.error("Файл не является валидной SQLite БД.")
                else:
                    import os as _os
                    db_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "transit_system.db")
                    with open(db_path, "wb") as f:
                        f.write(db_bytes)
                    st.success(f"✅ БД восстановлена из резервной копии ({len(db_bytes)} байт).")
                    sync_db_to_github()
                    st.rerun()
            except Exception as e:
                st.error(f"Не удалось восстановить БД. Ошибка: {e}")

    # ---------------- ВКЛАДКА: СОЗДАН ----------------
    with tab_created:
        st.markdown("### 📋 Счета со статусом «Создан»")
        st.caption("Отметьте нужно ли разрешение РБ и/или КЗ, проставьте плановую дату отгрузки. При сохранении счета перейдут в «В сборке» или «В сборке, ожидает разрешения».")

        created_invoices = get_invoices_by_filters(status_list=["Создан"])

        if created_invoices.empty:
            st.info("📭 Нет счетов. Загрузите новый файл из 1С.")
        else:
            rename_map_created = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'perm_rb': 'Разрешение РБ',
                'perm_kz': 'Разрешение КЗ', 'plan_ship_date': 'Плановая дата отгрузки',
                'status': 'Статус', 'note': 'Примечание',
            }

            vnu_df = created_invoices[created_invoices["warehouse"] == "Внуково"].copy()
            bridr_df = created_invoices[created_invoices["warehouse"].isin(["Брикета", "Дроздово"])].copy()

            st.markdown("#### 📝 Массовые параметры (применяются к отмеченным счетам)")
            col_mp1, col_mp2 = st.columns(2)
            with col_mp1:
                mass_plan_ship = st.date_input("Плановая дата отгрузки", value=None, key="mass_plan_ship_created")
            with col_mp2:
                st.write("")
            st.markdown("---")

            def _render_created_table(df, group_name, group_flag, key_suffix):
                if df.empty:
                    st.info(f"📭 {group_name}: нет счетов.")
                    return
                st.markdown(f"#### {group_flag} {group_name} ({len(df)} шт.)")
                disp = df.rename(columns={k: v for k, v in rename_map_created.items() if k in df.columns})
                for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'Статус',
                             'Плановая дата отгрузки', 'Примечание']:
                    if col in disp.columns:
                        disp[col] = disp[col].fillna('').astype(str)
                disp.insert(0, '✅ Отметка', False)
                disp.insert(1, '🗑️ Удалить', False)
                available = ['✅ Отметка', '🗑️ Удалить', 'ID', '№ счета', 'Дата счета', 'Клиент', 'Склад',
                             'Статус', 'Разрешение РБ', 'Разрешение КЗ', 'Плановая дата отгрузки', 'Примечание']
                disp = disp[[c for c in available if c in disp.columns]]

                edited = st.data_editor(
                    disp,
                    column_config={
                        '✅ Отметка': st.column_config.CheckboxColumn(),
                        '🗑️ Удалить': st.column_config.CheckboxColumn(help="Отметьте для удаления"),
                        'ID': st.column_config.NumberColumn(disabled=True),
                        '№ счета': st.column_config.TextColumn(disabled=True),
                        'Дата счета': st.column_config.TextColumn(disabled=True),
                        'Клиент': st.column_config.TextColumn(disabled=True),
                        'Склад': st.column_config.TextColumn(disabled=True),
                        'Статус': st.column_config.TextColumn(disabled=True),
                        'Разрешение РБ': st.column_config.CheckboxColumn(),
                        'Разрешение КЗ': st.column_config.CheckboxColumn(),
                        'Плановая дата отгрузки': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                        'Примечание': st.column_config.TextColumn(),
                    },
                    use_container_width=True, hide_index=True,
                    num_rows="fixed", key=f"editor_created_{key_suffix}"
                )

                col_c1, col_c2, col_c3 = st.columns([1, 1, 2])
                with col_c1:
                    if st.button(f"🗑️ Удалить", key=f"btn_del_created_{key_suffix}"):
                        to_delete = edited[edited['🗑️ Удалить'] == True]
                        if to_delete.empty:
                            st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                        else:
                            for _, row in to_delete.iterrows():
                                delete_invoice_by_id(int(row['ID']))
                            st.success(f"✅ Удалено: {len(to_delete)}")
                            sync_db_to_github()
                            st.rerun()
                with col_c2:
                    editor_state = st.session_state.get(f"editor_created_{key_suffix}", {})
                    edited_rows = editor_state.get("edited_rows", {}) if isinstance(editor_state, dict) else {}
                    selected_ids = []
                    for pos in range(len(df)):
                        changes = edited_rows.get(pos, {})
                        if changes.get('✅ Отметка') is True:
                            selected_ids.append(int(df.iloc[pos]['id']))
                    if st.button(f"📝 Применить дату ({len(selected_ids)} шт.)", key=f"btn_plan_ship_{key_suffix}"):
                        if not selected_ids:
                            st.warning("Отметьте счета галочкой «✅ Отметка».")
                        elif not mass_plan_ship:
                            st.warning("Укажите «Плановая дата отгрузки» в массовых параметрах.")
                        else:
                            ship_str = mass_plan_ship.strftime('%d.%m.%Y')
                            rows = [{'id': i, 'plan_ship_date': ship_str} for i in selected_ids]
                            update_invoices_batch(pd.DataFrame(rows))
                            st.success(f"✅ Плановая дата отгрузки ({ship_str}) применена к {len(rows)} счетам.")
                            sync_db_to_github()
                            st.rerun()
                with col_c3:
                    if st.button(f"💾 Сохранить и распределить", type="primary", key=f"btn_created_{key_suffix}"):
                        try:
                            editor_state = st.session_state.get(f"editor_created_{key_suffix}", {})
                            edited_rows = editor_state.get("edited_rows", {}) if isinstance(editor_state, dict) else {}

                            to_delete_ids = []
                            for row_pos, changes in edited_rows.items():
                                if changes.get('🗑️ Удалить') is True:
                                    rid = int(row_pos)
                                    if rid < len(df):
                                        to_delete_ids.append(int(df.iloc[rid]['id']))
                            for inv_id in to_delete_ids:
                                delete_invoice_by_id(inv_id)

                            row_to_id = {pos: int(df.iloc[pos]['id']) for pos in range(len(df))}

                            rows_to_update = []
                            count_to_permission = 0
                            count_to_assembly = 0
                            for pos in range(len(df)):
                                inv_id = row_to_id.get(pos)
                                if inv_id is None or inv_id in to_delete_ids:
                                    continue

                                perm_rb = int(df.iloc[pos].get('perm_rb', 0) or 0)
                                perm_kz = int(df.iloc[pos].get('perm_kz', 0) or 0)

                                changes = edited_rows.get(pos, {})
                                if 'Разрешение РБ' in changes:
                                    perm_rb = 1 if bool(changes['Разрешение РБ']) else 0
                                if 'Разрешение КЗ' in changes:
                                    perm_kz = 1 if bool(changes['Разрешение КЗ']) else 0

                                plan_ship = changes.get('Плановая дата отгрузки')
                                note = changes.get('Примечание')

                                if perm_rb or perm_kz:
                                    new_status = 'В сборке, ожидает разрешения'
                                    count_to_permission += 1
                                else:
                                    new_status = 'В сборке'
                                    count_to_assembly += 1

                                upd_row = {'id': inv_id, 'status': new_status,
                                           'perm_rb': perm_rb, 'perm_kz': perm_kz}
                                if plan_ship is not None:
                                    upd_row['plan_ship_date'] = plan_ship
                                if note is not None:
                                    upd_row['note'] = note
                                rows_to_update.append(upd_row)

                            if rows_to_update:
                                upd_df = pd.DataFrame(rows_to_update)
                                update_invoices_batch(upd_df)

                            parts = []
                            if count_to_permission:
                                parts.append(f"{count_to_permission} → «Разрешения»")
                            if count_to_assembly:
                                parts.append(f"{count_to_assembly} → «В сборке»")
                            if to_delete_ids:
                                parts.append(f"удалено: {len(to_delete_ids)}")
                            if parts:
                                st.success(f"✅ Распределено: {', '.join(parts)}.")
                            else:
                                st.info("Нет счетов для распределения.")
                            st.rerun()
                            sync_db_to_github()
                        except Exception as e:
                            st.error(f"Ошибка при сохранении: {e}")
                st.markdown("---")

            _render_created_table(vnu_df, "Внуково (РФ)", "🇷🇺", "vnu")
            _render_created_table(bridr_df, "Брикета + Дроздово (Беларусь)", "🇧🇾", "bridr")

    # ---------------- ВКЛАДКА: РАЗРЕШЕНИЯ ----------------
    with tab_permission:
        st.markdown("### 🛡️ Счета, ожидающие разрешения РБ / КЗ")
        st.caption("Проставьте даты прямо в таблице (раздельно) или массово блоком. Отметьте счета → «Отправить в сборку».")

        perm_invoices = get_invoices_by_filters(status_list=["В сборке, ожидает разрешения"])

        if perm_invoices.empty:
            st.info("📭 Нет счетов, ожидающих разрешения.")
        else:
            rename_map_perm = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'perm_rb': 'Разрешение РБ',
                'perm_kz': 'Разрешение КЗ', 'perm_send_date': 'Дата отправки на разрешение',
                'plan_ship_date': 'Плановая дата отгрузки', 'note': 'Примечание',
            }

            # Блок массовых дат (общий для всех таблиц)
            st.markdown("#### 📝 Массовые даты (применяются к отмеченным счетам)")
            col_md1, col_md2 = st.columns(2)
            with col_md1:
                mass_perm_send = st.date_input("Дата отправки на разрешение", value=None, key="mass_perm_send")
            with col_md2:
                mass_plan_ship = st.date_input("Плановая дата отгрузки", value=None, key="mass_plan_ship_perm")
            st.markdown("---")

            # 4 группы: Внуково РБ, Внуково КЗ, Брикета+Дроздово РБ, Брикета+Дроздово КЗ
            vnu_df = perm_invoices[perm_invoices["warehouse"] == "Внуково"].copy()
            bridr_df = perm_invoices[perm_invoices["warehouse"].isin(["Брикета", "Дроздово"])].copy()

            groups = [
                (vnu_df[vnu_df["perm_rb"] == 1].copy(), "🇷🇺 Внуково — разрешение РБ", "vnu_rb"),
                (vnu_df[vnu_df["perm_kz"] == 1].copy(), "🇷🇺 Внуково — разрешение КЗ", "vnu_kz"),
                (bridr_df[bridr_df["perm_rb"] == 1].copy(), "🇧🇾 Брикета+Дроздово — разрешение РБ", "bridr_rb"),
                (bridr_df[bridr_df["perm_kz"] == 1].copy(), "🇧🇾 Брикета+Дроздово — разрешение КЗ", "bridr_kz"),
            ]

            def _render_perm_table(df, group_name, key_suffix):
                if df.empty:
                    st.info(f"📭 {group_name}: нет счетов.")
                    return
                st.markdown(f"#### {group_name} ({len(df)} шт.)")
                disp = df.rename(columns={k: v for k, v in rename_map_perm.items() if k in df.columns})
                for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад',
                             'Дата отправки на разрешение', 'Плановая дата отгрузки', 'Примечание']:
                    if col in disp.columns:
                        disp[col] = disp[col].fillna('').astype(str)
                disp.insert(0, '✅ Отметка', False)
                disp.insert(1, '🗑️ Удалить', False)
                available = ['✅ Отметка', '🗑️ Удалить', 'ID', '№ счета', 'Дата счета', 'Клиент', 'Склад',
                              'Разрешение РБ', 'Разрешение КЗ', 'Дата отправки на разрешение',
                              'Плановая дата отгрузки', 'Примечание']
                disp = disp[[c for c in available if c in disp.columns]]

                edited = st.data_editor(
                    disp,
                    column_config={
                        '✅ Отметка': st.column_config.CheckboxColumn(),
                        '🗑️ Удалить': st.column_config.CheckboxColumn(help="Отметьте для удаления"),
                        'ID': st.column_config.NumberColumn(disabled=True),
                        '№ счета': st.column_config.TextColumn(disabled=True),
                        'Дата счета': st.column_config.TextColumn(disabled=True),
                        'Клиент': st.column_config.TextColumn(disabled=True),
                        'Склад': st.column_config.TextColumn(disabled=True),
                        'Разрешение РБ': st.column_config.CheckboxColumn(disabled=True),
                        'Разрешение КЗ': st.column_config.CheckboxColumn(disabled=True),
                        'Дата отправки на разрешение': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                        'Плановая дата отгрузки': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                        'Примечание': st.column_config.TextColumn(),
                    },
                    use_container_width=True, hide_index=True,
                    num_rows="dynamic", key=f"editor_perm_{key_suffix}"
                )

                selected = edited[edited['✅ Отметка'] == True]
                to_del = edited[edited['🗑️ Удалить'] == True]

                # Кнопка применения массовых дат
                if st.button(f"📝 Применить даты ({len(selected)} шт.)", key=f"btn_dates_{key_suffix}"):
                    if selected.empty:
                        st.warning("Отметьте счета галочками «✅ Отметка».")
                    else:
                        send_str = mass_perm_send.strftime('%d.%m.%Y') if mass_perm_send else ""
                        ship_str = mass_plan_ship.strftime('%d.%m.%Y') if mass_plan_ship else ""
                        for _, row in selected.iterrows():
                            update_data = {'id': int(row['ID'])}
                            if send_str:
                                update_data['perm_send_date'] = send_str
                            if ship_str:
                                update_data['plan_ship_date'] = ship_str
                            update_invoices_batch(pd.DataFrame([update_data]))
                        st.success(f"✅ Даты применены к {len(selected)} счетам.")
                        st.rerun()

                col_p1, col_p2 = st.columns([1, 2])
                with col_p1:
                    if st.button(f"🗑️ Удалить ({len(to_del)} шт.)", key=f"btn_del_perm_{key_suffix}"):
                        if to_del.empty:
                            st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                        else:
                            for _, row in to_del.iterrows():
                                delete_invoice_by_id(int(row['ID']))
                            st.success(f"✅ Удалено: {len(to_del)} счетов.")
                            st.rerun()
                            sync_db_to_github()
                with col_p2:
                    # Сохраняем даты, проставленные в таблице + отправляем в сборку
                    if st.button(f"🔧 Отправить в сборку ({len(selected)} шт.)", type="primary", key=f"btn_perm_{key_suffix}"):
                        if selected.empty:
                            st.warning("Отметьте счета галочками «✅ Отметка».")
                        else:
                            # Сохраняем даты из таблицы и меняем статус
                            to_send = selected.drop(columns=['✅ Отметка', '🗑️ Удалить'])
                            reverse = {v: k for k, v in rename_map_perm.items()}
                            upd = to_send.rename(columns=reverse)
                            upd['status'] = "В сборке"
                            upd['perm_rb'] = 0
                            upd['perm_kz'] = 0
                            update_invoices_batch(upd)
                            st.success(f"✅ {len(upd)} счетов отправлены в сборку (даты сохранены).")
                            st.rerun()
                            sync_db_to_github()
                st.markdown("---")

            for df_group, name, suffix in groups:
                _render_perm_table(df_group, name, suffix)

    # ---------------- ВКЛАДКА: В СБОРКЕ ----------------
    with tab_assembly:
        st.markdown("### 🔧 Счета в сборке")
        st.caption("Отметьте счета → заполните массовые параметры → «Применить». Затем назначьте авто и «Отправить в путь».")

        assembly_invoices = get_invoices_by_filters(status_list=["В сборке"])

        if assembly_invoices.empty:
            st.info("📭 Нет счетов в сборке.")
        else:
            rename_map_asm = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'pkcb': 'ПкЦБ',
                'fact_ship_date': 'Дата отгрузки (факт)', 'transit_days': 'Транзит (дней)',
                'plan_arrival': 'Плановая дата прибытия', 'plan_ship_date': 'Плановая дата отгрузки',
                'note': 'Примечание',
            }

            vnu_df = assembly_invoices[assembly_invoices["warehouse"] == "Внуково"].copy()
            bridr_df = assembly_invoices[assembly_invoices["warehouse"].isin(["Брикета", "Дроздово"])].copy()

            # Блок массовых параметров (общий)
            st.markdown("#### 📝 Массовые параметры (применяются к отмеченным счетам)")
            col_m1, col_m2, col_m3 = st.columns(3)
            with col_m1:
                mass_fact_ship = st.date_input("Дата отгрузки (факт)", value=None, key="mass_fact_ship")
            with col_m2:
                mass_pkcb = st.text_input("ПкЦБ", key="mass_pkcb")
            with col_m3:
                mass_transit = st.number_input("Транзит (дней)", min_value=0, max_value=30, value=8, step=1, key="mass_transit")
            st.markdown("---")

            def _render_assembly_table(df, group_name, group_flag, key_suffix):
                if df.empty:
                    st.info(f"📭 {group_name}: нет счетов в сборке.")
                    return None
                st.markdown(f"#### {group_flag} {group_name} ({len(df)} шт.)")
                disp = df.rename(columns={k: v for k, v in rename_map_asm.items() if k in df.columns})
                for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'ПкЦБ',
                             'Плановая дата отгрузки', 'Дата отгрузки (факт)',
                             'Плановая дата прибытия', 'Примечание']:
                    if col in disp.columns:
                        disp[col] = disp[col].fillna('').astype(str)
                disp.insert(0, '✅ Отметка', False)
                disp.insert(1, '🗑️ Удалить', False)
                available = ['✅ Отметка', '🗑️ Удалить', 'ID', '№ счета', 'Дата счета', 'Клиент', 'Склад', 'ПкЦБ',
                              'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Транзит (дней)',
                              'Плановая дата прибытия', 'Примечание']
                disp = disp[[c for c in available if c in disp.columns]]

                edited = st.data_editor(
                    disp,
                    column_config={
                        '✅ Отметка': st.column_config.CheckboxColumn(),
                        '🗑️ Удалить': st.column_config.CheckboxColumn(help="Отметьте для удаления"),
                        'ID': st.column_config.NumberColumn(disabled=True),
                        '№ счета': st.column_config.TextColumn(disabled=True),
                        'Дата счета': st.column_config.TextColumn(disabled=True),
                        'Клиент': st.column_config.TextColumn(disabled=True),
                        'Склад': st.column_config.TextColumn(disabled=True),
                        'ПкЦБ': st.column_config.TextColumn(),
                        'Плановая дата отгрузки': st.column_config.TextColumn(disabled=True),
                        'Дата отгрузки (факт)': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                        'Транзит (дней)': st.column_config.NumberColumn(help="Число"),
                        'Плановая дата прибытия': st.column_config.TextColumn(disabled=True),
                        'Примечание': st.column_config.TextColumn(),
                    },
                    use_container_width=True, hide_index=True,
                    num_rows="fixed", key=f"editor_asm_{key_suffix}"
                )

                editor_state = st.session_state.get(f"editor_asm_{key_suffix}", {})
                edited_rows = editor_state.get("edited_rows", {}) if isinstance(editor_state, dict) else {}
                row_to_id = {pos: int(df.iloc[pos]['id']) for pos in range(len(df))}

                selected_ids = []
                to_del_ids = []
                for pos in range(len(df)):
                    changes = edited_rows.get(pos, {})
                    if changes.get('✅ Отметка') is True:
                        selected_ids.append(row_to_id.get(pos))
                    if changes.get('🗑️ Удалить') is True:
                        to_del_ids.append(row_to_id.get(pos))

                if st.button(f"🗑️ Удалить ({len(to_del_ids)} шт.)", key=f"btn_del_asm_{key_suffix}"):
                    if not to_del_ids:
                        st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                    else:
                        for inv_id in to_del_ids:
                            delete_invoice_by_id(inv_id)
                        st.success(f"✅ Удалено: {len(to_del_ids)} счетов.")
                        st.rerun()
                        sync_db_to_github()

                if st.button(f"📝 Применить параметры ({len(selected_ids)} шт.)", key=f"btn_apply_{key_suffix}"):
                    if not selected_ids:
                        st.warning("Отметьте счета галочками «✅ Отметка».")
                    else:
                        ship_str = mass_fact_ship.strftime('%d.%m.%Y') if mass_fact_ship else ""
                        for inv_id in selected_ids:
                            update_data = {'id': inv_id}
                            if ship_str:
                                update_data['fact_ship_date'] = ship_str
                            if mass_pkcb:
                                update_data['pkcb'] = mass_pkcb
                            if mass_transit:
                                update_data['transit_days'] = int(mass_transit)
                            if ship_str and mass_transit:
                                try:
                                    ship_dt = pd.to_datetime(ship_str, format='%d.%m.%Y')
                                    update_data['plan_arrival'] = (ship_dt + pd.Timedelta(days=int(mass_transit))).strftime('%d.%m.%Y')
                                except Exception:
                                    pass
                            update_invoices_batch(pd.DataFrame([update_data]))
                        st.success(f"✅ Параметры применены к {len(selected_ids)} счетам.")
                        st.rerun()
                        sync_db_to_github()

                st.markdown(f"**🚛 Отправить отмеченные счета в путь:**")
                col_car1, col_car2, col_btn = st.columns([2, 2, 1])
                with col_car1:
                    car_pkcb = st.text_input("Номер авто (ПкЦБ)", key=f"car_pkcb_{key_suffix}",
                                              help="Счета с совпадающим ПкЦБ привяжутся к этому авто")
                with col_car2:
                    car_country = st.text_input("Страна", key=f"car_country_{key_suffix}",
                                                 placeholder="РФ / Беларусь")
                with col_btn:
                    st.write("")
                    send_clicked = st.button(f"🚛 Отправить в путь ({len(selected_ids)} шт.)",
                                              type="primary", key=f"btn_send_{key_suffix}")

                if send_clicked:
                    if not selected_ids:
                        st.warning("Отметьте счета галочками «✅ Отметка».")
                    elif not car_pkcb.strip():
                        st.warning("Укажите «Номер авто (ПкЦБ)».")
                    else:
                        try:
                            today_str = datetime.date.today().strftime('%d.%m.%Y')
                            auto_id = save_car_to_db(
                                dispatch_date=today_str,
                                country=car_country.strip(),
                                location="",
                                doc_number=car_pkcb.strip(),
                                rkz_number="",
                                estimated_arrival="",
                            )
                            linked = link_auto_to_invoices_by_pkcb(auto_id, [car_pkcb.strip()])

                            rows_to_update = []
                            for pos in range(len(df)):
                                inv_id = row_to_id.get(pos)
                                if inv_id is None or inv_id not in selected_ids:
                                    continue

                                fact_ship = df.iloc[pos].get('fact_ship_date', '')
                                ship_date_str = ''
                                changes = edited_rows.get(pos, {})
                                if 'Дата отгрузки (факт)' in changes:
                                    ship_date_str = str(changes.get('Дата отгрузки (факт)', '') or '').strip()
                                if not ship_date_str and fact_ship:
                                    ship_date_str = str(fact_ship).strip()

                                transit = 0
                                if 'Транзит (дней)' in changes:
                                    try:
                                        transit = int(changes.get('Транзит (дней)') or 0)
                                    except Exception:
                                        transit = 0
                                if not transit:
                                    try:
                                        transit = int(df.iloc[pos].get('transit_days', 0) or 0)
                                    except Exception:
                                        transit = 0

                                pkcb_val = ''
                                if 'ПкЦБ' in changes:
                                    pkcb_val = str(changes.get('ПкЦБ', '') or '').strip()
                                if not pkcb_val:
                                    pkcb_val = str(df.iloc[pos].get('pkcb', '') or '').strip()

                                note_val = changes.get('Примечание')

                                plan_arrival = None
                                if ship_date_str and transit:
                                    try:
                                        ship_dt = pd.to_datetime(ship_date_str, format='%d.%m.%Y', errors='coerce')
                                        if pd.isna(ship_dt):
                                            ship_dt = pd.to_datetime(ship_date_str, errors='coerce')
                                        if pd.notna(ship_dt):
                                            plan_arrival = (ship_dt + pd.Timedelta(days=int(transit))).strftime('%d.%m.%Y')
                                    except Exception:
                                        pass

                                upd_row = {'id': inv_id, 'status': 'В пути', 'auto_id': auto_id}
                                if ship_date_str:
                                    upd_row['fact_ship_date'] = ship_date_str
                                if transit:
                                    upd_row['transit_days'] = int(transit)
                                if pkcb_val:
                                    upd_row['pkcb'] = pkcb_val
                                if plan_arrival:
                                    upd_row['plan_arrival'] = plan_arrival
                                if note_val is not None:
                                    upd_row['note'] = note_val
                                rows_to_update.append(upd_row)

                            if rows_to_update:
                                update_invoices_batch(pd.DataFrame(rows_to_update))

                            st.success(f"✅ {len(rows_to_update)} счетов отправлены в «В пути». "
                                       f"Создано авто #{auto_id} (ПкЦБ: {car_pkcb.strip()}). "
                                       f"Привязано по ПкЦБ: {linked}.")
                            st.rerun()
                            sync_db_to_github()
                        except Exception as e:
                            st.error(f"Ошибка при отправке в путь: {e}")
                st.markdown("---")
                return edited

            _render_assembly_table(vnu_df, "Внуково (РФ)", "🇷🇺", "vnu")
            _render_assembly_table(bridr_df, "Брикета + Дроздово (Беларусь)", "🇧🇾", "bridr")

    # ---------------- ВКЛАДКА: В ПУТИ (АВТО) ----------------
    with tab_transit:
        st.markdown("### 🚛 Авто в пути (вкладка «Пополн»)")
        st.caption("Создайте авто, дополните данные, отметьте прибытие — счета автоматически перейдут в «Прибыл на склад Алматы».")

        # Создание нового авто
        with st.expander("➕ Добавить авто", expanded=False):
            col_a1, col_a2, col_a3 = st.columns(3)
            with col_a1:
                new_dispatch = st.date_input("Дата отгрузки", key="car_dispatch")
                new_country = st.text_input("Страна (РФ/Беларусь)", key="car_country")
            with col_a2:
                new_location = st.text_input("Локация", key="car_location")
                new_est_arrival = st.date_input("Плановая дата прибытия", key="car_est_arrival")
            with col_a3:
                new_doc_numbers = st.text_area("№ документов (ПкЦБ) — по одному в строке", key="car_docs", height=100)
                new_rkz_numbers = st.text_area("№ РКЗ (СЧКЗ) — по одному в строке", key="car_rkz", height=100)

            if st.button("💾 Сохранить авто", key="btn_save_car"):
                docs = "\n".join([d.strip() for d in new_doc_numbers.split("\n") if d.strip()])
                rkzs = "\n".join([d.strip() for d in new_rkz_numbers.split("\n") if d.strip()])
                auto_id = save_car_to_db(
                    dispatch_date=new_dispatch.strftime('%d.%m.%Y'),
                    country=new_country,
                    location=new_location,
                    doc_number=docs,
                    rkz_number=rkzs,
                    estimated_arrival=new_est_arrival.strftime('%d.%m.%Y'),
                )
                # Автоматически привязываем счета по № РКЗ и ПкЦБ
                linked_rkz = link_auto_to_invoices_by_rkz(auto_id, [r for r in rkzs.split("\n") if r.strip()])
                linked_pkcb = link_auto_to_invoices_by_pkcb(auto_id, [d for d in docs.split("\n") if d.strip()])
                msg = f"✅ Авто #{auto_id} добавлено."
                if linked_rkz or linked_pkcb:
                    msg += f" Привязано счетов: {linked_rkz} (по РКЗ) + {linked_pkcb} (по ПкЦБ)."
                st.success(msg)
                st.rerun()
                sync_db_to_github()

        # Список авто в пути
        st.markdown("### 🚛 Активные авто")
        active_cars = get_active_cars()

        if active_cars.empty:
            st.info("📭 Нет активных авто.")
        else:
            for _, car in active_cars.iterrows():
                car_id = car['id']
                flag = "🇷🇺" if "росс" in str(car.get('country', '')).lower() or "рф" in str(car.get('country', '')).lower() else "🇧🇾"
                header = f"{flag} Авто #{car_id} | Отгрузка: {car.get('dispatch_date', '-')} | {car.get('location', '-')} | План: {car.get('estimated_arrival', '-')}"

                with st.expander(header, expanded=False):
                    docs_list = [d.strip() for d in str(car.get('doc_number', '')).split("\n") if d.strip()]
                    rkz_list = [d.strip() for d in str(car.get('rkz_number', '')).split("\n") if d.strip()]

                    linked_doc_numbers = get_car_invoice_doc_numbers(car_id)
                    auto_rkz_list = [d for d in linked_doc_numbers if d not in rkz_list]
                    combined_rkz = rkz_list + auto_rkz_list

                    st.markdown("**📝 Редактировать данные авто:**")
                    col_e1, col_e2, col_e3 = st.columns(3)
                    with col_e1:
                        edit_dispatch = st.text_input("Дата отгрузки", value=car.get('dispatch_date', ''), key=f"edit_dispatch_{car_id}")
                        edit_country = st.text_input("Страна", value=car.get('country', ''), key=f"edit_country_{car_id}")
                    with col_e2:
                        edit_location = st.text_input("Локация", value=car.get('location', ''), key=f"edit_location_{car_id}")
                        edit_est_arrival = st.text_input("Плановая дата прибытия", value=car.get('estimated_arrival', ''), key=f"edit_est_arrival_{car_id}")
                    with col_e3:
                        edit_docs = st.text_area("№ документов (ПкЦБ) — по одному в строке",
                                                  value="\n".join(docs_list), key=f"edit_docs_{car_id}", height=80)
                        edit_rkz = st.text_area("№ РКЗ (СЧКЗ) — ручной ввод (по одному в строке)",
                                                  value="\n".join(rkz_list), key=f"edit_rkz_{car_id}", height=80)

                    if st.button("💾 Сохранить изменения авто", key=f"btn_save_edit_car_{car_id}"):
                        edit_docs_clean = "\n".join([d.strip() for d in edit_docs.split("\n") if d.strip()])
                        edit_rkz_clean = "\n".join([d.strip() for d in edit_rkz.split("\n") if d.strip()])
                        update_car(car_id, edit_dispatch, edit_country, edit_location,
                                   edit_docs_clean, edit_rkz_clean, edit_est_arrival)
                        link_statuses = ['В пути', 'В сборке', 'Прибыл на склад Алматы', 'Готов к отгрузке клиенту']
                        linked_rkz = link_auto_to_invoices_by_rkz(car_id, [r for r in edit_rkz_clean.split("\n") if r.strip()], status_list=link_statuses)
                        linked_pkcb = link_auto_to_invoices_by_pkcb(car_id, [d for d in edit_docs_clean.split("\n") if d.strip()], status_list=link_statuses)
                        msg = f"✅ Данные авто #{car_id} обновлены."
                        if linked_rkz or linked_pkcb:
                            msg += f" Доп. привязано счетов: {linked_rkz} (РКЗ) + {linked_pkcb} (ПкЦБ)."
                        st.success(msg)
                        st.rerun()

                    st.markdown("---")

                    st.markdown("**➕ ПкЦБ пополнения (дополнительная привязка счетов):**")
                    col_pk1, col_pk2 = st.columns([3, 1])
                    with col_pk1:
                        pkcb_extra = st.text_area("Введите номера ПкЦБ — по одному в строке",
                                                   key=f"pkcb_extra_{car_id}", height=70)
                    with col_pk2:
                        st.write("")
                        if st.button("🔗 Привязать", key=f"btn_link_pkcb_{car_id}"):
                            pkcb_list = [p.strip() for p in pkcb_extra.split("\n") if p.strip()]
                            if not pkcb_list:
                                st.warning("Введите хотя бы один номер ПкЦБ.")
                            else:
                                link_statuses = ['В пути', 'В сборке', 'Прибыл на склад Алматы', 'Готов к отгрузке клиенту']
                                n = link_auto_to_invoices_by_pkcb(car_id, pkcb_list, status_list=link_statuses)
                                st.success(f"✅ Привязано {n} счетов по ПкЦБ.")
                                st.rerun()
                                sync_db_to_github()

                    st.markdown("---")

                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.caption(f"📋 ПкЦБ ({len(docs_list)}):")
                        if docs_list:
                            st.code("\n".join(docs_list), language="")
                    with col_d2:
                        st.caption(f"📑 № РКЗ (СЧКЗ) — всего {len(combined_rkz)} "
                                   f"(ручных: {len(rkz_list)}, из счетов: {len(auto_rkz_list)}):")
                        if combined_rkz:
                            st.code("\n".join(combined_rkz), language="")

                    st.markdown("---")

                    # ---- Отметка прибытия ----
                    st.markdown("**✅ Отметка прибытия:**")
                    col_arr1, col_arr2, col_arr3 = st.columns([2, 1, 1])
                    with col_arr1:
                        fact_arr = st.date_input(f"Фактическая дата прибытия авто #{car_id}", key=f"fact_arr_{car_id}")
                    with col_arr2:
                        if st.button("✅ Отметить прибытие", key=f"btn_arrive_{car_id}", type="primary"):
                            affected = mark_car_arrived(car_id, fact_arr.strftime('%d.%m.%Y'))
                            st.success(f"✅ Авто #{car_id} прибыло на склад Алматы ({fact_arr.strftime('%d.%m.%Y')}). "
                                        f"Перенесено счетов в «Прибыл на склад Алматы»: {affected}.")
                            st.rerun()
                            sync_db_to_github()
                    with col_arr3:
                        if st.button("🗑️ Удалить", key=f"btn_del_car_{car_id}"):
                            delete_car_by_id(car_id)
                            st.rerun()

    # ---------------- ВКЛАДКА: АЛМАТЫ ----------------
    with tab_almaty:
        st.markdown("### 🏢 Счета на складе Алматы")
        st.caption("Счета сгруппированы по прибывшим авто. Проставьте дату «Расценен» (можно массово) — статус изменится на «Готов к отгрузке клиенту».")

        almaty_invoices = get_invoices_by_filters(status_list=["Прибыл на склад Алматы", "Готов к отгрузке клиенту"])

        if almaty_invoices.empty:
            st.info("📭 Нет счетов на складе Алматы.")
        else:
            st.markdown(f"**Всего счетов: {len(almaty_invoices)}**")

            arrived_cars = get_arrived_cars()
            invoices_by_car = {}
            no_car_invoices = almaty_invoices[almaty_invoices['auto_id'].isna() | (almaty_invoices['auto_id'] == 0) | (almaty_invoices['auto_id'] == '')]
            for _, inv in almaty_invoices.iterrows():
                aid = inv.get('auto_id')
                try:
                    aid = int(aid) if aid is not None and str(aid).strip() not in ('', 'nan', 'None') else None
                except Exception:
                    aid = None
                if aid is None:
                    continue
                invoices_by_car.setdefault(aid, []).append(inv)

            car_map = {int(c['id']): c for _, c in arrived_cars.iterrows()} if not arrived_cars.empty else {}

            def _render_almaty_car_table(car_id, car_row, df_car):
                flag = "🇷🇺" if "росс" in str(car_row.get('country', '')).lower() or "рф" in str(car_row.get('country', '')).lower() else "🇧🇾"
                fact_arr = car_row.get('fact_arrival_date', '-') or '-'
                header = f"{flag} Авто #{car_id} | Прибытие: {fact_arr} | {car_row.get('location', '-')} | {car_row.get('country', '-')}"

                with st.expander(header, expanded=False):
                    docs_list = [d.strip() for d in str(car_row.get('doc_number', '')).split("\n") if d.strip()]
                    rkz_list = [d.strip() for d in str(car_row.get('rkz_number', '')).split("\n") if d.strip()]
                    linked_doc_numbers = get_car_invoice_doc_numbers(car_id)
                    auto_rkz_list = [d for d in linked_doc_numbers if d not in rkz_list]
                    combined_rkz = rkz_list + auto_rkz_list

                    rename_map_alm = {
                        'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                        'client': 'Клиент', 'warehouse': 'Склад', 'fact_arrival': 'Дата прибытия',
                        'rated_date': 'Расценен', 'status': 'Статус', 'note': 'Примечание',
                    }
                    disp = df_car.rename(columns={k: v for k, v in rename_map_alm.items() if k in df_car.columns})
                    for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'Дата прибытия', 'Расценен', 'Статус', 'Примечание']:
                        if col in disp.columns:
                            disp[col] = disp[col].fillna('').astype(str)
                    disp.insert(0, '✅ Отметка', False)
                    disp.insert(1, '🗑️ Удалить', False)
                    available = [c for c in ['✅ Отметка', '🗑️ Удалить', 'ID', '№ счета', 'Дата счета', 'Клиент', 'Склад',
                                              'Дата прибытия', 'Расценен', 'Статус', 'Примечание'] if c in disp.columns]
                    disp = disp[available]

                    editor_key = f"editor_almaty_{car_id}"
                    edited = st.data_editor(
                        disp,
                        column_config={
                            '✅ Отметка': st.column_config.CheckboxColumn(),
                            '🗑️ Удалить': st.column_config.CheckboxColumn(help="Отметьте для удаления"),
                            'ID': st.column_config.NumberColumn(disabled=True),
                            '№ счета': st.column_config.TextColumn(disabled=True),
                            'Дата счета': st.column_config.TextColumn(disabled=True),
                            'Клиент': st.column_config.TextColumn(disabled=True),
                            'Склад': st.column_config.TextColumn(disabled=True),
                            'Дата прибытия': st.column_config.TextColumn(disabled=True),
                            'Расценен': st.column_config.TextColumn(help="ДД.ММ.ГГГГ — при заполнении статус → «Готов к отгрузке клиенту»"),
                            'Статус': st.column_config.SelectboxColumn(
                                options=["Прибыл на склад Алматы", "Готов к отгрузке клиенту"],
                                disabled=True,
                            ),
                            'Примечание': st.column_config.TextColumn(),
                        },
                        use_container_width=True, hide_index=True,
                        num_rows="fixed", key=editor_key
                    )

                    editor_state = st.session_state.get(editor_key, {})
                    edited_rows = editor_state.get("edited_rows", {}) if isinstance(editor_state, dict) else {}
                    row_to_id = {pos: int(df_car.iloc[pos]['id']) for pos in range(len(df_car))}

                    selected_ids = []
                    to_del_ids = []
                    for pos in range(len(df_car)):
                        changes = edited_rows.get(pos, {})
                        if changes.get('✅ Отметка') is True:
                            selected_ids.append(row_to_id.get(pos))
                        if changes.get('🗑️ Удалить') is True:
                            to_del_ids.append(row_to_id.get(pos))

                    col_r1, col_r2 = st.columns([1, 2])
                    with col_r1:
                        mass_rated = st.date_input("📅 Дата расценки (для отмеченных)", value=None, key=f"mass_rated_{car_id}")
                    with col_r2:
                        st.write("")

                    col_b1, col_b2, col_b3 = st.columns([1, 1, 1])
                    with col_b1:
                        if st.button(f"🗑️ Удалить ({len(to_del_ids)} шт.)", key=f"btn_del_alm_{car_id}"):
                            if not to_del_ids:
                                st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                            else:
                                for inv_id in to_del_ids:
                                    delete_invoice_by_id(inv_id)
                                st.success(f"✅ Удалено: {len(to_del_ids)} счетов.")
                                st.rerun()
                                sync_db_to_github()
                    with col_b2:
                        if st.button(f"📝 Применить дату расценки ({len(selected_ids)} шт.)", key=f"btn_rated_{car_id}"):
                            if not selected_ids:
                                st.warning("Отметьте счета галочкой «✅ Отметка».")
                            elif not mass_rated:
                                st.warning("Укажите дату расценки.")
                            else:
                                rated_str = mass_rated.strftime('%d.%m.%Y')
                                rows = [{'id': i, 'rated_date': rated_str, 'status': 'Готов к отгрузке клиенту'}
                                        for i in selected_ids]
                                update_invoices_batch(pd.DataFrame(rows))
                                st.success(f"✅ {len(rows)} счетов → «Готов к отгрузке клиенту» (расценен: {rated_str}).")
                                st.rerun()
                    with col_b3:
                        if st.button(f"💾 Сохранить ({len(df_car)} шт.)", type="primary", key=f"btn_save_alm_{car_id}"):
                            for inv_id in to_del_ids:
                                delete_invoice_by_id(inv_id)
                            rows = []
                            for pos in range(len(df_car)):
                                inv_id = row_to_id.get(pos)
                                if inv_id is None or inv_id in to_del_ids:
                                    continue
                                changes = edited_rows.get(pos, {})
                                rated_val = changes.get('Расценен')
                                note_val = changes.get('Примечание')
                                new_status = None
                                if rated_val is not None:
                                    rated_str = str(rated_val).strip()
                                    new_status = 'Готов к отгрузке клиенту' if rated_str else 'Прибыл на склад Алматы'
                                upd_row = {'id': inv_id}
                                if rated_val is not None:
                                    upd_row['rated_date'] = str(rated_val)
                                if new_status:
                                    upd_row['status'] = new_status
                                if note_val is not None:
                                    upd_row['note'] = note_val
                                rows.append(upd_row)
                            if rows:
                                update_invoices_batch(pd.DataFrame(rows))
                            deleted_msg = f" Удалено: {len(to_del_ids)}." if to_del_ids else ""
                            st.success(f"✅ Сохранено!{deleted_msg}")
                            st.rerun()
                            sync_db_to_github()

                    st.markdown("---")
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.caption(f"📋 ПкЦБ ({len(docs_list)}):")
                        if docs_list:
                            st.code("\n".join(docs_list), language="")
                    with col_d2:
                        st.caption(f"📑 № РКЗ (СЧКЗ) — всего {len(combined_rkz)} "
                                   f"(ручных: {len(rkz_list)}, из счетов: {len(auto_rkz_list)}):")
                        if combined_rkz:
                            st.code("\n".join(combined_rkz), language="")

                    st.markdown("---")
                    st.markdown(f"**📬 Email-рассылка по авто #{car_id}:**")
                    col_e1, col_e2, col_e3 = st.columns([3, 1, 1])
                    with col_e1:
                        notify_eml = st.text_input("Email получателей (через запятую):", value="",
                                                    key=f"alm_email_{car_id}",
                                                    help="Если пусто — используется список из st.secrets")
                    with col_e2:
                        if st.button("📬 Прибыл", key=f"btn_em_arr_{car_id}"):
                            with st.spinner("Отправка..."):
                                ok = send_status_email("Прибыл на склад Алматы", notify_eml)
                            if ok:
                                st.success("✅ Письмо отправлено!")
                    with col_e3:
                        if st.button("📬 Готов", key=f"btn_em_ready_{car_id}"):
                            with st.spinner("Отправка..."):
                                ok = send_status_email("Готов к отгрузке клиенту", notify_eml)
                            if ok:
                                st.success("✅ Письмо отправлено!")

            for car_id, car_row in car_map.items():
                df_car = pd.DataFrame(invoices_by_car.get(car_id, []))
                if df_car.empty:
                    continue
                _render_almaty_car_table(car_id, car_row, df_car)

            other_car_ids = [aid for aid in invoices_by_car.keys() if aid not in car_map]
            for aid in other_car_ids:
                df_car = pd.DataFrame(invoices_by_car.get(aid, []))
                if df_car.empty:
                    continue
                st.markdown(f"#### Авто #{aid} (данные авто удалены)")
                _render_almaty_car_table(aid, {'country': '', 'fact_arrival_date': '', 'location': '', 'doc_number': '', 'rkz_number': ''}, df_car)

            if not no_car_invoices.empty:
                st.markdown(f"#### 📋 Счета без привязки к авто ({len(no_car_invoices)})")
                df_no = no_car_invoices.reset_index(drop=True)
                _render_almaty_car_table(0, {'country': '', 'fact_arrival_date': '', 'location': '', 'doc_number': '', 'rkz_number': ''}, df_no)

            st.markdown("---")
            st.markdown("### 📥 Загрузка файла «Отгружено клиенту»")
            st.caption("Загрузите файл с отгруженными счетами. Счета найденные на вкладке «Прибытие» → «Отгрузки Алматы». Рейс и дата рейса обновляются из файла.")

            uploaded_shipped = st.file_uploader("Перетащите файл «Отгружено клиенту»:", type=["xlsx", "xls"], key="uploader_shipped")
            if uploaded_shipped is not None:
                try:
                    try:
                        raw_df = pd.read_excel(uploaded_shipped, header=None, engine='xlrd')
                    except Exception:
                        raw_df = pd.read_excel(uploaded_shipped, header=None)
                    raw_df = raw_df.dropna(how="all").reset_index(drop=True)
                    if raw_df.empty:
                        st.error("Файл пуст.")
                    else:
                        def _fix_enc_ship(t):
                            if pd.isna(t): return ""
                            t = str(t).strip()
                            try: return t.encode("cp1252").decode("cp1251")
                            except Exception: return t
                        header_idx = 0
                        for i in range(min(5, len(raw_df))):
                            rv = [str(v) for v in raw_df.iloc[i].tolist()]
                            if "номер" in " ".join(rv).lower() and "дата" in " ".join(rv).lower():
                                header_idx = i
                                break
                        raw_df = raw_df.iloc[header_idx + 1:].reset_index(drop=True)
                        raw_df.columns = list(range(1, len(raw_df.columns) + 1))
                        if 1 in raw_df.columns:
                            raw_df = raw_df[raw_df[1].notna() & (raw_df[1].astype(str).str.strip() != "")].reset_index(drop=True)
                        for col in [1, 3, 4]:
                            if col in raw_df.columns:
                                raw_df[col] = raw_df[col].apply(_fix_enc_ship)

                        file_invoices = {}
                        for _, r in raw_df.iterrows():
                            doc_num = str(r.get(1, "")).strip()
                            if not doc_num: continue
                            trip_name = str(r.get(3, "")).strip()
                            trip_date = str(r.get(4, "")).strip()
                            file_invoices[doc_num] = {"trip_name": trip_name, "trip_date": trip_date}

                        almaty_ready = get_invoices_by_filters(status_list=["Прибыл на склад Алматы", "Готов к отгрузке клиенту", "В пути", "В сборке"])
                        today_str = datetime.date.today().strftime('%d.%m.%Y')
                        shipped_rows = []
                        for _, inv in almaty_ready.iterrows():
                            doc_num = str(inv.get('doc_number', '') or '').strip()
                            if not doc_num: continue
                            if doc_num in file_invoices:
                                shipped_rows.append({
                                    'id': int(inv['id']),
                                    'status': 'Отгружено клиенту',
                                    'final_trip_name': file_invoices[doc_num]["trip_name"],
                                    'final_trip_date': file_invoices[doc_num]["trip_date"],
                                })
                        if shipped_rows:
                            update_invoices_batch(pd.DataFrame(shipped_rows))
                            sync_db_to_github()
                            st.success(f"✅ {len(shipped_rows)} счетов → «Отгружено клиенту».")
                            del st.session_state["uploader_shipped"]
                            st.rerun()
                        else:
                            st.warning("Не найдено счетов для отгрузки (счета уже отгружены или отсутствуют на вкладке «Прибытие»).")
                            del st.session_state["uploader_shipped"]
                            st.rerun()
                except Exception as e:
                    st.error(f"Не удалось обработать файл: {e}")

            st.markdown("---")
            st.markdown("### 📥 Загрузка файла «Отказ»")
            st.caption("Загрузите файл с отказными счетами. Счета найденные на вкладке «Прибытие» → вкладка «Отказ».")

            uploaded_reject = st.file_uploader("Перетащите файл «Отказ»:", type=["xlsx", "xls"], key="uploader_reject")
            if uploaded_reject is not None:
                try:
                    try:
                        raw_df = pd.read_excel(uploaded_reject, header=None, engine='xlrd')
                    except Exception:
                        raw_df = pd.read_excel(uploaded_reject, header=None)
                    raw_df = raw_df.dropna(how="all").reset_index(drop=True)
                    if raw_df.empty:
                        st.error("Файл пуст.")
                    else:
                        def _fix_enc_rej(t):
                            if pd.isna(t): return ""
                            t = str(t).strip()
                            try: return t.encode("cp1252").decode("cp1251")
                            except Exception: return t
                        header_idx = 0
                        for i in range(min(5, len(raw_df))):
                            rv = [str(v) for v in raw_df.iloc[i].tolist()]
                            if "номер" in " ".join(rv).lower() and "дата" in " ".join(rv).lower():
                                header_idx = i
                                break
                        raw_df = raw_df.iloc[header_idx + 1:].reset_index(drop=True)
                        raw_df.columns = list(range(1, len(raw_df.columns) + 1))
                        if 1 in raw_df.columns:
                            raw_df = raw_df[raw_df[1].notna() & (raw_df[1].astype(str).str.strip() != "")].reset_index(drop=True)
                        if 1 in raw_df.columns:
                            raw_df[1] = raw_df[1].apply(_fix_enc_rej)

                        reject_docs = set()
                        for _, r in raw_df.iterrows():
                            doc_num = str(r.get(1, "")).strip()
                            if doc_num:
                                reject_docs.add(doc_num)

                        almaty_ready = get_invoices_by_filters(status_list=["Прибыл на склад Алматы", "Готов к отгрузке клиенту", "В пути", "В сборке"])
                        today_str = datetime.date.today().strftime('%d.%m.%Y')
                        reject_rows = []
                        for _, inv in almaty_ready.iterrows():
                            doc_num = str(inv.get('doc_number', '') or '').strip()
                            if not doc_num: continue
                            if doc_num in reject_docs:
                                reject_rows.append({
                                    'id': int(inv['id']),
                                    'status': 'Отказ',
                                    'reject_date': today_str,
                                })
                        if reject_rows:
                            update_invoices_batch(pd.DataFrame(reject_rows))
                            sync_db_to_github()
                            st.success(f"✅ {len(reject_rows)} счетов → «Отказ».")
                            del st.session_state["uploader_reject"]
                            st.rerun()
                        else:
                            st.warning("Не найдено счетов для отказа (счета уже обработаны или отсутствуют на вкладке «Прибытие»).")
                            del st.session_state["uploader_reject"]
                            st.rerun()
                except Exception as e:
                    st.error(f"Не удалось обработать файл: {e}")

    # ---------------- ВКЛАДКА: ОТГРУЖЕНО КЛИЕНТУ ----------------
    with tab_shipped:
        st.markdown("### 📦 Счета со статусом «Отгружено клиенту»")
        st.caption("Счета переходят сюда после загрузки файла отгрузок из 1С на вкладке «Прибыл на склад Алматы».")

        shipped_invoices = get_invoices_by_filters(status_list=["Отгружено клиенту"])
        if shipped_invoices.empty:
            st.info("📭 Нет отгруженных счетов.")
        else:
            st.markdown(f"**Всего: {len(shipped_invoices)}**")
            rename_map_ship = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'fact_arrival': 'Дата прибытия',
                'final_trip_name': 'Рейс (отгрузка)', 'final_trip_date': 'Дата рейса',
                'delivery_date_to_client': 'Дата отгрузки клиенту', 'status': 'Статус', 'note': 'Примечание',
            }
            disp = shipped_invoices.rename(columns={k: v for k, v in rename_map_ship.items() if k in shipped_invoices.columns})
            for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'Дата прибытия', 'Рейс (отгрузка)', 'Дата рейса', 'Статус', 'Примечание']:
                if col in disp.columns:
                    disp[col] = disp[col].fillna('').astype(str)
            available = [c for c in ['ID', '№ счета', 'Дата счета', 'Клиент', 'Склад', 'Дата прибытия',
                                      'Рейс (отгрузка)', 'Дата рейса', 'Статус', 'Примечание'] if c in disp.columns]
            disp = disp[available]
            st.dataframe(disp, use_container_width=True, hide_index=True)

            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                disp.to_excel(writer, index=False, sheet_name='Отгружено клиенту')
            st.download_button(
                label="🟠 Выгрузить в Excel",
                data=output.getvalue(),
                file_name=f"Отгружено_клиенту_{today_dt}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    # ---------------- ВКЛАДКА: ОТКАЗ ----------------
    with tab_reject:
        st.markdown("### ❌ Счета со статусом «Отказ»")
        st.caption("Счета, отсутствующие в файле отгрузок из 1С → клиент отказался. Дата обнаружения отказа фиксируется автоматически.")

        reject_invoices = get_invoices_by_filters(status_list=["Отказ"])
        if reject_invoices.empty:
            st.info("📭 Нет отказов.")
        else:
            st.markdown(f"**Всего: {len(reject_invoices)}**")
            rename_map_rej = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'fact_arrival': 'Дата прибытия',
                'rated_date': 'Расценен', 'reject_date': 'Дата отказа',
                'status': 'Статус', 'note': 'Примечание',
            }
            disp = reject_invoices.rename(columns={k: v for k, v in rename_map_rej.items() if k in reject_invoices.columns})
            for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'Дата прибытия', 'Расценен', 'Дата отказа', 'Статус', 'Примечание']:
                if col in disp.columns:
                    disp[col] = disp[col].fillna('').astype(str)
            available = [c for c in ['ID', '№ счета', 'Дата счета', 'Клиент', 'Склад', 'Дата прибытия',
                                      'Расценен', 'Дата отказа', 'Статус', 'Примечание'] if c in disp.columns]
            disp = disp[available]
            st.dataframe(disp, use_container_width=True, hide_index=True)

            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                disp.to_excel(writer, index=False, sheet_name='Отказ')
            st.download_button(
                label="🟠 Выгрузить в Excel",
                data=output.getvalue(),
                file_name=f"Отказ_{today_dt}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    # ---------------- ВКЛАДКА: РЕДАКТОР БД ----------------
    with tab_db:
        st.markdown("### 🗄️ Ручное редактирование базы данных")
        st.caption("Добавляйте недостающие счета вручную. Заполняйте поля и нажимайте «Добавить». Для удаления — отметьте галочку в таблице ниже.")

        st.markdown("#### ➕ Добавить новый счёт")
        with st.form("add_invoice_form", clear_on_submit=True):
            col_f1, col_f2, col_f3, col_f4 = st.columns(4)
            with col_f1:
                new_doc = st.text_input("№ счета *", help="Например: CЧКЗ-00240087")
            with col_f2:
                new_inv_date = st.text_input("Дата счета", help="ДД.ММ.ГГГГ")
            with col_f3:
                new_client = st.text_input("Клиент")
            with col_f4:
                new_warehouse = st.selectbox("Склад", ["Внуково", "Брикета", "Дроздово", "Алматы"])

            col_f5, col_f6, col_f7, col_f8 = st.columns(4)
            with col_f5:
                new_pkcb = st.text_input("ПкЦБ")
            with col_f6:
                new_status = st.selectbox("Статус", [
                    "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути",
                    "Задержка поставки", "Прибыл на склад Алматы", "Готов к отгрузке клиенту",
                    "Отгружено клиенту", "Отказ"
                ])
            with col_f7:
                new_plan_ship = st.text_input("Плановая дата отгрузки", help="ДД.ММ.ГГГГ")
            with col_f8:
                new_fact_ship = st.text_input("Дата отгрузки (факт)", help="ДД.ММ.ГГГГ")

            col_f9, col_f10, col_f11, col_f12 = st.columns(4)
            with col_f9:
                new_transit = st.text_input("Транзит (дней)")
            with col_f10:
                new_plan_arr = st.text_input("Плановая дата прибытия", help="ДД.ММ.ГГГГ")
            with col_f11:
                new_fact_arr = st.text_input("Дата прибытия (факт)", help="ДД.ММ.ГГГГ")
            with col_f12:
                new_source = st.selectbox("Источник (лист)", ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм", "Отгрузки", "ручной"])

            new_note = st.text_input("Примечание")
            new_order = st.text_input("№ заявки")

            perm_col1, perm_col2 = st.columns(2)
            with perm_col1:
                new_perm_rb = st.checkbox("Разрешение РБ")
            with perm_col2:
                new_perm_kz = st.checkbox("Разрешение КЗ")

            submitted = st.form_submit_button("💾 Добавить счёт", type="primary")
            if submitted:
                if not new_doc.strip():
                    st.error("Укажите «№ счета» — обязательное поле.")
                else:
                    save_invoice_to_db({
                        'doc_number': new_doc.strip(),
                        'invoice_date': new_inv_date.strip(),
                        'client': new_client.strip(),
                        'warehouse': new_warehouse,
                        'pkcb': new_pkcb.strip(),
                        'status': new_status,
                        'plan_ship_date': new_plan_ship.strip(),
                        'fact_ship_date': new_fact_ship.strip(),
                        'transit_days': new_transit.strip(),
                        'plan_arrival': new_plan_arr.strip(),
                        'fact_arrival': new_fact_arr.strip(),
                        'source_sheet': new_source,
                        'note': new_note.strip(),
                        'order_number': new_order.strip(),
                        'perm_rb': 1 if new_perm_rb else 0,
                        'perm_kz': 1 if new_perm_kz else 0,
                        'added_by': 'admin_manual',
                    })
                    st.success(f"✅ Счёт {new_doc.strip()} добавлен в БД.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📋 Все счета в БД (редактирование)")

        db_filter_col1, db_filter_col2 = st.columns(2)
        with db_filter_col1:
            db_search = st.text_input("🔍 Поиск по № счета или клиенту:", key="db_search")
        with db_filter_col2:
            db_status_filter = st.multiselect("Фильтр по статусу:", [
                "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути",
                "Задержка поставки", "Прибыл на склад Алматы", "Готов к отгрузке клиенту",
                "Отгружено клиенту", "Отказ"
            ], key="db_status_filter")

        all_invoices = get_all_invoices()
        if not all_invoices.empty:
            if db_search.strip():
                mask = all_invoices['doc_number'].fillna('').astype(str).str.lower().str.contains(db_search.lower()) | \
                       all_invoices['client'].fillna('').astype(str).str.lower().str.contains(db_search.lower())
                all_invoices = all_invoices[mask]
            if db_status_filter:
                all_invoices = all_invoices[all_invoices['status'].isin(db_status_filter)]

            rename_db = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'pkcb': 'ПкЦБ', 'status': 'Статус',
                'plan_ship_date': 'Плановая дата отгрузки', 'fact_ship_date': 'Дата отгрузки (факт)',
                'transit_days': 'Транзит (дней)', 'plan_arrival': 'Плановая дата прибытия',
                'fact_arrival': 'Дата прибытия (факт)', 'rated_date': 'Расценен',
                'note': 'Примечание', 'order_number': '№ заявки', 'source_sheet': 'Источник',
                'perm_rb': 'Разрешение РБ', 'perm_kz': 'Разрешение КЗ',
                'final_trip_name': 'Рейс', 'final_trip_date': 'Дата рейса',
                'delivery_date_to_client': 'Дата отгрузки клиенту', 'reject_date': 'Дата отказа',
            }
            disp = all_invoices.rename(columns={k: v for k, v in rename_db.items() if k in all_invoices.columns})
            disp.insert(0, '🗑️ Удалить', False)
            for col in ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'ПкЦБ', 'Статус',
                        'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Транзит (дней)',
                        'Плановая дата прибытия', 'Дата прибытия (факт)', 'Расценен',
                        'Примечание', '№ заявки', 'Источник', 'Рейс', 'Дата рейса',
                        'Дата отгрузки клиенту', 'Дата отказа']:
                if col in disp.columns:
                    disp[col] = disp[col].fillna('').astype(str)

            editable_cols = ['№ счета', 'Дата счета', 'Клиент', 'Склад', 'ПкЦБ', 'Статус',
                             'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Транзит (дней)',
                             'Плановая дата прибытия', 'Дата прибытия (факт)', 'Расценен',
                             'Примечание', '№ заявки', 'Рейс', 'Дата рейса', 'Дата отгрузки клиенту', 'Дата отказа']
            available = ['🗑️ Удалить', 'ID'] + [c for c in editable_cols if c in disp.columns] + ['Источник']
            disp = disp[[c for c in available if c in disp.columns]]

            edited_db = st.data_editor(
                disp,
                column_config={
                    '🗑️ Удалить': st.column_config.CheckboxColumn(help="Отметьте для удаления"),
                    'ID': st.column_config.NumberColumn(disabled=True),
                    '№ счета': st.column_config.TextColumn(),
                    'Дата счета': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Клиент': st.column_config.TextColumn(),
                    'Склад': st.column_config.SelectboxColumn(options=["Внуково", "Брикета", "Дроздово", "Алматы"]),
                    'ПкЦБ': st.column_config.TextColumn(),
                    'Статус': st.column_config.SelectboxColumn(options=[
                        "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути",
                        "Задержка поставки", "Прибыл на склад Алматы", "Готов к отгрузке клиенту",
                        "Отгружено клиенту", "Отказ"
                    ]),
                    'Плановая дата отгрузки': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Дата отгрузки (факт)': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Транзит (дней)': st.column_config.NumberColumn(),
                    'Плановая дата прибытия': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Дата прибытия (факт)': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Расценен': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Примечание': st.column_config.TextColumn(),
                    '№ заявки': st.column_config.TextColumn(),
                    'Рейс': st.column_config.TextColumn(),
                    'Дата рейса': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Дата отгрузки клиенту': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Дата отказа': st.column_config.TextColumn(help="ДД.ММ.ГГГГ"),
                    'Источник': st.column_config.TextColumn(disabled=True),
                },
                use_container_width=True, hide_index=True,
                num_rows="fixed", key="editor_db"
            )

            col_db1, col_db2 = st.columns([1, 2])
            with col_db1:
                if st.button("🗑️ Удалить отмеченные", key="btn_del_db"):
                    to_del = edited_db[edited_db['🗑️ Удалить'] == True]
                    if to_del.empty:
                        st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                    else:
                        for _, row in to_del.iterrows():
                            delete_invoice_by_id(int(row['ID']))
                        st.success(f"✅ Удалено: {len(to_del)} счетов.")
                        st.rerun()
                        sync_db_to_github()
            with col_db2:
                if st.button("💾 Сохранить изменения", type="primary", key="btn_save_db"):
                    to_del = edited_db[edited_db['🗑️ Удалить'] == True]
                    for _, row in to_del.iterrows():
                        delete_invoice_by_id(int(row['ID']))
                    to_save = edited_db[edited_db['🗑️ Удалить'] != True].drop(columns=['🗑️ Удалить'])
                    reverse_db = {v: k for k, v in rename_db.items()}
                    upd = to_save.rename(columns=reverse_db)
                    if 'Разрешение РБ' in upd.columns:
                        upd['perm_rb'] = upd['Разрешение РБ'].apply(lambda x: 1 if x in (True, 1, '1', 'True') else 0)
                        upd.drop(columns=['Разрешение РБ'], inplace=True, errors='ignore')
                    if 'Разрешение КЗ' in upd.columns:
                        upd['perm_kz'] = upd['Разрешение КЗ'].apply(lambda x: 1 if x in (True, 1, '1', 'True') else 0)
                        upd.drop(columns=['Разрешение КЗ'], inplace=True, errors='ignore')
                    update_invoices_batch(upd)
                    deleted_msg = f" Удалено: {len(to_del)}." if not to_del.empty else ""
                    st.success(f"✅ Сохранено!{deleted_msg}")
                    st.rerun()
                    sync_db_to_github()
        else:
            st.info("📭 База данных пуста.")

    st.stop()

# ==================== КОНЕЦ АДМИН-ПАНЕЛИ ====================

if current_mode == "Авто в пути":
    show_replenishment_page()
    st.stop()

if current_mode == "Поиск по Клиенту":
    st.session_state.current_report = build_report(
        cols_all, status_list=None,
        invoice_text=invoice_input, client_text=client_input,
        start_dt=start_filter, end_dt=end_filter
    )
    st.session_state.report_name = "Поиск_по_Клиенту"

elif current_mode == "Разрешения":
    st.session_state.current_report = build_report(
        cols_no_finance, status_list=["Создан", "В сборке, ожидает разрешения"],
        invoice_text=invoice_input, client_text=client_input,
        start_dt=start_filter, end_dt=end_filter,
        sort_by="Дата счета", sort_ascending=True
    )
    st.session_state.report_name = "Разрешения"

elif current_mode == "В сборке":
    st.session_state.current_report = build_report(
        cols_no_finance, status_list=["Создан", "В сборке"],
        invoice_text=invoice_input, client_text=client_input,
        start_dt=start_filter, end_dt=end_filter,
        sort_by="Дата счета", sort_ascending=True
    )
    st.session_state.report_name = "В_сборке"

elif current_mode == "Отгружено":
    st.session_state.current_report = build_report(
        cols_all, status_list=["В пути"],
        invoice_text=invoice_input, client_text=client_input,
        start_dt=start_filter, end_dt=end_filter,
        sort_by="Дата отгрузки (факт)", sort_ascending=True
    )
    st.session_state.report_name = "Отгружено"

elif current_mode == "Прибытие":
    st.session_state.current_report = build_report(
        cols_all, status_list=["Прибыл на склад Алматы", "Готов к отгрузке клиенту"],
        invoice_text=invoice_input, client_text=client_input,
        start_dt=start_filter, end_dt=end_filter,
        sort_by="Прибыл (факт)", sort_ascending=True
    )
    st.session_state.report_name = "Прибытие"

elif current_mode == "Отгрузки Алматы":
    cols_almaty_delivery = ['№ счета', 'Дата счета', 'Клиент', 'Прибыл (факт)', 'Статус', 'Рейс', 'Дата рейса']
    st.session_state.current_report = build_report(
        cols_almaty_delivery, status_list=["Отгружено клиенту"],
        invoice_text=invoice_input, client_text=client_input,
        start_dt=start_filter, end_dt=end_filter,
        sort_by="Дата рейса", sort_ascending=True
    )
    st.session_state.report_name = "Отгрузки_Алматы"

if st.session_state.current_report is not None:
    st.write("---")
    st.subheader(f"📊 Результат отчета: {st.session_state.get('active_report_mode', 'Поиск по Клиенту')}")
    if st.session_state.current_report.empty:
        st.info("По заданным параметрам записей не найдено. Смените фильтр или период.")
    else:
        st.data_editor(st.session_state.current_report, hide_index=True, use_container_width=True, disabled=True)

        c5, c6 = st.columns(2)
        with c5:
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                st.session_state.current_report.to_excel(writer, index=False, sheet_name='Отчет')
            processed_data = output.getvalue()
            st.download_button(
                label="🟠 Выгрузить в Excel",
                data=processed_data,
                file_name=f"{st.session_state.report_name}_{today_dt}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        with c6:
            if st.button("💗 Оповестить"):
                st.session_state.show_email_modal = not st.session_state.show_email_modal

if st.session_state.get('show_email_modal', False):
    with st.expander("📬 Настройка отправки уведомлений", expanded=True):
        emails = st.text_input("Введите адреса электронной почты через запятую:")
        if st.button("✉️ Отправить сводку за сегодня"):
            if not emails:
                st.error("Укажите хотя бы один адрес!")
            else:
                with st.spinner("Формирование отчета за сегодня и отправка email..."):
                    success = send_today_report_email(recipient_emails=emails)
                    if success:
                        st.success(f"Сводка успешно отправлена на адреса: {emails}")
                        st.session_state.show_email_modal = False
                        st.rerun()
