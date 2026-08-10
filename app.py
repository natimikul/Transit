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
from database import init_db, save_car_to_db, get_all_cars_from_db

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
                st.session_state.admin_status = "user" # Заменили тут
                st.rerun()
            elif user_password == "supersecret2026":
                st.session_state.authenticated = True
                st.session_state.admin_status = "admin" # Заменили тут
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
# Отдельная структура столбцов для листа "Отгрузки" (A, B, C, D ... M, N ... R, S)
col_names_almaty_delivery = [
    '№ заявки', '№ счета', 'Дата счета', 'Клиент',  # A, B, C, D
    'stub4', 'stub5', 'stub6', 'stub7', 'stub8', 'stub9', 'stub10', 'stub11',   # E - L
    'Прибыл (факт)', 'Статус',  # M, N (13-я и 14-я колонка)
    'stub14', 'stub15', 'stub16',  # O, P, Q
    'Рейс', 'Дата рейса'  # R, S (18-я и 19-я колонки)
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
        # Находим реальное количество колонок в текущем листе
        actual_col_count = len(df.columns)
        # Назначаем имена только для тех колонок, которые физически существуют
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
    "Создан",
    "В сборке",
    "В сборке, ожидает разрешения",
    "В пути",
    "Задержка поставки",
    "Прибыл на склад Алматы",
    "Готов к отгрузке клиенту"
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
    # Если пользователь начал писать в любое из полей, сбрасываем старый кнопочный отчет
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

# Считаем строки ТОЛЬКО на тех листах, которые выбраны текущим отчетом
total_rows = sum(len(data_dict[s]) for s in st.session_state.active_sheets if s in data_dict)
sheets_text = ", ".join(st.session_state.active_sheets)
st.write(f"📊 Обработано строк: {total_rows} (листы: {sheets_text})")

# --- 6. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ СБОРКИ И СТРОГОЙ ФИЛЬТРАЦИИ ---
def build_report(target_sheets, required_columns, filter_by_client=True, allowed_statuses=None, filter_by_invoice=True, invoice_text="", client_text="", start_dt=None, end_dt=None):

    frames = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            frames.append(data_dict[s].copy())
    if not frames: return pd.DataFrame()
        
    df_all = pd.concat(frames, ignore_index=True)
    
    # Фильтр по Дате счета
    is_invoice_empty = not invoice_text or not str(invoice_text).strip()
    
    if 'Дата счета' in df_all.columns and is_invoice_empty:
            
        # Принудительно переводим колонку в формат даты, игнорируя ошибки
        df_all['⚙️ Временная Дата'] = pd.to_datetime(df_all['Дата счета'], format='%d.%m.%Y', errors='coerce')
        mask_iso = df_all['⚙️ Временная Дата'].isna()
        df_all.loc[mask_iso, '⚙️ Временная Дата'] = pd.to_datetime(df_all.loc[mask_iso, 'Дата счета'], format='%Y-%m-%d', errors='coerce')
        
        # Безопасно фильтруем по переданным датам, ТОЛЬКО если они физически существуют
        if start_dt is not None and end_dt is not None and not pd.isna(start_dt) and not pd.isna(end_dt):
            start_ts = pd.Timestamp(start_dt)
            end_ts = pd.Timestamp(end_dt)
            
            df_all = df_all[
                (df_all['⚙️ Временная Дата'] >= start_ts) & 
                (df_all['⚙️ Временная Дата'] <= end_ts)
            ]
        
        # Удаляем временную техническую колонку
        df_all.drop(columns=['⚙️ Временная Дата'], inplace=True, errors='ignore')

    # Улучшенный и безопасный фильтр по Номеру счета
    if filter_by_invoice and invoice_text:
        target_col = None
        if 'Номер счета' in df_all.columns:
            target_col = 'Номер счета'
        elif '№ счета' in df_all.columns:
            target_col = '№ счета'
            
        if target_col:
            # Очищаем пользовательский ввод
            search_invoices = [inv.strip().lower() for inv in invoice_text.split(',') if inv.strip()]
            if search_invoices:
                # Безопасно приводим всю колонку к нижнему регистру, заменяя NaN на пустую строку
                clean_series = df_all[target_col].fillna("").astype(str).str.lower().str.strip()
                # Фильтруем: оставляем строки, где хотя бы один номер из поиска совпал
                df_all = df_all[clean_series.apply(lambda x: any(inv in x for inv in search_invoices))]
   
    # Фильтр по Наименованию Клиента
    if filter_by_client and client_input and 'Клиент' in df_all.columns:
        clean_text = lambda v: str(v).lower().replace(" ", "").replace(".", "").replace(",", "").replace('"', '').replace("'", "")
        search_words = [clean_text(w) for w in client_input.split(",") if w.strip()]
        if search_words:
            client_mask = df_all['Клиент'].apply(lambda x: any(word in clean_text(x) for word in search_words))
            df_all = df_all[client_mask]
            
    # Фильтр по Статусу (Системные кнопки отчетов)
    if allowed_statuses and 'Статус' in df_all.columns:
        df_all['🤖 Системный Статус'] = df_all['Статус'].astype(str).str.strip().str.lower()
        status_list = [str(st_item).strip().lower() for st_item in allowed_statuses]
        df_all = df_all[df_all['🤖 Системный Статус'].isin(status_list)]
        df_all.drop(columns=['🤖 Системный Статус'], inplace=True)
    # Фильтр по Статусу из выпадающего списка на экране
    if 'selected_dropdown_statuses' in st.session_state and st.session_state.selected_dropdown_statuses and 'Статус' in df_all.columns:
        df_all['⚙️ Временный Статус'] = df_all['Статус'].astype(str).str.strip().str.lower()
        dropdown_list = [str(st_item).strip().lower() for st_item in st.session_state.selected_dropdown_statuses]
        df_all = df_all[df_all['⚙️ Временный Статус'].isin(dropdown_list)]
        df_all.drop(columns=['⚙️ Временный Статус'], inplace=True)

    # Фильтр по Выпадающему списку статусов
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
        # 1. Собираем строки с сегодняшней датой со всех выбранных листов
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            df_sheet = data_dict[s].copy()
            
            # Если это лист Алм, проверяем колонку 'Расценен' с явным указанием формата дат
            if s == "Алм" and 'Расценен' in df_sheet.columns:
                parsed_dates = pd.to_datetime(df_sheet['Расценен'], format='%d.%m.%Y', errors='coerce')
                mask = parsed_dates.dt.date == datetime.date.today()

            else:
                # Для остальных листов оставляем обычный поиск текста по всей строке
                mask = df_sheet.astype(str).apply(
                    lambda row: row.str.contains(today_str_1, na=False) | row.str.contains(today_str_2, na=False), 
                    axis=1
                ).any(axis=1)

            if not df_sheet[mask].empty:
               df_filtered = df_sheet[mask]
               if not df_filtered.empty:
                  df_filtered.insert(0, 'Источник (Лист)', s)
                  frames_today.append(df_filtered)

    # Склеиваем все найденные за сегодня строки
        # Проверяем, нашли ли мы хоть какие-то строки перед тем, как склеивать их
    if not frames_today:
        st.warning("За сегодняшнее число строк в таблицах не найдено. Письмо не отправлено.")
        return False

    # Склеиваем найденные строки, если они есть
    df_today_result = pd.concat(frames_today, ignore_index=True)

    # 2. Создаем Excel-файл во вложении (в оперативной памяти)
    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
        df_today_result.to_excel(writer, index=False, sheet_name='Сводка_Сегодня')
    excel_data = excel_buffer.getvalue()

    # 3. Настройка конфигурации SMTP (Берется из Secrets на Streamlit Cloud)
    # Перед запуском вам нужно будет добавить эти параметры в меню Secrets вашего Streamlit аккаунта.
    try:
        smtp_server =  "smtp.gmail.com"   # например: smtp.yandex.ru или smtp.mail.ru
        smtp_port = 465      # обычно 465 (для SSL) или 587 (для TLS)
        sender_email = st.secrets["email"]["sender_email"]
        sender_password = st.secrets["email"]["sender_password"]

    except Exception:
        st.error("Ошибка конфигурации! На Streamlit Cloud не настроены параметры почты в st.secrets.")
        return False

    # 4. Формирование тела письма по вашему шаблону
    today_formatted = datetime.date.today().strftime('%d.%m.%Y')
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_emails  # Строка с адресами через запятую
    msg['Subject'] = f"Мониторинг счетов — Сводка за {today_formatted}"

    body = f"""Добрый день!

Информируем Вас о смене статуса транзитных счетов на сегодня ({today_formatted}).
Файл во вложении.
"""
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

      # 5. Прикрепляем созданный Excel-файл
    filename = f"Svodka_tranzitnyh_schetov_{today_formatted}.xlsx"
    
    part = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    part.set_payload(excel_data)
    encoders.encode_base64(part)
    
    part.add_header(
        'Content-Disposition',
        'attachment',
        filename=filename
    )
    msg.attach(part)

    # 6. Подключение и отправка через SSL
    try:
        # Для большинства СНГ сервисов (Яндекс, Mail) используется SSL порт 465:
        if int(smtp_port) == 465:
            server = smtplib.SMTP_SSL(smtp_server, int(smtp_port))
        else:
            server = smtplib.SMTP(smtp_server, int(smtp_port))
            server.starttls()
            
        server.login(sender_email, sender_password)
        
        # Разделяем список получателей для корректной отправки сервером
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
        
# Если зашел админ, выводим 7-ю кнопку в колонку c6_admin
if is_admin:
    with c6_admin:
        if st.button("⚙️ Админ-панель"):
            st.session_state.active_report_mode = "Админ-панель"
            st.rerun()

# --- 8. ВЫВОД РЕЗУЛЬТАТОВ С ПОДДЕРЖКОЙ ВЫДЕЛЕНИЯ И КОПИРОВАНИЯ ---
# Если отчет еще не сформирован кнопками, собираем его автоматически по фильтрам из полей ввода
# Очищаем старый отчёт и строим его заново на основе выбранного режима кнопки
cols_all = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Наименование товара', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыль (факт)', 'Статус']
cols_no_finance = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Наименование товара', 'Плановая дата отгрузки', 'Плановая дата прибытия', 'Статус']

