# ES90 영업용 자동 업데이트 앱

영업직원은 GitHub 저장소가 아니라 GitHub Pages 배포 주소를 최초 한 번 설치해서 사용합니다.

## 업데이트 방법

1. 최신 결과물을 `app.html`로 교체합니다.
2. `version.json`의 `version`과 `updatedAt`을 변경합니다.
3. 변경 내용을 GitHub의 기본 브랜치에 반영합니다.
4. GitHub Pages 배포가 끝나면 사용자가 다음에 앱을 열 때 최신본이 자동 적용됩니다.

온라인에서는 항상 최신 `app.html`을 우선 불러오며, 네트워크가 없을 때는 마지막으로 정상 수신한 버전을 엽니다.

## ES90 예약정보 자동화

- GitHub Actions는 08:00~18:00의 각 정시 슬롯을 확인하고, 완료되지 않은 슬롯을 자동 수집합니다.
- GitHub 예약 이벤트는 플랫폼 사정으로 수 시간 지연될 수 있으므로 Windows 보조 감시기가 08:03~18:59에 5분마다 저장소 시각을 확인합니다.
- 보조 감시기는 누락된 슬롯이 있을 때만 `update-es90-reservations.yml`을 `workflow_dispatch`로 실행하며, 실행 중인 작업이 있으면 중복 요청하지 않습니다.
- 보조 감시기 로그는 `%LOCALAPPDATA%\ES90Matrix\reservation-watchdog.log`에 기록됩니다.
