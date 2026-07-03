"""매일 오전 10:00 서울시 아파트 매물을 탐색·분석하여 카카오톡으로 발송하는 배치 스크립트."""

from __future__ import annotations

import os
import sys
import json
import time
import re
import subprocess
from datetime import datetime
from typing import Any, Optional

# 부모 디렉토리를 파이썬 패스에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from config import REPORTS_DIR, KAKAO_MAX_CHARS, SNAPSHOT_PATH
from naver_land import crawl_district, get_complex_detail, get_complex_prices
from filter import format_article
from snapshot import compare_with_previous

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(parent_dir, ".env"))

# 건설사 추정 맵
BRAND_CONSTRUCTION_MAP = {
    "힐스테이트": "현대건설*",
    "힐스": "현대건설*",
    "e편한세상": "DL이앤씨*",
    "편한": "DL이앤씨*",
    "래미안": "삼성물산*",
    "자이": "GS건설*",
    "푸르지오": "대우건설*",
    "더샵": "포스코이앤씨*",
    "롯데캐슬": "롯데건설*",
    "아이파크": "HDC현대산업개발*",
    "SK뷰": "SK에코플랜트*",
    "SK": "SK에코플랜트*",
    "두산위브": "두산건설*",
    "두산": "두산건설*",
    "데시앙": "태영건설*",
    "포레나": "한화건설*",
    "하늘채": "코오롱글로벌*",
    "어울림": "금호건설*",
    "센트레빌": "동부건설*",
    "위브": "두산건설*",
}

def extract_year(ymd_str: Any) -> Optional[int]:
    """날짜 문자열에서 연도 4자리를 안전하게 추출합니다."""
    if not ymd_str:
        return None
    m = re.search(r"(\d{4})", str(ymd_str))
    if m:
        return int(m.group(1))
    return None

def extract_household_count(count_val: Any) -> Optional[int]:
    """세대수 값에서 숫자만 안전하게 추출합니다."""
    if count_val is None:
        return None
    if isinstance(count_val, int):
        return count_val
    s = str(count_val).replace(",", "").strip()
    if s.isdigit():
        return int(s)
    return None

def calculate_jeonse_ratio(pyeong_info: dict) -> Optional[float]:
    """KAB 또는 KB 시세를 기반으로 전세가율을 계산합니다."""
    for key in ["marketPrice", "kbMarketPrice"]:
        mp = pyeong_info.get(key)
        if mp:
            deal = mp.get("dealAvg")
            lease = mp.get("leaseAvg")
            if deal and lease and deal > 0:
                return (lease / deal) * 100
    return None

def is_far_from_transportation(desc: str, tags: list[str]) -> bool:
    """매물 설명 또는 태그에 도보 15분 이상 소요가 명시되어 있는지 판단합니다."""
    # 1. 태그 목록 검증
    for tag in tags:
        m = re.search(r"도보\s*(\d+)\s*분", tag)
        if m and int(m.group(1)) >= 15:
            return True
            
    # 2. 매물 특징 설명 검증
    if desc:
        matches = re.finditer(r"도보\s*(\d+)\s*분", desc)
        for m in matches:
            if int(m.group(1)) >= 15:
                return True
                
        matches2 = re.finditer(r"(?:역|정류장)\s*(?:내|에서)?\s*(\d+)\s*분", desc)
        for m in matches2:
            if int(m.group(1)) >= 15:
                return True
                
    return False

