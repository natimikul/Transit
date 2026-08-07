import streamlit as st
import pandas as pd

def show_replenishment_page():
    st.subheader("🟪 Мониторинг автомобилей в пути (Лист Пополн)")
    
    # 1. Ссылка на веб-публикацию нового листа "Пополн"
    POPOLN_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=60140824&single=true&output=csv"
    
    try:
        df = pd.read_csv(POPOLN_URL, encoding='utf-8-sig', header=None, on_bad_lines='skip')
        df = df.dropna(how='all').reset_index(drop=True)
        
        df = df.iloc[:, :5]
        df.columns = ['Дата отгрузки', 'Локация', '№ документа', 'Страна', 'Плановая дата прибытия']
        
        if not df.empty and ('дата' in str(df.values).lower() or 'локация' in str(df.values).lower()):
            df = df.iloc[1:].reset_index(drop=True)
            
    except Exception as e:
        st.error(f"Ошибка загрузки данных: {e}")
        return

    if df.empty:
        st.info("Нет данных по автомобилям.")
        return

    groupby_cols = ['Дата отгрузки', 'Локация', 'Страна', 'Плановая дата прибытия']
    
    df['Локация'] = df['Локация'].fillna("Не указана").astype(str).str.strip()
    df['Дата отгрузки'] = df['Дата отгрузки'].fillna("-").astype(str).str.strip()
    df['Страна'] = df['Страна'].fillna("Неизвестно").astype(str).str.strip()
    df['Плановая дата прибытия'] = df['Плановая дата прибытия'].fillna("-").astype(str).str.strip()
    df['№ документа'] = df['№ документа'].fillna("Без номера").astype(str).str.strip()

    unique_cars = df[groupby_cols].drop_duplicates()

    for _, car in unique_cars.iterrows():
        country_str = car['Страна'].lower()
        
        # Превращаем текстовые флаги "BY" и "RU" со скриншота в настоящие цветные эмодзи
        if 'беларусь' in country_str or 'рб' in country_str or 'by' in country_str:
            flag_emoji = "🇧🇾"
        elif 'россия' in country_str or 'рф' in country_str or 'ru' in country_str:
            flag_emoji = "🇷🇺"
        else:
            flag_emoji = "🏳️"
            
        location_info = f" ({car['Локация']})" if car['Локация'] != "Не указана" else ""
        header_title = (
            f"🟪 {flag_emoji} Отгрузка: {car['Дата отгрузки']} | "
            f"Маршрут: {car['Страна']}{location_info} | "
            f"🏁 План прибытия: {car['Плановая дата прибытия']}"
        )
        
        car_documents = df[
            (df['Дата отгрузки'] == car['Дата отгрузки']) &
            (df['Локация'] == car['Локация']) &
            (df['Страна'] == car['Страна']) &
            (df['Плановая дата прибытия'] == car['Плановая дата прибытия'])
        ]['№ документа'].tolist()
        
        with st.expander(header_title):
            st.markdown(f"**📄 Список документов в данном автомобиле ({len(car_documents)} шт.):**")
            
            # Делим документы поровну на две колонки внутри раскрывающегося блока
            doc_col1, doc_col2 = st.columns(2)
            midpoint = (len(car_documents) + 1) // 2
            
            with doc_col1:
                for doc in car_documents[:midpoint]:
                    st.markdown(f"- `{doc}`")
            with doc_col2:
                for doc in car_documents[midpoint:]:
                    st.markdown(f"- `{doc}`")
