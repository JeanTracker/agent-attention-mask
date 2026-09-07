# ASSUMPTIONS & HYPOTHESES

* **A-001 (Assumption)**: 타깃 환경은 macOS 기반 iTerm2이며, xterm-256color 호환 ANSI 제어 시퀀스를 완벽히 지원한다.
* **A-002 (Assumption)**: 실행 대상 에이전트는 표준 POSIX PTY 인터페이스를 통해 명령을 주고받는다.
* **H-001 (Hypothesis)**: PTY 버퍼에 일정 시간(예: 300~500ms) 동안 새로운 출력이 없고 커서가 대기 중이면 '사용자 입력 대기 상태'로 신뢰성 있게 판정할 수 있을 것이다.
