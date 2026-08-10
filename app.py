# --- ДОПОЛНИТЕЛЬНЫЕ ИМПОРТЫ ДЛЯ ОТПРАВКИ ПОЧТЫ (Добавить в начало файла) ---
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import streamlit as st
import pandas as pd
import datetime
from io import BytesIO
from replenishment import show_replenishment_page
from database import (
    init_db, save_car_to_db, get_all_cars_from_db,
    save_invoice_to_db, get_all_invoices, get_invoices_by_filters,
    update_invoices_batch, delete_invoices_by_status
)

# Инициализируем базу данных при старте
init_db()

# --- НАСТРОЙКА СТРАНИЦЫ И СТИЛЕЙ КНОПОК ---
st.set_page_config(page_title="Система мониторинга", layout="wide")
st.title("📦 Система мониторинга статуса счетов")

# --- 1. ЗАЩИТА ПАРОЛЕМ ---
CORRECT_PASSWORD = "Password123"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.subheader("🔒 Вход в систему")
    user_password = st.text_input("Введите пароль для доступа к отчетам:", type="password")
    if st.button("Войти 🔑"):
        if user_password == CORRECT_PASSWORD:
            st.session_state.authenticated = True
            st.session_state.admin_status = "user"
            st.rerun()
        elif user_password == "supersecret2026":
            st.session_state.authenticated = True
            st.session_state.admin_status = "admin"
            st.rerun()
        else:
            st.error("❌ Неверный пароль! Доступ заблокирован.")
            st.stop()

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

# Вход в админку по секретному хвостику в ссылке сайта (?admin=yes)
is_admin = False
if "admin" in st.query_params and st.query_params["admin"] == "yes":
    is_admin = True

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

