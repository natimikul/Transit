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

# --- НАСТРОЙКА СТРАНИЦЫ И СТИЛЕЙ КНОПОК ---
st.set_page_config(page_title="Мониторинг счетов", layout="wide")
st.title("📦 Система мониторинга статуса отгрузки счетов")

st.markdown("""
<style>
    div.stButton > button p {
        font-size: 24px !important;
        font-weight: bold !important;
    }
</style>
""", unsafe_allow_html=True)

# --- 1. ЗАЩИТА ПАРОЛЕМ ---
CORRECT_PASSWORD = "Password123"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.subheader("🔒 Вход в систему")
    user_password = st.text_input("Введите пароль для доступа к отчетам:", type="password")
    if st.button("Войти 🚀"):
        if user_password == CORRECT_PASSWORD:
            st.session_state.authenticated = True
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
    "Алм": "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=289794996&single=true&output=csv"
}

# --- 3. ЗАГРУЗКА И СТАНДАРТИЗАЦИЯ ТАБЛИЦ ---
data_dict = {}
unique_statuses_from_db = set()

for name, url in sheet_urls.items():
    try:
        df = pd.read_csv(url, encoding='utf-8-sig', header=None)
        df = df.dropna(how='all').reset_index(drop=True)
        col_names = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'ПкЦБ', 'Склад', 
                     'Разрешение', 'Дата отправки на разрешение', 'Плановая дата отгрузки', 
                     'Дата отгрузки (факт)', 'Транзит (дней)', 'Плановая дата прибытия', 
                     'Прибыл (факт)', 'Статус']
        df.columns = col_names + list(range(len(df.columns) - len(col_names)))
        if not df.empty and ('заявк' in str(df.iloc).lower() or 'счет' in str(df.iloc).lower()):
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
    selected_dropdown_statuses = st.multiselect("📊 Отфильтровать по статусу счетов:", options=list_all_statuses)

# Безопасный разбор границ календаря
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_filter, end_filter = date_range, date_range
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_filter, end_filter = date_range, today_dt
else:
    start_filter, end_filter = default_start_dt, today_dt

# Считаем строки ТОЛЬКО на тех листах, которые выбраны текущим отчетом
total_rows = sum(len(data_dict[s]) for s in st.session_state.active_sheets if s in data_dict)
sheets_text = ", ".join(st.session_state.active_sheets)
st.write(f"📊 Обработано строк: {total_rows} (листы: {sheets_text})")

