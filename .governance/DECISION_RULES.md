# DECISION RULES (Source of Truth)

1. **FORBIDDEN 판정**: 시스템 무결성 파괴, 보안 위험, credential 노출 시 즉시 FORBIDDEN으로 중단.
2. **HUMAN_REQUIRED 판정**: 사용자 경험(UX) 규격 변경, 성공 기준(SC) 수정, 스크롤백 보존 정책 변경 시 HUMAN_REVIEW_QUEUE에 등록.
3. **AUTONOMOUS 판정**: 내부 PTY 제어 루프, 버퍼 관리, 터미널 제어 시퀀스 최적화 등 구현 세부사항은 자율 수행.
4. **Escalation 절차**: 실행 에이전트(Claude 등)는 HUMAN_REQUIRED 발생 시 독단적으로 결정하지 않고 대기 큐에 등록 후 독립 작업 속행.
