import streamlit as st
import pandas as pd

def show_replenishment_page():
    st.subheader("🚀 Авто в пути")
    
    # Ваша ссылка на веб-публикацию листа "Пополн"
    POPOLN_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=60140824&single=true&output=csv"
    
    try:
        df = pd.read_csv(POPOLN_URL, encoding='utf-8-sig', header=None, on_bad_lines='skip')
        df = df.dropna(how='all').reset_index(drop=True)
        
        df = df.iloc[:, :6]
        df.columns = ['Дата отгрузки', 'Страна', 'Локация', '№ документа', '№ РКЗ', 'Плановая дата прибытия']
        
        if not df.empty and ('дата' in str(df.values).lower() or 'страна' in str(df.values).lower()):
            df = df.iloc[1:].reset_index(drop=True)
            
    except Exception as e:
        st.error(f"Ошибка загрузки данных: {e}")
        return

    if df.empty:
        st.info("Нет данных по автомобилям.")
        return

    df['Дата отгрузки'] = df['Дата отгрузки'].fillna("-").astype(str).str.strip()
    df['Страна'] = df['Страна'].fillna("Неизвестно").astype(str).str.strip()
    df['Локация'] = df['Локация'].fillna("Не указана").astype(str).str.strip()
    df['Плановая дата прибытия'] = df['Плановая дата прибытия'].fillna("-").astype(str).str.strip()
    df['№ документа'] = df['№ документа'].fillna("").astype(str).str.strip()
    df['№ РКЗ'] = df['№ РКЗ'].fillna("").astype(str).str.strip()

    search_query = st.text_input("🔍 Поиск авто по номеру документа:", "").strip()

    groupby_cols = ['Дата отгрузки', 'Страна', 'Локация', 'Плановая дата прибытия']
    unique_cars = df[groupby_cols].drop_duplicates()

    cars_found = 0

    for _, car in unique_cars.iterrows():
        car_rows = df[
            (df['Дата отгрузки'] == car['Дата отгрузки']) &
            (df['Страна'] == car['Страна']) &
            (df['Локация'] == car['Локация']) &
            (df['Плановая дата прибытия'] == car['Плановая дата прибытия'])
        ]
        
        docs_list = []
        for d in car_rows['№ документа'].tolist():
            if d:
                for sub_d in d.split('\n'):
                    if sub_d.strip():
                        docs_list.append(sub_d.strip())
                        
        rkz_list = []
        for r in car_rows['№ РКЗ'].tolist():
            if r:
                for sub_r in r.split('\n'):
                    if sub_r.strip():
                        rkz_list.append(sub_r.strip())

        if search_query:
            match_found = any(search_query.lower() in doc.lower() for doc in docs_list)
            if not match_found:
                continue

        cars_found += 1

        country_str = car['Страна'].lower()
        if 'беларусь' in country_str or 'рб' in country_str or 'by' in country_str:
            flag_emoji = "🇧🇾"
        elif 'россия' in country_str or 'рф' in country_str or 'ru' in country_str:
            flag_emoji = "🇷🇺"
        else:
            flag_emoji = "🏳️"
            
        header_title = (
            f"📅 Отгрузка: {car['Дата отгрузки']} | "
            f"{flag_emoji} {car['Страна']} | "
            f"📍 Локация: {car['Локация']} | "
            f"🏁 План прибытия: {car['Плановая дата прибытия']}"
        )
        
        is_expanded = True if search_query else False

        with st.expander(header_title, expanded=is_expanded):
            doc_col1, doc_col2 = st.columns(2)
            
            with doc_col1:
                if docs_list:
                    # Оставляем ТОЛЬКО выпадающий список. Внутри него — номера в столбик, готовые к копированию
                    with st.expander(f"📋 № документа ({len(docs_list)} шт.)"):
                        st.code("\n".join(docs_list), language="")
                else:
                    st.caption("📄 *№ документа: Нет данных*")
                    
            with doc_col2:
                if rkz_list:
                    # Оставляем ТОЛЬКО выпадающий список для РКЗ
                    with st.expander(f"📑 № РКЗ ({len(rkz_list)} шт.)"):
                        st.code("\n".join(rkz_list), language="")
                else:
                    st.caption("📑 *№ РКЗ: Нет данных*")

    if search_query and cars_found == 0:
        st.warning(f"Машины с документом '{search_query}' не найдены.")