# --- 6. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ СБОРКИ И СТРОГОЙ ФИЛЬТРАЦИИ ---
def build_report(target_sheets, required_columns, filter_by_client=True, allowed_statuses=None, filter_by_invoice=True, invoice_text=""):
    frames = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            frames.append(data_dict[s].copy())
    if not frames: return pd.DataFrame()
        
    df_all = pd.concat(frames, ignore_index=True)
    
    # Фильтр по Дате счета
    if 'Дата счета' in df_all.columns and not invoice_text:
        try:
            s_date = pd.to_datetime(start_filter).date()
            e_date = pd.to_datetime(end_filter).date()
            delta_days = (e_date - s_date).days
            allowed_text_dates = [(s_date + datetime.timedelta(days=i)).strftime('%d.%m.%Y') for i in range(delta_days + 1)]
            allowed_text_dates += [(s_date + datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(delta_days + 1)]
            df_all['Дата счета'] = df_all['Дата счета'].astype(str).str.strip()
            df_all = df_all[df_all['Дата счета'].isin(allowed_text_dates)]
        except Exception:
            pass

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

    # Фильтр по Выпадающему списку статусов
    if selected_dropdown_statuses and 'Статус' in df_all.columns:
        df_all = df_all[df_all['Статус'].astype(str).str.strip().isin(selected_dropdown_statuses)]
            
    final_cols = [c for c in required_columns if c in df_all.columns]
    return df_all[final_cols] if not df_all.empty else pd.DataFrame()

# --- ФУНКЦИЯ ДЛЯ ФИЛЬТРАЦИИ И ОТПРАВКИ СВОДКИ НА EMAIL ---
def send_today_report_email(recipient_emails, target_sheets):
    """
    Собирает данные за сегодня со всех активных листов, 
    формирует Excel и отправляет на указанные email-адреса.
    """
    today_str_1 = datetime.date.today().strftime('%d.%m.%Y') # Формат ДД.ММ.ГГГГ
    today_str_2 = datetime.date.today().strftime('%Y-%m-%d') # Формат ГГГГ-ММ-ДД
    
    frames_today = []
    
    # 1. Собираем строки с сегодняшней датой со всех выбранных листов
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            df_sheet = data_dict[s].copy()
            
            # Ищем сегодняшнюю дату в текстовом виде по всем ячейкам таблицы
            mask = df_sheet.astype(str).apply(
                lambda row: row.str.contains(today_str_1, na=False) | row.str.contains(today_str_2, na=False), 
                axis=1
            ).any(axis=1)
            
            df_filtered = df_sheet[mask]
            if not df_filtered.empty:
                # Добавляем колонку с источником, чтобы понимать откуда счет
                df_filtered.insert(0, 'Источник (Лист)', s)
                frames_today.append(df_filtered)
                
    if not frames_today:
        st.warning("За сегодняшнее число строк в таблицах не найдено. Письмо не отправлено.")
        return False

    # Склеиваем все найденные за сегодня строки
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
        sender_email = "natimikul@gmail.com"       # ваш технический ящик отправки
        sender_password = "cekg mswv wfbd efmk" # специальный пароль приложения (не от личного кабинета!)
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

# --- 7. ПАНЕЛЬ С КНОПКАМИ ОТЧЕТОВ ---
st.subheader("📋 Формирование отчетов")
c1, c2, c3, c4 = st.columns(4)

with c1:
    if st.button("🔵 Поиск по Клиенту"):
        st.session_state.active_sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"]
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = build_report(st.session_state.active_sheets, cols, filter_by_client=True, allowed_statuses=None, invoice_text=invoice_input)
        st.session_state.report_name = "Поиск_по_Клиенту"
        st.rerun()

with c2:
    if st.button("📑 Разрешения"):
        # Исправлено: строго 2 листа
        st.session_state.active_sheets = ["КЗ разр", "РБ разр"]
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = build_report(st.session_state.active_sheets, cols, filter_by_client=True, allowed_statuses=["Создан", "В сборке, ожидает разрешения"], invoice_text=invoice_input)
        st.session_state.report_name = "Разрешения"
        st.rerun()

with c3:
    if st.button("🚚 Отгружено"):
        # Исправлено: строго 4 листа (без Алм)
        st.session_state.active_sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр"]
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = build_report(st.session_state.active_sheets, cols, filter_by_client=True, allowed_statuses=["Создан", "В сборке", "В пути", "Задержка поставки"], invoice_text=invoice_input)
        st.session_state.report_name = "Отгружено"
        st.rerun()

with c4:
    if st.button("🏢 Прибытие"):
        # Исправлено: строго 1 лист (Алм)
        st.session_state.active_sheets = ["Алм"]
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Дата отгрузки (факт)', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = build_report(st.session_state.active_sheets, cols, filter_by_client=True, allowed_statuses=["Прибыл на склад Алматы", "Готов к отгрузке клиенту"], invoice_text=invoice_input)
        st.session_state.report_name = "Прибытие"
        st.rerun()

# --- 8. ВЫВОД РЕЗУЛЬТАТОВ С ПОДДЕРЖКОЙ ВЫДЕЛЕНИЯ И КОПИРОВАНИЯ ---
# Если отчет еще не сформирован кнопками, собираем его автоматически по фильтрам из полей ввода
if st.session_state.current_report is None:
    # Определяем колонки по умолчанию (как для поиска по клиенту)
    default_cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Наименование товара', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыль (факт)', 'Статус']
    st.session_state.current_report = build_report(
        target_sheets=st.session_state.active_sheets,
        required_columns=default_cols,
        filter_by_client=True,
        allowed_statuses=None,
        filter_by_invoice=True,
        invoice_text=invoice_input
    )
    st.session_state.report_name = "Быстрый_поиск"

if st.session_state.current_report is not None:
    st.write("---")
    st.subheader(f"📈 Результат отчета: {st.session_state.report_name.replace('_', ' ')}")
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
