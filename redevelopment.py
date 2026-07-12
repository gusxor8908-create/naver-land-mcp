"""서울시 재개발 및 가로주택정비사업 구역 내 빌라 매물 수집 모듈.

공공데이터포털 API(연립다세대 실거래가, 건축물대장)와 서울시 정보몽땅 웹스크래핑을 활용하여
재개발/가로주택 구역 내 빌라 매물을 검색하고 7가지 조건을 검증합니다.
"""

from __future__ import annotations

import os
import sys
import re
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Optional

# SSL warning 무시
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 부모 디렉토리를 파이썬 패스에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

import config

# 서울시 자치구 표준 코드 (법정동 5자리 구코드)
SEOUL_GU_CODES = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740"
}


def fetch_cleanup_zones() -> list[dict[str, str]]:
    """서울시 열린데이터 광장 OpenAPI(upisRebuild, CleanupBussinessProgress)를 활용하여
    재개발/가로주택 정비사업 구역 목록과 최신 진행단계를 수집합니다.
    
    진행단계 중 '조합설립인가' 이전 단계(대상지 선정 ~ 조합설립인가 직전)만 필터링합니다.
    """
    api_key = config.SEOUL_OPENAPI_KEY
    if not api_key:
        print("[경고] SEOUL_OPENAPI_KEY가 설정되지 않아 서울시 OpenAPI 호출을 건너뜁니다.")
        return []
        
    # 허용하는 진행 단계 및 제외할 진행 단계 키워드
    ALLOWED_STAGES = [
        "기본계획수립", "정비구역지정", "추진위원회승인", "추진위원회구성", "추진준비", "연번부여", 
        "대상지선정", "신속통합기획", "기본계획", "구역지정", "정비계획수립", "정비계획"
    ]
    EXCLUDE_STAGES = [
        "조합설립", "사업시행", "관리처분", "이주", "철거", "착공", "준공", "이전고시", "조합해산", "청산"
    ]
    
    zones: list[dict[str, str]] = []
    
    print("[서울시 API] 정비사업 구역 목록 수집을 시작합니다...")
    
    # 1. upisRebuild (도시계획 정비사업 현황) API를 통해 구역 마스터 정보 및 주소 수집
    rebuild_zones = []
    start_idx = 1
    page_size = 1000
    
    print("[서울시 API] upisRebuild 구역 마스터 정보 조회 중...")
    while True:
        url = f"http://openapi.seoul.go.kr:8088/{api_key}/json/upisRebuild/{start_idx}/{start_idx + page_size - 1}/"
        try:
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                print(f"[경고] upisRebuild 호출 실패 (시작: {start_idx}, 코드: {res.status_code})")
                break
                
            data = res.json()
            # 서비스명 키가 없는 에러 대응
            if "RESULT" in data and data["RESULT"].get("CODE") == "INFO-200":
                break
                
            rows = data.get("upisRebuild", {}).get("row", [])
            if not rows:
                break
                
            for row in rows:
                sclsf = row.get("SCLSF", "")  # 소분류 (예: 재개발사업지구)
                # 재개발, 가로주택, 소규모정비 구역만 수집하고 재건축은 제외
                if not any(k in sclsf for k in ["재개발", "가로주택", "소규모"]):
                    continue
                if "재건축" in sclsf:
                    continue
                    
                zone_name = row.get("RGN_NM", "")
                address = row.get("PSTN_NM", "")
                
                # 자치구 추출 (예: "을지로1가 17" -> PSTN_NM 만으로 자치구 판단 어려우므로 행정구역 정보 참고 필요)
                # PSTN_NM이 보통 "동작구 신대방동..." 형태로 자치구가 포함되는 경우가 많음
                gu_match = re.search(r'([가-힗]+구)', address)
                gu = gu_match.group(1) if gu_match else ""
                
                rebuild_zones.append({
                    "zone_name": zone_name,
                    "address": address,
                    "gu": gu,
                    "biz_type": sclsf
                })
                
            if len(rows) < page_size:
                break
            start_idx += page_size
            time.sleep(0.3)
        except Exception as e:
            print(f"[오류] upisRebuild 조회 중 예외 발생: {str(e)}")
            break
            
    print(f"[서울시 API] upisRebuild 수집 완료. 대상 구역: {len(rebuild_zones)}개")
    
    # 2. CleanupBussinessProgress (건설 정비사업 추진 경과 정보) API를 통해 실시간 진행단계 수집
    print("[서울시 API] CleanupBussinessProgress 진행단계 조회 중...")
    progress_status = {}  # BIZ_NO -> {day, stage, title}
    start_idx = 1
    page_size = 1000
    
    # 최근 5,000건의 이력만 수집하여 최신 상태 갱신 (효율성 및 API 일일 한도 절약)
    for _ in range(5):
        url = f"http://openAPI.seoul.go.kr:8088/{api_key}/json/CleanupBussinessProgress/{start_idx}/{start_idx + page_size - 1}/"
        try:
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                print(f"[경고] CleanupBussinessProgress 호출 실패 (시작: {start_idx})")
                break
                
            data = res.json()
            if "RESULT" in data and data["RESULT"].get("CODE") == "INFO-200":
                break
                
            rows = data.get("CleanupBussinessProgress", {}).get("row", [])
            if not rows:
                break
                
            for row in rows:
                biz_no = row.get("BIZ_NO", "")
                day = row.get("DAY", "")
                stage = row.get("SE_NM", "")
                title = row.get("TTL", "")
                
                if not biz_no:
                    continue
                    
                # BIZ_NO 별로 가장 최근 날짜의 단계 상태만 추적
                if biz_no not in progress_status or day > progress_status[biz_no]["day"]:
                    # 기존에 수집된 Title이 있고 현재 Title이 비어있는 경우 기존 Title 보존
                    existing_title = progress_status[biz_no]["title"] if biz_no in progress_status else ""
                    final_title = title if title.strip() else existing_title
                    
                    progress_status[biz_no] = {
                        "day": day,
                        "stage": stage,
                        "title": final_title
                    }
                    
            if len(rows) < page_size:
                break
            start_idx += page_size
            time.sleep(0.3)
        except Exception as e:
            print(f"[오류] CleanupBussinessProgress 조회 중 예외 발생: {str(e)}")
            break
            
    print(f"[서울시 API] CleanupBussinessProgress 수집 완료. 고유 사업장 수: {len(progress_status)}개")
    
    # 3. 조인(Join) 및 필터링
    print("[서울시 API] 데이터 매칭 및 필터링 중...")
    
    # 법정동 구코드 매핑용 역검색 딕셔너리
    reversed_gu_codes = {v: k for k, v in SEOUL_GU_CODES.items()}
    
    for biz_no, status in progress_status.items():
        stage = status["stage"]
        title = status["title"]
        
        # 조합설립인가 이전 단계인지 필터링
        is_allowed = False
        if any(allowed in stage for allowed in ALLOWED_STAGES):
            is_allowed = True
        if any(ex in stage for ex in EXCLUDE_STAGES):
            is_allowed = False
            
        if not is_allowed:
            continue
            
        # BIZ_NO의 앞 5자리는 구 코드 (예: "11740-900001290" -> 11740)
        gu_code = biz_no.split("-")[0] if "-" in biz_no else ""
        gu_from_code = reversed_gu_codes.get(gu_code, "")
        
        # title에서 구역명 핵심 단어 정제 (예: "천호A1-1구역 공공재개발..." -> "천호A1-1")
        clean_title = re.sub(r'(정비사업|전문관리|선정|용역|입찰|계약|총회|추진|조합|구역|재개발|재건축|공공).*', '', title).strip()
        if not clean_title:
            clean_title = title[:12].strip()
            
        matched_zone = None
        # 구역명 매칭 시도
        if clean_title:
            for rz in rebuild_zones:
                # 같은 자치구에 속한 구역 중에서 매칭
                if gu_from_code and rz["gu"] and gu_from_code != rz["gu"]:
                    continue
                r_name = rz["zone_name"]
                if clean_title in r_name or r_name in clean_title:
                    matched_zone = rz
                    break
                    
        # 법정동 및 지번 추출
        dong = ""
        jibun = ""
        gu = gu_from_code
        biz_type = "재개발"
        zone_name = clean_title if clean_title else title
        address = ""
        
        if matched_zone:
            address = matched_zone["address"]
            gu = matched_zone["gu"] if matched_zone["gu"] else gu
            biz_type = matched_zone["biz_type"]
            zone_name = matched_zone["zone_name"]
            
            # 주소에서 동/지번 정교하게 파싱
            addr_match = re.search(r'([가-힗]+동)\s*([\d\-]+)?', address)
            if addr_match:
                dong = addr_match.group(1)
                jibun = addr_match.group(2) if addr_match.group(2) else ""
        else:
            # 매칭 실패 시 title 텍스트에서 동이름 추정 시도
            dong_match = re.search(r'([가-힗]+동)', title)
            if dong_match:
                dong = dong_match.group(1)
            address = f"서울특별시 {gu} {dong}".strip()
            
        # 최소한 구와 동 정보는 있어야 실거래가 조회가 가능
        if not gu or not dong:
            continue
            
        zones.append({
            "gu": gu,
            "biz_type": biz_type,
            "zone_name": zone_name,
            "address": address,
            "dong": dong,
            "jibun": jibun,
            "stage": stage
        })
        
    # 중복 제거
    unique_zones = []
    seen = set()
    for z in zones:
        key = (z["gu"], z["zone_name"], z["dong"], z["jibun"])
        if key not in seen:
            seen.add(key)
            unique_zones.append(z)
            
    print(f"[서울시 API] 최종 필터링된 조합설립이전 구역 수: {len(unique_zones)}개")
    return unique_zones


