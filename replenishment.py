import streamlit as st
import pandas as pd

def show_replenishment_page():
    st.subheader("🟪 Авто в пути (Лист Пополн)")
    
    # Сюда вставьте вашу НАСТОЯЩУЮ CSV-ссылку на лист "Пополн"
    POPOLN_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQy_3jRua5IiYZD1tk7nCWISLhn_IbFJIucGc0-hxR3Z3DNVpgr32WYwurNJZ-lnELLpicod-6wGIAD/pub?gid=60140824&single=true&output=csv"
    
    try:
        # Читаем первые 6 колонок (A, B, C, D, E, F)
        df = pd.read_csv(POPOLN_URL, encoding='utf-8-sig', header=None, on_bad_lines='skip')
        df = df.dropna(how='all').reset_index(drop=True)
        
        df = df.iloc[:, :6]
        df.columns = ['Дата отгрузки', 'Страна', 'Локация', '№ документа', '№ РКЗ', 'Плановая дата прибытия']
        
        # Пропускаем шапку таблицы, если она загрузилась
        if not df.empty and ('дата' in str(df.values).lower() or 'страна' in str(df.values).lower()):
            df = df.iloc[1:].reset_index(drop=True)
            
    except Exception as e:
        st.error(f"Ошибка загрузки данных: {e}")
        return

    if df.empty:
        st.info("Нет данных по автомобилям на листе 'Пополн'.")
        return

    # Заполняем пустоты, чтобы строки корректно группировались по машинам
    df['Дата отгрузки'] = df['Дата отгрузки'].fillna("-").astype(str).str.strip()
    df['Страна'] = df['Страна'].fillna("Неизвестно").astype(str).str.strip()
    df['Локация'] = df['Локация'].fillna("Не указана").astype(str).str.strip()
    df['Плановая дата прибытия'] = df['Плановая дата прибытия'].fillna("-").astype(str).str.strip()
    
    # Документы переводим в строку, заменяя NaN на пустую строку для фильтрации
    df['№ документа'] = df['№ документа'].fillna("").astype(str).str.strip()
    df['№ РКЗ'] = df['№ РКЗ'].fillna("").astype(str).str.strip()

    # Группируем по уникальным рейсам авто
    groupby_cols = ['Дата отгрузки', 'Страна', 'Локация', 'Плановая дата прибытия']
    unique_cars = df[groupby_cols].drop_duplicates()

    for _, car in unique_cars.iterrows():
        country_str = car['Страна'].lower()
        
        # Определяем цветной флаг по значению из колонки B
        if 'беларусь' in country_str or 'рб' in country_str or 'by' in country_str:
            flag_emoji = "🇧🇾"
        elif 'россия' in country_str or 'рф' in country_str or 'ru' in country_str:
            flag_emoji = "🇷🇺"
        else:
            flag_emoji = "🏳️"
            
        # Формируем строгую последовательность заголовка по вашей задаче:
        # Дата отгрузки, Флаг страны, Страна, Локация авто, Плановая дата прибытия
        header_title = (
            f"📅 Отгрузка: {car['Дата отгрузки']} | "
            f"{flag_emoji} {car['Страна']} | "
            f"📍 Локация: {car['Локация']} | "
            f"🏁 План прибытия: {car['Плановая дата прибытия']}"
        )
        
        # Фильтруем все строки, принадлежащие текущему авто
        car_rows = df[
            (df['Дата отгрузки'] == car['Дата отгрузки']) &
            (df['Страна'] == car['Страна']) &
            (df['Локация'] == car['Локация']) &
            (df['Плановая дата прибытия'] == car['Плановая дата прибытия'])
        ]
        
        # Собираем списки документов, исключая пустые ячейки
        docs_list = [d for d in car_rows['№ документа'].tolist() if d != ""]
        rkz_list = [r for r in car_rows['№ РКЗ'].tolist() if r != ""]
        
        # Если в этой машине вообще есть хоть какие-то документы, выводим её expander
        with st.expander(header_title):
            doc_col1, doc_col2 = st.columns(2)
            
            with doc_col1:
                st.markdown(f"**📄 № документа ({len(docs_list)} шт.):**")
                if docs_list:
                    for doc in docs_list:
                        st.markdown(f"- `{doc}`")
                else:
                    st.write("*Нет данных*")
                    
            with doc_col2:
                st.markdown(f"**📑 № РКЗ ({len(rkz_list)} шт.):**")
                if rkz_list:
                    for rkz in rkz_list:
                        # Если ячейка содержит перенос строк (как в первой строке вашего скрина), разбиваем её
                        for sub_rkz in rkz.split('\n'):
                            if sub_rkz.strip():
                                st.markdown(f"- `{sub_rkz.strip()}`")
                else:
                    st.write("*Нет данных*")
