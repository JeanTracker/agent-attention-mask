# ENGINEERING POLICY

* **P-201 (Virtual PTY Isolation)**: 에이전트는 반드시 가상 PTY(master/slave)에서 실행되어야 하며, 터미널 직접 표준 입출력을 cmatrix와 공유하지 않는다.
* **P-202 (Alternate Screen Buffer Enforcement)**: cmatrix 렌더링은 반드시 ANSI `\033[?1049h` / `\033[?1049l` 제어 시퀀스를 통해 Alternate Screen Buffer 내에서만 수행한다.
* **P-203 (Buffer Flush on Exit)**: 대체 화면 해제 직후 메모리에 누적된 에이전트의 원본 출력을 일반 화면에 일괄 기록(Flush)하여 스크롤백을 복원한다.
* **P-204 (Mouse & Keyboard Tracking)**: Alternate Buffer 상태에서 마우스 이벤트(`\033[?1000h` / `\033[?1006h`) 및 키보드 raw mode 입력을 감지하여 즉시 탈출 트리거로 사용한다.