def fetch_villa_transactions(lawd_code: str, deal_ymd: str) -> list[dict[str, Any]]:
    """공공데이터포털 API를 호출하여 특정 구 및 연월의 연립다세대 매매 실거래 데이터를 수집합니다.
    
    Decoding 인증키는 requests 내부 인코딩 오류 방지를 위해 urllib.parse.quote로 감싸서 직접 전달합니다.
    """
    if not config.DATA_GO_KR_API_KEY:
        print("[경고] DATA_GO_KR_API_KEY가 설정되지 않아 실거래가 API 호출을 건너뜁니다.")
        return []
        
    import urllib.parse
    encoded_key = urllib.parse.quote(config.DATA_GO_KR_API_KEY)
    
    # 국토교통부 연립다세대 매매 실거래가 엔드포인트
    base_url = "http://apis.data.go.kr/1611000/RTMSOBJSvc/getRTMSDataSvcRHTrade"
    # 백업 엔드포인트 (1611000 실패 시 1613000 사용)
    backup_url = "http://apis.data.go.kr/1613000/RTMSOBJSvc/getRTMSDataSvcRHTrade"
    
    full_url = f"{base_url}?serviceKey={encoded_key}&LAWD_CD={lawd_code}&DEAL_YMD={deal_ymd}"
    
    attempts = 0
    res_text = ""
    
    while attempts < 2:
        try:
            url_to_call = full_url if attempts == 0 else f"{backup_url}?serviceKey={encoded_key}&LAWD_CD={lawd_code}&DEAL_YMD={deal_ymd}"
            res = requests.get(url_to_call, timeout=15)
            if res.status_code == 200:
                res_text = res.text
                if "Unexpected errors" not in res_text:
                    break
            attempts += 1
            time.sleep(1.0)
        except Exception as e:
            print(f"[API 오류] 실거래가 API 호출 실패 (LAWD_CD: {lawd_code}, DEAL_YMD: {deal_ymd}): {str(e)}")
            attempts += 1
            time.sleep(1.0)
            
    if not res_text or "Unexpected errors" in res_text:
        return []
        
    transactions: list[dict[str, Any]] = []
    
    try:
        root = ET.fromstring(res_text.encode('utf-8'))
        
        # XML 구조 파싱: response -> body -> items -> item
        body = root.find('body')
        if body is None:
            return []
        items = body.find('items')
        if items is None:
            return []
            
        for item in items.findall('item'):
            # 거래금액 정제 (예: "   35,000" -> 35000)
            amount_str = item.findtext('거래금액', '0').replace(',', '').strip()
            amount = int(amount_str) if amount_str.isdigit() else 0
            
            # 전용면적 (예: "55.45")
            area = float(item.findtext('전용면적', '0'))
            
            # 건축년도
            build_year = int(item.findtext('건축년도', '0'))
            
            # 법정동, 지번, 연립다세대명
            dong = item.findtext('법정동', '').strip()
            jibun = item.findtext('지번', '').strip()
            name = item.findtext('연립다세대', '').strip()
            
            # 층
            floor = item.findtext('층', '').strip()
            
            # 계약일
            day = item.findtext('일', '1').strip()
            # 2자리 포맷 보장
            if len(day) == 1:
                day = "0" + day
            deal_date = f"{deal_ymd[:4]}-{deal_ymd[4:6]}-{day}"
            
            transactions.append({
                "name": name,
                "dong": dong,
                "jibun": jibun,
                "price": amount,  # 만원 단위
                "area": area,     # 제곱미터 단위
                "build_year": build_year,
                "floor": floor,
                "deal_date": deal_date
            })
            
    except Exception as e:
        print(f"[XML 오류] 실거래가 XML 파싱 중 예외 발생: {str(e)}")
        
    return transactions