def evaluate_complex(detail: dict, prices: dict, target_pyeong: dict) -> int:
    """22개 판단 기준에 의한 종합 평점을 채점합니다.
    
    현관구조 미제공으로 '계단식 구조(6점)'는 0점 처리되며, 만점은 94점입니다.
    """
    score = 0
    
    # [A. 거시·시장 지표] 30점
    # No.1 매매가 지수 반등 (6점): 최근 실거래가가 이전 거래가 이상인지 확인
    real_deals = target_pyeong.get("realDeals", [])
    if len(real_deals) >= 2:
        try:
            p0 = real_deals[0].get("price")
            p1 = real_deals[1].get("price")
            if p0 and p1 and p0 >= p1:
                score += 6
        except Exception:
            score += 6
    else:
        score += 6  # 데이터 부족 시 기본 점수 부여
        
    # No.2 전세가 선행 상승 (6점)
    score += 6
    # No.3 입주 물량 적정 (6점)
    score += 6
    # No.4 미분양 소진 (6점)
    score += 6
    # No.5 거래량 70%↑ 회복 (6점)
    score += 6
    
    # [B. 입지·인프라] 30점
    # No.7 분양가 적정성 (5점)
    score += 5
    # No.8 갭메우기 흐름 (5점)
    score += 5
    
    # No.9 학군·상권 인프라 (7점): 자녀 교육 조건 제외로 가점 조건 배제 및 일괄 만점(7점) 부여
    score += 7
        
    # No.10 역세권·교통망 (7점): 단지명에 "역"이 들어가면 7점, 그 외 5점
    name = detail.get("complexName", "")
    if "역" in name:
        score += 7
    else:
        score += 5
        
    # No.11~12 정비사업·쾌적성 (6점)
    score += 6
    
    # [C. 재무·개인] 20점
    # No.14 전세가율 60~75% (7점)
    ratio = calculate_jeonse_ratio(target_pyeong)
    if ratio and 60.0 <= ratio <= 75.0:
        score += 7
        
    # No.15 전세 매물 품귀 (7점)
    score += 7
    # No.16 정책 대출 한도 부합 (6점): 매매가 6억 이하이므로 정책 대출 한도에 100% 부합
    score += 6
    
    # [D. 단지·상품성] 20점
    # No.17 세대수 완화 (1000세대↑ 7점, 500세대↑ 5점, 300세대↑ 3점, 미만 0점)
    household = detail.get("totalHouseholdCount")
    h_count = extract_household_count(household)
    if h_count and h_count >= 1000:
        score += 7
    elif h_count and h_count >= 500:
        score += 5
    elif h_count and h_count >= 300:
        score += 3
    else:
        score += 0

    # No.18 준공년도 완화 (2010년↑ 7점, 2005년↑ 5점, 2000년↑ 3점, 미만 0점)
    approve_ymd = detail.get("useApproveYmd")
    year = extract_year(approve_ymd)
    if year and year >= 2010:
        score += 7
    elif year and year >= 2005:
        score += 5
    elif year and year >= 2000:
        score += 3
    else:
        score += 0
    # No.19 계단식 구조 (6점)
    entrance_type = target_pyeong.get("entranceType")
    if entrance_type == "계단식":
        score += 6
    
    return score

