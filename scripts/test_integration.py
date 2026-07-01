import os
import sys
import pandas as pd
from datetime import datetime

# sys.path 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from daily_report import upload_to_google_drive, send_email_report

def main():
    print("통합 테스트 시작...")
    
    # 1. 임시 엑셀 파일 생성
    today_str = datetime.now().strftime("%Y-%m-%d_test")
    test_data = {"테스트항목": ["구글드라이브 업로드", "네이버 SMTP 메일 발송"], "결과": ["대기", "대기"]}
    df = pd.DataFrame(test_data)
    test_file_path = os.path.join(parent_dir, "reports", f"report_{today_str}.xlsx")
    os.makedirs(os.path.dirname(test_file_path), exist_ok=True)
    df.to_excel(test_file_path, index=False)
    print(f"임시 엑셀 파일 생성 완료: {test_file_path}")
    
    # 2. 구글 드라이브 업로드 테스트
    print("구글 드라이브 업로드 시작...")
    upload_to_google_drive(test_file_path, today_str)
    
    # 3. 메일 전송 테스트
    print("메일 발송 시작...")
    send_email_report(test_file_path, today_str)
    
    print("테스트 완료")

if __name__ == "__main__":
    main()
