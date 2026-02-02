import json
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import requests
import streamlit as st
from openai import OpenAI  # pip install openai

# =========================
# Streamlit 설정
# =========================
st.set_page_config(page_title="🎬 나와 어울리는 영화는?", page_icon="🎬", layout="wide")
st.title("🎬 나와 어울리는 영화는?")
st.write("질문 5개로 취향을 분석하고, TMDB 추천 목록 중 **진짜 내가 좋아할 것 같은 영화 1개**를 AI가 최종 픽해줘요 🤖🍿")

TMDB_BASE = "https://api.themoviedb.org/3"

# =========================
# 장르/분석 설정
# =========================
# ✅ 수정 포인트: 로맨스/드라마 → 로맨스(10749)만 사용
CATEGORY_TO_GENRE_IDS = {
    "로맨스/드라마": [10749],
    "액션/어드벤처": [28],
    "SF/판타지": [878, 14],
    "코미디": [35],
}

INDEX_TO_CATEGORY = {0: "로맨스/드라마", 1: "액션/어드벤처", 2: "SF/판타지", 3: "코미디"}

CATEGORY_BADGE = {
    "로맨스/드라마": "💕",
    "액션/어드벤처": "💥",
    "SF/판타지": "🚀",
    "코미디": "😂",
}

REASON_BY_CATEGORY = {
    "로맨스/드라마": "관계/감정선을 중요하게 여기는 선택이 많아서, TMDB 기준 **로맨스 영화(10749)** 위주로 추천할게요 💕",
    "액션/어드벤처": "스케일과 추진력을 선호하는 선택이 많아서, 시원한 전개가 있는 **액션/어드벤처**가 잘 맞아요 💥",
    "SF/판타지": "상상력과 세계관을 즐기는 선택이 많아서, 다른 세계로 떠나는 **SF/판타지**가 잘 맞아요 🚀",
    "코미디": "가볍게 즐기고 웃는 포인트를 중요하게 여겨서, 기분전환 되는 **코미디**가 잘 맞아요 😂",
}

SORT_OPTIONS = {
    "인기순 (TMDB)": ("popularity.desc", False),
    "평점 높은순 (TMDB)": ("vote_average.desc", False),
    "최신 개봉순 (TMDB)": ("primary_release_date.desc", False),
    "투표수 많은순 (TMDB)": ("vote_count.desc", False),
    "개인 취향 가중치 (로컬 점수)": (None, True),
}

# =========================
# 사이드바: API 키/옵션
# =========================
with st.sidebar:
    st.header("🔑 API 설정")

    tmdb_key = st.text_input("TMDB API Key", type="password", placeholder="TMDB API Key 입력")
    openai_key = st.text_input("OpenAI API Key", type="password", placeholder="OpenAI API Key 입력")

    st.divider()
    sort_label = st.selectbox("정렬 옵션", list(SORT_OPTIONS.keys()), index=0)

    st.subheader("🎛️ 개인 취향 가중치(슬라이더)")
    st.caption("‘개인 취향 가중치(로컬 점수)’ 정렬에서만 적용돼요.")
    w_recency = st.slider("최신성 가중치", 0, 100, 30, 5)
    w_rating = st.slider("평점 가중치", 0, 100, 50, 5)
    w_votes = st.slider("투표수 가중치", 0, 100, 20, 5)

    st.divider()
    st.subheader("🤖 최종 1개 AI 추천")
    st.caption("TMDB 추천 5개 중에서, AI가 당신 취향에 가장 맞는 영화 1개를 최종 선택해요.")
    llm_strict = st.toggle("엄격 선택(정확히 1개만)", value=True)
    llm_model = st.text_input("OpenAI 모델", value="gpt-4o-mini")

st.divider()

# =========================
# 분석/유틸 함수
# =========================
def analyze_genre(selected_indices: List[int]) -> Tuple[str, List[int], Dict[str, int], Optional[str]]:
    counts = {k: 0 for k in CATEGORY_TO_GENRE_IDS.keys()}
    for idx in selected_indices:
        counts[INDEX_TO_CATEGORY[idx]] += 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_cat, top_score = ranked[0]
    second_cat, second_score = ranked[1]

    blended = None
    # 동점 또는 1점 차이면 OR로 섞기
    if top_score == second_score or (top_score - second_score == 1):
        blended = f"{top_cat} + {second_cat}"
        genre_ids = list(set(CATEGORY_TO_GENRE_IDS[top_cat] + CATEGORY_TO_GENRE_IDS[second_cat]))
        return top_cat, genre_ids, counts, blended

    return top_cat, CATEGORY_TO_GENRE_IDS[top_cat], counts, None