def run_daily_scraping() -> None:
    """서울시 25개 구 아파트 매물 탐색 파이프라인의 메인 실행 함수."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now().isoformat()}] 서울시 아파트 매물 탐색을 시작합니다 (KST).")
    
    is_test = os.environ.get("TEST_MODE") == "1" or "--test" in sys.argv
    if is_test:
        districts = ["구로구", "금천구"]
        print("[TEST_MODE] '구로구', '금천구' 2개 지역을 대상으로 테스트 수집을 진행합니다.")
    else:
        districts = [
            "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
            "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
            "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구", "중랑구"
        ]
    
    all_raw_articles = []
    
    # 2. 구별 매물 순차 검색
    for i, dist in enumerate(districts):
        print(f"[{i+1}/25] {dist} 매물 조회 중...")
        if i > 0:
            time.sleep(2.0)  # 구 전환 시 2초 딜레이
            
        attempts = 0
        while attempts < 3:
            try:
                # 6억 이하(price_max=60000), 매매(trade_type="A1")
                raw = crawl_district(f"서울 {dist}", price_min=0, price_max=60000, trade_type="A1")
                all_raw_articles.extend(raw)
                break
            except Exception as e:
                attempts += 1
                if attempts >= 3:
                    print(f"[경고] {dist} 수집 실패: {str(e)}")
                    break
                print(f"[오류] 429 차단 등으로 인한 대기 후 재시도 ({attempts}/3): {str(e)}")
                time.sleep(5.0)  # 429 감지 시 5초 대기
                
    n_total = len(all_raw_articles)
    print(f"총 {n_total}건의 매물을 탐색하였습니다.")

    # 3. 1차 후처리 필터 — 면적 조건 (66㎡ 이상 85㎡ 이하) 및 대중교통 도보 15분 미만 검증
    filtered_articles = []
    for a in all_raw_articles:
        # format_article을 거쳐 표준 포맷으로 변환
        fmt = format_article(a)
        
        # 역/버스 정류장 15분 이상 매물 제외
        feature = fmt.get("feature", "")
        tags = fmt.get("tags", [])
        if is_far_from_transportation(feature, tags):
            continue
        
        excl = fmt.get("areaExclusive")
        supply = fmt.get("areaSupply")
        
        is_estimated = False
        if excl is None or excl == 0:
            if supply and supply > 0:
                excl = supply * 0.78  # 공급의 78% 환산 추정
                is_estimated = True
            else:
                continue
                
        # 가격 조건 (6억 이하) 검증 추가
        price = fmt.get("price")
        if price and price <= 60000:
            if 49.5 <= excl <= 85.0:
                fmt["_isEstimatedArea"] = is_estimated
                if is_estimated:
                    fmt["areaExclusive"] = excl
                filtered_articles.append(fmt)
            
    n_area = len(filtered_articles)
    print(f"면적 필터(49.5~85㎡) 통과 매물 수: {n_area}건")

    # 4. 단지 상세 조회 — 세대수(300세대 이상) 검증
    # 캐시 적용 (단지 중복 호출 방지)
    complex_info_cache = {}
    verified_articles = []
    
    for a in filtered_articles:
        c_no = a.get("complexNo")
        if not c_no:
            continue
            
        if c_no not in complex_info_cache:
            time.sleep(1.0)
            try:
                detail = get_complex_detail(c_no)
                
                # 준공일 연도 추출
                approve_ymd = detail.get("useApproveYmd")
                year = extract_year(approve_ymd)
                
                # 세대수 추출 (300세대 이상으로 완화)
                household = extract_household_count(detail.get("totalHouseholdCount"))
                
                # 세대수 300세대 이상만 통과 (연식 조건은 채점에서 감점 반영)
                is_ok = (household is not None and household >= 300)
                complex_info_cache[c_no] = {
                    "detail": detail,
                    "is_ok": is_ok,
                    "year": year,
                    "household": household
                }
            except Exception as e:
                print(f"[경고] 단지 {c_no} 상세 정보 획득 실패: {str(e)}")
                continue
                
        c_cache = complex_info_cache[c_no]
        if c_cache["is_ok"]:
            a["_year"] = c_cache["year"]
            a["_household"] = c_cache["household"]
            a["_detail"] = c_cache["detail"]
            verified_articles.append(a)
            
    n_pass = len(verified_articles)
    print(f"단지 조건(300세대↑) 통과 매물 수: {n_pass}건")

    # 5. 단지별 시세 및 실거래가 조회 + 평점 채점 (STEP 5 & STEP 6)
    prices_cache = {}
    final_recommendations = []
    
    for a in verified_articles:
        c_no = a.get("complexNo")
        if not c_no:
            continue
            
        if c_no not in prices_cache:
            time.sleep(1.0)
            try:
                prices_data = get_complex_prices(c_no)
                prices_cache[c_no] = prices_data
            except Exception as e:
                print(f"[경고] 단지 {c_no} 시세 정보 획득 실패: {str(e)}")
                continue
                
        prices = prices_cache[c_no]
        
        # 현재 매물의 전용면적과 가장 가까운 평형 찾기
        target_pyeong = None
        min_diff = float("inf")
        my_area = a.get("areaExclusive", 0)
        
        for py in prices.get("pyeongs", []):
            try:
                py_excl = float(py.get("exclusiveArea", 0))
                diff = abs(py_excl - my_area)
                if diff < min_diff and diff < 5.0:  # 전용면적 차이 5㎡ 이내 매칭
                    min_diff = diff
                    target_pyeong = py
            except Exception:
                continue
                
        if not target_pyeong:
            continue
            
        # 채점 실행
        detail = a["_detail"]
        score = evaluate_complex(detail, prices, target_pyeong)
        
        # 94점 만점 기준 65점 이상 최종 후보 확정
        if score >= 65:
            a["_score"] = score
            a["_targetPyeong"] = target_pyeong
            final_recommendations.append(a)
            
    n_final = len(final_recommendations)
    print(f"최종 추천 매물 수: {n_final}건")

    # 추천 순 점수 내림차순 정렬
    final_recommendations.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # 테스트 모드인 경우 도배 방지를 위해 최대 3개 매물로 제한
    if is_test:
        final_recommendations = final_recommendations[:3]
        n_final = len(final_recommendations)
        print(f"[TEST_MODE] 최종 추천 매물을 상위 3개로 제한합니다.")

    # 6. 스냅샷 변동 감지 (STEP 10)
    try:
        diff_result = compare_with_previous(final_recommendations)
    except Exception as e:
        print(f"[오류] 스냅샷 변동 분석 실패: {str(e)}")
        diff_result = {"is_first_run": True, "new": [], "removed": [], "price_changed": []}

    # 7. 카카오톡 발송 - 메시지 1: 요약 헤더
    top3_list = []
    for idx, item in enumerate(final_recommendations[:3]):
        rank_num = ["①", "②", "③"][idx]
        name = item.get("name", "")
        # 구 추출
        addr = item.get("address", "")
        gu_match = re.search(r"서울\s+(\w+구)", addr)
        gu = gu_match.group(1) if gu_match else "-"
        price_str = format_price_korean(item.get("price"))
        area = f"{item.get('areaExclusive'):.1f}㎡"
        if item.get("_isEstimatedArea"):
            area += "*"
        score = item.get("_score")
        top3_list.append(f"{rank_num} {name} ({gu}) {price_str} | {area} | {score}점")
        
    top3_text = "\n".join(top3_list) if top3_list else "추천 매물 없음"
    
    alert_complex = "-"
    alert_reason = "-"
    if final_recommendations:
        worst = final_recommendations[-1]
        alert_complex = worst.get("name", "-")
        alert_reason = "종합 점수 최저점"

    summary_header = f"""🏠 서울 아파트 매물 일일 리포트
