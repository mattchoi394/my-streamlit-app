# app.py
# CareFit (케어핏) MVP - 단일 Streamlit 앱
# 실행:
#   pip install -r requirements.txt  (아래 requirements 예시 참고)
#   streamlit run app.py
#
# OpenAI API Key 입력:
#   - 사이드바에 직접 입력하거나
#   - 환경변수 OPENAI_API_KEY 로 설정 (선택)
#
# NOTE (선택 확장):
# - SQLite 저장 레이어를 붙이고 싶다면, session_state의
#   st.session_state["checkins"], st.session_state["plan_history"]
#   를 테이블로 저장하면 됩니다. (하단 expander에 설계 제안 포함)

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from openai import OpenAI  # pip install openai

def scroll_to_top():
    st.components.v1.html(
        """
        <script>
          window.parent.scrollTo(0, 0);
        </script>
        """,
        height=0,
    )

# =========================================================
# 기본 설정
# =========================================================
APP_TITLE = "케어핏(CareFit)"
APP_SUBTITLE = "습관 개선을 위한 라이프스타일/헬스 케어 앱"
MODEL_DEFAULT = "gpt-5-mini"

st.set_page_config(page_title=APP_TITLE, page_icon="🧩", layout="wide")
st.title("🧩 케어핏(CareFit)")
st.caption(f"{APP_SUBTITLE} · 설문 → 플랜 → 리마인더 → 체크인 → 개인화 루프 ✨")

# =========================================================
# 세션 상태 초기화
# =========================================================
def _init_state():
    st.session_state.setdefault("step", 1)  # 1: 설문, 2: 결과/플랜, 3: 체크인/기록
    st.session_state.setdefault("profile", {})  # 설문 결과
    st.session_state.setdefault("plan", None)  # 현재 플랜(JSON dict)
    st.session_state.setdefault("plan_history", [])  # 플랜 버전 히스토리
    st.session_state.setdefault("reminders_custom", [])  # 사용자가 수정/추가한 리마인더
    st.session_state.setdefault("checkins", [])  # 체크인 기록 리스트
    st.session_state.setdefault("last_adjustment_note", "")  # 조정 사유/요약
    st.session_state.setdefault("ui_error", "")  # 최근 에러 메시지

_init_state()

