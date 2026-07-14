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
    "Вну": "https://google.com",
    "Бри-Дро": "https://google.com",
    "КЗ разр": "https://google.com",
    "РБ разр": "https://google.com",
    "Алм": "https://google.com"
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
        
        if 'Статус' in df.columns:
            for s_val in df['Статус'].dropna().astype(str).unique():
                s_clean = s_val.strip()
                if s_clean and not any(char in s_clean for char in ['{', '}', '(', ')', ';', '=', ':']):
                    unique_statuses_from_db.add(s_clean)
                    
        data_dict[name] = df
    except Exception:
        data_dict[name] = pd.DataFrame()

list_all_statuses = sorted(list(unique_statuses_from_db))

# --- 4. ИНИЦИАЛИЗАЦИЯ ПАМЯТИ СОСТОЯНИЯ ---
if 'current_report' not in st.session_state: st.session_state.current_report = None
if 'report_name' not in st.session_state: st.session_state.report_name = ""
if 'show_email_modal' not in st.session_state: st.session_state.show_email_modal = False

# --- 5. ИНТЕРФЕЙС ПАРАМЕТРОВ ПОИСКА ---
st.subheader("🔍 Параметры поиска")
col_client, col_date = st.columns(2)

with col_client:
    client_input = st.text_input("Наименование клиента (быстрый текстовый поиск):", placeholder="Например: Авиа, Техно, Рольф")

with col_date:
    today_dt = datetime.date.today()
    default_start_dt = today_dt - datetime.timedelta(days=30)
    date_range = st.date_input("Период поиска (по Дате счета):", value=(default_start_dt, today_dt))
    selected_dropdown_statuses = st.multiselect("📊 Отфильтровать по статусу счетов:", options=list_all_statuses)

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_filter, end_filter = date_range, date_range
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_filter, end_filter = date_range, today_dt
else:
    start_filter, end_filter = default_start_dt, today_dt

total_rows = sum(len(df) for df in data_dict.values())
st.write(f"📊 Всего загружено строк со всех 5 листов таблицы: {total_rows}")

# --- 6. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ СБОРКИ И СТРОГОЙ ФИЛЬТРАЦИИ ---
def build_report(target_sheets, required_columns, filter_by_client=True, allowed_statuses=None):
    frames = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            frames.append(data_dict[s].copy())
    if not frames: return pd.DataFrame()
        
    df_all = pd.concat(frames, ignore_index=True)
    
    if 'Дата счета' in df_all.columns:
        s_date = pd.to_datetime(start_filter).date()
        e_date = pd.to_datetime(end_filter).date()
        delta_days = (e_date - s_date).days
        allowed_text_dates = [(s_date + datetime.timedelta(days=i)).strftime('%d.%m.%Y') for i in range(delta_days + 1)]
        allowed_text_dates += [(s_date + datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(delta_days + 1)]
        df_all['Дата счета'] = df_all['Дата счета'].astype(str).str.strip()
        df_all = df_all[df_all['Дата счета'].isin(allowed_text_dates)]
        
    if filter_by_client and client_input and 'Клиент' in df_all.columns:
        clean_text = lambda v: str(v).lower().replace(" ", "").replace(".", "").replace(",", "").replace('"', '').replace("'", "")
        search_words = [clean_text(w) for w in client_input.split(",") if w.strip()]
        if search_words:
            client_mask = df_all['Клиент'].apply(lambda x: any(word in clean_text(x) for word in search_words))
            df_all = df_all[client_mask]
            
    if allowed_statuses and 'Status' not in df_all.columns and 'Статус' in df_all.columns:
        df_all['🤖 Системный Статус'] = df_all['Статус'].astype(str).str.strip().str.lower()
        status_list = [str(st_item).strip().lower() for st_item in allowed_statuses]
        df_all = df_all[df_all['🤖 Системный Статус'].isin(status_list)]
        df_all.drop(columns=['🤖 Системный Статус'], inplace=True)

    if selected_dropdown_statuses and 'Статус' in df_all.columns:
        df_all = df_all[df_all['Статус'].astype(str).str.strip().isin(selected_dropdown_statuses)]
            
    final_cols = [c for c in required_columns if c in df_all.columns]
    return df_all[final_cols] if not df_all.empty else pd.DataFrame()

# --- 7. ПАНЕЛЬ С КНОПКАМИ ОТЧЕТОВ ---
st.subheader("📋 Формирование отчетов")
c1, c2, c3, c4 = st.columns(4)

with c1:
    if st.button("🔵 Поиск по Клиенту"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = build_report(["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"], cols, filter_by_client=True, allowed_statuses=None)
        st.session_state.report_name = "Поиск_по_Клиенту"

with c2:
    if st.button("📑 Разрешения"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = build_report(["КЗ разр", "РБ разр"], cols, filter_by_client=True, allowed_statuses=["На разрешении", "Получено разрешение"])
        st.session_state.report_name = "Разрешения"

with c3:
    if st.button("🚚 Отгружено"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = build_report(["Вну", "Бри-Дро", "КЗ разр", "РБ разр"], cols, filter_by_client=True, allowed_statuses=["В пути"])
        st.session_state.report_name = "Отгружено"

with c4:
    if st.button("🏢 Прибытие"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Дата отгрузки (факт)', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = build_report(["Алм"], cols, filter_by_client=True, allowed_statuses=["Прибыл на склад Алматы"])
        st.session_state.report_name = "Прибытие"

# --- 8. ВЫВОД РЕЗУЛЬТАТОВ С ПОДДЕРЖКОЙ ВЫДЕЛЕНИЯ И КОПИРОВАНИЯ ---
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
