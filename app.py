import streamlit as st
from datetime import date

st.set_page_config(page_title="🧩 습관 트래커", page_icon="🧩")

# =========================
# 1) 세션 상태 초기화
# =========================
if "habit_logs" not in st.session_state:
    st.session_state["habit_logs"] = []  # 저장된 기록 리스트

# =========================
# UI
# =========================
st.title("🧩 습관 트래커")
st.write("오늘의 습관을 체크하고 기록을 저장해보세요! ✨")

st.divider()

# 오늘 날짜
today = date.today()
st.subheader("📅 오늘 날짜")
st.info(f"오늘은 **{today.strftime('%Y-%m-%d')}** 입니다 😊")

st.divider()

# 습관 체크
st.subheader("✅ 오늘의 습관 체크")
workout_done = st.checkbox("🏃 운동하기")
reading_done = st.checkbox("📚 독서하기")
water_done = st.checkbox("💧 물 마시기")

st.divider()

# 숫자 입력
st.subheader("📌 오늘의 기록 입력")
col1, col2, col3 = st.columns(3)

with col1:
    workout_time = st.number_input("🏃 운동 시간(분)", min_value=0, max_value=600, value=0, step=10)

with col2:
    reading_pages = st.number_input("📚 독서 페이지(장)", min_value=0, max_value=1000, value=0, step=5)

with col3:
    water_count = st.number_input("💧 물 마신 횟수(컵)", min_value=0, max_value=50, value=0, step=1)

st.divider()

# =========================
# 2) 저장 버튼 -> 세션 리스트에 추가
# =========================
if st.button("💾 저장하기"):
    log = {
        "date": today.strftime("%Y-%m-%d"),
        "workout_done": workout_done,
        "reading_done": reading_done,
        "water_done": water_done,
        "workout_time": workout_time,
        "reading_pages": reading_pages,
        "water_count": water_count,
    }

    st.session_state["habit_logs"].append(log)
    st.success("저장 완료! 오늘도 한 걸음 성장했어요 😎✨")

st.divider()

# =========================
# 3) 저장된 기록 보여주기
# =========================
st.subheader("📚 저장된 기록")

if len(st.session_state["habit_logs"]) == 0:
    st.info("아직 저장된 기록이 없어요. 오늘 기록을 저장해보세요! 📝")
else:
    for i, log in enumerate(reversed(st.session_state["habit_logs"]), start=1):
        st.write(f"### 🗓️ 기록 {i} - {log['date']}")
        st.write(f"- 🏃 운동: {'✅' if log['workout_done'] else '❌'} ({log['workout_time']}분)")
        st.write(f"- 📚 독서: {'✅' if log['reading_done'] else '❌'} ({log['reading_pages']}장)")
        st.write(f"- 💧 물: {'✅' if log['water_done'] else '❌'} ({log['water_count']}컵)")
        st.divider()
