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
st.write(
    "질문 5개로 취향을 분석하고, **TMDB에서 장르 적합성과 객관성(평점/투표수/필터)**을 강화해 추천해요 🍿✨\n"
    "그리고 마지막엔 **후보 5개 중 AI가 1편만 최종 픽**해줘요 🤖\n\n"
    "✅ 마음에 안 드는 영화가 나오면, 체크해서 **제외 후 다시 추천**할 수 있어요!"
)

TMDB_BASE = "https://api.themoviedb.org/3"

# =========================
# 장르/분석 설정
# =========================
CATEGORY_TO_GENRE_IDS = {
    "로맨스/드라마": [10749],  # 로맨스만
    "액션/어드벤처": [28],
    "SF/판타지": [878, 14],
    "코미디": [35],
}

INDEX_TO_CATEGORY = {0: "로맨스/드라마", 1: "액션/어드벤처", 2: "SF/판타지", 3: "코미디"}

CATEGORY_BADGE = {"로맨스/드라마": "💕", "액션/어드벤처": "💥", "SF/판타지": "🚀", "코미디": "😂"}

REASON_BY_CATEGORY = {
    "로맨스/드라마": "관계/감정선을 중요하게 여기는 선택이 많아서, TMDB 기준 **로맨스(10749)** 영화만 엄격하게 골라요 💕",
    "액션/어드벤처": "스케일과 추진력을 선호하는 선택이 많아서, 시원한 전개가 있는 **액션(28)** 중심으로 골라요 💥",
    "SF/판타지": "상상력/세계관을 즐기는 선택이 많아서, **SF(878)/판타지(14)** 중심으로 골라요 🚀",
    "코미디": "가볍게 즐기고 웃는 포인트를 중요하게 여겨서, **코미디(35)** 중심으로 골라요 😂",
}

SORT_OPTIONS = {
    "인기순 (TMDB)": ("popularity.desc", False),
    "평점 높은순 (TMDB)": ("vote_average.desc", False),
    "최신 개봉순 (TMDB)": ("primary_release_date.desc", False),
    "투표수 많은순 (TMDB)": ("vote_count.desc", False),
    "개인 취향 가중치 (로컬 점수)": (None, True),
}

# =========================
# 세션 상태 초기화(제외 목록/마지막 추천 캐시)
# =========================
if "excluded_movie_ids" not in st.session_state:
    st.session_state["excluded_movie_ids"] = set()
if "last_reco_context" not in st.session_state:
    st.session_state["last_reco_context"] = None  # 추천 재생성에 필요한 정보 저장
if "last_llm_candidates" not in st.session_state:
    st.session_state["last_llm_candidates"] = []
if "last_cfg" not in st.session_state:
    st.session_state["last_cfg"] = None
if "last_picked_id" not in st.session_state:
    st.session_state["last_picked_id"] = None
if "last_picked_md" not in st.session_state:
    st.session_state["last_picked_md"] = ""


# =========================
# 사이드바: API 키/옵션
# =========================
with st.sidebar:
    st.header("🔑 API 설정")
    tmdb_key = st.text_input("TMDB API Key", type="password", placeholder="TMDB API Key 입력")
    openai_key = st.text_input("OpenAI API Key", type="password", placeholder="OpenAI API Key 입력")

    st.divider()
    st.header("🎛️ 추천 품질(객관성/장르 적합성) 필터")
    min_vote_count = st.slider("최소 투표수(vote_count)", 0, 5000, 300, 50)
    min_vote_avg = st.slider("최소 평점(vote_average)", 0.0, 9.0, 6.5, 0.1)
    require_poster = st.toggle("포스터 있는 작품만", value=True)
    require_overview = st.toggle("줄거리 있는 작품만", value=True)

    strict_genre = st.toggle("장르 엄격 모드(추천 리스트)", value=True)
    st.caption(
        "- 켜짐: 후보 영화의 **상세 장르에 목표 장르가 실제 포함**된 것만 통과\n"
        "- 혼합 장르(동점/근접)일 때도 더 엄격하게 걸러요"
    )

    st.divider()
    st.header("🧭 정렬/가중치")
    sort_label = st.selectbox("정렬 옵션", list(SORT_OPTIONS.keys()), index=0)

    st.subheader("개인 취향 가중치(슬라이더)")
    st.caption("‘개인 취향 가중치(로컬 점수)’ 정렬에서만 적용돼요.")
    w_recency = st.slider("최신성 가중치", 0, 100, 30, 5)
    w_rating = st.slider("평점 가중치", 0, 100, 50, 5)
    w_votes = st.slider("투표수 가중치", 0, 100, 20, 5)

    st.divider()
    st.header("🤖 최종 1편 AI 픽")
    llm_model = st.text_input("OpenAI 모델", value="gpt-4o-mini")

    st.divider()
    st.header("🚫 제외 목록")
    st.caption("아래 버튼으로 제외한 영화들은 다음 추천에서 나오지 않아요.")
    if st.button("🧹 제외 목록 초기화"):
        st.session_state["excluded_movie_ids"] = set()
        st.success("제외 목록을 초기화했어요!")


