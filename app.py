import json
import random
from datetime import date, timedelta
from typing import Dict, Optional, Any, List

import pandas as pd
import requests
import streamlit as st
import altair as alt
from openai import OpenAI  # pip install openai


# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(page_title="AI 습관 트래커 (포켓몬)", page_icon="🎮", layout="wide")
st.title("🎮 AI 습관 트래커 (포켓몬)")
st.caption("습관 체크 + 날씨 + 랜덤 1세대 포켓몬 + AI 코치 리포트까지 한 번에 ✨")

# 세션 상태
if "today_log" not in st.session_state:
    st.session_state["today_log"] = None


# =========================================================
# 사이드바: API Keys
# =========================================================
with st.sidebar:
    st.header("🔑 API Keys")
    openai_api_key = st.text_input("OpenAI API Key", type="password", placeholder="sk-...")
    owm_api_key = st.text_input("OpenWeatherMap API Key", type="password", placeholder="OpenWeatherMap Key")
    st.divider()
    st.caption("※ 키는 브라우저 세션에서만 사용돼요.")


# =========================================================
# API 연동 함수들
# =========================================================
def get_weather(city: str, api_key: str) -> Optional[Dict[str, Any]]:
    """
    OpenWeatherMap에서 날씨 가져오기 (한국어, 섭씨)
    실패 시 None 반환, timeout=10
    """
    if not api_key:
        return None
    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": api_key,
            "units": "metric",
            "lang": "kr",  # OWM: kr는 한국어로 동작하는 경우가 많고, ko도 종종 동작하지만 일관성 위해 kr 사용
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        weather = {
            "city": city,
            "desc": (data.get("weather") or [{}])[0].get("description", ""),
            "temp_c": float(data.get("main", {}).get("temp", 0.0)),
            "feels_like_c": float(data.get("main", {}).get("feels_like", 0.0)),
            "humidity": int(data.get("main", {}).get("humidity", 0)),
            "wind_mps": float(data.get("wind", {}).get("speed", 0.0)),
        }
        return weather
    except Exception:
        return None


def get_pokemon() -> Optional[Dict[str, Any]]:
    """
    PokeAPI에서 1세대(1~151) 랜덤 포켓몬 가져오기
    - 공식 아트워크 이미지 URL
    - 이름, 도감 번호, 타입, 스탯
    실패 시 None 반환, timeout=10
    """
    try:
        pid = random.randint(1, 151)
        url = f"https://pokeapi.co/api/v2/pokemon/{pid}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()

        name = (data.get("name") or "").strip()
        dex_no = int(data.get("id") or pid)
        types = [t["type"]["name"] for t in (data.get("types") or []) if t.get("type")]

        stats_map = {
            "hp": 0,
            "attack": 0,
            "defense": 0,
            "special-attack": 0,
            "special-defense": 0,
            "speed": 0,
        }
        for s in (data.get("stats") or []):
            k = s.get("stat", {}).get("name")
            v = s.get("base_stat")
            if k in stats_map and isinstance(v, int):
                stats_map[k] = v

        artwork = (
            data.get("sprites", {})
            .get("other", {})
            .get("official-artwork", {})
            .get("front_default")
        )

        return {
            "id": dex_no,
            "name": name,
            "types": types,
            "artwork_url": artwork,
            "stats": stats_map,
        }
    except Exception:
        return None


# =========================================================
# AI 리포트
# =========================================================
STYLE_SYSTEM_PROMPTS = {
    "스파르타 코치": (
        "너는 엄격하지만 성장에 진심인 스파르타 코치다. "
        "핑계는 줄이고, 실행 가능한 지시를 명확하고 짧게 제시한다. "
        "단호하지만 인신공격/비난은 하지 않는다."
    ),
    "따뜻한 멘토": (
        "너는 다정하고 현실적인 멘토다. "
        "사용자의 노력을 인정하고, 부담을 줄이면서도 다음 행동을 구체적으로 안내한다. "
        "따뜻한 말투로, 비난 없이 개선점을 제안한다."
    ),
    "게임 마스터": (
        "너는 RPG 게임 마스터다. "
        "사용자의 하루를 퀘스트/경험치/레벨업으로 비유해 재미있게 말한다. "
        "과몰입/과장된 위협은 피하고, 가볍고 유쾌하게 다음 미션을 제시한다."
    ),
}


