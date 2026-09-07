# AGENT AUTONOMY (Source of Truth)

## 1. AUTONOMOUS (자율 결정)
- 래퍼 구현 언어 선택 (단일 바이너리와 고성능 동시성 처리가 뛰어난 Go 언어 권장, 또는 Python ptyprocess)
- 내부 링 버퍼(Ring buffer) 크기 및 메모리 관리 알고리즘
- cmatrix 실행 방식 (시스템 cmatrix 바이너리 호출 또는 내장형 초경량 디지털 레인 렌더러 구현)
- 상세 ANSI 이스케이프 시퀀스 파싱 및 상태 머신 구현

## 2. AUTONOMOUS_WITH_RECORD (결정 후 기록)
- 외부 서드파티 라이브러리 추가 (예: `creack/pty`, `golang.org/x/term`)
- 에이전트의 입력 대기 상태(Prompt) 감지 휴리스틱 (PTY 읽기 타임아웃 윈도우 등)

## 3. HUMAN_REQUIRED (사람 승인 필수)
- CLI 인터페이스 및 기본 동작 규격 변경
- 지원 터미널 환경의 변경 (iTerm2 / xterm 호환성 이탈)
- G-001 목표 범위의 확장 또는 축소

## 4. FORBIDDEN (절대 수행 금지)
- sudo 권한 요구 또는 시스템 레벨 터미널 드라이버 변조
- 사용자 홈 디렉토리의 개인 설정 파일(.zshrc 등) 임의 덮어쓰기
- 인증 토큰 또는 환경변수 로깅/유출