# =========================================================
# 사이드바: 키/모델/네비게이션
# =========================================================
with st.sidebar:
    # -------------------------
    # (선택) 스크롤 맨 위로 올리기 유틸
    # - 이미 파일에 같은 함수가 있다면 이 함수는 제거해도 됨
    # -------------------------
    import streamlit.components.v1 as components

    def scroll_to_top():
        components.html(
            """
            <script>
              window.parent.scrollTo(0, 0);
            </script>
            """,
            height=0,
        )

    st.header("🔑 OpenAI 설정")
    openai_key_input = st.text_input("OpenAI API Key", type="password", placeholder="sk-... (사이드바 입력)")
    model_name = st.text_input("모델", value=MODEL_DEFAULT)

    st.divider()
    st.header("🧭 이동")

    step_label = {1: "1) 설문", 2: "2) 플랜/리마인더", 3: "3) 체크인/기록"}

    # ✅ 라디오 변경 시: step 업데이트 + 상단 스크롤
    def on_step_change():
        st.session_state["step"] = st.session_state["sidebar_step"]
        scroll_to_top()

    # ✅ 현재 step을 라디오에 반영(초기값 동기화)
    if "sidebar_step" not in st.session_state:
        st.session_state["sidebar_step"] = st.session_state.get("step", 1)
    else:
        # 다른 버튼(설문 저장/체크인 이동)으로 step이 바뀐 경우에도 라디오가 따라오게
        st.session_state["sidebar_step"] = st.session_state.get("step", 1)

    st.radio(
        "단계",
        options=[1, 2, 3],
        format_func=lambda x: step_label[x],
        index=int(st.session_state["sidebar_step"]) - 1,
        key="sidebar_step",
        on_change=on_step_change,
    )

    st.divider()
    st.header("🧰 유틸")
    if st.button("🧹 세션 초기화(리셋)", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# 키 결정: 사이드바 입력 우선, 없으면 환경변수 사용(openai SDK가 자동 인식 가능)
OPENAI_API_KEY = openai_key_input.strip() if openai_key_input else None

# =========================================================
# 도메인(분야) / 설문 템플릿
# =========================================================
DOMAINS = [
    "수면",
    "식습관(야식/폭식)",
    "운동",
    "집중/공부",
    "스트레스/정서",
    "디지털 습관(스마트폰/스크롤링)",
]

DIFFICULTY_PREF = ["가볍게(쉬움)", "적당히(중간)", "빡세게(도전)"]
PRIORITIES = ["지속가능성", "빠른 변화", "에너지/컨디션", "생산성", "멘탈 안정"]
TIME_WINDOWS = ["아침(06-10)", "점심(11-14)", "오후(15-18)", "저녁(19-22)", "야간(23-02)"]

# =========================================================
# LLM 프롬프트 / JSON 스키마
# =========================================================
SYSTEM_PROMPT = """
너는 생활습관 개선 코치다. 사용자의 습관을 '의학적 진단' 없이 생활습관 수준에서 분석하고,
실행 가능한 작은 행동으로 구성된 플랜을 설계한다.

규칙:
- 의학적 진단/치료/약물 처방을 하지 않는다.
- 안전을 최우선으로: 무리한 운동/극단적 식이/수면 박탈 등을 권하지 않는다.
- 사용자의 시간/난이도/우선순위를 반영해 현실적이고 구체적으로 제안한다.
- 출력은 반드시 JSON 하나만. (코드블록/설명/추가 텍스트 금지)
"""

JSON_SCHEMA_GUIDE = {
    "summary": "string",
    "pain_points": ["string"],
    "solutions": ["string"],
    "new_habits": [
        {
            "name": "string",
            "why": "string",
            "schedule": {"days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "time": "HH:MM", "frequency_per_week": 3},
            "difficulty": "easy|mid|hard",
        }
    ],
    "reminders": [{"title": "string", "time": "HH:MM", "rrule": "FREQ=DAILY|WEEKLY;..."}],
    "next_adjustment_rules": [{"if": "string", "then": "string"}],
}

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# =========================================================
# 유틸: 안전 JSON 파싱/보정
# =========================================================
def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    # 1) ```json ... ``` 우선
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 2) 가장 바깥 { ... } 추출
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        blob = text[s : e + 1]
        try:
            return json.loads(blob)
        except Exception:
            # 흔한 꼬리 콤마 보정
            try:
                blob2 = re.sub(r",\s*}", "}", blob)
                blob2 = re.sub(r",\s*]", "]", blob2)
                return json.loads(blob2)
            except Exception:
                return None
    return None


def normalize_plan(plan: dict) -> dict:
    """필수 키를 채우고, 최소한의 형태를 보장."""
    if not isinstance(plan, dict):
        return {}

    plan.setdefault("summary", "")
    plan.setdefault("pain_points", [])
    plan.setdefault("solutions", [])
    plan.setdefault("new_habits", [])
    plan.setdefault("reminders", [])
    plan.setdefault("next_adjustment_rules", [])

    # days/time/frequency 기본 보정
    for h in plan.get("new_habits", []) or []:
        if not isinstance(h, dict):
            continue
        h.setdefault("name", "새 습관")
        h.setdefault("why", "")
        h.setdefault("difficulty", "easy")
        sch = h.setdefault("schedule", {})
        if not isinstance(sch, dict):
            sch = {}
            h["schedule"] = sch
        sch.setdefault("days", ["Mon", "Wed", "Fri"])
        sch.setdefault("time", "09:00")
        sch.setdefault("frequency_per_week", 3)

        # 요일 정상화
        days = sch.get("days") or []
        if isinstance(days, list):
            sch["days"] = [d for d in days if d in WEEKDAYS] or ["Mon", "Wed", "Fri"]
        else:
            sch["days"] = ["Mon", "Wed", "Fri"]

        # time 형식 보정
        t = str(sch.get("time") or "09:00")
        if not re.match(r"^\d{2}:\d{2}$", t):
            sch["time"] = "09:00"

        # difficulty 보정
        if h.get("difficulty") not in ["easy", "mid", "hard"]:
            h["difficulty"] = "easy"

    # reminders 보정
    for r in plan.get("reminders", []) or []:
        if not isinstance(r, dict):
            continue
        r.setdefault("title", "리마인더")
        r.setdefault("time", "09:00")
        r.setdefault("rrule", "FREQ=DAILY")

        t = str(r.get("time") or "09:00")
        if not re.match(r"^\d{2}:\d{2}$", t):
            r["time"] = "09:00"
        if not str(r.get("rrule") or "").startswith("FREQ="):
            r["rrule"] = "FREQ=DAILY"

    return plan


# =========================================================
# OpenAI 호출 (플랜 생성 / 재조정)
# =========================================================
def openai_client(api_key: Optional[str]) -> OpenAI:
    # api_key가 None이어도 OpenAI SDK가 ENV에서 가져올 수 있음
    return OpenAI(api_key=api_key) if api_key else OpenAI()


def generate_plan_with_llm(
    api_key: Optional[str],
    model: str,
    domain: str,
    habit_to_improve: str,
    survey: dict,
) -> Tuple[Optional[dict], str]:
    """
    설문/분야/습관 입력 기반으로 플랜 JSON 생성.
    반환: (plan_dict or None, raw_text)
    """
    user_payload = {
        "domain": domain,
        "habit_to_improve": habit_to_improve,
        "survey": survey,
        "required_json_schema": JSON_SCHEMA_GUIDE,
        "instruction": (
            "사용자의 부정적 습관을 원인 가설 → 해결 전략 → 실행 플랜으로 구조화해서 "
            "해결책(solutions)과 새로운 습관(new_habits), 리마인더(reminders), 다음 조정 규칙(next_adjustment_rules)을 JSON으로 출력해라."
        ),
        "tone": "실행 가능한 코치",
        "constraints": [
            "너무 많은 습관을 제시하지 말고 new_habits는 3~5개로 제한",
            "reminders는 new_habits와 매칭되도록 3~5개",
            "difficulty는 easy/mid/hard 중 하나",
            "days는 Mon~Sun 약어 사용",
            "시간은 HH:MM 24시간",
        ],
    }

    prompt = "아래 데이터를 바탕으로 CareFit 맞춤 케어 플랜을 생성해줘. 출력은 JSON 하나만.\n\n" + json.dumps(
        user_payload, ensure_ascii=False
    )

    client = openai_client(api_key)

    # 1차: response_format json_object (가능하면 강제)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        data = extract_json(text) or json.loads(text)
        return normalize_plan(data), text
    except Exception:
        pass

    # 2차: responses.create fallback
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": prompt},
            ],
        )
        text = (getattr(resp, "output_text", "") or "").strip()
        data = extract_json(text)
        if data:
            return normalize_plan(data), text
        return None, text
    except Exception as e:
        return None, f"ERROR: {e}"