def with_genres_or(genre_ids: List[int]) -> str:
    return "|".join(str(g) for g in genre_ids)


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def tmdb_get_configuration(api_key: str) -> Dict:
    r = requests.get(f"{TMDB_BASE}/configuration", params={"api_key": api_key}, timeout=15)
    r.raise_for_status()
    return r.json()


def pick_poster_size(cfg: Dict, prefer: str = "w500") -> str:
    sizes = (cfg.get("images") or {}).get("poster_sizes") or []
    if prefer in sizes:
        return prefer
    for candidate in ["w500", "w342", "w780", "original"]:
        if candidate in sizes:
            return candidate
    return "w500"


def build_poster_url(cfg: Dict, poster_path: Optional[str]) -> Optional[str]:
    if not poster_path:
        return None
    images = cfg.get("images") or {}
    base = images.get("secure_base_url") or images.get("base_url")
    if not base:
        return "https://image.tmdb.org/t/p/w500" + poster_path
    size = pick_poster_size(cfg, "w500")
    return f"{base}{size}{poster_path}"


@st.cache_data(show_spinner=False, ttl=300)
def discover_movies(
    api_key: str,
    with_genres: str,
    language: str = "ko-KR",
    sort_by: str = "popularity.desc",
    page: int = 1,
    n: int = 20,
) -> List[Dict]:
    params = {
        "api_key": api_key,
        "with_genres": with_genres,
        "language": language,
        "sort_by": sort_by,
        "include_adult": "false",
        "page": page,
    }
    r = requests.get(f"{TMDB_BASE}/discover/movie", params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return (data.get("results") or [])[:n]


@st.cache_data(show_spinner=False, ttl=60 * 60)
def movie_details(api_key: str, movie_id: int, language: str = "ko-KR") -> Dict:
    r = requests.get(
        f"{TMDB_BASE}/movie/{movie_id}",
        params={"api_key": api_key, "language": language},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def parse_date_yyyymmdd(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


def compute_personal_score(
    movie: Dict,
    primary_category: str,
    chosen_counts: Dict[str, int],
    w_recency: float,
    w_rating: float,
    w_votes: float,
) -> float:
    rating = float(movie.get("vote_average") or 0.0)  # 0~10
    vote_count = float(movie.get("vote_count") or 0.0)
    release_date = parse_date_yyyymmdd(movie.get("release_date") or "")

    # 선호도(0~1)
    pref_weight = float(chosen_counts.get(primary_category, 0)) / 5.0

    # 최신성(0~1): 1년 기준 감쇠
    recency = 0.0
    if release_date:
        days = max((datetime.now() - release_date).days, 0)
        recency = max(0.0, 1.0 - (days / 365.0))

    # 투표수(0~1): sqrt 완화 + 캡
    vote_component = 0.0
    if vote_count > 0:
        vote_component = min(1.0, (vote_count ** 0.5) / 200.0)

    # 평점(0~1)
    rating_component = max(0.0, min(1.0, rating / 10.0))

    # 가중치(0~1)
    wr = w_recency / 100.0
    wra = w_rating / 100.0
    wv = w_votes / 100.0

    score = (pref_weight * 1.5) + (recency * wr) + (rating_component * wra) + (vote_component * wv)
    return score


def why_recommended_text(category: str) -> str:
    if category == "로맨스/드라마":
        return "TMDB 로맨스(10749) 기준으로 **설레거나 감정선이 살아있는 로맨스 영화** 위주로 골랐어요 💕"
    if category == "액션/어드벤처":
        return "전개가 빠르고 에너지가 확 올라가서, 스트레스 풀기 딱 좋아요 💥"
    if category == "SF/판타지":
        return "현실을 잠깐 잊고 세계관에 빠지기 좋아서, 머리 환기하기 좋아요 🚀"
    return "가볍게 웃고 넘어갈 수 있어서, 과제/시험 기간에도 부담 없이 보기 좋아요 😂"


def safe_json_extract(text: str) -> Optional[dict]:
    """LLM 출력에서 JSON만 최대한 뽑아오기(방어적 파싱)."""
    if not text:
        return None
    # 1) 코드블록 JSON 우선
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 2) 첫 { ... } 덩어리 파싱
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        blob = m.group(1)
        try:
            return json.loads(blob)
        except Exception:
            # trailing comma 등 가벼운 오류 보정 시도
            try:
                blob2 = re.sub(r",\s*}", "}", blob)
                blob2 = re.sub(r",\s*]", "]", blob2)
                return json.loads(blob2)
            except Exception:
                return None
    return None


def llm_pick_one_movie(
    openai_api_key: str,
    model: str,
    user_profile: Dict,
    candidates: List[Dict],
    strict_one: bool = True,
) -> Tuple[Optional[int], str]:
    """
    candidates: [{id,title,vote_average,vote_count,release_date,overview,genres(list[str])}, ...]
    반환: (movie_id, explanation_markdown)
    """
    client = OpenAI(api_key=openai_api_key)

    # 후보를 너무 길게 보내지 않도록 요약
    compact = []
    for c in candidates:
        compact.append(
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "vote_average": c.get("vote_average"),
                "vote_count": c.get("vote_count"),
                "release_date": c.get("release_date"),
                "genres": c.get("genres", []),
                "overview": c.get("overview", "")[:600],
            }
        )

    system = (
        "너는 대학생 사용자의 '영화 취향 심리테스트' 결과를 바탕으로, "
        "주어진 후보 영화들 중에서 사용자가 '진짜 좋아할 확률'이 가장 높은 영화 1개를 고르는 추천 전문가야. "
        "사용자의 취향(장르 성향/선호 가중치/분위기)을 최우선으로 반영하고, "
        "가능하면 '입문 난이도(부담 없는 선택)'와 '만족도'를 함께 고려해."
    )

    # 엄격 모드면 1개만, 아니면 1개 + 대안 1개를 같이 제안할 수도 있는데
    # 이번 요구사항은 "단 한 개"라서 기본 strict=True로 두고, 항상 1개만 반환하도록 지시.
    strict_rule = (
        "반드시 후보 중 정확히 1개의 id만 선택해. 다른 영화는 추천하지 마."
        if strict_one
        else "가능하면 1개를 선택하되, 정말 동률이면 1개를 선택하고 그 이유를 더 설득력 있게 써."
    )

    user = {
        "instruction": "후보 영화 중 최종 추천 1개를 선택해줘.",
        "user_profile": user_profile,
        "candidates": compact,
        "output_format": {
            "movie_id": "number (must be one of candidates.id)",
            "reason": "string (Korean, 2~4문장, 구체적으로)",
            "why_youll_like": ["string", "string", "string"],  # 3개 불릿
        },
        "rules": [
            strict_rule,
            "출력은 반드시 JSON만. 다른 텍스트/설명/코드블록 금지.",
            "reason/why_youll_like는 한국어로.",
        ],
    }

    # Responses API 사용 (권장)
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    )

    # 응답 텍스트 추출(최대한 호환적으로)
    text_out = ""
    try:
        # 최신 SDK는 output_text 제공
        text_out = resp.output_text
    except Exception:
        # fallback: 구조 탐색
        try:
            for item in resp.output:
                if item.type == "message":
                    for c in item.content:
                        if c.type == "output_text":
                            text_out += c.text
        except Exception:
            text_out = ""

    data = safe_json_extract(text_out)
    if not data:
        return None, "🤖 최종 추천을 만들지 못했어요. (LLM 출력 파싱 실패)"

    movie_id = data.get("movie_id")
    reason = data.get("reason", "")
    bullets = data.get("why_youll_like", [])
    if not isinstance(bullets, list):
        bullets = []

    md = "### 🤖 AI 최종 추천 이유\n"
    if reason:
        md += f"- {reason}\n"
    if bullets:
        md += "\n**✅ 당신이 좋아할 포인트**\n"
        for b in bullets[:3]:
            md += f"- {b}\n"

    try:
        return int(movie_id), md
    except Exception:
        return None, "🤖 최종 추천을 만들지 못했어요. (movie_id 오류)"