# =========================
# 분석/유틸 함수
# =========================
def analyze_genre(selected_indices: List[int]) -> Tuple[str, List[int], Dict[str, int], Optional[str], Optional[str]]:
    counts = {k: 0 for k in CATEGORY_TO_GENRE_IDS.keys()}
    for idx in selected_indices:
        counts[INDEX_TO_CATEGORY[idx]] += 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_cat, top_score = ranked[0]
    second_cat, second_score = ranked[1]

    blended = None
    secondary = None

    if top_score == second_score or (top_score - second_score == 1):
        secondary = second_cat
        blended = f"{top_cat} + {second_cat}"
        genre_ids = list(set(CATEGORY_TO_GENRE_IDS[top_cat] + CATEGORY_TO_GENRE_IDS[second_cat]))
        return top_cat, genre_ids, counts, blended, secondary

    return top_cat, CATEGORY_TO_GENRE_IDS[top_cat], counts, None, None


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
    rating = float(movie.get("vote_average") or 0.0)
    vote_count = float(movie.get("vote_count") or 0.0)
    release_date = parse_date_yyyymmdd(movie.get("release_date") or "")

    pref_weight = float(chosen_counts.get(primary_category, 0)) / 5.0

    recency = 0.0
    if release_date:
        days = max((datetime.now() - release_date).days, 0)
        recency = max(0.0, 1.0 - (days / 365.0))

    vote_component = 0.0
    if vote_count > 0:
        vote_component = min(1.0, (vote_count ** 0.5) / 200.0)

    rating_component = max(0.0, min(1.0, rating / 10.0))

    wr = w_recency / 100.0
    wra = w_rating / 100.0
    wv = w_votes / 100.0

    score = (pref_weight * 1.5) + (recency * wr) + (rating_component * wra) + (vote_component * wv)
    return score


def passes_quality_filters(
    movie: Dict,
    cfg: Dict,
    min_vote_count: int,
    min_vote_avg: float,
    require_poster: bool,
    require_overview: bool,
) -> bool:
    if float(movie.get("vote_average") or 0.0) < float(min_vote_avg):
        return False
    if int(movie.get("vote_count") or 0) < int(min_vote_count):
        return False
    if require_overview and not (movie.get("overview") or "").strip():
        return False
    if require_poster and not movie.get("poster_path"):
        return False
    if require_poster and not build_poster_url(cfg, movie.get("poster_path")):
        return False
    return True


def movie_has_required_genres(detail: Dict, required_any: List[int], required_all: Optional[List[int]] = None) -> bool:
    genres = detail.get("genres") or []
    ids = {g.get("id") for g in genres if isinstance(g, dict)}
    if required_all:
        return all(g in ids for g in required_all)
    return any(g in ids for g in required_any)


def build_candidates_strict(
    api_key: str,
    cfg: Dict,
    with_genres: str,
    sort_by: str,
    primary_required_ids: List[int],
    secondary_required_ids: Optional[List[int]],
    strict_genre: bool,
    min_vote_count: int,
    min_vote_avg: float,
    require_poster: bool,
    require_overview: bool,
    excluded_ids: set,
    fetch_pages: int = 4,
    per_page_take: int = 20,
    target_n: int = 10,
) -> List[Dict]:
    picked: List[Dict] = []
    seen = set()

    required_all = None
    if strict_genre and secondary_required_ids:
        required_all = [primary_required_ids[0], secondary_required_ids[0]]

    for page in range(1, fetch_pages + 1):
        raw = discover_movies(api_key, with_genres, sort_by=sort_by, page=page, n=per_page_take)
        for m in raw:
            mid = int(m.get("id") or 0)
            if not mid or mid in seen or mid in excluded_ids:
                continue
            seen.add(mid)

            if not passes_quality_filters(m, cfg, min_vote_count, min_vote_avg, require_poster, require_overview):
                continue

            try:
                d = movie_details(api_key, mid, language="ko-KR")
            except Exception:
                continue

            required_any = primary_required_ids
            ok = movie_has_required_genres(d, required_any=required_any, required_all=required_all)
            if not ok:
                continue

            merged = {**m, **d}
            if not passes_quality_filters(merged, cfg, min_vote_count, min_vote_avg, require_poster, require_overview):
                continue

            picked.append(merged)
            if len(picked) >= target_n:
                return picked

    return picked


