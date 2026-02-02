import streamlit as st
import requests
from typing import Dict, List, Tuple, Optional

# -------------------------
# Streamlit 기본 설정
# -------------------------
st.set_page_config(page_title="🎬 나와 어울리는 영화는?", page_icon="🎬", layout="wide")
st.title("🎬 나와 어울리는 영화는?")
st.write("질문 5개로 당신의 영화 취향(장르)을 분석하고, TMDB에서 인기 영화를 추천해드려요 🍿✨")

with st.sidebar:
    st.header("🔑 TMDB 설정")
    tmdb_key = st.text_input("TMDB API Key", type="password", placeholder="여기에 TMDB API Key 입력")
    st.caption("API Key는 저장되지 않아요. (세션 동안만 사용)")

st.divider()

# -------------------------
# (선택) tmdbsimple 사용 시도
# -------------------------
USE_TMDBSIMPLE = False
try:
    import tmdbsimple as tmdb  # type: ignore
    USE_TMDBSIMPLE = True
except Exception:
    USE_TMDBSIMPLE = False

# -------------------------
# 장르/분석 로직 (고도화)
# -------------------------
CATEGORY_TO_GENRE_IDS = {
    # "로맨스/드라마"는 로맨스(10749) + 드라마(18) 모두 후보로 둠
    "로맨스/드라마": [10749, 18],
    "액션/어드벤처": [28],
    "SF/판타지": [878, 14],
    "코미디": [35],
}

# 4지선다 인덱스(0~3) -> 카테고리
INDEX_TO_CATEGORY = {
    0: "로맨스/드라마",
    1: "액션/어드벤처",
    2: "SF/판타지",
    3: "코미디",
}

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

def analyze_genre(selected_indices: List[int]) -> Tuple[str, List[int], Dict[str, int], Optional[str]]:
    """
    선택 결과로 카테고리 점수 집계 후,
    1등 장르를 선택하되 동점/근접이면 2개 장르를 섞어 추천 폭을 넓힘(OR 조합).
    return:
      - primary_category
      - genre_ids_for_discover (여러 개일 수 있음: OR 조합)
      - counts
      - blended_category (있으면 "A + B" 형태, 없으면 None)
    """
    counts = {k: 0 for k in CATEGORY_TO_GENRE_IDS.keys()}
    for idx in selected_indices:
        counts[INDEX_TO_CATEGORY[idx]] += 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_cat, top_score = ranked[0]
    second_cat, second_score = ranked[1]

    # 고도화: 동점 또는 1점 차이면 장르를 섞어서(OR) 더 다양하게 추천
    blended = None
    if top_score == second_score or (top_score - second_score == 1):
        blended = f"{top_cat} + {second_cat}"
        genre_ids = list(set(CATEGORY_TO_GENRE_IDS[top_cat] + CATEGORY_TO_GENRE_IDS[second_cat]))
        return top_cat, genre_ids, counts, blended

    genre_ids = CATEGORY_TO_GENRE_IDS[top_cat]
    return top_cat, genre_ids, counts, None


def build_with_genres_param(genre_ids: List[int]) -> str:
    """
    with_genres에 여러 장르를 넣을 때:
    - OR: '28|35' 처럼 파이프(|)
    - AND: '28,35' 처럼 콤마(,)
    여기서는 '추천 폭을 넓히기' 목적이므로 OR(|) 사용.
    (comma/pipe 조합 의미는 discover 필터 설명에 존재 :contentReference[oaicite:3]{index=3})
    """
    return "|".join(str(g) for g in genre_ids)


# -------------------------
# TMDB API 호출 (configuration + discover + details)
# -------------------------
TMDB_BASE = "https://api.themoviedb.org/3"

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)  # 24h
def tmdb_get_configuration(api_key: str) -> Dict:
    """
    이미지 URL은 configuration에서 base_url/size 조합을 권장(캐시 권장) :contentReference[oaicite:4]{index=4}
    """
    url = f"{TMDB_BASE}/configuration"
    r = requests.get(url, params={"api_key": api_key}, timeout=15)
    r.raise_for_status()
    return r.json()

def pick_poster_size(cfg: Dict, prefer: str = "w500") -> str:
    sizes = (cfg.get("images") or {}).get("poster_sizes") or []
    if prefer in sizes:
        return prefer
    # 없으면 가능한 것 중 적당한 크기 선택
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
        # fallback
        return "https://image.tmdb.org/t/p/w500" + poster_path
    size = pick_poster_size(cfg, "w500")
    return f"{base}{size}{poster_path}"

