import streamlit as st
import requests

st.set_page_config(page_title="🎬 나와 어울리는 영화는?", page_icon="🎬", layout="wide")

# =========================
# TMDB 설정
# =========================
GENRE_IDS = {
    "로맨스/드라마": 18,     # 기본: 드라마
    "액션/어드벤처": 28,
    "SF/판타지": 878,        # 기본: SF
    "코미디": 35,
}

# 각 질문의 4개 선택지 순서:
# 0: 로맨스/드라마, 1: 액션/어드벤처, 2: SF/판타지, 3: 코미디
INDEX_TO_CATEGORY = {
    0: "로맨스/드라마",
    1: "액션/어드벤처",
    2: "SF/판타지",
    3: "코미디",
}

REASON_BY_CATEGORY = {
    "로맨스/드라마": "감정선과 관계에 공감하는 선택이 많아서, 몰입감 있는 **드라마/로맨스**가 잘 맞아요 💕",
    "액션/어드벤처": "스케일과 추진력을 선호하는 선택이 많아서, 시원한 전개가 있는 **액션/어드벤처**가 잘 맞아요 💥",
    "SF/판타지": "상상력과 세계관을 즐기는 선택이 많아서, 다른 세계로 떠나는 **SF/판타지**가 잘 맞아요 🚀",
    "코미디": "가볍게 즐기고 웃는 포인트를 중요하게 여겨서, 기분전환 되는 **코미디**가 잘 맞아요 😂",
}

def analyze_genre(selected_indices):
    counts = {k: 0 for k in GENRE_IDS.keys()}
    for idx in selected_indices:
        cat = INDEX_TO_CATEGORY[idx]
        counts[cat] += 1

    best_category = max(counts, key=counts.get)
    genre_id = GENRE_IDS[best_category]
    return best_category, genre_id, counts


@st.cache_data(show_spinner=False, ttl=300)
def fetch_popular_movies_by_genre(api_key, genre_id, language="ko-KR", n=5):
    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": api_key,
        "with_genres": genre_id,
        "language": language,
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "page": 1,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return (data.get("results") or [])[:n]


def poster_url(poster_path):
    if not poster_path:
        return None
    return "https://image.tmdb.org/t/p/w500" + poster_path


def why_recommended_text(category):
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
st.write("질문 5개로 당신의 영화 취향(장르)을 분석하고, 그 장르의 인기 영화를 추천해드려요 🍿✨")

with st.sidebar:
    st.header("🔑 TMDB 설정")
    tmdb_key = st.text_input("TMDB API Key", type="password", placeholder="여기에 TMDB API Key 입력")
    st.caption("API Key는 저장되지 않아요. (세션 동안만 사용)")

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

# 선택지 인덱스
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

    with st.spinner("🧠 분석 중..."):
        category, genre_id, counts = analyze_genre(selected_indices)

    st.markdown(f"## 🏷️ 당신에게 딱인 장르는: **{category}**!")
    st.info(REASON_BY_CATEGORY[category])

    with st.spinner("🎁 TMDB에서 인기 영화를 불러오는 중..."):
        try:
            movies = fetch_popular_movies_by_genre(tmdb_key, genre_id, n=5)
        except requests.HTTPError as e:
            st.error("TMDB API 요청에 실패했어요. API Key가 올바른지 확인해주세요.")
            st.caption(f"에러: {e}")
            st.stop()
        except requests.RequestException as e:
            st.error("네트워크 오류로 TMDB 요청에 실패했어요. 잠시 후 다시 시도해주세요.")
            st.caption(f"에러: {e}")
            st.stop()

    if not movies:
        st.warning("추천할 영화를 찾지 못했어요. 다른 장르로 다시 시도해보세요!")
        st.stop()

    st.markdown("### 🍿 추천 영화 TOP 5")

    # 3열 카드 레이아웃
    cols = st.columns(3, gap="large")

    for i, m in enumerate(movies):
        col = cols[i % 3]

        title = m.get("title") or m.get("name") or "제목 없음"
        rating = m.get("vote_average", 0.0)
        overview = (m.get("overview") or "").strip() or "줄거리 정보가 없어요."
        purl = poster_url(m.get("poster_path"))

        with col:
            # 카드 느낌을 주기 위해 컨테이너(border=True)
            with st.container(border=True):
                if purl:
                    st.image(purl, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")

                st.markdown(f"**{title}**")
                st.caption(f"⭐ 평점: {rating:.1f} / 10")

                # 클릭(펼치기)하면 상세
                with st.expander("📌 상세 보기"):
                    st.markdown(f"📝 **줄거리**\n\n{overview}")
                    st.markdown(f"💡 **이 영화를 추천하는 이유**\n\n{why_recommended_text(category)}")

    st.caption(f"📊 선택 분포: {counts}")
