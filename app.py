import streamlit as st
from datetime import date

st.set_page_config(page_title="🧩 습관 트래커", page_icon="🧩")

# 1) 앱 제목과 설명
st.title("🧩 습관 트래커")
st.write("오늘의 습관을 체크하고, 간단한 기록도 남겨보세요! ✨")

st.divider()

# 2) 오늘 날짜 표시
today = date.today()
st.subheader("📅 오늘 날짜")
st.info(f"오늘은 **{today.strftime('%Y-%m-%d')}** 입니다 😊")

st.divider()

# 3) 3가지 습관 체크박스
st.subheader("✅ 오늘의 습관 체크")

workout_done = st.checkbox("🏃 운동하기")
reading_done = st.checkbox("📚 독서하기")
water_done = st.checkbox("💧 물 마시기")

st.divider()

# 4) 숫자 입력 (운동 시간, 독서 페이지, 물 횟수)
st.subheader("📌 오늘의 기록 입력")

col1, col2, col3 = st.columns(3)

with col1:
    workout_time = st.number_input("🏃 운동 시간(분)", min_value=0, max_value=600, value=0, step=10)

with col2:
    reading_pages = st.number_input("📚 독서 페이지(장)", min_value=0, max_value=1000, value=0, step=5)

with col3:
    water_count = st.number_input("💧 물 마신 횟수(컵)", min_value=0, max_value=50, value=0, step=1)

st.divider()

# 5) 저장 버튼
if st.button("💾 저장하기"):
    st.success("저장 완료! 오늘도 멋지게 해냈어요 😎✨")

    st.write("### 📋 저장된 내용")
    st.write(f"- 🏃 운동하기: {'✅ 완료' if workout_done else '❌ 미완료'} (시간: {workout_time}분)")
    st.write(f"- 📚 독서하기: {'✅ 완료' if reading_done else '❌ 미완료'} (페이지: {reading_pages}장)")
    st.write(f"- 💧 물 마시기: {'✅ 완료' if water_done else '❌ 미완료'} (횟수: {water_count}컵)")
