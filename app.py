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
from excel_import import parse_excel_1c, import_invoices_to_db
from database import (
    init_db, save_car_to_db, get_all_cars_from_db,
    save_invoice_to_db, get_all_invoices, get_invoices_by_filters,
    update_invoices_batch, delete_invoices_by_status,
    update_invoice_status, get_active_cars, mark_car_arrived,
    delete_car_by_id, update_car, delete_invoice_by_id,
    link_auto_to_invoices_by_rkz, link_auto_to_invoices_by_pkcb,
    get_car_invoices_count, get_invoices_for_email
)

# Инициализируем базу данных при старте
init_db()

# --- НАСТРОЙКА СТРАНИЦЫ И СТИЛЕЙ КНОПОК ---
st.set_page_config(page_title="Система мониторинга", layout="wide")
st.title("📦 Система мониторинга статуса счетов")

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

# --- 2. ПРЯМЫЕ ССЫЛКИ НА ВЕБ-ПУБЛИКАЦИИ CSV ЛИСТОВ ---
sheet_urls = {
    "Вну": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=0&single=true&output=csv",
    "Бри-Дро": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=1228744427&single=true&output=csv",
    "КЗ разр": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=1220441722&single=true&output=csv",
    "РБ разр": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=104608385&single=true&output=csv",
    "Алм": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=289794996&single=true&output=csv",
    "Отгрузки": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=1819554436&single=true&output=csv"
}

# --- 3. ЗАГРУЗКА И СТАНДАРТИЗАЦИЯ ТАБЛИЦ ---
data_dict = {}
col_names_almaty_delivery = [
    '№ заявки', '№ счета', 'Дата счета', 'Клиент',
    'stub4', 'stub5', 'stub6', 'stub7', 'stub8', 'stub9', 'stub10', 'stub11',
    'Прибыл (факт)', 'Статус',
    'stub14', 'stub15', 'stub16',
    'Рейс', 'Дата рейса'
]
unique_statuses_from_db = set()

for name, url in sheet_urls.items():
    try:
        df = pd.read_csv(url, encoding='utf-8-sig', header=None)
        df = df.dropna(how='all').reset_index(drop=True)
        col_names = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'ПкЦБ', 'Склад',
                     'Разрешение', 'Дата отправки на разрешение', 'Плановая дата отгрузки',
                     'Дата отгрузки (факт)', 'Транзит (дней)', 'Плановая дата прибытия',
                     'Прибыл (факт)', 'Статус', 'Расценен']
        actual_col_count = len(df.columns)
        if name == "Отгрузки":
            df.columns = col_names_almaty_delivery[:actual_col_count] + list(range(max(0, actual_col_count - len(col_names_almaty_delivery))))
        else:
            df.columns = col_names[:actual_col_count] + list(range(max(0, actual_col_count - len(col_names))))

        if not df.empty and len(df) > 0:
            first_row_str = str(df.iloc[0].values).lower()
            if 'заявк' in first_row_str or 'счет' in first_row_str:
                df = df.iloc[1:].reset_index(drop=True)

        data_dict[name] = df
    except Exception:
        data_dict[name] = pd.DataFrame()

list_all_statuses = [
    "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути",
    "Задержка поставки", "Прибыл на склад Алматы", "Готов к отгрузке клиенту"
]

# --- 4. ИНИЦИАЛИЗАЦИЯ ПАМЯТИ СОСТОЯНИЯ ---
if 'current_report' not in st.session_state: st.session_state.current_report = None
if 'report_name' not in st.session_state: st.session_state.report_name = ""
if 'show_email_modal' not in st.session_state: st.session_state.show_email_modal = False
if 'active_sheets' not in st.session_state: st.session_state.active_sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"]

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
    default_start_dt = today_dt - datetime.timedelta(days=30)
    date_range = st.date_input("Период поиска (по Дате счета):", value=(default_start_dt, today_dt))
    selected_dropdown_statuses = st.multiselect("📊 Отфильтровать по статусу счетов:", list_all_statuses, key='selected_dropdown_statuses')

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_filter, end_filter = date_range[0], date_range[1]
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_filter, end_filter = date_range[0], today_dt
else:
    start_filter, end_filter = default_start_dt, today_dt

