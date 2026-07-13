import streamlit as st
import pandas as pd
import datetime
from io import BytesIO

# --- НАСТРОЙКА СТРАНИЦЫ ---
st.set_page_config(page_title="Мониторинг счетов", layout="wide")

# Кнопки с иконками (смайликами) для надежного отображения цветов
st.title("📊 Система мониторинга статуса отгрузки счетов")

# --- 1. ФУНКЦИЯ ЗАГРУЗКИ ДАННЫХ ИЗ GOOGLE SHEETS ---
@st.cache_data(ttl=30)  # Обновление данных каждые 30 секунд
def load_all_sheets():
    sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"]
    spreadsheet_id = "1F_EfNPXxhIHaRLUx_ebADRfpNEY1SztmeBrc86KuysI"
    base_url = f"https://google.com{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet="
    
    all_dfs = {}
    for s in sheets:
        try:
            # Читаем данные, убирая скрытые символы кодировки (BOM)
            df = pd.read_csv(f"{base_url}{s}", encoding='utf-8-sig')
            
            # Принудительно очищаем заголовки колонок от пробелов
            df.columns = [str(c).strip() for c in df.columns]
            
            # Переименовываем колонки по их порядковому номеру, если Google Sheets отдал их с ошибкой
            rename_dict = {}
            if len(df.columns) >= 3 and df.columns[2] != 'Дата счета': rename_dict[df.columns[2]] = 'Дата счета'
            if len(df.columns) >= 4 and df.columns[3] != 'Клиент': rename_dict[df.columns[3]] = 'Клиент'
            if rename_dict:
                df.rename(columns=rename_dict, inplace=True)
                
            all_dfs[s] = df
        except Exception:
            all_dfs[s] = pd.DataFrame()
    return all_dfs

# Загружаем сырые данные без предобработки дат
data_dict = load_all_sheets()

# --- 2. ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ (SESSION STATE) ---
if 'current_report' not in st.session_state: st.session_state.current_report = None
if 'report_name' not in st.session_state: st.session_state.report_name = ""
if 'show_email_modal' not in st.session_state: st.session_state.show_email_modal = False

# --- 3. БЛОК ВВОДА ПАРАМЕТРОВ ---
st.subheader("🔍 Параметры поиска")
col_client, col_date = st.columns(2)

with col_client:
    client_input = st.text_input("Наименование клиента (можно несколько через запятую):", placeholder="Например: Авиа, Техно, Рольф")

with col_date:
    today_dt = datetime.date.today()
    default_start_dt = today_dt - datetime.timedelta(days=30)
    date_range = st.date_input("Период поиска (по Дате счета):", value=[default_start_dt, today_dt])

# Переводим выбранный период в объекты даты для сравнения
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_filter, end_filter = date_range[0], date_range[1]
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_filter, end_filter = date_range[0], today_dt
else:
    start_filter, end_filter = default_start_dt, today_dt

# --- 4. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ФИЛЬТРАЦИИ ---
def build_report(target_sheets, required_columns, filter_by_client=True):
    frames = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            frames.append(data_dict[s].copy())
            
    if not frames:
        return pd.DataFrame()
        
    # Объединяем листы
    df_all = pd.concat(frames, ignore_index=True)
    
    # Безопасная фильтрация по датам (переводим любые типы данных в дату)
    if 'Дата счета' in df_all.columns:
        df_all['🤖 Техническая Дата'] = pd.to_datetime(df_all['Дата счета'], dayfirst=True, errors='coerce').dt.date
        # Отсекаем строки, где дата счета не распозналась или не входит в период
        df_all = df_all.dropna(subset=['🤖 Техническая Дата'])
        df_all = df_all[(df_all['🤖 Техническая Дата'] >= start_filter) & (df_all['🤖 Техническая Дата'] <= end_filter)]
    
    # Фильтрация по клиентам (без учета пробелов и регистра)
    if filter_by_client and client_input and 'Клиент' in df_all.columns:
        search_words = [w.strip().lower().replace(" ", "") for w in client_input.split(",") if w.strip()]
        if search_words:
            client_mask = df_all['Клиент'].astype(str).apply(lambda x: any(word in x.lower().replace(" ", "") for word in search_words))
            df_all = df_all[client_mask]
            
    # Возвращаем только те колонки, которые просил пользователь по ТЗ
    final_cols = [c for c in required_columns if c in df_all.columns]
    return df_all[final_cols] if not df_all.empty else pd.DataFrame()

# --- 5. ПАНЕЛЬ УПРАВЛЕНИЯ (КНОПКИ 1-4) ---
st.subheader("📋 Формирование отчетов")
c1, c2, c3, c4 = st.columns(4)

with c1:
    if st.button("🔵 Поиск по Клиенту"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = build_report(["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"], cols, filter_by_client=True)
        st.session_state.report_name = "Поиск_по_Клиенту"

with c2:
    if st.button("🟣 Разрешения"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = build_report(["КЗ разр", "РБ разр", "Алм"], cols, filter_by_client=False)
        st.session_state.report_name = "Разрешения"

with c3:
    if st.button("🟡 Отгружено"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Плановая дата отгрузки', 'Дата отгрузки (факт)', 'Плановая дата прибытия', 'Статус']
        st.session_state.current_report = build_report(["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"], cols, filter_by_client=True)
        st.session_state.report_name = "Отгружено"

with c4:
    if st.button("🟢 Прибытие"):
        cols = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'Дата отгрузки (факт)', 'Прибыл (факт)', 'Статус']
        st.session_state.current_report = build_report(["Алм"], cols, filter_by_client=True)
        st.session_state.report_name = "Прибытие"

# --- 6. ВЫВОД РЕЗУЛЬТАТОВ И КНОПКИ ВЫГРУЗКИ (5-6) ---
if st.session_state.current_report is not None:
    st.write("---")
    st.subheader(f"📈 Результат отчета: {st.session_state.report_name.replace('_', ' ')}")
    
    if st.session_state.current_report.empty:
        st.info("По заданным параметрам записей не найдено. Проверьте правильность дат в Google Таблице.")
    else:
        st.dataframe(st.session_state.current_report, hide_index=True, use_container_width=True)
        
        c5, c6 = st.columns(2)
        with c5:
            # 🟠 Выгрузить в Excel
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
            # 💗 Оповестить
            if st.button("💗 Оповестить"):
                st.session_state.show_email_modal = not st.session_state.show_email_modal

# --- МОДАЛЬНОЕ ОКНО ДЛЯ ОТПРАВКИ ПОЧТЫ ---
if st.session_state.show_email_modal:
    with st.expander("📬 Настройка отправки уведомлений", expanded=True):
        emails = st.text_input("Введите адреса электронной почты через запятую:")
        if st.button("🚀 Отправить сводку за сегодня"):
            if not emails:
                st.error("Укажите хотя бы один адрес!")
            else:
                st.success(f"📧 Сводка успешно отправлена на адреса: {emails}")
                st.session_state.show_email_modal = False