@st.cache_data(show_spinner=False, ttl=300)
def discover_movies(api_key: str, with_genres: str, language: str = "ko-KR", n: int = 5) -> List[Dict]:
    url = f"{TMDB_BASE}/discover/movie"
    params = {
        "api_key": api_key,
        "with_genres": with_genres,
        "language": language,
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "page": 1,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return (data.get("results") or [])[:n]

@st.cache_data(show_spinner=False, ttl=60 * 60)  # 1h
def movie_details(api_key: str, movie_id: int, language: str = "ko-KR") -> Dict:
    # 상세 정보(런타임/장르/개봉일 등)
    url = f"{TMDB_BASE}/movie/{movie_id}"
    params = {
        "api_key": api_key,
        "language": language,
        # credits도 같이 가져오고 싶다면 아래 주석 해제
        # "append_to_response": "credits",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def make_reco_reason(category: str, movie: Dict) -> str:
    """
    간단 추천 이유: 장르 기반 + 영화 특징(평점/키워드) 조금 반영
    """
    rating = float(movie.get("vote_average") or 0.0)
    overview = (movie.get("overview") or "").lower()

    base = {
        "로맨스/드라마": "감정선에 몰입하기 좋고, 대학 생활의 관계 고민과도 공감 포인트가 있어요 💕",
        "액션/어드벤처": "전개가 빠르고 에너지 충전이 돼서, 스트레스 풀기 좋아요 💥",
        "SF/판타지": "세계관에 빠져 현실을 잠깐 잊고 머리 환기하기 좋아요 🚀",
        "코미디": "부담 없이 웃으면서 보기 좋아서 기분전환에 딱이에요 😂",
    }[category]

    # 평점 보너스 문구
    if rating >= 7.5:
        base += " (평점도 꽤 높아요 ⭐)"

    # 줄거리 키워드 기반 살짝 보정
    if category == "로맨스/드라마" and any(k in overview for k in ["사랑", "연애", "관계", "가족"]):
        base += " (내용도 감정선 중심!)"
    if category == "액션/어드벤처" and any(k in overview for k in ["전쟁", "추격", "미션", "탈출"]):
        base += " (액션 키워드가 딱!)"
    if category == "SF/판타지" and any(k in overview for k in ["우주", "미래", "마법", "괴물", "외계"]):
        base += " (세계관 취향 저격!)"
    if category == "코미디" and any(k in overview for k in ["웃", "코미디", "유쾌", "엉뚱"]):
        base += " (웃음 포인트 기대!)"

    return base


# -------------------------
# 질문 5개 UI
# -------------------------
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

# -------------------------
# 결과 보기 (예쁘게 + 고도화)
# -------------------------
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

    # 2) configuration (이미지 url 고도화)
    with st.spinner("🖼️ 포스터 설정을 불러오는 중..."):
        try:
            cfg = tmdb_get_configuration(tmdb_key)
        except requests.RequestException as e:
            st.warning("configuration을 불러오지 못해 기본 포스터 URL(w500)로 진행할게요.")
            cfg = {"images": {"secure_base_url": "https://image.tmdb.org/t/p/", "poster_sizes": ["w500"]}}
            st.caption(f"에러: {e}")

    # 3) discover
    with_genres = build_with_genres_param(genre_ids)
    discover_title = f"🎁 추천 영화 TOP 5"
    if blended:
        discover_title += f" (취향 믹스: {blended})"

    with st.spinner("🎬 TMDB에서 인기 영화를 불러오는 중..."):
        try:
            # tmdbsimple이 있으면 사용(선택), 없으면 requests 사용
            if USE_TMDBSIMPLE:
                tmdb.API_KEY = tmdb_key
                d = tmdb.Discover()
                # with_genres는 문자열로 전달 (예: "28|35")
                resp = d.movie(with_genres=with_genres, language="ko-KR", sort_by="popularity.desc")
                movies = (resp.get("results") or [])[:5]
            else:
                movies = discover_movies(tmdb_key, with_genres, n=5)
        except requests.HTTPError as e:
            st.error("TMDB API 요청에 실패했어요. API Key가 올바른지 확인해주세요.")
            st.caption(f"에러: {e}")
            st.stop()
        except Exception as e:
            st.error("TMDB 요청 중 오류가 발생했어요.")
            st.caption(f"에러: {e}")
            st.stop()

    if not movies:
        st.warning("추천할 영화를 찾지 못했어요. 다른 선택으로 다시 시도해보세요!")
        st.stop()

    st.markdown(f"### {discover_title}")

    # 4) 3열 카드 + expander 상세
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
                    # 상세 정보는 필요할 때만 로딩 (고도화: 불필요한 호출 줄이기)
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

                    st.markdown(f"🗓️ **개봉일**: {release_date}")
                    st.markdown(f"🏷️ **장르**: {genre_names}")
                    if runtime:
                        st.markdown(f"⏱️ **러닝타임**: {runtime}분")

                    st.markdown("📝 **줄거리**")
                    st.write(overview)

                    st.markdown("💡 **이 영화를 추천하는 이유**")
                    st.write(make_reco_reason(category, detail or m))

    st.caption("※ 인기순(popularity) 기반 추천이며, 동점/근접 점수일 때는 장르를 섞어서 더 폭넓게 추천해요.")