total_rows = sum(len(data_dict[s]) for s in st.session_state.active_sheets if s in data_dict)
sheets_text = ", ".join(st.session_state.active_sheets)
st.write(f"📊 Обработано строк: {total_rows} (листы: {sheets_text})")

# --- 6. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ СБОРКИ И СТРОГОЙ ФИЛЬТРАЦИИ ---
def build_report(target_sheets, required_columns, filter_by_client=True, allowed_statuses=None,
                 filter_by_invoice=True, invoice_text="", client_text="", start_dt=None, end_dt=None):
    frames = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            frames.append(data_dict[s].copy())
    if not frames: return pd.DataFrame()

    df_all = pd.concat(frames, ignore_index=True)

    is_invoice_empty = not invoice_text or not str(invoice_text).strip()

    if 'Дата счета' in df_all.columns and is_invoice_empty:
        df_all['⚙️ Временная Дата'] = pd.to_datetime(df_all['Дата счета'], format='%d.%m.%Y', errors='coerce')
        mask_iso = df_all['⚙️ Временная Дата'].isna()
        df_all.loc[mask_iso, '⚙️ Временная Дата'] = pd.to_datetime(df_all.loc[mask_iso, 'Дата счета'], format='%Y-%m-%d', errors='coerce')

        if start_dt is not None and end_dt is not None and not pd.isna(start_dt) and not pd.isna(end_dt):
            start_ts = pd.Timestamp(start_dt)
            end_ts = pd.Timestamp(end_dt)
            df_all = df_all[
                (df_all['⚙️ Временная Дата'] >= start_ts) &
                (df_all['⚙️ Временная Дата'] <= end_ts)
            ]
        df_all.drop(columns=['⚙️ Временная Дата'], inplace=True, errors='ignore')

    if filter_by_invoice and invoice_text:
        target_col = None
        if 'Номер счета' in df_all.columns:
            target_col = 'Номер счета'
        elif '№ счета' in df_all.columns:
            target_col = '№ счета'

        if target_col:
            search_invoices = [inv.strip().lower() for inv in invoice_text.split(',') if inv.strip()]
            if search_invoices:
                clean_series = df_all[target_col].fillna("").astype(str).str.lower().str.strip()
                df_all = df_all[clean_series.apply(lambda x: any(inv in x for inv in search_invoices))]

    if filter_by_client and client_input and 'Клиент' in df_all.columns:
        clean_text = lambda v: str(v).lower().replace(" ", "").replace(".", "").replace(",", "").replace('"', '').replace("'", "")
        search_words = [clean_text(w) for w in client_input.split(",") if w.strip()]
        if search_words:
            client_mask = df_all['Клиент'].apply(lambda x: any(word in clean_text(x) for word in search_words))
            df_all = df_all[client_mask]

    if allowed_statuses and 'Статус' in df_all.columns:
        df_all['🤖 Системный Статус'] = df_all['Статус'].astype(str).str.strip().str.lower()
        status_list = [str(st_item).strip().lower() for st_item in allowed_statuses]
        df_all = df_all[df_all['🤖 Системный Статус'].isin(status_list)]
        df_all.drop(columns=['🤖 Системный Статус'], inplace=True)

    if 'selected_dropdown_statuses' in st.session_state and st.session_state.selected_dropdown_statuses and 'Статус' in df_all.columns:
        df_all['⚙️ Временный Статус'] = df_all['Статус'].astype(str).str.strip().str.lower()
        dropdown_list = [str(st_item).strip().lower() for st_item in st.session_state.selected_dropdown_statuses]
        df_all = df_all[df_all['⚙️ Временный Статус'].isin(dropdown_list)]
        df_all.drop(columns=['⚙️ Временный Статус'], inplace=True)

    if selected_dropdown_statuses and 'Статус' in df_all.columns:
        df_all = df_all[df_all['Статус'].astype(str).str.strip().isin(selected_dropdown_statuses)]

    final_cols = [c for c in required_columns if c in df_all.columns]
    return df_all[final_cols] if not df_all.empty else pd.DataFrame()