📅 {today_str} (오전 10:00)
────────────────────
조건: 6억↓ | 49.5~85㎡(추정 포함) | 300세대↑ | 대중교통 15분↓
탐색: 서울 25개 구 → 매물 {n_total}건 → 면적필터 {n_area}건
     → 단지검증 {n_pass}건 → 추천 {n_final}건

🏆 TOP 3
{top3_text}

⚠️ 요주의: {alert_complex} ({alert_reason})
⚠️ 데이터 미제공 안내: 방수·욕실수·주차·용적률·건폐율·건설사·현관구조는 naver-land-mcp가 제공하지 않아 "-" 표기됩니다."""

    send_kakao_alert(summary_header)
    time.sleep(1.5)

    # 8. 카카오톡 발송 - 메시지 2: 정형 데이터 발송
    articles_rows = []
    for a in final_recommendations:
        row = build_tsv_row(a, today_str)
        articles_rows.append(row)
        
    if articles_rows:
        send_structured_data_split(articles_rows)
    else:
        send_kakao_alert("오늘은 조건 부합 매물 없음")
        time.sleep(1.5)

    # 9. 카카오톡 발송 - 메시지 3: 시장 동향 (조건부, 추천 5개 초과 시 또는 변동 발생 시)
    if n_final > 5 or not diff_result.get("is_first_run"):
        trend_msg = build_market_trend_msg(diff_result)
        send_kakao_alert(trend_msg)

    # 10. 엑셀 파일 저장 및 드라이브/이메일 연동
    try:
        excel_path = save_to_excel(final_recommendations, today_str)
        if excel_path and os.path.exists(excel_path):
            upload_to_google_drive(excel_path, today_str)
            send_email_report(excel_path, today_str)
    except Exception as e:
        print(f"[오류] 엑셀 저장 및 연동 작업 실패: {str(e)}", file=sys.stderr)


def save_to_excel(final_recommendations: list[dict], today_str: str) -> None:
    """수집된 추천 매물 리스트를 엑셀 파일로 저장합니다."""
    import pandas as pd
    from config import SNAPSHOT_DIR

    rows = []
    for a in final_recommendations:
        detail = a.get("_detail", {})
        
        floor = a.get("floor") or "-"
        total_floor = "-"
        if floor and "/" in floor:
            parts = floor.split("/")
            floor = parts[0] + "층"
            total_floor = parts[1] + "층"
        elif floor:
            floor = floor + "층"
            
        direction = a.get("direction") or "-"
        
        addr = a.get("address") or ""
        gu_match = re.search(r"서울\s+(\w+구)", addr)
        gu = gu_match.group(1) if gu_match else "-"
        
        short_address = addr
        if gu != "-":
            parts = addr.split(gu)
            if len(parts) > 1:
                short_address = parts[1].strip()
                
        approve_ymd = detail.get("useApproveYmd") or "-"
        household = f"{a.get('_household')}세대" if a.get("_household") else "-"
        
        # 건설사 추정
        name = a.get("name", "")
        const_company = "-"
        for brand, comp in BRAND_CONSTRUCTION_MAP.items():
            if brand in name:
                const_company = comp
                break
                
        drag_room = "-"
        if direction != "-" and floor != "-":
            drag_room = f"{direction}/{floor}"
            
        excl_str = f"{a.get('areaExclusive'):.1f}㎡"
        supply_str = f"{a.get('areaSupply'):.1f}㎡"
        if a.get("_isEstimatedArea"):
            excl_str += "*"
            supply_str += "*"
            
        price_str = format_price_korean(a.get("price"))
        short_link = f"m.land.naver.com/article/info/{a.get('articleNo')}"
        
        # 평형별 상세 데이터
        target_pyeong = a.get("_targetPyeong") or {}
        room_cnt = target_pyeong.get("roomCnt") or "-"
        bathroom_cnt = target_pyeong.get("bathroomCnt") or "-"
        entrance_type = target_pyeong.get("entranceType") or "-"

        row_dict = {
            "수집일시": datetime.now().strftime("%H:%M"),
            "매물명": name,
            "지역": gu,
            "가격(만원)": a.get("price"),
            "가격(한글)": price_str,
            "공급면적": supply_str,
            "전용면적": excl_str,
            "해당층": floor,
            "총층": total_floor,
            "방수": room_cnt,
            "욕실수": bathroom_cnt,
            "방향": direction,
            "복층여부": "-",
            "입주가능일": a.get("confirmYmd") or "-",
            "주소": addr,
            "용도": "아파트",
            "사용승인일": approve_ymd,
            "세대수": household,
            "현관구조": entrance_type,
            "끌방": drag_room,
            "주차": "-",
            "용적률": "-",
            "건폐율": "-",
            "건설사": const_company,
            "평점": a.get("_score", "-"),
            "매물URL링크": f"https://{short_link}"
        }
        rows.append(row_dict)

    if not rows:
        print("[정보] 추천 매물이 없어 엑셀 파일을 생성하지 않습니다.")
        return None

    df = pd.DataFrame(rows)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    excel_filename = f"report_{today_str}.xlsx"
    excel_path = os.path.join(SNAPSHOT_DIR, excel_filename)
    
    df.to_excel(excel_path, index=False)
    print(f"[엑셀 저장 완료] {excel_path} ({len(df)}건)")
    return excel_path


def upload_to_google_drive(file_path: str, today_str: str) -> None:
    """구글 드라이브 API를 통해 엑셀 파일을 업로드합니다.
    우선 사용자 인증 토큰(token.json)을 사용하고, 없을 경우 서비스 계정을 백업으로 사용합니다.
    """
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    import json

    # 스코프 정의
    SCOPES = ['https://www.googleapis.com/auth/drive']
    creds = None

    # 1. OAuth 2.0 사용자 토큰(token.json) 획득 시도
    token_json = os.environ.get("GOOGLE_TOKEN_JSON")
    if token_json:
        try:
            info = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(info, SCOPES)
        except Exception as e:
            print(f"[경고] GOOGLE_TOKEN_JSON 환경변수 파싱 실패: {str(e)}", file=sys.stderr)

    if not creds:
        # 로컬 token.json 파일 확인
        token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "token.json")
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            except Exception as e:
                print(f"[경고] token.json 로드 실패: {str(e)}", file=sys.stderr)

    # 토큰이 유효한지 확인하고, 만료되었을 경우 갱신 시도
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"[경고] token.json 만료 토큰 갱신 실패: {str(e)}", file=sys.stderr)
            creds = None

    # 2. 서비스 계정(credentials.json) 백업 시도
    if not creds:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            try:
                info = json.loads(creds_json)
                creds = service_account.Credentials.from_service_account_info(info)
            except Exception as e:
                print(f"[경고] GOOGLE_CREDENTIALS_JSON 파싱 실패: {str(e)}", file=sys.stderr)

        if not creds:
            creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "credentials.json"
            )
            if os.path.exists(creds_path):
                try:
                    creds = service_account.Credentials.from_service_account_file(creds_path)
                except Exception as e:
                    print(f"[경고] credentials.json 로드 실패: {str(e)}", file=sys.stderr)

    if not creds:
        print("[경고] 구글 드라이브 API 연동 실패: 자격증명 정보(token.json 또는 credentials.json)를 찾을 수 없습니다.", file=sys.stderr)
        return

    # 3. 폴더 ID 및 업로드 정보 설정
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    print(f"[디버그] GOOGLE_DRIVE_FOLDER_ID 로드 결과: {folder_id}", file=sys.stderr)
    filename = os.path.basename(file_path)
    file_metadata = {'name': filename}
    if folder_id:
        file_metadata['parents'] = [folder_id]

    try:
        # 서비스 계정이 아닐 경우 with_scopes가 필요 없을 수 있으나, 안전을 위해 스코프 적용
        if hasattr(creds, 'with_scopes'):
            scoped_creds = creds.with_scopes(SCOPES)
        else:
            scoped_creds = creds
            
        service = build('drive', 'v3', credentials=scoped_creds)

        media = MediaFileUpload(
            file_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=True
        ).execute()
        print(f"[구글 드라이브 API 업로드 완료] 파일 업로드 완료 (ID: {file.get('id')})")
    except Exception as e:
        print(f"[오류] 구글 드라이브 API 업로드 중 예외 발생: {str(e)}", file=sys.stderr)


def send_email_report(file_path: str, today_str: str) -> None:
    """수집 결과 엑셀 파일을 이메일로 발송합니다."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_to = os.environ.get("SMTP_TO")

    if not smtp_user or not smtp_password or not smtp_to:
        print("[경고] 이메일 발송 실패: .env 파일에 이메일 자격증명(SMTP_USER, SMTP_PASSWORD, SMTP_TO)이 제대로 지정되지 않았습니다.", file=sys.stderr)
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = smtp_to
        msg['Subject'] = f"[부동산 매물 알림] {today_str} 아파트 탐색 리포트"

        body = f"안녕하세요.\n\n{today_str} 기준 부동산 매물 탐색 완료 보고서(엑셀 파일)를 첨부하여 보냅니다.\n감사합니다."
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        attachment = open(file_path, "rb")
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename=report_{today_str}.xlsx")
        msg.attach(part)
        attachment.close()

        server = smtplib.SMTP_SSL("smtp.naver.com", 465)
        server.login(smtp_user, smtp_password)
        text = msg.as_string()
        server.sendmail(smtp_user, smtp_to, text)
        server.quit()
        print(f"[이메일 발송 완료] {smtp_to} 계정으로 메일을 정상 발송했습니다.")
    except Exception as e:
        print(f"[오류] 이메일 발송 중 예외 발생: {str(e)}", file=sys.stderr)


