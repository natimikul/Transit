import streamlit as st
import pandas as pd
import datetime
from io import BytesIO

# --- НАСТРОЙКА СТРАНИЦЫ ---
st.set_page_config(page_title="Мониторинг счетов", layout="wide")

# --- СТИЛИЗАЦИЯ ЦВЕТНЫХ КНОПОК ---
st.markdown("""
<style>
    div.stButton > button { font-weight: bold; width: 100%; border: none; color: white !important; }
    /* 1. Поиск по Клиенту (Голубой) */
    div.stButton > button[key="btn_client"] { background-color: #00BFFF; }
    /* 2. Разрешения (Фиолетовый) */
    div.stButton > button[key="btn_perm"] { background-color: #8A2BE2; }
    /* 3. Отгружено (Желтый) */
    div.stButton > button[key="btn_ship"] { background-color: #FFD700; color: black !important; }
    /* 4. Прибытие (Зеленый) */
    div.stButton > button[key="btn_arr"] { background-color: #32CD32; }
    /* 5. Выгрузить в Excel (Оранжевый) */
    div.stButton > button[key="btn_excel"] { background-color: #FF8C00; }
    /* 6. Оповестить (Розовый) */
    div.stButton > button[key="btn_notify"] { background-color: #FF69B4; }
</style>
""", unsafe_allow_html=True)

st.title("📊 Система мониторинга статуса отгрузки счетов")

# --- 1. ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS ---
@st.cache_data(ttl=60)
def load_all_sheets():
    sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"]
    spreadsheets_id = "1F_EfNPXxhIHaRLUx_ebADRfpNEY1SztmeBrc86KuysI"
    base_url = f"https://docs.google.com/spreadsheets/d/1F_EfNPXxhIHaRLUx_ebADRfpNEY1SztmeBrc86KuysI/gviz/tq?tqx=out:csv&sheet="
                            
    all_dfs = {}
    for s in sheets:
        try:
            # encoding='utf-8-sig' убирает невидимый мусор из заголовков Google таблиц
            df = pd.read_csv(f"{base_url}{s}", encoding='utf-8-sig')
            df.columns = df.columns.str.strip()
            
            if 'Дата счета' in df.columns:
                df['Дата счета'] = pd.to_datetime(df['Дата счета'], dayfirst=True, errors='coerce').dt.date
            all_dfs[s] = df
        except Exception:
            all_dfs[s] = pd.DataFrame()
    return all_dfs

# --- 2. ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ (SESSION STATE) ---
if 'current_report' not in st.session_state:
    st.session_state.current_report = None
if 'report_name' not in st.session_state:
    st.session_state.report_name = ""

# --- 3. БЛОК ВВОДА ПАРАМЕТРОВ ---
st.subheader("🔍 Параметры поиска")
col_client, col_date = st.columns(2)

with col_client:
    client_input = st.text_input("Наименование клиента (можно несколько через запятую):", placeholder="Например: Авиа, Техно, Рольф")

with col_date:
    today = datetime.date.today()
    default_start = today - datetime.timedelta(days=30)
    date_range = st.date_input("Период поиска (по Дате счета):", value=[default_start, today], max_value=today)

# Определение выбранных дат
if isinstance(date_range, list) or isinstance(date_range, tuple):
    start_date = date_range[0] if len(date_range) > 0 else default_start
    end_date = date_range[1] if len(date_range) > 1 else today
else:
    start_date = date_range
    end_date = today

# Вспомогательная функция фильтрации по клиентам и датам
def filter_base_data(dfs_list, search_clients=True):
    # Если база данных не создана или пуста, принудительно скачиваем её
    try:
        active_dict = data_dict
    except NameError:
        active_dict = load_all_sheets()
        
    valid_dfs = []
    for name in dfs_list:
        if name in active_dict and not active_dict[name].empty:
            valid_dfs.append(active_dict[name])
    if not valid_dfs:
        return pd.DataFrame()
    combined_df = pd.concat(valid_dfs, ignore_index=True)

    
    # Фильтр по периоду (последние 30 дней по умолчанию или выбранный интервал)
    combined_df = combined_df[(combined_df['Дата счета'] >= start_date) & (combined_df['Дата счета'] <= end_date)]
    
    # Сортировка по клиентам (частичное совпадение + поиск через запятую)
    if search_clients and client_input:
        clients_list = [c.strip().lower().replace(" ", "") for c in client_input.split(",") if c.strip()]
        if clients_list:
            mask = combined_df['Клиент'].astype(str).apply(lambda x: any(sub in x.lower().replace(" ", "") for sub in clients_list))
            combined_df = combined_df[mask]

            
    return combined_df

