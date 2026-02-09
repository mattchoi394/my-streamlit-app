import streamlit as st
from datetime import date

st.set_page_config(page_title="🧩 습관 트래커", page_icon="🧩")

# =========================
# 세션 상태 초기화
# =========================
if "habit_logs" not in st.session_state:
    st.session_state["habit_logs"] = []

# =========================
# UI
# =========================
st.title("🧩 습관 트래커")
st.write("오늘의 습관을 체크하고 기록을 저장해보세요! ✨")

st.divider()

today = date.today()
st.subheader("📅 오늘 날짜")
st.info(f"오늘은 **{today.strftime('%Y-%m-%d')}** 입니다 😊")

st.divider()

# =========================
# 습관 체크 + 입력
# =========================
st.subheader("✅ 오늘의 습관 체크 & 기록")

# 1) 물 주기적으로 마시기
st.markdown("### 💧 물 주기적으로 마시기")
water_ok = st.checkbox("💧 오늘 물을 규칙적으로 마셨나요?")
water_cups = st.number_input("🥤 물 마신 컵 수", min_value=0, max_value=50, value=0, step=1)

st.divider()

# 2) 일정한 수면 시간 유지하기
st.markdown("### 😴 일정한 수면 시간 유지하기")
sleep_ok = st.checkbox("😴 오늘 일정한 수면 시간을 유지했나요?")
sleep_hours = st.slider("🛌 수면 시간(시간)", min_value=0.0, max_value=12.0, value=7.0, step=0.5)

st.divider()

# 3) 하루 2시간 이상 스크롤링 하지 않기
st.markdown("### 📵 하루 2시간 이상 스크롤링 하지 않기")
scroll_ok = st.checkbox("📵 오늘 스크롤링을 2시간 미만으로 했나요?")
scroll_minutes = st.number_input("📱 스크롤링 시간(분) (유튜브/인스타 등)", min_value=0, max_value=1440, value=0, step=10)
st.caption("✅ 목표: 120분 미만")

st.divider()

# =========================
# 저장 버튼 -> 세션 리스트에 추가
# =========================
if st.button("💾 저장하기"):
    log = {
        "date": today.strftime("%Y-%m-%d"),

        "water_ok": water_ok,
        "water_cups": water_cups,

        "sleep_ok": sleep_ok,
        "sleep_hours": sleep_hours,

        "scroll_ok": scroll_ok,
        "scroll_minutes": scroll_minutes,
    }

    st.session_state["habit_logs"].append(log)
    st.success("저장 완료! 오늘도 한 걸음 성장했어요 😎✨")

st.divider()

# =========================
# 저장된 기록 보여주기
# =========================
st.subheader("📚 저장된 기록")

if not st.session_state["habit_logs"]:
    st.info("아직 저장된 기록이 없어요. 오늘 기록을 저장해보세요! 📝")
else:
    for i, log in enumerate(reversed(st.session_state["habit_logs"]), start=1):
        st.write(f"### 🗓️ 기록 {i} - {log['date']}")

        st.write(f"- 💧 물: {'✅' if log['water_ok'] else '❌'} (🥤 {log['water_cups']}컵)")
        st.write(f"- 😴 수면: {'✅' if log['sleep_ok'] else '❌'} (🛌 {log['sleep_hours']}시간)")
        st.write(f"- 📵 스크롤링: {'✅' if log['scroll_ok'] else '❌'} (📱 {log['scroll_minutes']}분)")

        # 목표 대비 표시(추가로 보기 좋게)
        if log["scroll_minutes"] < 120:
            st.caption("📵 스크롤링 목표 달성! (120분 미만)")
        else:
            st.caption("⚠️ 스크롤링 시간이 120분 이상이에요. 내일은 조금만 줄여봐요!")

        st.divider()
