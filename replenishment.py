import streamlit as st
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_active_cars, get_car_invoice_doc_numbers


def show_replenishment_page():
    st.subheader("🚀 Авто в пути")

    active_cars = get_active_cars()

    if active_cars.empty:
        st.info("📭 Нет активных авто в пути.")
        return

    search_query = st.text_input("🔍 Поиск авто по номеру документа (ПкЦБ):", "").strip()

    cars_found = 0

    for _, car in active_cars.iterrows():
        car_id = car['id']
        dispatch_date = str(car.get('dispatch_date', '') or '').strip() or '-'
        country = str(car.get('country', '') or '').strip() or 'Неизвестно'
        location = str(car.get('location', '') or '').strip() or 'Не указана'
        estimated_arrival = str(car.get('estimated_arrival', '') or '').strip() or '-'
        docs_raw = str(car.get('doc_number', '') or '')
        rkz_raw = str(car.get('rkz_number', '') or '')

        docs_list = [d.strip() for d in docs_raw.split("\n") if d.strip()]
        rkz_list = [d.strip() for d in rkz_raw.split("\n") if d.strip()]

        linked_doc_numbers = get_car_invoice_doc_numbers(car_id)
        auto_rkz_list = [d for d in linked_doc_numbers if d not in rkz_list]
        combined_rkz = rkz_list + auto_rkz_list

        if search_query:
            match_found = any(search_query.lower() in doc.lower() for doc in docs_list)
            if not match_found:
                continue

        cars_found += 1

        country_str = country.lower()
        if 'беларусь' in country_str or 'рб' in country_str or 'by' in country_str:
            flag_emoji = "🇧🇾"
        elif 'россия' in country_str or 'рф' in country_str or 'ru' in country_str:
            flag_emoji = "🇷🇺"
        else:
            flag_emoji = "🏳️"

        header_title = (
            f"📅 Отгрузка: {dispatch_date} | "
            f"{flag_emoji} {country} | "
            f"📍 Локация: {location} | "
            f"🏁 План прибытия: {estimated_arrival}"
        )

        is_expanded = True if search_query else False

        with st.expander(header_title, expanded=is_expanded):
            doc_col1, doc_col2 = st.columns(2)

            with doc_col1:
                if docs_list:
                    with st.expander(f"📋 № документа / ПкЦБ ({len(docs_list)} шт.)"):
                        st.code("\n".join(docs_list), language="")
                else:
                    st.caption("📄 *№ документа: Нет данных*")

            with doc_col2:
                if combined_rkz:
                    with st.expander(f"📑 № РКЗ (СЧКЗ) — всего {len(combined_rkz)} "
                                     f"(ручных: {len(rkz_list)}, из счетов: {len(auto_rkz_list)} шт.)"):
                        st.code("\n".join(combined_rkz), language="")
                else:
                    st.caption("📑 *№ РКЗ: Нет данных*")

    if search_query and cars_found == 0:
        st.warning(f"Машины с документом '{search_query}' не найдены.")
