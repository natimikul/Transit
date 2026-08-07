import streamlit as st
import pandas as pd

def show_replenishment_page():
    st.subheader("🚚 Мониторинг автомобилей в пути (Лист Пополн)")
    
    # 1. Ссылка на веб-публикацию нового листа "Пополн"
    # СЮДА НУЖНО ПОДСТАВИТЬ РЕАЛЬНУЮ ССЫЛКУ ИЗ GOOGLE SHEETS ДЛЯ ЭТОЙ СТРАНИЦЫ
    POPOLN_URL = "https://google.com"
    
    try:
        df = pd.read_csv(POPOLN_URL, encoding='utf-8-sig', header=None)
        df = df.dropna(how='all').reset_index(drop=True)
        
        # Столбцы по скриншоту (Колонки A, B, C, D, E)
        df.columns = ['Дата отгрузки', 'Локация', '№ документа', 'Страна', 'Плановая дата прибытия'] + list(range(len(df.columns) - 5))
        
        if not df.empty and ('дата' in str(df.iloc[0]).lower() or 'локация' in str(df.iloc[0]).lower()):
            df = df.iloc[1:].reset_index(drop=True)
            
    except Exception as e:
        st.error(f"Не удалось загрузить данные с листа 'Пополн'. Ошибка: {e}")
        return

    if df.empty:
        st.info("Нет данных по автомобилям на листе 'Пополн'.")
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
        if 'беларусь' in country_str or 'рб' in country_str or 'by' in country_str:
            flag_emoji = "🇧🇾"
        elif 'россия' in country_str or 'рф' in country_str or 'ru' in country_str:
            flag_emoji = "🇷🇺"
        else:
            flag_emoji = "🏳️"
            
        location_info = f" ({car['Локация']})" if car['Локация'] != "Не указана" else ""
        header_title = (
            f"🚛 {flag_emoji}  Отгрузка: {car['Дата отгрузки']} | "
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
            for doc in car_documents:
                st.markdown(f"- `{doc}`")

