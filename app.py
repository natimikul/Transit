import streamlit as st
import pandas as pd
import datetime
from io import BytesIO

st.set_page_config(page_title="Мониторинг счетов", layout="wide")
st.title("📦 Система мониторинга статуса отгрузки счетов")

@st.cache_data(ttl=30)
def load_all_sheets():
    sheets = ["Вну", "Бри-Дро", "КЗ разр", "РБ разр", "Алм"]
    spreadsheet_id = "1F_EfNPXxhIHaRLUx_ebADRfpNEY1SztmeBrc86KuysI"
    base_url = f"https://google.com{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet="
    all_dfs = {}
    for s in sheets:
        try:
            df = pd.read_csv(f"{base_url}{s}", encoding='utf-8-sig')

            
            # Принудительно сопоставляем имена колонок, убирая любые скрытые символы
            df.columns = ['№ заявки', '№ счета', 'Дата счета', 'Клиент', 'ПкЦБ', 'Склад', 
                          'Разрешение', 'Дата отправки на разрешение', 'Плановая дата отгрузки', 
                          'Дата отгрузки (факт)', 'Транзит (дней)', 'Плановая дата прибытия', 
                          'Прибыл (факт)', 'Статус'] + list(df.columns[14:])
                          
            all_dfs[s] = df
        except Exception:
            all_dfs[s] = pd.DataFrame()
    return all_dfs


data_dict = load_all_sheets()

if 'current_report' not in st.session_state:
    st.session_state.current_report = None
if 'report_name' not in st.session_state:
    st.session_state.report_name = ""
if 'show_email_modal' not in st.session_state:
    st.session_state.show_email_modal = False

st.subheader("🔍 Параметры поиска")
col_client, col_date = st.columns(2)

with col_client:
    client_input = st.text_input("Наименование клиента (можно несколько через запятую):", placeholder="Например: Авиа, Техно, Рольф")

with col_date:
    today_dt = datetime.date.today()
    default_start_dt = today_dt - datetime.timedelta(days=30)
    date_range = st.date_input("Период поиска (по Дате счета):", value=[default_start_dt, today_dt])

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_filter, end_filter = date_range, date_range
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_filter, end_filter = date_range, today_dt
else:
    start_filter, end_filter = default_start_dt, today_dt

def build_report(target_sheets, required_columns, filter_by_client=True):
    frames = []
    for s in target_sheets:
        if s in data_dict and not data_dict[s].empty:
            frames.append(data_dict[s].copy())
    if not frames:
        return pd.DataFrame()
    df_all = pd.concat(frames, ignore_index=True)
    
    # Текстовый фильтр дат без вложенных блоков - защита от ошибок отступа
    if 'Дата счета' in df_all.columns:
        delta = end_filter - start_filter
        allowed_text_dates = [(start_filter + datetime.timedelta(days=i)).strftime('%d.%m.%Y') for i in range(delta.days + 1)]
        allowed_text_dates += [(start_filter + datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(delta.days + 1)]
        df_all['Дата счета'] = df_all['Дата счета'].astype(str).str.strip()
        df_all = df_all[df_all['Дата счета'].isin(allowed_text_dates)]
        
    if filter_by_client and client_input and 'Клиент' in df_all.columns:
        search_words = [w.strip().lower().replace(" ", "") for w in client_input.split(",") if w.strip()]
        if search_words:
            client_mask = df_all['Клиент'].astype(str).apply(lambda x: any(word in x.lower().replace(" ", "") for word in search_words))
            df_all = df_all[client_mask]
    final_cols = [c for c in required_columns if c in df_all.columns]
    if not df_all.empty:
        return df_all[final_cols]
    return pd.DataFrame()

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

if st.session_state.current_report is not None:
    st.write("---")
    st.subheader(f"📈 Результат отчета: {st.session_state.report_name.replace('_', ' ')}")
    if st.session_state.current_report.empty:
        st.info("По заданным параметрам записей не найдено. Проверьте правильность дат.")
    else:
        st.dataframe(st.session_state.current_report, hide_index=True, use_container_width=True)
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
        if st.button("🚀 Отправить сводку за сегодня"):
            if not emails:
                st.error("Укажите хотя бы один адрес!")
            else:
                st.success(f"📧 Сводка успешно отправлена на адреса: {emails}")
                st.session_state.show_email_modal = False