def adjust_plan_with_llm(
    api_key: Optional[str],
    model: str,
    current_plan: dict,
    checkin_summary: dict,
    adjustment_note: str,
) -> Tuple[Optional[dict], str]:
    """
    체크인 결과(완료/미완료 패턴) 기반으로 플랜 재조정(JSON).
    """
    client = openai_client(api_key)

    payload = {
        "current_plan": current_plan,
        "checkin_summary": checkin_summary,
        "adjustment_note": adjustment_note,
        "required_json_schema": JSON_SCHEMA_GUIDE,
        "rules": [
            "출력은 JSON 하나만",
            "달성률이 낮으면 난이도/빈도/시간대를 조정하여 더 현실적으로",
            "달성률이 높으면 유지 또는 소폭 상향(무리하지 않게)",
            "reminders도 new_habits 변화에 맞춰 업데이트",
        ],
    }
    prompt = "다음 정보를 기반으로 현재 플랜을 '개인화 재조정'해줘. 출력은 JSON 하나만.\n\n" + json.dumps(payload, ensure_ascii=False)

    # 1차
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        data = extract_json(text) or json.loads(text)
        return normalize_plan(data), text
    except Exception:
        pass

    # 2차
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": prompt},
            ],
        )
        text = (getattr(resp, "output_text", "") or "").strip()
        data = extract_json(text)
        if data:
            return normalize_plan(data), text
        return None, text
    except Exception as e:
        return None, f"ERROR: {e}"


# =========================================================
# 체크인/달성률/요약 계산
# =========================================================
def compute_daily_completion(checkin: dict) -> Tuple[int, int, float]:
    """(done, total, rate[0~100])"""
    items = checkin.get("items") or []
    total = len(items)
    done = sum(1 for it in items if it.get("done") is True)
    rate = (done / total * 100) if total else 0.0
    return done, total, rate