def why_recommended_text(category: str) -> str:
    if category == "로맨스/드라마":
        return "TMDB 로맨스(10749) 기준으로 **로맨스 장르가 실제 포함된 작품만** 엄격히 골랐어요 💕"
    if category == "액션/어드벤처":
        return "액션(28) 장르가 실제 포함된 작품만 엄격히 골랐어요 💥"
    if category == "SF/판타지":
        return "SF(878)/판타지(14) 장르가 실제 포함된 작품만 엄격히 골랐어요 🚀"
    return "코미디(35) 장르가 실제 포함된 작품만 엄격히 골랐어요 😂"


# =========================
# LLM 최종 1편 픽
# =========================
def safe_json_extract(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        blob = m.group(1)
        try:
            return json.loads(blob)
        except Exception:
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
) -> Tuple[Optional[int], str]:
    client = OpenAI(api_key=openai_api_key)

    compact = []
    for c in candidates:
        compact.append(
            {
                "id": c.get("id"),
                "title": c.get("title") or c.get("name"),
                "vote_average": c.get("vote_average"),
                "vote_count": c.get("vote_count"),
                "release_date": c.get("release_date"),
                "genres": c.get("genres", []),
                "overview": (c.get("overview") or "")[:650],
            }
        )

    system = (
        "너는 대학생 사용자의 심리테스트 결과를 바탕으로, 후보 영화 중 '가장 좋아할 확률'이 높은 영화 1편을 고르는 추천 전문가야. "
        "사용자의 성향(선택한 답변의 분위기/장르 선호)을 최우선으로, "
        "그 다음으로는 객관 지표(평점/투표수)와 접근성(부담 없는 선택)을 고려해."
    )

    payload = {
        "task": "pick_exactly_one",
        "user_profile": user_profile,
        "candidates": compact,
        "output_schema": {
            "movie_id": "number (must be one of candidates.id)",
            "reason": "string (Korean, 2~4 sentences, specific)",
            "why_youll_like": ["string", "string", "string"],
        },
        "rules": [
            "반드시 후보 중 정확히 1개의 id만 선택해.",
            "출력은 반드시 JSON만. 다른 텍스트/코드블록/설명 금지.",
            "한국어로 작성.",
        ],
    }

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    text_out = ""
    try:
        text_out = resp.output_text
    except Exception:
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
# 추천 실행 함수 (버튼 재사용용)
# =========================
def run_recommendation(reuse_context: Optional[dict] = None) -> None:
    """
    reuse_context가 있으면(=제외 후 다시 추천) 기존 분석/답변을 재사용
    """
    if not tmdb_key or not openai_key:
        st.error("TMDB/OpenAI API Key를 사이드바에 입력해주세요! 🔑")
        return

    if reuse_context:
        context = reuse_context
        category = context["category"]
        genre_ids = context["genre_ids"]
        counts = context["counts"]
        blended = context["blended"]
        secondary_category = context["secondary_category"]
        answers = context["answers"]
        sort_label_local = context["sort_label"]
        is_personal = context["is_personal"]
        sort_by = context["sort_by"]
        with_genres = context["with_genres"]
    else:
        # 새로 분석
        category, genre_ids, counts, blended, secondary_category = analyze_genre(selected_indices)
        answers = {"q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5}
        sort_by, is_personal = SORT_OPTIONS[sort_label]
        with_genres = with_genres_or(genre_ids)
        sort_label_local = sort_label

        context = {
            "category": category,
            "genre_ids": genre_ids,
            "counts": counts,
            "blended": blended,
            "secondary_category": secondary_category,
            "answers": answers,
            "sort_label": sort_label_local,
            "is_personal": is_personal,
            "sort_by": sort_by,
            "with_genres": with_genres,
        }
        st.session_state["last_reco_context"] = context

    badge = CATEGORY_BADGE[category]
    st.markdown(f"## 🎯 당신에게 딱인 장르는: **{badge} {category}**!")
    st.info(REASON_BY_CATEGORY[category])
    st.caption(f"📊 선택 분포: {counts}")

    # configuration
    with st.spinner("🖼️ 포스터 설정을 불러오는 중..."):
        try:
            cfg = tmdb_get_configuration(tmdb_key)
        except requests.RequestException:
            cfg = {"images": {"secure_base_url": "https://image.tmdb.org/t/p/", "poster_sizes": ["w500"]}}
    st.session_state["last_cfg"] = cfg

    # 후보 필터링
    primary_required = CATEGORY_TO_GENRE_IDS[category]
    secondary_required = CATEGORY_TO_GENRE_IDS.get(secondary_category) if secondary_category else None

    discover_sort_for_fetch = "popularity.desc" if is_personal else (sort_by or "popularity.desc")

    with st.spinner("🎬 TMDB에서 후보를 모으고, 장르/객관 필터로 엄격 선별 중..."):
        filtered = build_candidates_strict(
            api_key=tmdb_key,
            cfg=cfg,
            with_genres=with_genres,
            sort_by=discover_sort_for_fetch,
            primary_required_ids=primary_required,
            secondary_required_ids=secondary_required,
            strict_genre=strict_genre,
            min_vote_count=min_vote_count,
            min_vote_avg=min_vote_avg,
            require_poster=require_poster,
            require_overview=require_overview,
            excluded_ids=st.session_state["excluded_movie_ids"],
            fetch_pages=5,
            per_page_take=20,
            target_n=20 if is_personal else 8,
        )

    if not filtered:
        st.warning(
            "조건을 만족하는 영화를 찾지 못했어요 😢\n\n"
            "👉 해결 팁: 최소 평점/최소 투표수를 낮추거나, 포스터/줄거리 필수 옵션을 꺼보세요.\n"
            "또는 제외 목록이 너무 많다면 초기화해보세요."
        )
        return

    # 최종 후보 5개 결정
    if is_personal:
        scored = []
        for m in filtered:
            s = compute_personal_score(m, category, counts, w_recency, w_rating, w_votes)
            scored.append((s, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        movies = [m for _, m in scored[:5]]
    else:
        movies = filtered[:5]

    st.markdown(
        ("### 🍿 추천 영화 TOP 5 (장르/객관 필터 적용)" + (f" · 취향 믹스: {blended}" if blended else "") + f" · 정렬: {sort_label_local}")
    )
    st.caption(
        f"적용 필터: 평점 ≥ {min_vote_avg}, 투표수 ≥ {min_vote_count}"
        + (" · 포스터필수" if require_poster else "")
        + (" · 줄거리필수" if require_overview else "")
        + (" · 장르엄격" if strict_genre else "")
        + (f" · 제외 {len(st.session_state['excluded_movie_ids'])}개" if st.session_state["excluded_movie_ids"] else "")
    )

    # LLM 후보 준비
    llm_candidates = []
    for m in movies:
        genres = m.get("genres") or []
        genre_names = [g.get("name") for g in genres if isinstance(g, dict) and g.get("name")]
        llm_candidates.append(
            {
                "id": int(m.get("id")),
                "title": m.get("title") or "제목 없음",
                "vote_average": float(m.get("vote_average") or 0.0),
                "vote_count": int(m.get("vote_count") or 0),
                "release_date": m.get("release_date") or "",
                "overview": (m.get("overview") or "").strip(),
                "genres": genre_names,
                "poster_path": m.get("poster_path"),
            }
        )
    st.session_state["last_llm_candidates"] = llm_candidates

    # LLM 최종 픽
    user_profile = {
        "primary_category": category,
        "category_counts": counts,
        "selected_choices": answers,
        "sorting_mode": sort_label_local,
        "personal_weights": {"recency": w_recency, "rating": w_rating, "votes": w_votes},
        "quality_filters": {
            "min_vote_average": min_vote_avg,
            "min_vote_count": min_vote_count,
            "strict_genre": strict_genre,
            "require_poster": require_poster,
            "require_overview": require_overview,
        },
        "excluded_movie_ids": sorted(list(st.session_state["excluded_movie_ids"]))[:50],
        "note": "대학생 기준으로, 부담 없이 재미/만족도가 높을 1편을 골라줘.",
    }

    with st.spinner("🤖 AI가 후보 5개 중 ‘진짜 취향저격’ 1편을 고르는 중..."):
        picked_id, picked_md = llm_pick_one_movie(openai_key, llm_model, user_profile, llm_candidates)

    st.session_state["last_picked_id"] = picked_id
    st.session_state["last_picked_md"] = picked_md

    # 최종 추천 표시
    if picked_id:
        picked = next((x for x in llm_candidates if x["id"] == picked_id), None)
        if picked:
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
                st.markdown(f"🗳️ 투표수: **{picked['vote_count']}**")
                rd = picked.get("release_date") or "정보 없음"
                st.markdown(f"🗓️ 개봉일: {rd}")
                if picked.get("genres"):
                    st.markdown(f"🏷️ 장르: {', '.join(picked['genres'])}")
                st.markdown(picked_md)

                with st.expander("📝 줄거리 보기"):
                    st.write(picked.get("overview") or "줄거리 정보가 없어요.")

            st.divider()

    # 후보 5개 카드 + 제외 체크
    st.markdown("### 🧩 추천 리스트 TOP 5")
    cols = st.columns(3, gap="large")

    for i, c in enumerate(llm_candidates):
        col = cols[i % 3]
        title = c.get("title") or "제목 없음"
        rating = float(c.get("vote_average") or 0.0)
        poster = build_poster_url(cfg, c.get("poster_path"))
        is_picked = (picked_id is not None and c["id"] == picked_id)

        with col:
            with st.container(border=True):
                if poster:
                    st.image(poster, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")

                st.markdown(f"**{title}**")
                st.caption(
                    f"⭐ {rating:.1f} / 10 · 🗳️ {c.get('vote_count', 0)}"
                    + (" · ✅ 최종 픽" if is_picked else "")
                )

                # ✅ 제외 체크박스(후단 버튼으로 제외 확정)
                chk_key = f"exclude_chk_{c['id']}"
                default_checked = (c["id"] in st.session_state["excluded_movie_ids"])
                st.checkbox("🚫 이 영화는 제외", key=chk_key, value=default_checked)

                with st.expander("📌 상세 정보 보기"):
                    st.markdown(f"💡 **추천 기준**: {why_recommended_text(category)}")
                    if c.get("release_date"):
                        st.markdown(f"🗓️ **개봉일**: {c['release_date']}")
                    if c.get("genres"):
                        st.markdown(f"🏷️ **장르**: {', '.join(c['genres'])}")
                    st.markdown("📝 **줄거리**")
                    st.write(c.get("overview") or "줄거리 정보가 없어요.")


# =========================
# 질문 5개 UI
# =========================
st.subheader("📝 질문에 답해주세요")
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

selected_indices = [
    q1_options.index(q1),
    q2_options.index(q2),
    q3_options.index(q3),
    q4_options.index(q4),
    q5_options.index(q5),
]

st.divider()

# =========================
# (1) 첫 추천 버튼
# =========================
if st.button("🔮 결과 보기"):
    run_recommendation(reuse_context=None)

# =========================
# (2) 추천 후 하단 버튼: 제외 반영 + 다시 추천
# =========================
# last_llm_candidates가 있으면(=추천을 한 번이라도 했으면) 버튼 노출
if st.session_state.get("last_llm_candidates"):
    st.divider()
    st.subheader("🔁 마음에 안 드는 영화가 있나요?")
    st.write("위 리스트에서 `🚫 이 영화는 제외`를 체크한 뒤 아래 버튼을 누르면, 해당 영화들은 제외하고 다시 추천해요!")

    if st.button("🚀 제외 반영해서 다시 추천"):
        # 체크된 항목을 excluded_movie_ids에 반영
        newly_excluded = set(st.session_state["excluded_movie_ids"])
        for c in st.session_state["last_llm_candidates"]:
            chk_key = f"exclude_chk_{c['id']}"
            if st.session_state.get(chk_key):
                newly_excluded.add(int(c["id"]))
        st.session_state["excluded_movie_ids"] = newly_excluded

        # 재추천 실행(같은 분석/답변 컨텍스트 재사용)
        ctx = st.session_state.get("last_reco_context")
        run_recommendation(reuse_context=ctx)
