"""설정 상수: API URL, 헤더, 거래 유형, 기본값."""

import os
from dotenv import load_dotenv

# .env 파일 로드
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(current_dir, ".env"))

API_BASE = "https://new.land.naver.com/api"
MAIN_PAGE_URL = "https://new.land.naver.com/complexes"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://new.land.naver.com/complexes",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# Rate limiting
REQUEST_DELAY_SEC = 1.0       # 요청 간 최소 딜레이
RETRY_DELAY_SEC = 10.0         # 429 시 재시도 대기
MAX_RETRIES = 3
REQUEST_TIMEOUT_SEC = 20

# 스냅샷 저장 경로 (변동 감지용)
SNAPSHOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
SNAPSHOT_PATH = os.path.join(SNAPSHOT_DIR, "snapshot.json")

# MCP 타임아웃(60초) 에 맞춘 기본 한도.
# crawl_district 한 번에 처리할 최대 단지 수. dealCount 내림차순으로 선택.
DEFAULT_MAX_COMPLEXES = 5

# 가격 기본 범위 (만원 단위) — 가격 명시 안 한 호출 시 전 범위 검색
DEFAULT_PRICE_MIN = 0
DEFAULT_PRICE_MAX = 999999

TRADE_TYPES = {
    "A1": "매매",
    "B1": "전세",
    "B2": "월세",
}

# 엑셀 시트에서 도출된 핵심 타겟 아파트 단지 목록
TARGET_COMPLEXES = [
    "힐스테이트영통",
    "e편한세상영통2차1단지",
    "돈암삼성",
    "문정시영",
    "대전도안아이파크",
    "두산위브더제니스구미",
    "구미아이파크더샵",
    "SK북한산시티",
    "염창동 신동아",
    "홍은현대",
    "이문동 중앙하이츠빌",
    "전농우성",
    "은평기자촌11단지",
    "수락리버시티3단지",
]

# 일일 상세 리포트 저장 폴더
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 쿠키 파일 저장 경로
COOKIE_PATH = os.path.join(SNAPSHOT_DIR, "cookies.txt")

# 카카오톡 글자수 제한 (mcp-gateway KakaotalkChat-MemoChat 제한 기준)
KAKAO_MAX_CHARS = 200

# 공공데이터포털 API 관련 설정
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")
SEOUL_OPENAPI_KEY = os.environ.get("SEOUL_OPENAPI_KEY", "")

# 빌라 검색 필터 기준 (재개발/가로주택)
VILLA_PRICE_MAX = 40000            # 4억 미만 (만원 단위)
VILLA_AREA_MIN = 50.0              # 전용면적 50㎡ 이상
VILLA_AREA_MAX = 84.0              # 전용면적 84㎡ 이하
VILLA_BUILD_YEAR_MIN = 2010        # 2010년 이후 준공
VILLA_LAND_SHARE_MIN = 25.0        # 토지지분 25㎡ 이상

# 지역코드는 네이버 검색 API (/search) 로 동적 조회 — 하드코딩 제거.
# naver_land.resolve_region() / crawl_district() 참조.