# =========================
# 질문 5개
# =========================
q1_options = [
    "💕 좋아하는 사람과 카페에서 오래 얘기하기",
    "💥 친구들이랑 바로 여행이나 액티비티 떠나기",
    "🚀 게임·영화 몰아보면서 다른 세계로 도피하기",
    "😂 웃긴 영상 보면서 아무 생각 없이 쉬기",
]
q2_options = [
    "💕 감정이입 되는 영화나 드라마 보며 울기",
    "💥 운동하거나 몸을 많이 움직이기",
    "🚀 상상력 자극하는 콘텐츠에 빠져들기",
    "😂 친구랑 수다 떨거나 웃긴 거 찾기",
]
q3_options = [
    "💕 현실 공감 100% 인간관계 이야기",
    "💥 스케일 크고 박진감 넘치는 영화",
    "🚀 세계관이 탄탄한 판타지나 미래 이야기",
    "😂 가볍게 웃으면서 볼 수 있는 영화",
]
q4_options = [
    "💕 감정이 섬세하고 관계가 중요한 인물",
    "💥 위기마다 활약하는 히어로",
    "🚀 특별한 능력을 가진 존재",
    "😂 주변 분위기를 살리는 웃긴 캐릭터",
]
q5_options = [
    "💕 공감 잘 해주고 이야기를 잘 들어준다",
    "💥 추진력 있고 같이 있으면 든든하다",
    "🚀 독특하고 생각이 깊다",
    "😂 같이 있으면 웃을 일이 많다",
]