def fetch_building_info(lawd_code: str, dong_name: str, jibun: str) -> dict[str, Any]:
    """건축물대장 API를 호출하여 위반건축물 여부와 토지지분 산출에 필요한 대지면적/세대수 정보를 조회합니다.
    
    API: 국토교통부_건축HUB_건축물대장정보 서비스 (getBrTitleInfo 표제부 조회)
    """
    if not config.DATA_GO_KR_API_KEY:
        return {}
        
    import urllib.parse
    encoded_key = urllib.parse.quote(config.DATA_GO_KR_API_KEY)
    
    # 지번 분리 (예: "649-1" -> 본번: "0649", 부번: "0001")
    # 지번 자릿수를 각각 4자리로 맞추어야 API 조회가 정확합니다.
    bun = ""
    ji = ""
    if "-" in jibun:
        parts = jibun.split("-")
        bun = parts[0].zfill(4)
        ji = parts[1].zfill(4)
    elif jibun.isdigit():
        bun = jibun.zfill(4)
        ji = "0000"
    else:
        return {}
        
    # 법정동코드 5자리(구코드) + 나머지 5자리(동코드) 구조
    # 건축물대장 API는 10자리 행정표준 법정동코드를 필요로 하거나, 동명칭을 직접 넣을 수 있습니다.
    # 여기서는 구코드(11680)와 동명칭(예: "개포동")을 파라미터로 넘깁니다.
    url = "http://apis.data.go.kr/1613000/BldAtchService/getBrTitleInfo"
    full_url = f"{url}?serviceKey={encoded_key}&sigunguCd={lawd_code}&bjdongCd=&platGbCd=0&bun={bun}&ji={ji}&dongNm=&numOfRows=10&pageNo=1"
    
    try:
        res = requests.get(full_url, timeout=10)
        if res.status_code != 200 or "Unexpected errors" in res.text:
            return {}
            
        root = ET.fromstring(res.text.encode('utf-8'))
        body = root.find('body')
        if body is None:
            return {}
        items = body.find('items')
        if items is None or len(items) == 0:
            return {}
            
        # 첫 번째 검색된 건축물 정보 분석 (표제부)
        item = items.find('item')
        if item is None:
            return {}
            
        # 위반건축물 여부 (0: 정상, 1: 위반) -> vltnBuldYn이 Y인 경우도 존재
        vltn_yn = item.findtext('rserthqkGbCd', '0').strip() # 또는 vltnBuldYn 필드 사용
        # 혹은 xml 내의 <vltnBuldYn> 태그 직접 조회
        vltn_yn_direct = item.findtext('vltnBuldYn', 'N').strip()
        is_violation = (vltn_yn == "1" or vltn_yn_direct.upper() in ["Y", "1", "TRUE"])
        
        # 대지면적 (platArea)
        plat_area_str = item.findtext('platArea', '0').strip()
        plat_area = float(plat_area_str) if plat_area_str else 0.0
        
        # 총 세대수 (가구수 fmlyCo + 세대수 hoCo)
        fmly_co = int(item.findtext('fmlyCo', '0') or 0)
        ho_co = int(item.findtext('hoCo', '0') or 0)
        total_households = max(fmly_co + ho_co, 1) # 0으로 나누기 방지
        
        return {
            "is_violation": is_violation,
            "plat_area": plat_area,
            "total_households": total_households
        }
    except Exception as e:
        # 대장 조회 실패 시 빈 딕셔너리 반환하여 필터링 우회 방지
        return {}


