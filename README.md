# kiwi-last (간단 안내)

이 프로젝트는 **키위 생육 지표를 자동 수집해서 웹에 보여주는 저장소**입니다.

## 폴더 구조
- `collector/collect.py` : 센서 API를 읽어서 계산하고 파일을 만듭니다.
- `data/status.json` : 웹 화면이 읽는 최신 상태 파일입니다.
- `data/daily.csv` : 날짜별 누적 계산 데이터입니다.
- `index.html` : 실제 표시 화면(정적 웹 페이지)입니다.
- `.github/workflows/collect.yml` : 매시간 자동 실행 설정입니다.

## 동작 순서(아주 간단)
1. GitHub Actions가 매시간 `collector/collect.py` 실행
2. 스크립트가 `data/status.json`, `data/daily.csv` 갱신
3. `index.html`이 `data/status.json` 읽어 화면 표시

## 이번 정리 내용
- 중복/충돌 가능성이 있던 워크플로우 파일을 1개로 통일했습니다.
- 비개발자 기준으로 파일 역할을 이해하기 쉽게 루트 설명 문서를 추가했습니다.