def summarize_checkins(checkins: List[dict], days: int = 7) -> dict:
    """최근 N일 체크인을 요약해서 재조정 입력으로 사용."""
    if not checkins:
        return {"days": days, "count": 0, "avg_completion_rate": 0, "patterns": []}

    cutoff = date.today() - timedelta(days=days - 1)
    recent = [c for c in checkins if datetime.fromisoformat(c["date"]).date() >= cutoff]

    if not recent:
        return {"days": days, "count": 0, "avg_completion_rate": 0, "patterns": ["최근 체크인 없음"]}

    rates = []
    low_days = []
    for c in recent:
        done, total, rate = compute_daily_completion(c)
        rates.append(rate)
        if rate < 50:
            low_days.append({"date": c["date"][:10], "done": done, "total": total, "rate": round(rate, 1)})

    avg_rate = sum(rates) / len(rates) if rates else 0

    patterns = []
    if avg_rate < 50:
        patterns.append("전반적으로 달성률이 낮음(50% 미만) → 난이도/빈도 조정 필요")
    elif avg_rate >= 80:
        patterns.append("달성률이 높음(80% 이상) → 유지 또는 소폭 상향 가능")
    else:
        patterns.append("중간 달성률(50~79%) → 유지하되 어려운 항목 미세 조정")

    if low_days:
        patterns.append(f"낮은 달성일 {len(low_days)}일 존재 → 시간대/빈도 낮추기 후보")

    # 어떤 항목이 자주 실패했는지
    habit_fail_counts: Dict[str, int] = {}
    habit_total_counts: Dict[str, int] = {}
    for c in recent:
        for it in c.get("items") or []:
            name = it.get("name", "habit")
            habit_total_counts[name] = habit_total_counts.get(name, 0) + 1
            if it.get("done") is False:
                habit_fail_counts[name] = habit_fail_counts.get(name, 0) + 1

    hard_habits = []
    for name, fail in sorted(habit_fail_counts.items(), key=lambda x: x[1], reverse=True):
        total = habit_total_counts.get(name, 1)
        fail_rate = fail / total
        if fail_rate >= 0.5 and total >= 3:
            hard_habits.append({"name": name, "fail_rate": round(fail_rate * 100, 1), "samples": total})

    if hard_habits:
        patterns.append("자주 미완료되는 습관: " + ", ".join([f"{h['name']}({h['fail_rate']}%)" for h in hard_habits]))

    return {
        "days": days,
        "count": len(recent),
        "avg_completion_rate": round(avg_rate, 1),
        "low_days": low_days,
        "hard_habits": hard_habits,
        "patterns": patterns,
    }


def build_7day_chart_df(checkins: List[dict]) -> pd.DataFrame:
    """최근 6일 + 오늘(있으면) 형태로 차트용 DF 구성. 없으면 데모."""
    today = date.today()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]  # 7일
    labels = [d.strftime("%m/%d") for d in dates]

    # date(str)-> rate
    rate_map = {}
    for c in checkins:
        d = datetime.fromisoformat(c["date"]).date()
        if d in dates:
            _, _, rate = compute_daily_completion(c)
            rate_map[d] = round(rate, 1)

    # 데모(체크인 없을 때)
    demo = [55, 70, 45, 80, 60, 75, 65]

    rates = []
    for i, d in enumerate(dates):
        rates.append(rate_map.get(d, demo[i] if not checkins else 0))

    return pd.DataFrame({"date": labels, "rate": rates})