# --- 4. ПАНЕЛЬ УПРАВЛЕНИЯ (КНОПКИ 1-4) ---
st.subheader("📋 Формирование отчетов")
c1, c2, c3, c4 = st.columns(4)

with c1:
    if st.button("🔍 Поиск по Клиенту", key="btn_client"):
        res = filter_base_data(["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"], search_clients=True)
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = res[[c for c in cols if c in res.columns]] if not res.empty else pd.DataFrame()
        st.session_state.report_name = "Поиск_по_Клиенту"

with c2:
    if st.button("📜 Разрешения", key="btn_perm"):
        # Поиск по дате (клиент не учитывается по ТЗ, но период работает)
        res = filter_base_data(["КЗ разр", "РБ разр", "Алм"], search_clients=False)
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = res[[c for c in cols if c in res.columns]] if not res.empty else pd.DataFrame()
        st.session_state.report_name = "Разрешения"

with c3:
    if st.button("🚚 Отгружено", key="btn_ship"):
        res = filter_base_data(["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"], search_clients=True)
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = res[[c for c in cols if c in res.columns]] if not res.empty else pd.DataFrame()
        st.session_state.report_name = "Отгружено"

with c4:
    if st.button("🏁 Прибытие", key="btn_arr"):
        res = filter_base_data(["Алм"], search_clients=True)
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Дата отгрузки (факт)', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = res[[c for c in cols if c in res.columns]] if not res.empty else pd.DataFrame()
        st.session_state.report_name = "Прибытие"


# --- 5. ВЫВОД РЕЗУЛЬТАТОВ И СЛУЖЕБНЫЕ КНОПКИ (5-6) ---
if st.session_state.current_report is not None:
    st.write("---")
    st.subheader(f"📈 Результат отчета: {st.session_state.report_name.replace('_', ' ')}")
    
    if st.session_state.current_report.empty:
        st.info("По заданным параметрам записей не найдено.")
    else:
        st.dataframe(st.session_state.current_report, hide_index=True, use_container_width=True)
        
        # Панель выгрузки и оповещения
        c5, c6 = st.columns(2)
        
        with c5:
            # 5. Выгрузить в Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                st.session_state.current_report.to_excel(writer, index=False, sheet_name='Отчет')
            processed_data = output.getvalue()
            
            st.download_button(
                label="📥 Выгрузить в Excel",
                data=processed_data,
                file_name=f"{st.session_state.report_name}_{today}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="btn_excel"
            )
            
        with c6:
            # 6. Оповестить
            if st.button("✉️ Оповестить", key="btn_notify"):
                st.session_state.show_email_modal = True

# --- ОКНО ОПОВЕЩЕНИЯ (МОДАЛЬНЫЙ БЛОК) ---
if st.session_state.get('show_email_modal', False):
    with st.expander("📬 Настройка отправки уведомлений", expanded=True):
        emails = st.text_input("Введите адреса электронной почты через запятую:")
        if st.button("🚀 Отправить сводку за сегодня"):
            # Сбор счетов с текущей датой (сегодня) по всем листам
            all_today_data = filter_base_data(["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"], search_clients=False)
            today_records = all_today_data[all_today_data['Дата счета'] == today]
            
            if today_records.empty:
                st.warning("Сегодня счетов с текущей датой нет. Нечего отправлять.")
            elif not emails:
                st.error("Пожалуйста, укажите хотя бы один адрес почты.")
            else:
                # Здесь настраивается SMTP-клиент или вызов API (например, SendGrid / Mailgun)
                # Ниже представлена имитация успешной отправки
                st.success(f"📧 Сводка по {len(today_records)} счетам успешно отправлена на адреса: {emails}")
                st.session_state.show_email_modal = False
