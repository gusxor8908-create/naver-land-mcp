"""전체 아파트 매물 조회 및 카카오톡 알림 발송 파이프라인 통합 테스트."""

import os
import sys

# 프로젝트 루트를 파이썬 패스에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
if project_root not in sys.path:
    sys.path.append(project_root)

# scripts 폴더 추가
scripts_path = os.path.join(project_root, "scripts")
if scripts_path not in sys.path:
    sys.path.append(scripts_path)

from daily_report import run_daily_scraping


def test_pipeline() -> None:
    print("=== [통합 테스트] 아파트 매물 크롤링 및 카카오톡 발송 파이프라인 검증 ===")
    try:
        # daily_report 의 실행 로직을 가동
        run_daily_scraping()
        print("\n=== [성공] 파이프라인 통합 테스트가 정상 완료되었습니다. ===")
    except Exception as e:
        print(f"\n=== [실패] 파이프라인 테스트 중 예외 발생: {str(e)} ===", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    test_pipeline()