q1 = st.radio("1) 시험 끝나고 가장 하고 싶은 건?", q1_options, index=0)
q2 = st.radio("2) 스트레스가 쌓였을 때 너의 반응은?", q2_options, index=0)
q3 = st.radio("3) 주말에 영화 한 편을 본다면?", q3_options, index=0)
q4 = st.radio("4) 영화 속 주인공이 된다면 더 끌리는 역할은?", q4_options, index=0)
q5 = st.radio("5) 친구들이 말하는 나의 이미지와 가장 가까운 건?", q5_options, index=0)

st.divider()

selected_indices = [
    q1_options.index(q1),
    q2_options.index(q2),
    q3_options.index(q3),
    q4_options.index(q4),
    q5_options.index(q5),
]


# =========================
# 결과 보기
# =========================
if st.button("🔮 결과 보기"):
    if not tmdb_key:
        st.error("TMDB API Key를 사이드바에 입력해주세요! 🔑")
        st.stop()
    if not openai_key:
        st.error("OpenAI API Key를 사이드바에 입력해주세요! 🔑")
        st.stop()

    # 1) 분석
    with st.spinner("🧠 답변을 분석 중..."):
        category, genre_ids, counts, blended = analyze_genre(selected_indices)

    badge = CATEGORY_BADGE[category]
    st.markdown(f"## 🎯 당신에게 딱인 장르는: **{badge} {category}**!")
    st.info(REASON_BY_CATEGORY[category])
    st.caption(f"📊 선택 분포: {counts}")

    # 2) 포스터 설정
    with st.spinner("🖼️ 포스터 설정을 불러오는 중..."):
        try:
            cfg = tmdb_get_configuration(tmdb_key)
        except requests.RequestException:
            cfg = {"images": {"secure_base_url": "https://image.tmdb.org/t/p/", "poster_sizes": ["w500"]}}

    # 3) TMDB 추천 5개 만들기 (정렬 옵션 반영)
    sort_by, is_personal = SORT_OPTIONS[sort_label]
    with_genres = with_genres_or(genre_ids)

    with st.spinner("🎬 TMDB에서 추천 영화를 불러오는 중..."):
        try:
            if is_personal:
                candidates = discover_movies(
                    tmdb_key,
                    with_genres,
                    sort_by="popularity.desc",
                    page=1,
                    n=40,
                )
                scored = []
                for m in candidates:
                    score = compute_personal_score(
                        m,
                        category,
                        counts,
                        w_recency=w_recency,
                        w_rating=w_rating,
                        w_votes=w_votes,
                    )
                    scored.append((score, m))
                scored.sort(key=lambda x: x[0], reverse=True)
                movies = [m for _, m in scored[:5]]
            else:
                movies = discover_movies(tmdb_key, with_genres, sort_by=sort_by, page=1, n=5)
        except requests.RequestException as e:
            st.error("TMDB 요청에 실패했어요. API Key/네트워크를 확인해주세요.")
            st.caption(f"에러: {e}")
            st.stop()

    if not movies:
        st.warning("추천할 영화를 찾지 못했어요. 다른 선택으로 다시 시도해보세요!")
        st.stop()

    header = "### 🍿 TMDB 추천 후보 TOP 5"
    if blended:
        header += f" (취향 믹스: {blended})"
    header += f" · 정렬: {sort_label}"
    st.markdown(header)

    # 4) 후보 영화 상세를 LLM 입력용으로 준비 (필요 최소 호출)
    #    - overview/genres 등은 movie_details에서 더 정확한 값을 얻을 수 있음
    llm_candidates = []
    with st.spinner("📚 후보 영화 상세 정보를 정리 중..."):
        for m in movies:
            mid = int(m.get("id"))
            try:
                d = movie_details(tmdb_key, mid, language="ko-KR")
            except Exception:
                d = {}

            merged = {**m, **d}  # detail 우선
            genres = merged.get("genres") or []
            genre_names = [g.get("name") for g in genres if isinstance(g, dict) and g.get("name")]

            llm_candidates.append(
                {
                    "id": mid,
                    "title": merged.get("title") or "제목 없음",
                    "vote_average": float(merged.get("vote_average") or 0.0),
                    "vote_count": int(merged.get("vote_count") or 0),
                    "release_date": merged.get("release_date") or "",
                    "overview": (merged.get("overview") or "").strip(),
                    "genres": genre_names,
                    "poster_path": merged.get("poster_path"),
                }
            )

    # 5) 🤖 LLM 최종 1개 픽
    user_profile = {
        "primary_category": category,
        "category_counts": counts,
        "selected_choices": {
            "q1": q1,
            "q2": q2,
            "q3": q3,
            "q4": q4,
            "q5": q5,
        },
        "sorting_mode": sort_label,
        "personal_weights": {"recency": w_recency, "rating": w_rating, "votes": w_votes},
        "note": "대학생 대상, 부담 없이 즐길 수 있는 만족도 높은 1편을 골라줘.",
    }

    with st.spinner("🤖 AI가 ‘진짜 취향저격’ 영화 1개를 고르는 중..."):
        picked_id, picked_md = llm_pick_one_movie(
            openai_api_key=openai_key,
            model=llm_model,
            user_profile=user_profile,
            candidates=llm_candidates,
            strict_one=llm_strict,
        )

    # 6) 최종 추천 표시
    if picked_id is None:
        st.error("AI 최종 추천을 만들지 못했어요. (대신 후보 목록만 보여줄게요)")
    else:
        picked = next((x for x in llm_candidates if x["id"] == picked_id), None)
        if not picked:
            st.error("AI가 고른 영화가 후보에 없어요. (대신 후보 목록만 보여줄게요)")
        else:
            st.markdown("## ⭐ 최종 추천 1편")
            poster = build_poster_url(cfg, picked.get("poster_path"))
            left, right = st.columns([1, 2], gap="large")
            with left:
                if poster:
                    st.image(poster, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")
            with right:
                st.markdown(f"### 🎬 {picked['title']}")
                st.markdown(f"⭐ 평점: **{picked['vote_average']:.1f}** / 10")
                rd = picked.get("release_date") or "정보 없음"
                st.markdown(f"🗓️ 개봉일: {rd}")
                if picked.get("genres"):
                    st.markdown(f"🏷️ 장르: {', '.join(picked['genres'])}")
                st.markdown(picked_md)

                with st.expander("📝 줄거리 보기"):
                    st.write(picked.get("overview") or "줄거리 정보가 없어요.")

            st.divider()

    # 7) 후보 5개 카드(3열) 표시 + 상세
    st.markdown("### 🧩 후보 5개 전체 보기")
    cols = st.columns(3, gap="large")
    for i, c in enumerate(llm_candidates):
        col = cols[i % 3]
        title = c.get("title") or "제목 없음"
        rating = float(c.get("vote_average") or 0.0)
        poster = build_poster_url(cfg, c.get("poster_path"))

        is_picked = (picked_id is not None and c["id"] == picked_id)
        badge = "✅ 최종 픽" if is_picked else "후보"

        with col:
            with st.container(border=True):
                if poster:
                    st.image(poster, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")

                st.markdown(f"**{title}**")
                st.caption(f"⭐ 평점: {rating:.1f} / 10 · {badge}")

                with st.expander("📌 상세 정보 보기"):
                    st.markdown(f"💡 **추천 이유(장르 기반)**: {why_recommended_text(category)}")
                    if c.get("release_date"):
                        st.markdown(f"🗓️ **개봉일**: {c['release_date']}")
                    if c.get("genres"):
                        st.markdown(f"🏷️ **장르**: {', '.join(c['genres'])}")
                    st.markdown("📝 **줄거리**")
                    st.write(c.get("overview") or "줄거리 정보가 없어요.")