# ==================== АДМИН-ПАНЕЛЬ (ПОЛНОСТЬЮ ПЕРЕРАБОТАННАЯ) ====================
if current_mode == "Админ-панель" and is_admin:
    st.subheader("⚙️ Панель администратора: Умный импорт ежедневного Excel")

    st.markdown("### 📥 1. Загрузка ежедневного отчета")

    upload_warehouse = st.selectbox(
        "Укажите склад отправления для загружаемого файла:",
        ["Все склады вместе (Внуково, Брикета, Дроздово)", "Внуково (Россия)", "Брикета (Беларусь)", "Дроздово (Беларусь)"]
    )

    uploaded_excel = st.file_uploader("Перетащите сюда файл Excel (.xlsx, .xls) из 1С:", type=["xlsx", "xls"])

    # ==== ФУНКЦИЯ ИСПРАВЛЕНИЯ КОДИРОВКИ 1С (вынесена наружу) ====
    def fix_encoding(text):
        if pd.isna(text): return ""
        t_str = str(text).strip()
        try:
            return t_str.encode('cp1252').decode('cp1251')
        except:
            return t_str

    excel_df = None
    if uploaded_excel is not None:
        try:
            excel_df = pd.read_excel(uploaded_excel, header=None)
            excel_df = excel_df.dropna(how='all').reset_index(drop=True)

            if not excel_df.empty:
                header_idx = 0
                for i in range(min(5, len(excel_df))):
                    row_str = " ".join(excel_df.iloc[i].astype(str).str.lower().tolist())
                    if "номер" in row_str or "дата" in row_str or "рейс" in row_str:
                        header_idx = i
                        break

                excel_df.columns = excel_df.iloc[header_idx]
                excel_df = excel_df.iloc[header_idx + 1:].reset_index(drop=True)
                excel_df.columns = list(range(1, len(excel_df.columns) + 1))

                if 1 in excel_df.columns:
                    excel_df = excel_df[excel_df[1].notna() & (excel_df[1].astype(str).str.strip() != "")]

                if 3 in excel_df.columns:
                    excel_df[3] = excel_df[3].apply(fix_encoding)
                if 5 in excel_df.columns:
                    excel_df[5] = excel_df[5].apply(fix_encoding)
                if 7 in excel_df.columns:
                    excel_df[7] = excel_df[7].apply(fix_encoding)
                if 9 in excel_df.columns:
                    excel_df[9] = excel_df[9].apply(fix_encoding)

                def Энциклопедия_Строки(row):
                    row_text = " ".join(row.dropna().astype(str).str.lower().tolist())
                    if "алматы" in row_text:
                        return None, None
                    if "Внуково" in upload_warehouse and "внуково" in row_text:
                        return "Внуково", "Россия"
                    elif "Брикета" in upload_warehouse and "брикета" in row_text:
                        return "Брикета", "Беларусь"
                    elif "Дроздово" in upload_warehouse and "дроздово" in row_text:
                        return "Дроздово", "Беларусь"
                    elif "Все склады" in upload_warehouse:
                        if "внуково" in row_text:
                            return "Внуково", "Россия"
                        elif "брикета" in row_text:
                            return "Брикета", "Беларусь"
                        elif "дроздово" in row_text:
                            return "Дроздово", "Беларусь"
                    return None, None

                warehouse_and_country = excel_df.apply(Энциклопедия_Строки, axis=1)
                excel_df['Системный_Склад'] = [item[0] for item in warehouse_and_country]
                excel_df['Системная_Страна'] = [item[1] for item in warehouse_and_country]
                excel_df = excel_df[excel_df['Системный_Склад'].notna()].reset_index(drop=True)

                total_rows_loaded = len(excel_df)
                st.success(f"📋 Файл успешно отфильтрован! Оставлено целевых счетов: {total_rows_loaded}")

                if "Все склады" in upload_warehouse:
                    st.info("📊 **Распределение по складам внутри файла (Игнорируя Алматы):**")
                    for wh in ["Внуково", "Брикета", "Дроздово"]:
                        count_wh = len(excel_df[excel_df['Системный_Склад'] == wh])
                        flag = "🇷🇺" if wh == "Внуково" else "🇧🇾"
                        if count_wh > 0:
                            st.write(f"{flag} Склад **{wh}**: {count_wh} счетов")
                else:
                    flag_sys = "🇷🇺" if "Внуково" in upload_warehouse else "🇧🇾"
                    st.info(f"📍 Оставлен только склад: **{upload_warehouse}** | Страна: {flag_sys}")

            else:
                st.error("Файл пуст или имеет неверную структуру.")
        except Exception as e:
            st.error(f"Не удалось обработать Excel-файл. Ошибка: {e}")

    # ==== ШАГ 2: АВТОМАТИЧЕСКОЕ РАСПРЕДЕЛЕНИЕ ПО СТАТУСАМ 1С ====
    if excel_df is not None and not excel_df.empty:
        st.markdown("---")
        st.markdown("### 📊 2. Автоматическое распределение данных из файла")

        if 5 in excel_df.columns:
            st.markdown("#### 🔹 Разделение счетов по Статусам из 1С:")
            excel_df[5] = excel_df[5].replace("", "Не указан")
            statuses_1c = excel_df[5].drop_duplicates().tolist()
            for stat_1c in statuses_1c:
                sub_df_stat = excel_df[excel_df[5] == stat_1c]
                st.caption(f"▪️ Статус **'{stat_1c}'**: {len(sub_df_stat)} шт. счетов")

        if 3 in excel_df.columns and 4 in excel_df.columns:
            st.markdown("#### 🔹 Обнаруженные плановые рейсы (Колонки 3 и 4):")
            excel_df[3] = excel_df[3].replace("", "БЕЗ РЕЙСА").fillna("БЕЗ РЕЙСА")
            excel_df[4] = excel_df[4].fillna("-").astype(str).str.strip()
            unique_excel_trips = excel_df[[3, 4]].drop_duplicates()
            for _, trip in unique_excel_trips.iterrows():
                trip_name = trip[3]
                trip_date = trip[4]
                trip_rows = excel_df[(excel_df[3] == trip_name) & (excel_df[4] == trip_date)]
                st.write(f"🚢 Рейс: `{trip_name}` от `{trip_date}` — **{len(trip_rows)} счетов**")

        # ==== ШАГ 2.5: РАЗДЕЛЕНИЕ "НЕТ НАКЛАДНОЙ" ====
        st.markdown("---")
        st.markdown("### 📋 2.5 Разделение счетов со статусом 'НЕТ НАКЛАДНОЙ'")

        no_invoice_mask = excel_df[5].astype(str).str.strip().str.lower() == "нет накладной"
        no_invoice_df = excel_df[no_invoice_mask].copy()

        if not no_invoice_df.empty:
            st.info(f"Найдено **{len(no_invoice_df)}** счетов со статусом 'НЕТ НАКЛАДНОЙ'")

            # Формируем DataFrame для редактирования
            edit_no_inv = pd.DataFrame({
                '№ счета': no_invoice_df[1].astype(str).str.strip(),
                'Дата счета': no_invoice_df[2].astype(str).str.strip(),
                'Клиент': no_invoice_df[7].apply(fix_encoding) if 7 in no_invoice_df.columns else "",
                'ПкЦБ': "",
                'Склад': no_invoice_df['Системный_Склад'] if 'Системный_Склад' in no_invoice_df.columns else (no_invoice_df[9].astype(str).str.strip() if 9 in no_invoice_df.columns else ""),
                'Разрешение РБ': False,
                'Разрешение КЗ': False,
                'Примечание': ""
            })

            edited_no_inv = st.data_editor(
                edit_no_inv,
                column_config={
                    '№ счета': st.column_config.TextColumn(disabled=True),
                    'Дата счета': st.column_config.TextColumn(disabled=True),
                    'Клиент': st.column_config.TextColumn(disabled=True),
                    'ПкЦБ': st.column_config.TextColumn(help="Ручной ввод"),
                    'Склад': st.column_config.TextColumn(disabled=True),
                    'Разрешение РБ': st.column_config.CheckboxColumn(help="Отметить если нужно РБ"),
                    'Разрешение КЗ': st.column_config.CheckboxColumn(help="Отметить если нужно КЗ"),
                    'Примечание': st.column_config.TextColumn(help="Ручной ввод")
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="editor_no_invoice"
            )

            col_save1, col_clear1 = st.columns(2)
            with col_save1:
                if st.button("💾 Сохранить 'НЕТ НАКЛАДНОЙ' в базу", key="save_no_inv"):
                    saved_count = 0
                    for _, row in edited_no_inv.iterrows():
                        data = {
                            'doc_number': str(row['№ счета']),
                            'invoice_date': str(row['Дата счета']),
                            'client': str(row['Клиент']),
                            'pkcb': str(row['ПкЦБ']),
                            'warehouse': str(row['Склад']),
                            'perm_rb': 1 if row['Разрешение РБ'] else 0,
                            'perm_kz': 1 if row['Разрешение КЗ'] else 0,
                            'note': str(row['Примечание']),
                            'status': 'НЕТ НАКЛАДНОЙ',
                            'status_1c': 'НЕТ НАКЛАДНОЙ',
                            'source_sheet': upload_warehouse,
                            'added_by': 'admin'
                        }
                        save_invoice_to_db(data)
                        saved_count += 1
                    st.success(f"✅ Успешно сохранено {saved_count} счетов 'НЕТ НАКЛАДНОЙ' в базу данных!")
                    st.balloons()
            with col_clear1:
                if st.button("🗑️ Очистить 'НЕТ НАКЛАДНОЙ' из базы", key="clear_no_inv"):
                    delete_invoices_by_status('НЕТ НАКЛАДНОЙ')
                    st.warning("🗑️ Все счета со статусом 'НЕТ НАКЛАДНОЙ' удалены из базы.")
                    st.rerun()
        else:
            st.info("Счетов со статусом 'НЕТ НАКЛАДНОЙ' в загруженном файле не обнаружено.")

        # ==== ШАГ 3: ИНСТРУМЕНТЫ ГРУППИРОВКИ И ЛОГИСТИКИ ====
        st.markdown("---")
        st.markdown("### 🛠️ 3. Инструменты группировки и логистики")

        st.markdown("#### Фильтры отбора счетов из базы данных:")
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            filter_status = st.multiselect(
                "Статус счета:",
                ['НЕТ НАКЛАДНОЙ', 'Отгружен', 'Обработан', 'Не обрабатывать', 'Выгружен в WMS'],
                default=[],
                key="filter_status"
            )
        with col_f2:
            filter_warehouse = st.multiselect(
                "Склад:",
                ['Внуково', 'Брикета', 'Дроздово'],
                default=[],
                key="filter_warehouse"
            )
        with col_f3:
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                filter_perm_rb = st.checkbox("Разрешение РБ", key="filter_perm_rb")
            with col_p2:
                filter_perm_kz = st.checkbox("Разрешение КЗ", key="filter_perm_kz")

        # Загружаем счета из БД с учетом фильтров
        db_invoices = get_invoices_by_filters(
            status_list=filter_status if filter_status else None,
            warehouse_list=filter_warehouse if filter_warehouse else None,
            perm_rb=1 if filter_perm_rb else None,
            perm_kz=1 if filter_perm_kz else None
        )

        st.markdown("---")
        st.markdown("#### Массовые параметры логистики (применятся ко всем отображенным счетам при сохранении):")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1:
            mass_plan_ship = st.date_input("Плановая дата отгрузки", value=None, key="mass_plan_ship")
        with col_m2:
            mass_perm_send = st.date_input("Дата отправки на разрешение", value=None, key="mass_perm_send")
        with col_m3:
            mass_trip = st.text_input("Рейс", key="mass_trip")
        with col_m4:
            mass_status = st.selectbox(
                "Статус сборки:",
                ["", "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути", "Задержка поставки", "Прибыл на склад Алматы"],
                key="mass_status"
            )

        # Отображение таблицы с редактированием
        if not db_invoices.empty:
            st.markdown(f"**Найдено счетов в базе: {len(db_invoices)}**")

            # Подготовка колонок для отображения
            display_df = db_invoices.copy()

            # Переименование для читаемости
            rename_map = {
                'id': 'ID',
                'doc_number': '№ счета',
                'invoice_date': 'Дата счета',
                'client': 'Клиент',
                'pkcb': 'ПкЦБ',
                'warehouse': 'Склад',
                'perm_rb': 'Разрешение РБ',
                'perm_kz': 'Разрешение КЗ',
                'note': 'Примечание',
                'plan_ship_date': 'Плановая дата отгрузки',
                'fact_ship_date': 'Дата отгрузки (факт)',
                'transit_days': 'Транзит (дней)',
                'plan_arrival': 'Плановая дата прибытия',
                'fact_arrival': 'Прибыл (факт)',
                'status': 'Статус',
                'perm_send_date': 'Дата отправки на разрешение',
                'trip_name': 'Рейс'
            }
            display_df = display_df.rename(columns={k: v for k, v in rename_map.items() if k in display_df.columns})

            # Определяем порядок колонок
            desired_cols = ['ID', '№ счета', 'Дата счета', 'Клиент', 'ПкЦБ', 'Склад',
                           'Разрешение РБ', 'Разрешение КЗ', 'Примечание',
                           'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Транзит (дней)',
                           'Плановая дата прибытия', 'Прибыл (факт)', 'Статус',
                           'Дата отправки на разрешение', 'Рейс']
            available_cols = [c for c in desired_cols if c in display_df.columns]
            display_df = display_df[available_cols]

            # Заполняем массовые значения в пустые ячейки (для наглядности)
            if mass_plan_ship and 'Плановая дата отгрузки' in display_df.columns:
                display_df['Плановая дата отгрузки'] = display_df['Плановая дата отгрузки'].fillna(mass_plan_ship.strftime('%d.%m.%Y'))
            if mass_perm_send and 'Дата отправки на разрешение' in display_df.columns:
                display_df['Дата отправки на разрешение'] = display_df['Дата отправки на разрешение'].fillna(mass_perm_send.strftime('%d.%m.%Y'))
            if mass_trip and 'Рейс' in display_df.columns:
                display_df['Рейс'] = display_df['Рейс'].fillna(mass_trip)
            if mass_status and 'Статус' in display_df.columns:
                display_df['Статус'] = display_df['Статус'].replace('', mass_status).fillna(mass_status)

            edited_db = st.data_editor(
                display_df,
                column_config={
                    'ID': st.column_config.NumberColumn(disabled=True),
                    '№ счета': st.column_config.TextColumn(disabled=True),
                    'Дата счета': st.column_config.TextColumn(disabled=True),
                    'Клиент': st.column_config.TextColumn(disabled=True),
                    'Склад': st.column_config.TextColumn(disabled=True),
                    'Разрешение РБ': st.column_config.CheckboxColumn(),
                    'Разрешение КЗ': st.column_config.CheckboxColumn(),
                    'ПкЦБ': st.column_config.TextColumn(),
                    'Примечание': st.column_config.TextColumn(),
                    'Плановая дата отгрузки': st.column_config.TextColumn(),
                    'Дата отгрузки (факт)': st.column_config.TextColumn(),
                    'Транзит (дней)': st.column_config.TextColumn(),
                    'Плановая дата прибытия': st.column_config.TextColumn(),
                    'Прибыл (факт)': st.column_config.TextColumn(),
                    'Статус': st.column_config.SelectboxColumn(
                        options=["НЕТ НАКЛАДНОЙ", "Отгружен", "Обработан", "Не обрабатывать", "Выгружен в WMS",
                                 "Создан", "В сборке", "В сборке, ожидает разрешения", "В пути", "Задержка поставки", "Прибыл на склад Алматы"]
                    ),
                    'Дата отправки на разрешение': st.column_config.TextColumn(),
                    'Рейс': st.column_config.TextColumn()
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="editor_db_invoices"
            )

            col_save2, col_refresh2 = st.columns(2)
            with col_save2:
                if st.button("💾 Сохранить изменения в базу", key="save_db_changes"):
                    # Переименовываем обратно для записи в БД
                    reverse_map = {v: k for k, v in rename_map.items()}
                    update_df = edited_db.rename(columns=reverse_map)

                    # Применяем массовые параметры к пустым полям
                    if mass_plan_ship and 'plan_ship_date' in update_df.columns:
                        update_df['plan_ship_date'] = update_df['plan_ship_date'].replace('', mass_plan_ship.strftime('%d.%m.%Y')).fillna(mass_plan_ship.strftime('%d.%m.%Y'))
                    if mass_perm_send and 'perm_send_date' in update_df.columns:
                        update_df['perm_send_date'] = update_df['perm_send_date'].replace('', mass_perm_send.strftime('%d.%m.%Y')).fillna(mass_perm_send.strftime('%d.%m.%Y'))
                    if mass_trip and 'trip_name' in update_df.columns:
                        update_df['trip_name'] = update_df['trip_name'].replace('', mass_trip).fillna(mass_trip)
                    if mass_status and 'status' in update_df.columns:
                        update_df['status'] = update_df['status'].replace('', mass_status).fillna(mass_status)

                    # Приводим boolean к int
                    if 'perm_rb' in update_df.columns:
                        update_df['perm_rb'] = update_df['perm_rb'].astype(int)
                    if 'perm_kz' in update_df.columns:
                        update_df['perm_kz'] = update_df['perm_kz'].astype(int)

                    update_invoices_batch(update_df)
                    st.success("✅ Все изменения успешно сохранены в базу данных!")
                    st.balloons()
            with col_refresh2:
                if st.button("🔄 Обновить таблицу", key="refresh_db"):
                    st.rerun()
        else:
            st.info("📭 По заданным фильтрам счетов в базе не найдено. Загрузите данные через раздел 'НЕТ НАКЛАДНОЙ' или проверьте фильтры.")

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