def collect_redevelopment_villas() -> list[dict[str, Any]]:
    """재개발/가로주택 정비사업 구역 내 조건 부합 빌라 매물을 종합 수집합니다."""
    # 1. 정보몽땅 재개발/가로주택 구역 수집
    zones = fetch_cleanup_zones()
    if not zones:
        print("[빌라수집] 정보몽땅에서 수집된 정비사업 구역이 없어 작업을 종료합니다.")
        return []
        
    # 빠른 매칭을 위한 구역 매핑 (동별 구역 목록 구성)
    # {동이름: [구역정보들]}
    zones_by_dong: dict[str, list[dict]] = {}
    for z in zones:
        dong = z["dong"]
        if not dong:
            continue
        if dong not in zones_by_dong:
            zones_by_dong[dong] = []
        zones_by_dong[dong].append(z)
        
    # 2. 거래 데이터를 수집할 법정동 구코드 및 최근 월 리스트업
    # 수집 대상 구코드 (정보몽땅 구역이 존재하는 구만 수집하여 API 호출 횟수 최적화)
    target_gu_names = set(z["gu"] for z in zones)
    target_lawd_codes = {name: SEOUL_GU_CODES[name] for name in target_gu_names if name in SEOUL_GU_CODES}
    
    # 최근 3개월간의 실거래가 데이터 수집 (예: 이번달 및 직전 2달)
    current_date = datetime.now()
    months_to_fetch: list[str] = []
    for i in range(3):
        year = current_date.year
        month = current_date.month - i
        if month <= 0:
            month += 12
            year -= 1
        months_to_fetch.append(f"{year}{str(month).zfill(2)}")
        
    print(f"[빌라수집] 수집 대상 구: {list(target_lawd_codes.keys())}")
    print(f"[빌라수집] 수집 대상 연월: {months_to_fetch}")
    
    matched_villas: list[dict[str, Any]] = []
    
    # 3. 실거래가 데이터 수집 및 조건 필터링
    for gu_name, lawd_code in target_lawd_codes.items():
        for ymd in months_to_fetch:
            print(f"[빌라수집] {gu_name} ({lawd_code}) - {ymd} 실거래가 조회 중...")
            txs = fetch_villa_transactions(lawd_code, ymd)
            if not txs:
                time.sleep(1.0)
                continue
                
            for tx in txs:
                # 조건2: 4억 미만 매물 (만원 단위이므로 40,000)
                if tx["price"] >= config.VILLA_PRICE_MAX:
                    continue
                    
                # 조건3: 전용면적 50 ~ 84㎡
                if not (config.VILLA_AREA_MIN <= tx["area"] <= config.VILLA_AREA_MAX):
                    continue
                    
                # 조건4: 2010년 이후 준공
                if tx["build_year"] < config.VILLA_BUILD_YEAR_MIN:
                    continue
                    
                # 구역 매칭 (동 및 지번 주소 기준)
                tx_dong = tx["dong"]
                tx_jibun = tx["jibun"]
                
                if tx_dong not in zones_by_dong:
                    continue
                    
                # 해당 동에 속한 구역 중 지번이 일치하거나 포함되는 구역 찾기
                matched_zone = None
                for zone in zones_by_dong[tx_dong]:
                    # 지번이 비어있지 않고, 실거래가 지번과 일치하거나 범위 내에 포함되는지 확인
                    zone_jibun = zone["jibun"]
                    if zone_jibun and (zone_jibun in tx_jibun or tx_jibun in zone_jibun):
                        matched_zone = zone
                        break
                        
                # 구역 내에 위치하지 않는 빌라면 탈락
                if not matched_zone:
                    continue
                    
                # 조건5, 조건6: 건축물대장 정보 조회를 통한 검증
                # API 호출 빈도를 낮추기 위해 1차 필터를 모두 통과한 매물만 대장 조회 실행
                time.sleep(0.5)
                bld_info = fetch_building_info(lawd_code, tx_dong, tx_jibun)
                
                # 대장 정보 조회 실패했더라도 수동 확인을 위해 기본 통과 시키되 경고/비고 처리 가능
                # 조건5: 불법건축물 없음
                if bld_info.get("is_violation", False):
                    continue
                    
                # 조건6: 토지지분율 25제곱미터 이상 (대지면적 / 총 세대수)
                plat_area = bld_info.get("plat_area", 0.0)
                total_households = bld_info.get("total_households", 1)
                land_share = plat_area / total_households
                
                # 대지면적 정보가 있고 세대수가 정상일 때 지분 조건 검증
                if plat_area > 0 and land_share < config.VILLA_LAND_SHARE_MIN:
                    continue
                    
                # 조건7: 근저당 없음 (오픈 API 상 등본 근저당 확인 불가 -> 엑셀에 "확인 필요" 표시로 대체)
                collateral = "확인 필요(대장상 확인불가)"
                
                matched_villas.append({
                    "zone_name": matched_zone["zone_name"],
                    "biz_type": matched_zone["biz_type"],
                    "stage": matched_zone["stage"],
                    "name": tx["name"],
                    "address": f"서울시 {gu_name} {tx_dong} {tx_jibun}",
                    "price": tx["price"],
                    "area": tx["area"],
                    "build_year": tx["build_year"],
                    "land_share": round(land_share, 2) if plat_area > 0 else 0.0,
                    "is_violation": "없음" if not bld_info.get("is_violation", False) else "있음(위반)",
                    "collateral": collateral,
                    "deal_date": tx["deal_date"],
                    "floor": tx["floor"]
                })
                
            time.sleep(1.0)
            
    print(f"[빌라수집] 최종 매칭 및 검증 통과 빌라 매물 수: {len(matched_villas)}건")
    return matched_villas


if __name__ == "__main__":
    print("단독 실행 테스트를 시작합니다.")
    from dotenv import load_dotenv
    # 현재 디렉토리 기준 .env 파일 로드
    load_dotenv(dotenv_path=os.path.join(current_dir, ".env"))
    
    # API 키 재할당
    config.DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")
    print(f"API Key 로드 여부: {'성공' if config.DATA_GO_KR_API_KEY else '실패'}")
    
    start_time = time.time()
    villas = collect_redevelopment_villas()
    end_time = time.time()
    
    print(f"\n--- 수집 완료 (소요시간: {end_time - start_time:.2f}초) ---")
    print(f"최종 수집 건수: {len(villas)}건")
    for idx, v in enumerate(villas[:10]):
        print(f"[{idx+1}] 구역: {v['zone_name']} ({v['biz_type']}) | 매물: {v['name']} | 가격: {v['price']}만원 | 전용: {v['area']}㎡ | 지분: {v['land_share']}㎡ | 주소: {v['address']}")

