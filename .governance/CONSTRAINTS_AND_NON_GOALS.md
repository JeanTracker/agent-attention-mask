# CONSTRAINTS & NON-GOALS

## CONSTRAINTS (제약사항)
- macOS (Darwin) 및 iTerm2 우선 지원
- 외부 윈도우 매니저나 GUI 프레임워크 의존성 금지 (순수 터미널 CLI)
- 가상 PTY 및 메모리 버퍼로 인한 시스템 부하 최소화 (< 50MB RAM)

## NON-GOALS (현재 하지 않을 것)
- Windows 콘솔(cmd/powershell) 지원
- 복잡한 cmatrix 설정 GUI 창 제공
- Tmux 세션과의 복합 세션 매니징 (단일 PTY에만 집중)
