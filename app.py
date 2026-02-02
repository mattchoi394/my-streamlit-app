import streamlit as st
import requests
from datetime import datetime
from typing import Dict, List, Tuple, Optional

st.set_page_config(page_title="🎬 나와 어울리는 영화는?", page_icon="🎬", layout="wide")

TMDB_BASE = "https://api.themoviedb.org/3"

# =========================
# 장르/분석 설정
# =========================
CATEGORY_TO_GENRE_IDS = {
    "로맨스/드라마": [10749, 18],
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
    "로맨스/드라마": "감정선과 관계에 공감하는 선택이 많아서, 몰입감 있는 **드라마/로맨스**가 잘 맞아요 💕",
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

def analyze_genre(selected_indices: List[int]) -> Tuple[str, List[int], Dict[str, int], Optional[str]]:
    counts = {k: 0 for k in CATEGORY_TO_GENRE_IDS.keys()}
    for idx in selected_indices:
        counts[INDEX_TO_CATEGORY[idx]] += 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_cat, top_score = ranked[0]
    second_cat, second_score = ranked[1]

    blended = None
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
    r = requests.get(f"{TMDB_BASE}/movie/{movie_id}", params={"api_key": api_key, "language": language}, timeout=15)
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
    """
    개인 취향 점수 = (선호도 기반) + (슬라이더 가중치 적용 최신성/평점/투표수)
    - w_*는 0~100 입력을 0~1로 정규화해서 사용
    """
    rating = float(movie.get("vote_average") or 0.0)    # 0~10
    vote_count = float(movie.get("vote_count") or 0.0)  # large
    release_date = parse_date_yyyymmdd(movie.get("release_date") or "")

    # 선호도(0~1): 해당 장르를 고른 비율
    pref_weight = float(chosen_counts.get(primary_category, 0)) / 5.0

    # 최신성(0~1): 최근일수록 높음 (1년 기준 감쇠)
    recency = 0.0
    if release_date:
        days = max((datetime.now() - release_date).days, 0)
        recency = max(0.0, 1.0 - (days / 365.0))

    # 투표수(0~1): sqrt로 완화 + 캡
    vote_component = 0.0
    if vote_count > 0:
        vote_component = min(1.0, (vote_count ** 0.5) / 200.0)

    # 평점(0~1)
    rating_component = max(0.0, min(1.0, rating / 10.0))

    # 가중치 정규화(0~1)
    wr = w_recency / 100.0
    wra = w_rating / 100.0
    wv = w_votes / 100.0

    # 최종 점수 (선호도는 기본으로 1.5 비중)
    score = (
        (pref_weight * 1.5) +
        (recency * wr) +
        (rating_component * wra) +
        (vote_component * wv)
    )
    return score

def why_recommended_text(category: str) -> str:
    if category == "로맨스/드라마":
        return "감정선이 진하고 공감 포인트가 많아서, 바쁜 학기 중에도 몰입해서 보기 좋아요 💕"
    if category == "액션/어드벤처":
        return "전개가 빠르고 에너지가 확 올라가서, 스트레스 풀기 딱 좋아요 💥"
    if category == "SF/판타지":
        return "현실을 잠깐 잊고 세계관에 빠지기 좋아서, 머리 환기하기 좋아요 🚀"
    return "가볍게 웃고 넘어갈 수 있어서, 과제/시험 기간에도 부담 없이 보기 좋아요 😂"


# =========================
# UI
# =========================
st.title("🎬 나와 어울리는 영화는?")
st.write("질문 5개로 취향을 분석하고, 원하는 기준(최신성/평점/투표수)에 따라 추천을 조절해보세요 🎛️✨")

with st.sidebar:
    st.header("🔑 TMDB 설정")
    tmdb_key = st.text_input("TMDB API Key", type="password", placeholder="여기에 TMDB API Key 입력")
    st.divider()

    sort_label = st.selectbox("정렬 옵션", list(SORT_OPTIONS.keys()), index=0)

    st.subheader("🎛️ 개인 취향 가중치(슬라이더)")
    st.caption("‘개인 취향 가중치’ 정렬에서만 적용돼요.")

    w_recency = st.slider("최신성 가중치", 0, 100, 30, 5)
    w_rating = st.slider("평점 가중치", 0, 100, 50, 5)
    w_votes = st.slider("투표수 가중치", 0, 100, 20, 5)

    st.caption("팁: 평점↑ = 완성도 중심, 최신성↑ = 최신작 위주, 투표수↑ = 대중성/화제성 반영")

st.divider()

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

    # 3) 영화 가져오기 + 정렬
    sort_by, is_personal = SORT_OPTIONS[sort_label]
    with_genres = with_genres_or(genre_ids)

    with st.spinner("🎬 TMDB에서 영화를 불러오는 중..."):
        try:
            if is_personal:
                # 후보 많이 가져온 뒤 로컬 점수 재정렬
                candidates = discover_movies(
                    tmdb_key, with_genres,
                    sort_by="popularity.desc",
                    page=1, n=30
                )
                scored = []
                for m in candidates:
                    score = compute_personal_score(
                        m, category, counts,
                        w_recency=w_recency,
                        w_rating=w_rating,
                        w_votes=w_votes
                    )
                    scored.append((score, m))
                scored.sort(key=lambda x: x[0], reverse=True)
                movies = [m for _, m in scored[:5]]
            else:
                movies = discover_movies(
                    tmdb_key, with_genres,
                    sort_by=sort_by,
                    page=1, n=5
                )
        except requests.RequestException as e:
            st.error("TMDB 요청에 실패했어요. API Key/네트워크를 확인해주세요.")
            st.caption(f"에러: {e}")
            st.stop()

    if not movies:
        st.warning("추천할 영화를 찾지 못했어요. 다른 선택으로 다시 시도해보세요!")
        st.stop()

    header = "### 🍿 추천 영화 TOP 5"
    if blended:
        header += f" (취향 믹스: {blended})"
    header += f" · 정렬: {sort_label}"
    st.markdown(header)

    # 4) 3열 카드 + 상세
    cols = st.columns(3, gap="large")

    for i, m in enumerate(movies):
        col = cols[i % 3]
        movie_id = int(m.get("id"))
        title = m.get("title") or m.get("name") or "제목 없음"
        rating = float(m.get("vote_average") or 0.0)
        poster = build_poster_url(cfg, m.get("poster_path"))

        with col:
            with st.container(border=True):
                if poster:
                    st.image(poster, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")

                st.markdown(f"**{title}**")
                st.caption(f"⭐ 평점: {rating:.1f} / 10")

                with st.expander("📌 상세 정보 보기"):
                    with st.spinner("📚 상세 정보를 불러오는 중..."):
                        try:
                            detail = movie_details(tmdb_key, movie_id, language="ko-KR")
                        except Exception:
                            detail = {}

                    overview = (detail.get("overview") or m.get("overview") or "").strip() or "줄거리 정보가 없어요."
                    release_date = detail.get("release_date") or m.get("release_date") or "정보 없음"
                    runtime = detail.get("runtime")
                    genres = detail.get("genres") or []
                    genre_names = ", ".join(g.get("name") for g in genres if g.get("name")) or "정보 없음"
                    vote_count = detail.get("vote_count") or m.get("vote_count") or "정보 없음"

                    st.markdown(f"🗓️ **개봉일**: {release_date}")
                    st.markdown(f"🏷️ **장르**: {genre_names}")
                    st.markdown(f"🗳️ **투표수**: {vote_count}")
                    if runtime:
                        st.markdown(f"⏱️ **러닝타임**: {runtime}분")

                    st.markdown("📝 **줄거리**")
                    st.write(overview)

                    st.markdown("💡 **이 영화를 추천하는 이유**")
                    st.write(why_recommended_text(category))