# --- ФУНКЦИЯ ДЛЯ ФИЛЬТРАЦИИ И ОТПРАВКИ СВОДКИ НА EMAIL ---
def send_today_report_email(recipient_emails, target_sheets):
    sender_email = st.secrets["email"]["sender_email"]
    sender_password = st.secrets["email"]["sender_password"]

    today_str_1 = datetime.date.today().strftime('%d.%m.%Y')
    today_str_2 = datetime.date.today().strftime('%Y-%m-%d')

    frames_today = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            df_sheet = data_dict[s].copy()
            if s == "Алм" and 'Расценен' in df_sheet.columns:
                parsed_dates = pd.to_datetime(df_sheet['Расценен'], format='%d.%m.%Y', errors='coerce')
                mask = parsed_dates.dt.date == datetime.date.today()
            else:
                mask = df_sheet.astype(str).apply(
                    lambda row: row.str.contains(today_str_1, na=False) | row.str.contains(today_str_2, na=False),
                    axis=1
                ).any(axis=1)

            if not df_sheet[mask].empty:
                df_filtered = df_sheet[mask]
                if not df_filtered.empty:
                    df_filtered.insert(0, 'Источник (Лист)', s)
                    frames_today.append(df_filtered)

    if not frames_today:
        st.warning("За сегодняшнее число строк в таблицах не найдено. Письмо не отправлено.")
        return False

    df_today_result = pd.concat(frames_today, ignore_index=True)
    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
        df_today_result.to_excel(writer, index=False, sheet_name='Сводка_Сегодня')
    excel_data = excel_buffer.getvalue()

    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        sender_email = st.secrets["email"]["sender_email"]
        sender_password = st.secrets["email"]["sender_password"]
    except Exception:
        st.error("Ошибка конфигурации! На Streamlit Cloud не настроены параметры почты в st.secrets.")
        return False

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
    c1, c2, c3, c4_new, c4, c5, c6_admin = st.columns(7)
else:
    c1, c2, c3, c4_new, c4, c5 = st.columns(6)

if "active_report_mode" not in st.session_state:
    st.session_state.active_report_mode = "Поиск по Клиенту"

with c1:
    if st.button("🔵 Поиск по Клиенту"):
        st.session_state.active_sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм", "Отгрузки"]
        st.session_state.active_report_mode = "Поиск по Клиенту"
        st.rerun()
with c2:
    if st.button("📄 Разрешения"):
        st.session_state.active_sheets = ["КЗ разр", "РБ разр"]
        st.session_state.active_report_mode = "Разрешения"
        st.rerun()
with c3:
    if st.button("🚚 Отгружено"):
        st.session_state.active_sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр"]
        st.session_state.active_report_mode = "Отгружено"
        st.rerun()
with c4_new:
    if st.button("🚀 Авто в пути"):
        st.session_state.active_report_mode = "Авто в пути"
        st.rerun()
with c4:
    if st.button("🏢 Прибытие"):
        st.session_state.active_sheets = ["Алм"]
        st.session_state.active_report_mode = "Прибытие"
        st.rerun()
with c5:
    if st.button("🚛 Отгрузки Алматы"):
        st.session_state.active_sheets = ["Отгрузки"]
        st.session_state.active_report_mode = "Отгрузки Алматы"
        st.rerun()

if is_admin:
    with c6_admin:
        if st.button("⚙️ Админ-панель"):
            st.session_state.active_report_mode = "Админ-панель"
            st.rerun()

# --- 8. ВЫВОД РЕЗУЛЬТАТОВ ---
cols_all = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Наименование товара', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыль (факт)', 'Статус']
cols_no_finance = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Наименование товара', 'Плановая дата отгрузки', 'Плановая дата прибытия', 'Статус']

current_mode = st.session_state.get("active_report_mode", "Поиск по Клиенту")

