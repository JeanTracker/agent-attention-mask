# PRODUCT POLICY

* **P-101 (Non-Interference)**: 오버레이 러너는 에이전트의 내부 실행 로직이나 반환 코드를 왜곡해서는 안 되며, 단순 시각적 래퍼로서만 기능한다.
* **P-102 (Instant Responsiveness)**: 사용자가 입력을 시도할 때 지연 시간 없이 즉각적으로 에이전트 터미널로 제어권이 반환되어야 한다.
* **P-103 (Clean Scrollback Guarantee)**: 스크롤백 오염은 제품의 실패로 간주한다. cmatrix 텍스트는 일반 버퍼에 절대 흘러들어가지 않는다.