def _compact_weather(weather: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not weather:
        return {"available": False}
    return {
        "available": True,
        "city": weather.get("city"),
        "desc": weather.get("desc"),
        "temp_c": weather.get("temp_c"),
        "feels_like_c": weather.get("feels_like_c"),
        "humidity": weather.get("humidity"),
        "wind_mps": weather.get("wind_mps"),
    }


def _compact_pokemon(pokemon: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not pokemon:
        return {"available": False}
    return {
        "available": True,
        "id": pokemon.get("id"),
        "name": pokemon.get("name"),
        "types": pokemon.get("types"),
        "stats": pokemon.get("stats"),
    }


def generate_report(
    openai_key: str,
    coach_style: str,
    habits: Dict[str, bool],
    mood: int,
    weather: Optional[Dict[str, Any]],
    pokemon: Optional[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """
    AI 코치 리포트 생성 (모델: gpt-5-mini)

    출력(JSON):
      grade: S|A|B|C|D
      habit_analysis: string
      weather_comment: string
      tomorrow_missions: string
      pokemon_cheer: string
      share_text: string
    """
    if not openai_key:
        return None

    client = OpenAI(api_key=openai_key)
    system = STYLE_SYSTEM_PROMPTS.get(coach_style, STYLE_SYSTEM_PROMPTS["따뜻한 멘토"])

    schema = {
        "grade": "S|A|B|C|D",
        "habit_analysis": "string",
        "weather_comment": "string",
        "tomorrow_missions": "string",
        "pokemon_cheer": "string",
        "share_text": "string",
    }

    # ✅ 모델에 전달할 데이터는 '텍스트'로 직렬화해 명확하게 전달
    payload = {
        "date": str(date.today()),
        "mood_1_to_10": int(mood),
        "habits": habits,
        "weather": _compact_weather(weather),
        "pokemon": _compact_pokemon(pokemon),
        "output_format": schema,
        "rules": [
            "출력은 JSON '하나'만. 다른 텍스트/코드블록/설명 금지.",
            "grade는 반드시 S/A/B/C/D 중 하나.",
            "tomorrow_missions는 짧은 불릿 3개 권장(줄바꿈 가능).",
            "pokemon_cheer에는 포켓몬 이름/타입/스탯(HP/attack/defense/sp_atk/sp_def/speed)을 활용해 응원.",
            "한국어로 작성.",
        ],
    }

    user_prompt = (
        "다음 데이터를 바탕으로 '컨디션 리포트'를 작성해줘.\n"
        "반드시 JSON 하나만 출력해.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )

    # ✅ 1차 시도: Chat Completions + JSON object 강제 (가장 안정적)
    try:
        resp = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text)

        if data.get("grade") not in ["S", "A", "B", "C", "D"]:
            return None

        return {
            "grade": str(data.get("grade", "")).strip(),
            "habit_analysis": str(data.get("habit_analysis", "")).strip(),
            "weather_comment": str(data.get("weather_comment", "")).strip(),
            "tomorrow_missions": str(data.get("tomorrow_missions", "")).strip(),
            "pokemon_cheer": str(data.get("pokemon_cheer", "")).strip(),
            "share_text": str(data.get("share_text", "")).strip(),
        }
    except Exception:
        pass

    # ✅ 2차 시도: Responses API (SDK/환경에 따라 이쪽이 더 잘 될 수도 있어서 fallback)
    try:
        resp = client.responses.create(
            model="gpt-5-mini",
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (getattr(resp, "output_text", "") or "").strip()

        # JSON만 뽑아내기(방어)
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        data = json.loads(text[start : end + 1])

        if data.get("grade") not in ["S", "A", "B", "C", "D"]:
            return None

        return {
            "grade": str(data.get("grade", "")).strip(),
            "habit_analysis": str(data.get("habit_analysis", "")).strip(),
            "weather_comment": str(data.get("weather_comment", "")).strip(),
            "tomorrow_missions": str(data.get("tomorrow_missions", "")).strip(),
            "pokemon_cheer": str(data.get("pokemon_cheer", "")).strip(),
            "share_text": str(data.get("share_text", "")).strip(),
        }
    except Exception:
        return None


# =========================================================
# 습관 체크인 UI
# =========================================================
st.subheader("✅ 오늘의 체크인")

colA, colB = st.columns(2, gap="large")
with colA:
    habit_wakeup = st.checkbox("🌅 기상 미션")
    habit_water = st.checkbox("💧 물 마시기")
    habit_study = st.checkbox("📚 공부/독서")
with colB:
    habit_workout = st.checkbox("🏃 운동하기")
    habit_sleep = st.checkbox("😴 수면")

mood = st.slider("😊 오늘 기분은 어떤가요? (1~10)", min_value=1, max_value=10, value=6)

cities = [
    "Seoul", "Busan", "Incheon", "Daegu", "Daejeon",
    "Gwangju", "Suwon", "Ulsan", "Sejong", "Jeju",
]
c1, c2 = st.columns([1, 1], gap="large")
with c1:
    city = st.selectbox("🏙️ 도시 선택", cities, index=0)
with c2:
    coach_style = st.radio("🧑‍🏫 코치 스타일", ["스파르타 코치", "따뜻한 멘토", "게임 마스터"], horizontal=True)

habits = {
    "기상 미션": habit_wakeup,
    "물 마시기": habit_water,
    "공부/독서": habit_study,
    "운동하기": habit_workout,
    "수면": habit_sleep,
}

checked_count = sum(1 for v in habits.values() if v)
achievement_rate = round((checked_count / len(habits)) * 100)

st.divider()


# =========================================================
# 달성률 + 메트릭 + 7일 차트
# =========================================================
st.subheader("📈 달성률 요약")

mcol1, mcol2, mcol3 = st.columns(3, gap="large")
mcol1.metric("달성률", f"{achievement_rate}%")
mcol2.metric("달성 습관", f"{checked_count} / {len(habits)}")
mcol3.metric("기분", f"{mood} / 10")


def build_demo_7days(today_rate: int) -> pd.DataFrame:
    base_dates = [date.today() - timedelta(days=i) for i in range(6, 0, -1)]
    demo_rates = [55, 70, 45, 80, 60, 75]  # 데모 고정
    rows = [{"date": d.strftime("%m/%d"), "rate": r} for d, r in zip(base_dates, demo_rates)]
    rows.append({"date": date.today().strftime("%m/%d"), "rate": int(today_rate)})
    return pd.DataFrame(rows)


df7 = build_demo_7days(achievement_rate)
st.bar_chart(df7.set_index("date")["rate"])

st.divider()


# =========================================================
# 결과 표시: 버튼 + 날씨/포켓몬 카드 + 리포트
# =========================================================
if st.button("🧾 컨디션 리포트 생성"):
    if not openai_api_key:
        st.error("사이드바에 OpenAI API Key를 입력해주세요! 🔑")
        st.stop()

    with st.spinner("🔎 날씨와 포켓몬을 불러오는 중..."):
        weather = get_weather(city, owm_api_key)
        pokemon = get_pokemon()

    st.session_state["today_log"] = {
        "date": str(date.today()),
        "city": city,
        "coach_style": coach_style,
        "mood": mood,
        "habits": habits,
        "achievement_rate": achievement_rate,
        "weather": weather,
        "pokemon": pokemon,
    }

    with st.spinner("🤖 AI 코치가 리포트를 작성 중..."):
        report = generate_report(
            openai_key=openai_api_key,
            coach_style=coach_style,
            habits=habits,
            mood=mood,
            weather=weather,
            pokemon=pokemon,
        )

    left, right = st.columns(2, gap="large")

    # ---- 날씨 카드 ----
    with left:
        st.markdown("### 🌦️ 오늘의 날씨")
        with st.container(border=True):
            if weather is None:
                st.warning("날씨 정보를 가져오지 못했어요. (OpenWeatherMap API Key/도시/네트워크 확인)")
            else:
                st.write(f"**도시:** {weather['city']}")
                st.write(f"**상태:** {weather['desc']}")
                st.write(f"**기온:** {weather['temp_c']:.1f}℃ (체감 {weather['feels_like_c']:.1f}℃)")
                st.write(f"**습도:** {weather['humidity']}%")
                st.write(f"**바람:** {weather['wind_mps']:.1f} m/s")

    # ---- 포켓몬 카드 ----
    with right:
        st.markdown("### 🧩 오늘의 파트너 포켓몬")
        with st.container(border=True):
            if pokemon is None:
                st.warning("포켓몬 정보를 가져오지 못했어요. (PokeAPI/네트워크 확인)")
            else:
                name = (pokemon.get("name") or "").title()
                st.write(f"**#{pokemon.get('id')} · {name}**")
                st.write(f"**타입:** {', '.join(pokemon.get('types') or []) if pokemon.get('types') else '알 수 없음'}")

                if pokemon.get("artwork_url"):
                    st.image(pokemon["artwork_url"], use_container_width=True)
                else:
                    st.caption("공식 아트워크 이미지를 찾지 못했어요.")

                stats = pokemon.get("stats") or {}
                stat_rows = [
                    {"stat": "HP", "value": int(stats.get("hp", 0))},
                    {"stat": "공격", "value": int(stats.get("attack", 0))},
                    {"stat": "방어", "value": int(stats.get("defense", 0))},
                    {"stat": "특수공격", "value": int(stats.get("special-attack", 0))},
                    {"stat": "특수방어", "value": int(stats.get("special-defense", 0))},
                    {"stat": "스피드", "value": int(stats.get("speed", 0))},
                ]
                stat_df = pd.DataFrame(stat_rows)

                # 요구사항: st.bar_chart + 빨간색 → st.bar_chart는 색 지정 불가라,
                # 빨간색은 Altair로 구현 (시각적 요구 충족)
                chart = (
                    alt.Chart(stat_df)
                    .mark_bar(color="red")
                    .encode(
                        x=alt.X("stat:N", title="스탯"),
                        y=alt.Y("value:Q", title="값"),
                        tooltip=["stat:N", "value:Q"],
                    )
                    .properties(height=220)
                )
                st.altair_chart(chart, use_container_width=True)

    st.divider()

    st.markdown("## 🧠 AI 코치 리포트")
    if report is None:
        st.error(
            "리포트를 생성하지 못했어요.\n"
            "- OpenAI API Key가 유효한지\n"
            "- 모델(gpt-5-mini) 접근 권한이 있는지\n"
            "- 네트워크/요금 한도 문제가 없는지 확인해 주세요."
        )
    else:
        grade_badge = {"S": "🏆", "A": "🥇", "B": "🥈", "C": "🥉", "D": "🪫"}.get(report["grade"], "📘")
        st.markdown(f"### {grade_badge} 컨디션 등급: **{report['grade']}**")

        st.markdown("**✅ 습관 분석**")
        st.write(report["habit_analysis"] or "-")

        st.markdown("**🌦️ 날씨 코멘트**")
        st.write(report["weather_comment"] or "-")

        st.markdown("**🎯 내일 미션**")
        st.write(report["tomorrow_missions"] or "-")

        st.markdown("**🧩 오늘의 파트너 포켓몬 응원**")
        st.write(report["pokemon_cheer"] or "-")

        st.markdown("### 📌 공유용 텍스트")
        st.code(report["share_text"] or "공유용 텍스트를 만들지 못했어요.", language="text")

st.divider()

with st.expander("ℹ️ API 안내 / 설정 방법"):
    st.markdown("""
**OpenWeatherMap**
- 현재 날씨 API를 사용해요.
- 도시명을 영어로 넣고(`Seoul`, `Busan` 등), `units=metric`, `lang=kr`로 요청합니다.

**PokeAPI**
- 1세대(1~151) 중 랜덤 포켓몬을 가져와요.
- 공식 아트워크 URL을 사용합니다.

**OpenAI**
- 모델: `gpt-5-mini`
- 습관/기분/날씨/포켓몬 정보를 묶어서 코치 리포트를 생성해요.
""")