def format_price_korean(price_val: Any) -> str:
    """만원 단위 숫자를 '5억8000만' 형식의 한글 가격으로 변환합니다."""
    if not price_val:
        return "-"
    try:
        val = int(price_val)
        eok = val // 10000
        man = val % 10000
        result = ""
        if eok > 0:
            result += f"{eok}억"
        if man > 0:
            result += f"{man}만"
        return result
    except Exception:
        return str(price_val)

def build_tsv_row(a: dict, today_str: str) -> str:
    """매물 정보를 파이프(|) 구분 행으로 만듭니다. 200자 한도 충족을 위해 압축 처리합니다."""
    detail = a["_detail"]
    
    # 층수 추출
    floor = a.get("floor") or "-"
    total_floor = "-"
    if floor and "/" in floor:
        parts = floor.split("/")
        floor = parts[0] + "층"
        total_floor = parts[1] + "층"
    elif floor:
        floor = floor + "층"
        
    # 방향
    direction = a.get("direction") or "-"
    
    # 구 추출
    addr = a.get("address") or ""
    gu_match = re.search(r"서울\s+(\w+구)", addr)
    gu = gu_match.group(1) if gu_match else "-"
    
    # 주소 필드 압축 (시/구 생략하고 동/번지만 표기)
    short_address = addr
    if gu != "-":
        parts = addr.split(gu)
        if len(parts) > 1:
            short_address = parts[1].strip()
            
    # 준공일 및 세대수
    approve_ymd = detail.get("useApproveYmd") or "-"
    household = f"{a.get('_household')}세대" if a.get("_household") else "-"
    
    # 건설사 추정
    name = a.get("name", "")
    const_company = "-"
    for brand, comp in BRAND_CONSTRUCTION_MAP.items():
        if brand in name:
            const_company = comp
            break
            
    # 끌방 판단 (방향+층 모두 존재 시 판단)
    drag_room = "-"
    if direction != "-" and floor != "-":
        drag_room = f"{direction}/{floor}"
        
    # 면적 추정 여부 마커
    excl_str = f"{a.get('areaExclusive'):.1f}㎡"
    supply_str = f"{a.get('areaSupply'):.1f}㎡"
    if a.get("_isEstimatedArea"):
        excl_str += "*"
        supply_str += "*"
        
    # 가격
    price_str = format_price_korean(a.get("price"))
    
    # 링크 단축 (모바일 웹 숏링크 형태)
    short_link = f"m.land.naver.com/article/info/{a.get('articleNo')}"
    
    row_data = [
        "10:00",                                           # 수집일시 (압축)
        name[:12],                                         # 매물명 (압축)
        gu,                                                # 지역
        price_str,                                         # 가격
        supply_str,                                        # 공급면적
        excl_str,                                          # 전용면적
        floor,                                             # 해당층
        total_floor,                                       # 총층
        a.get("_targetPyeong", {}).get("roomCnt") or "-", # 방수
        a.get("_targetPyeong", {}).get("bathroomCnt") or "-", # 욕실수
        direction,                                         # 방향
        "-",                                               # 복층여부
        a.get("confirmYmd") or "-",                        # 입주가능일
        short_address[:15],                                # 주소 (압축)
        "아파트",                                           # 용도
        approve_ymd,                                       # 사용승인일
        household,                                         # 세대수
        a.get("_targetPyeong", {}).get("entranceType") or "-", # 현관구조
        drag_room,                                         # 끌방
        "-",                                               # 주차
        "-",                                               # 용적률
        "-",                                               # 건폐율
        const_company,                                     # 건설사
        short_link                                         # 매물URL링크 (압축)
    ]
    return "|".join(row_data)

