import os
import sys
import requests

# dotenv 로드
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(parent_dir)
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(parent_dir, ".env"))

def trigger():
    pat = os.environ.get("GITHUB_PAT")
    repo = "gusxor8908-create/naver-land-mcp"
    
    if not pat:
        print("[오류] GITHUB_PAT 환경변수가 설정되지 않았습니다. .env 파일에 GITHUB_PAT=your_token 을 입력해 주세요.")
        print("토큰은 github.com > Settings > Developer Settings > Personal Access Tokens (classic) 에서 repo 및 workflow 권한을 체크해 발급받을 수 있습니다.")
        return

    url = f"https://api.github.com/repos/{repo}/actions/workflows/daily_report.yml/dispatches"
    headers = {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "ref": "main"
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 204:
            print("🎉 [성공] GitHub Actions 워크플로우(daily_report)를 성공적으로 기동했습니다!")
            print(f"진행 상황 확인: https://github.com/{repo}/actions")
        else:
            print(f"❌ [실패] API 응답 코드: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"❌ [에러] 호출 중 예외 발생: {e}")

if __name__ == '__main__':
    trigger()