# ==================== АДМИН-ПАНЕЛЬ (НОВАЯ ВЕРСИЯ) ====================
if current_mode == "Админ-панель" and is_admin:
    st.subheader("⚙️ Панель администратора")

    # Вкладки по этапам логистики
    tab_import, tab_created, tab_permission, tab_assembly, tab_transit, tab_almaty = st.tabs([
        "📥 Импорт из 1С",
        "📋 Создан",
        "🛡️ Разрешения (ожидание)",
        "🔧 В сборке",
        "🚛 В пути (авто)",
        "🏢 Прибыл на склад Алматы",
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

    # ---------------- ВКЛАДКА: СОЗДАН ----------------
    with tab_created:
        st.markdown("### 📋 Счета со статусом «Создан» и «Не обрабатывать»")
        st.caption("Отметьте нужно ли разрешение РБ и/или КЗ, проставьте плановую дату отгрузки. При сохранении счета перейдут в «В сборке» или «В сборке, ожидает разрешения».")

        created_invoices = get_invoices_by_filters(status_list=["Создан", "Не обрабатывать"])

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

            def _render_created_table(df, group_name, group_flag, key_suffix):
                if df.empty:
                    st.info(f"📭 {group_name}: нет счетов.")
                    return
                st.markdown(f"#### {group_flag} {group_name} ({len(df)} шт.)")
                disp = df.rename(columns={k: v for k, v in rename_map_created.items() if k in df.columns})
                disp.insert(0, '🗑️ Удалить', False)
                available = ['🗑️ Удалить', 'ID', '№ счета', 'Дата счета', 'Клиент', 'Склад',
                             'Статус', 'Разрешение РБ', 'Разрешение КЗ', 'Плановая дата отгрузки', 'Примечание']
                disp = disp[[c for c in available if c in disp.columns]]

                edited = st.data_editor(
                    disp,
                    column_config={
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
                    num_rows="dynamic", key=f"editor_created_{key_suffix}"
                )

                col_c1, col_c2 = st.columns([1, 2])
                with col_c1:
                    if st.button(f"🗑️ Удалить", key=f"btn_del_created_{key_suffix}"):
                        to_delete = edited[edited['🗑️ Удалить'] == True]
                        if to_delete.empty:
                            st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                        else:
                            for _, row in to_delete.iterrows():
                                delete_invoice_by_id(int(row['ID']))
                            st.success(f"✅ Удалено: {len(to_delete)}")
                            st.rerun()
                with col_c2:
                    if st.button(f"💾 Сохранить и распределить", type="primary", key=f"btn_created_{key_suffix}"):
                        to_delete = edited[edited['🗑️ Удалить'] == True]
                        for _, row in to_delete.iterrows():
                            delete_invoice_by_id(int(row['ID']))
                        to_save = edited[edited['🗑️ Удалить'] != True].drop(columns=['🗑️ Удалить'])
                        reverse = {v: k for k, v in rename_map_created.items()}
                        upd = to_save.rename(columns=reverse)
                        # Меняем статус только у «Создан»; «Не обрабатывать» оставляем как есть
                        def _new_status(row):
                            if row.get('status') == "Не обрабатывать":
                                return "Не обрабатывать"
                            if row.get('perm_rb') or row.get('perm_kz'):
                                return "В сборке, ожидает разрешения"
                            return "В сборке"
                        upd['status'] = upd.apply(_new_status, axis=1)
                        upd['perm_rb'] = upd['perm_rb'].fillna(0).astype(int)
                        upd['perm_kz'] = upd['perm_kz'].fillna(0).astype(int)
                        update_invoices_batch(upd)
                        deleted_msg = f" Удалено: {len(to_delete)}." if not to_delete.empty else ""
                        st.success(f"✅ Сохранено!{deleted_msg}")
                        st.rerun()
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

            # Получаем список активных авто для назначения
            active_cars_list = get_active_cars()
            car_options = ["— не назначать —"]
            car_ids = [0]
            for _, c in active_cars_list.iterrows():
                label = f"#{c['id']} | {c.get('dispatch_date', '-')} | {c.get('location', '-')} | {c.get('country', '-')}"
                car_options.append(label)
                car_ids.append(c['id'])

            def _render_assembly_table(df, group_name, group_flag, key_suffix):
                if df.empty:
                    st.info(f"📭 {group_name}: нет счетов в сборке.")
                    return None
                st.markdown(f"#### {group_flag} {group_name} ({len(df)} шт.)")
                disp = df.rename(columns={k: v for k, v in rename_map_asm.items() if k in df.columns})
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
                    num_rows="dynamic", key=f"editor_asm_{key_suffix}"
                )

                selected = edited[edited['✅ Отметка'] == True]
                to_del = edited[edited['🗑️ Удалить'] == True]

                # Кнопка удаления
                if st.button(f"🗑️ Удалить ({len(to_del)} шт.)", key=f"btn_del_asm_{key_suffix}"):
                    if to_del.empty:
                        st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                    else:
                        for _, row in to_del.iterrows():
                            delete_invoice_by_id(int(row['ID']))
                        st.success(f"✅ Удалено: {len(to_del)} счетов.")
                        st.rerun()

                # Кнопка применения массовых параметров
                if st.button(f"📝 Применить параметры ({len(selected)} шт.)", key=f"btn_apply_{key_suffix}"):
                    if selected.empty:
                        st.warning("Отметьте счета галочками «✅ Отметка».")
                    else:
                        ship_str = mass_fact_ship.strftime('%d.%m.%Y') if mass_fact_ship else ""
                        for _, row in selected.iterrows():
                            update_data = {'id': int(row['ID'])}
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
                        st.success(f"✅ Параметры применены к {len(selected)} счетам.")
                        st.rerun()

                # Назначение авто и отправка в путь
                st.markdown(f"**🚛 Назначить авто для отправки в путь:**")
                col_car, col_btn = st.columns([3, 1])
                with col_car:
                    chosen_idx = st.selectbox(
                        "Выберите авто:",
                        range(len(car_options)),
                        format_func=lambda i: car_options[i],
                        key=f"car_select_{key_suffix}"
                    )
                with col_btn:
                    if st.button(f"🚛 Отправить в путь ({len(selected)} шт.)", type="primary", key=f"btn_send_{key_suffix}"):
                        if selected.empty:
                            st.warning("Отметьте счета галочками «✅ Отметка».")
                        else:
                            to_send = selected.drop(columns=['✅ Отметка', '🗑️ Удалить'])
                            reverse = {v: k for k, v in rename_map_asm.items()}
                            upd = to_send.rename(columns=reverse)
                            # Считаем плановую дату прибытия только если есть дата отгрузки и транзит
                            def _calc_arrival(row):
                                ship_date_str = str(row.get('fact_ship_date', '')).strip()
                                transit = row.get('transit_days')
                                if not ship_date_str or not transit:
                                    return None
                                try:
                                    ship_dt = pd.to_datetime(ship_date_str, format='%d.%m.%Y', errors='coerce')
                                    if pd.isna(ship_dt):
                                        ship_dt = pd.to_datetime(ship_date_str, errors='coerce')
                                    if pd.notna(ship_dt):
                                        return (ship_dt + pd.Timedelta(days=int(transit))).strftime('%d.%m.%Y')
                                except Exception:
                                    pass
                                return None
                            arrival_values = upd.apply(_calc_arrival, axis=1)
                            # Применяем только непустые значения (чтобы не затирать существующие)
                            upd['plan_arrival'] = arrival_values
                            upd['status'] = "В пути"
                            # Назначаем авто, если выбрано
                            chosen_car_id = car_ids[chosen_idx]
                            if chosen_car_id:
                                upd['auto_id'] = chosen_car_id
                            chosen_car_id = car_ids[chosen_idx]
                            if chosen_car_id:
                                upd['auto_id'] = chosen_car_id
                            update_invoices_batch(upd)
                            msg = f"✅ {len(upd)} счетов переведены в «В пути»."
                            if chosen_car_id:
                                msg += f" Назначено авто #{chosen_car_id}."
                            st.success(msg)
                            st.rerun()
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

                    # ---- Редактирование данных авто ----
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
                        edit_rkz = st.text_area("№ РКЗ (СЧКЗ) — по одному в строке",
                                                  value="\n".join(rkz_list), key=f"edit_rkz_{car_id}", height=80)

                    if st.button("💾 Сохранить изменения авто", key=f"btn_save_edit_car_{car_id}"):
                        edit_docs_clean = "\n".join([d.strip() for d in edit_docs.split("\n") if d.strip()])
                        edit_rkz_clean = "\n".join([d.strip() for d in edit_rkz.split("\n") if d.strip()])
                        update_car(car_id, edit_dispatch, edit_country, edit_location,
                                   edit_docs_clean, edit_rkz_clean, edit_est_arrival)
                        # Перепривязываем счета по обновлённым РКЗ/ПкЦБ
                        linked_rkz = link_auto_to_invoices_by_rkz(car_id, [r for r in edit_rkz_clean.split("\n") if r.strip()])
                        linked_pkcb = link_auto_to_invoices_by_pkcb(car_id, [d for d in edit_docs_clean.split("\n") if d.strip()])
                        msg = f"✅ Данные авто #{car_id} обновлены."
                        if linked_rkz or linked_pkcb:
                            msg += f" Доп. привязано счетов: {linked_rkz} (РКЗ) + {linked_pkcb} (ПкЦБ)."
                        st.success(msg)
                        st.rerun()

                    st.markdown("---")

                    # ---- Просмотр документов ----
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.caption(f"📋 ПкЦБ ({len(docs_list)}):")
                        if docs_list:
                            st.code("\n".join(docs_list), language="")
                    with col_d2:
                        st.caption(f"📑 № РКЗ ({len(rkz_list)}):")
                        if rkz_list:
                            st.code("\n".join(rkz_list), language="")

                    st.markdown("---")

                    # ---- Отметка прибытия ----
                    st.markdown("**✅ Отметка прибытия:**")
                    col_arr1, col_arr2, col_arr3 = st.columns([2, 1, 1])
                    with col_arr1:
                        fact_arr = st.date_input(f"Фактическая дата прибытия авто #{car_id}", key=f"fact_arr_{car_id}")
                    with col_arr2:
                        if st.button("✅ Отметить прибытие", key=f"btn_arrive_{car_id}", type="primary"):
                            affected = mark_car_arrived(car_id, fact_arr.strftime('%d.%m.%Y'))
                            st.success(f"✅ Авто прибыло. Перенесено счетов в «Прибыл на склад Алматы»: {affected}")
                            st.rerun()
                    with col_arr3:
                        if st.button("🗑️ Удалить", key=f"btn_del_car_{car_id}"):
                            delete_car_by_id(car_id)
                            st.rerun()

    # ---------------- ВКЛАДКА: АЛМАТЫ ----------------
    with tab_almaty:
        st.markdown("### 🏢 Счета на складе Алматы")
        st.caption("Проставьте дату «Расценен» — статус изменится на «Готов к отгрузке клиенту».")

        almaty_invoices = get_invoices_by_filters(status_list=["Прибыл на склад Алматы", "Готов к отгрузке клиенту"])

        if almaty_invoices.empty:
            st.info("📭 Нет счетов на складе Алматы.")
        else:
            st.markdown(f"**Найдено: {len(almaty_invoices)}**")

            rename_map_alm = {
                'id': 'ID', 'doc_number': '№ счета', 'invoice_date': 'Дата счета',
                'client': 'Клиент', 'warehouse': 'Склад', 'fact_arrival': 'Дата прибытия',
                'rated_date': 'Расценен', 'status': 'Статус', 'note': 'Примечание',
            }
            disp = almaty_invoices.rename(columns={k: v for k, v in rename_map_alm.items() if k in almaty_invoices.columns})
            disp.insert(0, '🗑️ Удалить', False)
            available = [c for c in ['🗑️ Удалить', 'ID', '№ счета', 'Дата счета', 'Клиент', 'Склад', 'Дата прибытия',
                                      'Расценен', 'Статус', 'Примечание'] if c in disp.columns]
            disp = disp[available]

            edited = st.data_editor(
                disp,
                column_config={
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
                num_rows="dynamic", key="editor_almaty"
            )

            col_a1, col_a2 = st.columns([1, 2])
            with col_a1:
                to_del = edited[edited['🗑️ Удалить'] == True]
                if st.button("🗑️ Удалить отмеченные", key="btn_del_almaty"):
                    if to_del.empty:
                        st.warning("Отметьте счета галочкой «🗑️ Удалить».")
                    else:
                        for _, row in to_del.iterrows():
                            delete_invoice_by_id(int(row['ID']))
                        st.success(f"✅ Удалено: {len(to_del)} счетов.")
                        st.rerun()
            with col_a2:
                if st.button("💾 Сохранить", type="primary", key="btn_almaty"):
                    to_del = edited[edited['🗑️ Удалить'] == True]
                    for _, row in to_del.iterrows():
                        delete_invoice_by_id(int(row['ID']))
                    to_save = edited[edited['🗑️ Удалить'] != True].drop(columns=['🗑️ Удалить'])
                    reverse = {v: k for k, v in rename_map_alm.items()}
                    upd = to_save.rename(columns=reverse)
                    def _alm_status(rated):
                        return "Готов к отгрузке клиенту" if str(rated).strip() else "Прибыл на склад Алматы"
                    upd['status'] = upd['rated_date'].apply(_alm_status)
                    upd['perm_rb'] = 0
                    upd['perm_kz'] = 0
                    update_invoices_batch(upd)
                    deleted_msg = f" Удалено: {len(to_del)}." if not to_del.empty else ""
                    st.success(f"✅ Сохранено!{deleted_msg}")
                    st.rerun()

            # ---- Блок email-рассылки ----
            st.markdown("---")
            st.markdown("### 📬 Email-рассылка")
            st.caption("Отправить уведомление получателям. Список адресов берётся из st.secrets['email']['notify_emails'] (или введите вручную).")

            col_e1, col_e2, col_e3 = st.columns([2, 1, 1])
            with col_e1:
                notify_emails = st.text_input(
                    "Email получателей (через запятую):",
                    value="",
                    key="almaty_notify_emails",
                    help="Если пусто — используется список из st.secrets"
                )
            with col_e2:
                if st.button("📬 «Прибыл на склад Алматы»", key="btn_email_arrived"):
                    with st.spinner("Отправка..."):
                        ok = send_status_email("Прибыл на склад Алматы", notify_emails)
                    if ok:
                        st.success("✅ Письмо отправлено!")
            with col_e3:
                if st.button("📬 «Готов к отгрузке»", key="btn_email_ready"):
                    with st.spinner("Отправка..."):
                        ok = send_status_email("Готов к отгрузке клиенту", notify_emails)
                    if ok:
                        st.success("✅ Письмо отправлено!")

    st.stop()

# ==================== КОНЕЦ АДМИН-ПАНЕЛИ ====================

if current_mode == "Авто в пути":
    show_replenishment_page()
    st.stop()

if current_mode == "Поиск по Клиенту":
    st.session_state.current_report = build_report(
        st.session_state.active_sheets, cols_all,
        filter_by_client=True, allowed_statuses=None,
        filter_by_invoice=True, invoice_text=invoice_input,
        start_dt=start_filter, end_dt=end_filter
    )
    st.session_state.report_name = "Поиск_по_Клиенту"

elif current_mode == "Разрешения":
    st.session_state.current_report = build_report(
        st.session_state.active_sheets, cols_no_finance,
        filter_by_client=True, allowed_statuses=["Создан", "В сборке, ожидает разрешения"],
        filter_by_invoice=True, invoice_text=invoice_input,
        start_dt=start_filter, end_dt=end_filter
    )
    st.session_state.report_name = "Разрешения"

elif current_mode == "Отгружено":
    st.session_state.current_report = build_report(
        st.session_state.active_sheets, cols_all,
        filter_by_client=True, allowed_statuses=["Создан", "В сборке", "В пути", "Задержка поставки"],
        filter_by_invoice=True, invoice_text=invoice_input,
        start_dt=start_filter, end_dt=end_filter
    )
    st.session_state.report_name = "Отгружено"

elif current_mode == "Прибытие":
    st.session_state.current_report = build_report(
        st.session_state.active_sheets, cols_all,
        filter_by_client=True, allowed_statuses=None,
        filter_by_invoice=True, invoice_text=invoice_input,
        start_dt=start_filter, end_dt=end_filter
    )
    st.session_state.report_name = "Прибытие"

elif current_mode == "Отгрузки Алматы":
    cols_almaty_delivery = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Прибыл (факт)', 'Статус', 'Рейс', 'Дата рейса']
    st.session_state.current_report = build_report(
        st.session_state.active_sheets, cols_almaty_delivery,
        filter_by_client=True, allowed_statuses=["Отгружено клиенту"],
        filter_by_invoice=True, invoice_text=invoice_input,
        start_dt=start_filter, end_dt=end_filter
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
                    success = send_today_report_email(
                        recipient_emails=emails,
                        target_sheets=st.session_state.active_sheets
                    )
                    if success:
                        st.success(f"Сводка успешно отправлена на адреса: {emails}")
                        st.session_state.show_email_modal = False
                        st.rerun()