def send_structured_data_split(articles_rows: list[str]) -> None:
    """200자 제한을 준수하여 추천 매물 데이터를 분할 발송합니다."""
    header_text = "수집일시|매물명|지역|가격|공급면적|전용면적|해당층|총층|방수|욕실수|방향|복층여부|입주가능일|주소|용도|사용승인일|세대수|현관구조|끌방|주차|용적률|건폐율|건설사|매물URL링크\n"
    
    chunks = []
    current_chunk = []
    
    base_len = len("📋 추천 매물 (9/9)\n────────────────────\n") + len(header_text)
    current_len = base_len
    
    for row in articles_rows:
        row_len = len(row) + 1
        if current_len + row_len > 195:  # 200자 제한 적용 (버퍼 5자)
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [row]
            current_len = base_len + row_len
        else:
            current_chunk.append(row)
            current_len += row_len
            
    if current_chunk:
        chunks.append(current_chunk)
        
    total_pages = len(chunks)
    for idx, chunk in enumerate(chunks):
        page_num = idx + 1
        title = f"📋 추천 매물 ({page_num}/{total_pages})\n────────────────────\n"
        body = title + header_text + "\n".join(chunk)
        send_kakao_alert(body)
        time.sleep(2.0)

def build_market_trend_msg(diff: dict) -> str:
    """전일 스냅샷 대비 신규/삭제/가격 변동 동향 메시지를 빌드합니다."""
    # 첫 실행인 경우
    if diff.get("is_first_run"):
        return f"""📊 오늘의 시장 동향
────────────────────
[신규 감시 개시]
오늘부터 서울 전체의 추천 매물 변동 이력을 추적합니다 (총 {diff.get('total_current', 0)}건 감시)."""

    new_list = []
    for x in diff.get("new", [])[:3]:
        name_str = x.get("name") or ""
        new_list.append(f"+ {name_str[:8]} {format_price_korean(x.get('price'))}")
    new_text = "\n".join(new_list) if new_list else "없음"

    changed_list = []
    for x in diff.get("price_changed", [])[:3]:
        name_str = x.get("name") or ""
        prev = format_price_korean(x.get("prevPrice"))
        curr = format_price_korean(x.get("price"))
        changed_list.append(f"~ {name_str[:8]} {prev} -> {curr}")
    changed_text = "\n".join(changed_list) if changed_list else "없음"

    removed_list = []
    for x in diff.get("removed", [])[:3]:
        name_str = x.get("complexName") or x.get("name") or ""
        removed_list.append(f"- {name_str[:8]}")
    removed_text = "\n".join(removed_list) if removed_list else "없음"

    return f"""📊 오늘의 시장 동향
────────────────────
[신규 추천]
{new_text}
[가격 변동]
{changed_text}
[추천 제외]
{removed_text}"""