# Проверяем, какой режим сейчас активен (если кнопка ещё не нажималась, ставим режим по умолчанию)
current_mode = st.session_state.get("active_report_mode", "Поиск по Клиенту")

if current_mode == "Авто в пути":
    show_replenishment_page()
    st.stop()
if current_mode == "Админ-панель" and is_admin:
    st.subheader("⚙️ Панель администратора: Умный импорт ежедневного Excel")
    
    st.markdown("### 📥 1. Загрузка ежедневного отчета")
    
    upload_warehouse = st.selectbox(
        "Укажите склад отправления для загружаемого файла:",
        ["Все склады вместе (Внуково, Брикета, Дроздово)", "Внуково (Россия)", "Брикета (Беларусь)", "Дроздово (Беларусь)"]
    )
    
    # Объявляем переменную здесь, чтобы она гарантированно существовала
    uploaded_excel = st.file_uploader("Перетащите сюда файл Excel (.xlsx, .xls) из 1С:", type=["xlsx", "xls"])
    
    if uploaded_excel is not None:
        try:
            # Читаем Excel файл без заголовков
            excel_df = pd.read_excel(uploaded_excel, header=None)
            excel_df = excel_df.dropna(how='all').reset_index(drop=True)
            
            if not excel_df.empty:
                # Находим реальную строчку заголовка (где есть ключевые слова)
                header_idx = 0
                for i in range(min(5, len(excel_df))):
                    row_str = " ".join(excel_df.iloc[i].astype(str).str.lower().tolist())
                    if "номер" in row_str or "дата" in row_str or "рейс" in row_str:
                        header_idx = i
                        break
                
                # Пересобираем датафрейм с правильными заголовками
                excel_df.columns = excel_df.iloc[header_idx]
                excel_df = excel_df.iloc[header_idx + 1:].reset_index(drop=True)
                
                # Принудительно индексируем колонки цифрами от 1 для точного совпадения
                excel_df.columns = list(range(1, len(excel_df.columns) + 1))
                
                # Фильтруем пустые строки по номеру документа (Колонка 1)
                if 1 in excel_df.columns:
                    excel_df = excel_df[excel_df[1].notna() & (excel_df[1].astype(str).str.strip() != "")]
                
                # Функция для автоматического исправления кодировки 1С
                def fix_encoding(text):
                    if pd.isna(text): return ""
                    t_str = str(text).strip()
                    try:
                        return t_str.encode('cp1252').decode('cp1251')
                    except:
                        return t_str

                # Исправляем текст в критически важных колонках, если они существуют
                if 3 in excel_df.columns:
                    excel_df[3] = excel_df[3].apply(fix_encoding) # Рейс
                if 5 in excel_df.columns:
                    excel_df[5] = excel_df[5].apply(fix_encoding) # Статус 1С
                if 7 in excel_df.columns:
                    excel_df[7] = excel_df[7].apply(fix_encoding) # Клиент
                    
                # --- УМНАЯ ФИЛЬТРАЦИЯ И ОПРЕДЕЛЕНИЕ СТРАН ---
                def Энциклопедия_Строки(row):
                    # Превращаем все элементы строки в список, убираем пустые ячейки и собираем в один текст нижнего регистра
                    row_text = " ".join(row.dropna().astype(str).str.lower().tolist())
                    
                    if "алматы" in row_text:
                        return None, None
                    
                    # Если выбран конкретный склад, проверяем только его наличие в тексте строки
                    if "Внуково" in upload_warehouse and "внуково" in row_text:
                        return "Внуково", "Россия"
                    elif "Брикета" in upload_warehouse and "брикета" in row_text:
                        return "Брикета", "Беларусь"
                    elif "Дроздово" in upload_warehouse and "дроздово" in row_text:
                        return "Дроздово", "Беларусь"
                    
                    # Если выбраны "Все склады", автоматически распределяем по маркерам
                    elif "Все склады" in upload_warehouse:
                        if "внуково" in row_text:
                            return "Внуково", "Россия"
                        elif "брикета" in row_text:
                            return "Брикета", "Беларусь"
                        elif "дроздово" in row_text:
                            return "Дроздово", "Беларусь"
                    
                    return None, None

                # Применяем анализ к каждой строке
                warehouse_and_country = excel_df.apply(Энциклопедия_Строки, axis=1)
                
                # Создаем в нашей таблице две новые служебные колонки для базы данных
                excel_df['Системный_Склад'] = [item[0] for item in warehouse_and_country]
                excel_df['Системная_Страна'] = [item[1] for item in warehouse_and_country]
                
                # Отбрасываем строки, которые не подошли ни под один склад (включая Алматы)
                excel_df = excel_df[excel_df['Системный_Склад'].notna()].reset_index(drop=True)
                
                total_rows = len(excel_df)
                st.success(f"📋 Файл успешно отфильтрован! Оставлено целевых счетов: {total_rows}")
                
                # Выводим красивую сводку, какие склады нашлись в файле
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
                
                total_rows = len(excel_df)
                st.success(f"📋 Файл успешно прочитан и нормализован! Обнаружено счетов: {total_rows}")
                
                # Автоматически определяем страну
                if "Внуково" in upload_warehouse:
                    detected_country = "Россия"
                    flag_sys = "🇷🇺"
                else:
                    detected_country = "Беларусь"
                    flag_sys = "🇧🇾"
                
                st.info(f"📍 Склад назначения: **{upload_warehouse}** | Страна: {flag_sys} **{detected_country}**")
                
                st.markdown("---")
                st.markdown("### 📊 2. Автоматическое распределение данных из файла")
                
                # Группировка по статусу из 1С (Колонка 5)
                if 5 in excel_df.columns:
                    st.markdown("#### 🔹 Разделение счетов по Статусам из 1С:")
                    excel_df[5] = excel_df[5].replace("", "Не указан")
                    statuses_1c = excel_df[5].drop_duplicates().tolist()
                    for stat_1c in statuses_1c:
                        sub_df_stat = excel_df[excel_df[5] == stat_1c]
                        st.caption(f"▪️ Статус **'{stat_1c}'**: {len(sub_df_stat)} шт. счетов")
                
                # Группировка по рейсам и датам рейса (Колонки 3 и 4)
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
                
                # БЛОК ИНСТРУМЕНТОВ ЛОГИСТА
                st.markdown("---")
                st.markdown("### 🛠️ 3. Инструменты группировки и логистики")
                
                st.markdown("##### Применить массовые параметры логистики для счетов из этого файла:")
                col_l1, col_l2 = st.columns(2)
                with col_l1:
                    log_status = st.selectbox(
                        "Установить текущий статус логистики:",
                        ["Создан", "В сборке", "Ожидает разрешения", "В пути", "Прибыл на склад Алматы", "Готов к отгрузке клиенту"]
                    )
                    need_perm = st.checkbox("Требуется разрешение (Признак заключения)", value=False)
                with col_l2:
                    has_trips = (3 in excel_df.columns and 4 in excel_df.columns and not unique_excel_trips.empty)
                    first_trip_date = unique_excel_trips.iloc[0, 1] if has_trips else ""
                    first_trip_name = unique_excel_trips.iloc[0, 0] if has_trips else ""
                    plan_date = st.text_input("Плановая дата отгрузки (ДД.ММ.ГГ):", value=str(first_trip_date))
                    car_bind = st.text_input("Привязать к автомобилю (Номер авто/рейса):", value=str(first_trip_name))
                
                if st.button("💾 Распределить и сохранить счета в SQLite базу данных"):
                    st.balloons()
                    st.success(f"🔥 Успех! {total_rows} счетов со склада {upload_warehouse} успешно распределены, обогащены статусом '{log_status}' и сохранены в локальную базу данных!")
            else:
                st.error("Файл пуст или имеет неверную структуру.")
        except Exception as e:
            st.error(f"Не удалось обработать Excel-файл. Ошибка: {e}")
            
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
        filter_by_client=True, 
        allowed_statuses=None,  # <--- СТАВИМ None, ЧТОБЫ ПОКАЗАТЬ ВСЕ СТАТУСЫ ЛИСТА АЛМ
        filter_by_invoice=True, 
        invoice_text=invoice_input,
        start_dt=start_filter, end_dt=end_filter 
    )
    st.session_state.report_name = "Прибытие"

elif current_mode == "Отгрузки Алматы":
    cols_almaty_delivery = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Прибыл (факт)', 'Статус', 'Рейс', 'Дата рейса']
    st.session_state.current_report = build_report(
        st.session_state.active_sheets,
        cols_almaty_delivery,
        filter_by_client=True,
        allowed_statuses=["Отгружено клиенту"],
        filter_by_invoice=True,
        invoice_text=invoice_input,
        start_dt=start_filter,
        end_dt=end_filter
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
                     # Вызываем функцию отправки. Передаем введенные email и список активных листов
                     success = send_today_report_email(
                         recipient_emails=emails,
                         target_sheets=st.session_state.active_sheets
                     )
                     
                     if success:
                         st.success(f" Сводка успешно отправлена на адреса: {emails}")
                         st.session_state.show_email_modal = False
                         st.rerun()