# =========================================================
# UI 섹션: 1) 설문
# =========================================================
def section_survey():
    st.subheader("1) 분야 선택 + 세부 설문")
    st.write("먼저 **해결이 필요한 분야**를 고르고, 생활 패턴 설문을 작성해주세요.")

    with st.form("survey_form", clear_on_submit=False):
        domain = st.selectbox("🧭 해결이 필요한 분야", DOMAINS, index=0)
        habit_to_improve = st.text_input(
            "📝 개선하고 싶은 습관(구체적으로)",
            placeholder="예: 불규칙한 수면(새벽 3시에 잠듦), 잦은 야식, 스마트폰 스크롤링 3시간 이상 등",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            difficulty_pref = st.selectbox("난이도 선호", DIFFICULTY_PREF, index=0)
        with col2:
            priority = st.selectbox("가장 중요한 목표", PRIORITIES, index=0)
        with col3:
            available_time = st.multiselect("실행 가능한 시간대", TIME_WINDOWS, default=["아침(06-10)"])

        st.markdown("#### 생활 패턴(소그룹 설문)")
        c1, c2 = st.columns(2)
        with c1:
            sleep_time = st.selectbox("평소 취침 시간대", ["22-24", "00-02", "02-04", "04 이후"], index=1)
            wake_time = st.selectbox("평소 기상 시간대", ["05-07", "07-09", "09-11", "11 이후"], index=1)
            stress_level = st.slider("최근 스트레스 정도", 1, 10, 5)
        with c2:
            schedule_consistency = st.radio("일정 규칙성", ["매우 불규칙", "조금 불규칙", "보통", "규칙적"], index=1, horizontal=False)
            energy_level = st.slider("최근 에너지/컨디션", 1, 10, 6)
            commitment = st.slider("이번 주 실천 의지", 1, 10, 7)

        obstacles = st.multiselect(
            "실천을 방해하는 요인(복수 선택)",
            ["의지 부족", "시간 부족", "피로", "스트레스", "환경(야근/과제)", "유혹(야식/폰)", "기타"],
            default=["시간 부족"],
        )
        notes = st.text_area("추가 상황/제약(선택)", placeholder="예: 야근이 많아서 밤에만 시간이 남음 / 주 3회만 가능 등")

        submitted = st.form_submit_button("✅ 설문 저장")

    if submitted:
        if not habit_to_improve.strip():
            st.error("‘개선하고 싶은 습관’을 입력해야 플랜을 만들 수 있어요.")
            return

        st.session_state["profile"] = {
            "domain": domain,
            "habit_to_improve": habit_to_improve.strip(),
            "difficulty_pref": difficulty_pref,
            "priority": priority,
            "available_time_windows": available_time,
            "sleep_time": sleep_time,
            "wake_time": wake_time,
            "stress_level": stress_level,
            "schedule_consistency": schedule_consistency,
            "energy_level": energy_level,
            "commitment": commitment,
            "obstacles": obstacles,
            "notes": notes.strip(),
        }
        st.success("설문이 저장됐어요! 이제 AI 플랜을 생성해볼까요?")
        st.session_state["step"] = 2
        st.session_state["sidebar_step"] = 2  # ✅ 사이드바 라디오도 같이 동기화
        scroll_to_top()
        st.rerun()

    # 저장된 설문 요약
    if st.session_state["profile"]:
        with st.expander("📌 현재 저장된 설문 보기"):
            st.json(st.session_state["profile"])


# =========================================================
# UI 섹션: 2) 플랜 생성/출력 + 리마인더 설정
# =========================================================
def section_plan():
    st.subheader("2) AI 플랜 생성 + 리마인더 설정")
    profile = st.session_state.get("profile") or {}
    if not profile:
        st.info("먼저 1단계에서 설문을 작성해주세요.")
        return

    # 핵심 요약
    st.markdown("#### 🎯 입력 요약")
    c1, c2, c3 = st.columns(3)
    c1.metric("분야", profile.get("domain", "-"))
    c2.metric("개선 습관", profile.get("habit_to_improve", "-")[:16] + ("…" if len(profile.get("habit_to_improve", "")) > 16 else ""))
    c3.metric("우선순위", profile.get("priority", "-"))

    # 플랜 생성
    gen_col1, gen_col2 = st.columns([1, 2], gap="large")
    with gen_col1:
        if st.button("🤖 AI 플랜 생성", use_container_width=True):
            with st.spinner("AI가 맞춤 케어 플랜을 생성 중..."):
                plan, raw = generate_plan_with_llm(
                    api_key=OPENAI_API_KEY,
                    model=model_name,
                    domain=profile["domain"],
                    habit_to_improve=profile["habit_to_improve"],
                    survey=profile,
                )
            if plan is None:
                st.error("플랜 생성에 실패했어요. (API Key/모델/네트워크 확인)")
                st.session_state["ui_error"] = raw[:1200]
            else:
                st.session_state["plan"] = plan
                st.session_state["plan_history"].append(
                    {"created_at": datetime.now().isoformat(timespec="seconds"), "plan": plan, "type": "generated"}
                )
                st.session_state["ui_error"] = ""
                # 초기 리마인더를 custom으로도 복사 (사용자가 수정할 수 있게)
                st.session_state["reminders_custom"] = list(plan.get("reminders", []) or [])
                st.success("플랜이 생성됐어요!")
                st.rerun()

    with gen_col2:
        if st.session_state.get("ui_error"):
            st.warning("최근 오류(디버그):")
            st.code(st.session_state["ui_error"], language="text")

    plan = st.session_state.get("plan")
    if not plan:
        st.info("AI 플랜을 생성하면 결과가 여기 표시돼요.")
        return

    # 플랜 출력
    st.markdown("### ✅ 맞춤 케어 플랜")
    left, right = st.columns([1.2, 1], gap="large")

    with left:
        st.markdown("#### 🧾 요약")
        st.write(plan.get("summary", "") or "-")

        st.markdown("#### 🔎 문제 포인트(pain points)")
        pps = plan.get("pain_points") or []
        if pps:
            for p in pps[:10]:
                st.write(f"- {p}")
        else:
            st.write("-")

        st.markdown("#### 🛠️ 해결책(solutions)")
        sols = plan.get("solutions") or []
        if sols:
            for s in sols[:10]:
                st.write(f"- {s}")
        else:
            st.write("-")

        st.markdown("#### 🌱 새 습관(new habits)")
        habits = plan.get("new_habits") or []
        if not habits:
            st.warning("new_habits가 비어있어요. AI 출력 문제일 수 있어요.")
        else:
            for idx, h in enumerate(habits, start=1):
                sch = h.get("schedule") or {}
                days = ", ".join(sch.get("days") or [])
                st.markdown(
                    f"""
**{idx}. {h.get('name','새 습관')}**  \n
- 이유: {h.get('why','-')}  \n
- 스케줄: {days} · {sch.get('time','09:00')} · 주 {sch.get('frequency_per_week',3)}회  \n
- 난이도: `{h.get('difficulty','easy')}`
"""
                )

    with right:
        st.markdown("#### ⏰ 리마인더(달력/알람 형태)")
        st.caption("MVP에서는 실제 푸시 알림 대신, 일정/알람 설정값을 저장하고 리스트/캘린더 형태로 보여줍니다.")

        # 리마인더 수정 UI
        reminders = st.session_state.get("reminders_custom") or []
        if not reminders:
            st.info("리마인더가 없어요. 아래에서 추가해보세요.")
        else:
            for i, r in enumerate(reminders):
                with st.container(border=True):
                    st.write(f"**{i+1}. {r.get('title','리마인더')}**")
                    c1, c2 = st.columns(2)
                    with c1:
                        new_time = st.text_input(f"시간(HH:MM) #{i+1}", value=r.get("time", "09:00"), key=f"rem_t_{i}")
                    with c2:
                        new_rr = st.text_input(f"반복 규칙(RRULE) #{i+1}", value=r.get("rrule", "FREQ=DAILY"), key=f"rem_r_{i}")
                    new_title = st.text_input(f"제목 #{i+1}", value=r.get("title", "리마인더"), key=f"rem_title_{i}")

                    # 저장 반영
                    r["time"] = new_time.strip()
                    r["rrule"] = new_rr.strip()
                    r["title"] = new_title.strip()

                    if st.button("🗑️ 삭제", key=f"rem_del_{i}", use_container_width=True):
                        reminders.pop(i)
                        st.session_state["reminders_custom"] = reminders
                        st.rerun()

        st.markdown("##### ➕ 리마인더 추가")
        with st.form("add_reminder"):
            title = st.text_input("제목", placeholder="예: 취침 루틴 시작")
            time_str = st.text_input("시간(HH:MM)", value="21:30")
            rrule = st.text_input("반복(RRULE)", value="FREQ=DAILY")
            add = st.form_submit_button("추가")

        if add:
            if not title.strip():
                st.error("제목을 입력하세요.")
            else:
                st.session_state["reminders_custom"].append({"title": title.strip(), "time": time_str.strip(), "rrule": rrule.strip()})
                st.success("리마인더를 추가했어요.")
                st.rerun()

        st.divider()
        st.markdown("#### 📅 7일 캘린더(리스트) 미리보기")
        # 아주 단순한 “캘린더 형태” 리스트(향후 캘린더 위젯으로 확장 가능)
        preview = []
        start = date.today()
        for d in [start + timedelta(days=i) for i in range(7)]:
            dow = WEEKDAYS[d.weekday()]
            for r in (st.session_state.get("reminders_custom") or []):
                rr = (r.get("rrule") or "").upper()
                if "FREQ=DAILY" in rr:
                    ok = True
                elif "FREQ=WEEKLY" in rr:
                    # BYDAY=Mon,Tue ... 가 있으면 해당 요일만
                    m = re.search(r"BYDAY=([A-Z,]+)", rr)
                    if m:
                        by = m.group(1).split(",")
                        ok = dow in by
                    else:
                        ok = True
                else:
                    ok = True
                if ok:
                    preview.append({"date": d.isoformat(), "dow": dow, "time": r.get("time", "09:00"), "title": r.get("title", "리마인더")})

        if preview:
            df = pd.DataFrame(preview)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("리마인더가 없어서 캘린더 미리보기가 비어있어요.")

    st.divider()
    st.markdown("### ✅ 다음 단계: 체크인")
    if st.button("➡️ 체크인 화면으로 이동", use_container_width=True):
        st.session_state["step"] = 3
        st.session_state["sidebar_step"] = 3  # ✅ 사이드바 라디오도 같이 동기화
        scroll_to_top()
        st.rerun()


# =========================================================
# UI 섹션: 3) 체크인 + 기록 + 개인화 재조정
# =========================================================
def section_checkin():
    st.subheader("3) 체크인(완료/미완료) + 기록 + 개인화 루프")
    plan = st.session_state.get("plan")
    if not plan:
        st.info("먼저 2단계에서 플랜을 생성해주세요.")
        return

    # 오늘 체크인 대상: new_habits 기반
    habits = plan.get("new_habits") or []
    if not habits:
        st.warning("플랜의 new_habits가 비어있어서 체크인을 만들 수 없어요. 플랜 재생성을 시도해보세요.")
        return

    # 체크인 폼
    st.markdown("#### ✅ 오늘 체크인")
    today_str = datetime.now().isoformat(timespec="seconds")
    mood = st.slider("오늘 컨디션/기분(1~10)", 1, 10, 6)

    items_state = []
    cols = st.columns(2, gap="large")
    for i, h in enumerate(habits):
        col = cols[i % 2]
        with col:
            with st.container(border=True):
                name = h.get("name", f"습관 {i+1}")
                done = st.checkbox(f"✅ {name}", key=f"ck_{i}")
                note = st.text_input("메모(선택)", key=f"note_{i}", placeholder="예: 시간 부족 / 너무 피곤함 / 의외로 쉬웠음")
                items_state.append({"name": name, "done": done, "note": note})

    if st.button("💾 오늘 체크인 저장", use_container_width=True):
        checkin = {
            "date": today_str,
            "mood": int(mood),
            "items": items_state,
        }
        st.session_state["checkins"].append(checkin)
        done, total, rate = compute_daily_completion(checkin)
        st.success(f"저장 완료! 오늘 달성: {done}/{total} ({rate:.1f}%)")
        st.rerun()

    st.divider()

    # 7일 달성률 차트
    st.markdown("#### 📊 최근 7일 달성률")
    df7 = build_7day_chart_df(st.session_state.get("checkins") or [])
    st.bar_chart(df7.set_index("date")["rate"])

    # 기록 목록
    st.markdown("#### 🗂️ 체크인 기록")
    checkins = st.session_state.get("checkins") or []
    if not checkins:
        st.info("아직 체크인 기록이 없어요. 오늘 체크인을 저장해보세요!")
    else:
        for idx, c in enumerate(reversed(checkins), start=1):
            d = c["date"][:19].replace("T", " ")
            done, total, rate = compute_daily_completion(c)
            with st.expander(f"🗓️ {d} · 달성 {done}/{total} ({rate:.1f}%) · 기분 {c.get('mood', '-')}/10"):
                for it in c.get("items") or []:
                    st.write(f"- {'✅' if it.get('done') else '❌'} {it.get('name')}" + (f"  · 메모: {it.get('note')}" if it.get("note") else ""))

    st.divider()

    # 개인화 재조정(기록 기반 규칙 + LLM 조정)
    st.markdown("#### 🔁 플랜 재조정(개인화 루프)")
    summary = summarize_checkins(checkins, days=7)

    c1, c2, c3 = st.columns(3)
    c1.metric("최근 7일 체크인", f"{summary.get('count', 0)}일")
    c2.metric("평균 달성률", f"{summary.get('avg_completion_rate', 0)}%")
    c3.metric("패턴", " / ".join((summary.get("patterns") or [])[:1]) if summary.get("patterns") else "-")

    with st.expander("📌 패턴/요약 상세 보기"):
        st.json(summary)

    # 기록 기반 규칙(로컬)으로 조정 노트 생성
    auto_note = []
    avg = summary.get("avg_completion_rate", 0)
    if avg < 50:
        auto_note.append("달성률이 낮아서 난이도/빈도/시간대를 더 현실적으로 낮추는 방향 권장")
    elif avg >= 80:
        auto_note.append("달성률이 높으니 유지 또는 소폭 상향(빈도 +1 등) 검토 가능")
    else:
        auto_note.append("중간 달성률이므로 어려운 항목 중심 미세 조정")

    hard = summary.get("hard_habits") or []
    if hard:
        auto_note.append("자주 실패하는 습관을 더 쉬운 대안 행동으로 분해하거나, 시간대를 옮기는 방향 추천")

    adjustment_note = st.text_area(
        "조정 메모(자동 제안 포함, 수정 가능)",
        value="; ".join(auto_note),
        help="이 메모와 체크인 요약을 기반으로 AI가 플랜을 재조정합니다.",
    )

    if st.button("🧠 AI로 플랜 재조정", use_container_width=True):
        with st.spinner("AI가 체크인 패턴을 반영해 플랜을 재조정 중..."):
            new_plan, raw = adjust_plan_with_llm(
                api_key=OPENAI_API_KEY,
                model=model_name,
                current_plan=st.session_state["plan"],
                checkin_summary=summary,
                adjustment_note=adjustment_note,
            )

        if new_plan is None:
            st.error("플랜 재조정에 실패했어요. (API Key/모델/네트워크 확인)")
            st.session_state["ui_error"] = raw[:1200]
        else:
            st.session_state["plan"] = new_plan
            st.session_state["plan_history"].append(
                {"created_at": datetime.now().isoformat(timespec="seconds"), "plan": new_plan, "type": "adjusted"}
            )
            st.session_state["reminders_custom"] = list(new_plan.get("reminders", []) or [])
            st.session_state["last_adjustment_note"] = adjustment_note
            st.session_state["ui_error"] = ""
            st.success("플랜을 재조정했어요! (완료/미완료 패턴 반영)")
            st.rerun()

    if st.session_state.get("ui_error"):
        st.warning("최근 오류(디버그):")
        st.code(st.session_state["ui_error"], language="text")

    # 플랜 히스토리
    st.divider()
    st.markdown("#### 🧾 플랜 히스토리")
    hist = st.session_state.get("plan_history") or []
    if not hist:
        st.info("아직 플랜 히스토리가 없어요.")
    else:
        for i, h in enumerate(reversed(hist), start=1):
            with st.expander(f"{i}. {h.get('type','plan')} · {h.get('created_at','-')}"):
                st.json(h.get("plan") or {})


# =========================================================
# 메인 라우팅
# =========================================================
step = st.session_state["step"]

if step == 1:
    section_survey()
elif step == 2:
    section_plan()
else:
    section_checkin()

# step 라디오와 탭을 함께 쓸 때 UX 보완 (원하면 주석 처리 가능)
# 사용자가 사이드바 step을 바꾸면 해당 탭을 “강제로” 옮기긴 어렵지만,
# 내용은 모두 보이므로 탭/라디오 중 하나만 쓰도록 커스터마이징 가능.

# =========================================================
# 하단: 안내 / SQLite 확장 설계
# =========================================================
with st.expander("ℹ️ 실행 방법 / 키 입력 / SQLite 확장 설계"):
    st.markdown(
        """
### 실행 방법
1) 패키지 설치
```bash
pip install streamlit pandas openai
"""
)