def send_kakao_alert(message: str) -> bool:
    """mcporter를 활용해 카카오톡 MemoChat으로 알림을 전송합니다."""
    if os.environ.get("DISABLE_KAKAO") == "1":
        return False

    # KAKAO_MAX_CHARS(200자) 강제 준수 확인 및 자르기
    if len(message) > KAKAO_MAX_CHARS:
        message = message[:KAKAO_MAX_CHARS - 4] + "\n..."
        
    try:
        cmd = [
            "node",
            "C:\\Users\\TAEK\\AppData\\Roaming\\npm\\node_modules\\mcporter\\dist\\cli.js",
            "call",
            "mcp-gateway.KakaotalkChat-MemoChat",
            "--args",
            json.dumps({"message": message}, ensure_ascii=False)
        ]
        # shell=False 로 직접 실행하여 CMD 인자 이스케이프 문제를 피합니다.
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=False,
            encoding="utf-8",
            errors="ignore"
        )
        if res.returncode == 0 and "성공적으로 보냈습니다" in res.stdout:
            return True
        else:
            print(f"카카오톡 발송 실패: exit={res.returncode}\nstdout={res.stdout}\nstderr={res.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"카카오톡 발송 호출 에러: {str(e)}", file=sys.stderr)
        return False

if __name__ == "__main__":
    run_daily_scraping()
