#!/usr/bin/env bash
# ==============================================================================
# AI Tri-Model Configurable Pipeline
# Step-by-Step Agent Dispatcher with Governance Context
# ==============================================================================
set -euo pipefail

TASK="${1:-}"
GOV_DIR=".governance"
PIPELINE_DIR=".ai_pipeline"
mkdir -p "$PIPELINE_DIR"

# 색상 정의
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_CYAN="\033[36m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_MAGENTA="\033[35m"
C_GRAY="\033[90m"

# ------------------------------------------------------------------------------
# 1. 설정 파일 (pipeline.config.env) 로드
# ------------------------------------------------------------------------------
CONFIG_FILE="pipeline.config.env"
if [ ! -f "$CONFIG_FILE" ] && [ -f "../$CONFIG_FILE" ]; then
  CONFIG_FILE="../$CONFIG_FILE"
fi

if [ -f "$CONFIG_FILE" ]; then
  echo -e "${C_BOLD}${C_CYAN}⚙️  설정 파일 로드됨: $CONFIG_FILE${C_RESET}"
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
else
  echo -e "${C_YELLOW}ℹ️  설정 파일($CONFIG_FILE)이 없어 기본 에이전트 매핑을 사용합니다.${C_RESET}"
fi

# 기본값 폴백 설정
STEP1_ENABLED="${STEP1_ENABLED:-true}"
STEP1_AGENT="${STEP1_AGENT:-agy}"
STEP1_MODEL="${STEP1_MODEL:-}"

STEP2_ENABLED="${STEP2_ENABLED:-true}"
STEP2_AGENT="${STEP2_AGENT:-codex}"
STEP2_MODEL="${STEP2_MODEL:-gpt-5.6-sol}"

STEP3_ENABLED="${STEP3_ENABLED:-true}"
STEP3_AGENT="${STEP3_AGENT:-claude}"
STEP3_MODEL="${STEP3_MODEL:-}"

STEP4_ENABLED="${STEP4_ENABLED:-true}"
STEP4_AGENT="${STEP4_AGENT:-codex}"
STEP4_MODEL="${STEP4_MODEL:-gpt-5.6-sol}"

STEP5_ENABLED="${STEP5_ENABLED:-true}"
STEP5_AGENT="${STEP5_AGENT:-agy}"
STEP5_MODEL="${STEP5_MODEL:-}"

# ------------------------------------------------------------------------------
# 에이전트 실행 공통 디스패처 함수
# ------------------------------------------------------------------------------
execute_step_agent() {
  local agent="$1"
  local model="$2"
  local prompt="$3"
  local out_file="${4:-}"
  local agy_mode="${5:-}"

  local agent_display=""
  case "$agent" in
    agy) agent_display="🏗️ Antigravity (agy)" ;;
    codex) agent_display="☀️ ChatGPT (codex)" ;;
    claude) agent_display="⚡ Claude Code (claude)" ;;
    *) agent_display="🤖 $agent" ;;
  esac

  echo -e "${C_YELLOW}▶ 실행 에이전트: ${C_BOLD}$agent_display${C_RESET} ${model:+(모델: $model)}"

  case "$agent" in
    agy)
      local cmd=("agy" "-p" "$prompt" "--dangerously-skip-permissions")
      [ -n "$model" ] && cmd+=("--model" "$model")
      [ -n "$agy_mode" ] && cmd+=("--mode" "$agy_mode")
      if [ -n "$out_file" ]; then
        "${cmd[@]}" > "$out_file"
      else
        "${cmd[@]}"
      fi
      ;;
    codex)
      local cmd=("codex" "exec" "--skip-git-repo-check" "-a" "never")
      [ -n "$model" ] && cmd+=("-m" "$model")
      cmd+=("$prompt")
      if [ -n "$out_file" ]; then
        "${cmd[@]}" -o "$out_file" </dev/null >/dev/null 2>&1 || {
          "${cmd[@]}" </dev/null > "$out_file" 2>&1
        }
      else
        "${cmd[@]}" </dev/null
      fi
      ;;
    claude)
      local cmd=("claude" "-p" "$prompt" "--permission-mode" "auto")
      [ -n "$model" ] && cmd+=("--model" "$model")
      if [ -n "$out_file" ]; then
        "${cmd[@]}" </dev/null > "$out_file" 2>&1 || true
      else
        "${cmd[@]}" </dev/null || true
      fi
      ;;
    *)
      echo "오류: 지원되지 않는 에이전트 '$agent' 입니다. (agy, codex, claude 중 선택)"
      exit 1
      ;;
  esac
}

log_step() {
  echo -e "\n${C_BOLD}${C_CYAN}================================================================${C_RESET}"
  echo -e "${C_BOLD}${C_GREEN}[Step $1] $2${C_RESET}"
  echo -e "${C_BOLD}${C_CYAN}================================================================${C_RESET}\n"
}

# ------------------------------------------------------------------------------
# 0. Governance Package 확인
# ------------------------------------------------------------------------------
GOV_CONTEXT=""
if [ -d "$GOV_DIR" ] && [ -f "$GOV_DIR/CURRENT_GOAL.md" ]; then
  echo -e "${C_BOLD}${C_GREEN}✓ Active Governance Package 감지됨 ($GOV_DIR/)${C_RESET}"
  GOV_CONTEXT="
[Governance Context]
- CURRENT GOAL: $(cat "$GOV_DIR/CURRENT_GOAL.md" 2>/dev/null || true)
- SUCCESS CRITERIA: $(cat "$GOV_DIR/SUCCESS_CRITERIA.md" 2>/dev/null || true)
- NON-GOAL & CONSTRAINTS: $(cat "$GOV_DIR/CONSTRAINTS_AND_NON_GOALS.md" 2>/dev/null || true)
- AGENT AUTONOMY: $(cat "$GOV_DIR/AGENT_AUTONOMY.md" 2>/dev/null || true)
- PRODUCT & ENGINEERING POLICY: $(cat "$GOV_DIR/ENGINEERING_POLICY.md" 2>/dev/null || true)
"
  if [ -z "$TASK" ]; then
    TASK=$(cat "$GOV_DIR/CURRENT_GOAL.md")
  fi
else
  if [ -z "$TASK" ]; then
    echo "사용법: $0 \"구현하고자 하는 작업 목표\""
    echo "또는 .governance/CURRENT_GOAL.md 가 존재해야 합니다."
    exit 1
  fi
  echo -e "${C_YELLOW}ℹ️  .governance/ 가 없습니다. 단독 작업 모드로 진행합니다.${C_RESET}"
fi

# ------------------------------------------------------------------------------
# Step 1. 1차 분석 및 기획서 작성
# ------------------------------------------------------------------------------
PLAN_FILE="$PIPELINE_DIR/1_plan.md"
if [ "$STEP1_ENABLED" = "true" ]; then
  log_step "1/5" "전체 코드베이스 분석 및 구현 계획 수립"
  PROMPT_STEP1="당신은 수석 소프트웨어 아키텍트입니다.
현재 프로젝트의 전체 코드베이스를 분석하고, 아래 작업 목표를 달성하기 위한 구체적인 구현 계획서를 작성해주세요.

작업 목표:
$TASK
$GOV_CONTEXT

지침:
1. 영향받는 파일 목록 및 모듈 분석
2. 단계별 상세 구현 절차
3. Non-Goal 범위를 침범하지 않도록 주의하고, 기존 정책과 충돌하지 않도록 설계
결과는 순수 마크다운으로 작성해주세요."

  execute_step_agent "$STEP1_AGENT" "$STEP1_MODEL" "$PROMPT_STEP1" "$PLAN_FILE" "plan"
  echo -e "${C_GREEN}✓ 1단계 완료! 계획서 저장됨: $PLAN_FILE${C_RESET}"
else
  log_step "1/5" "1단계 스킵됨 (설정에 의해 비활성화)"
  [ ! -f "$PLAN_FILE" ] && echo "# 작업 목표: $TASK" > "$PLAN_FILE"
fi

# ------------------------------------------------------------------------------
# Step 2. 1차 기획 리뷰
# ------------------------------------------------------------------------------
PLAN_REVIEW_FILE="$PIPELINE_DIR/2_plan_review.md"
if [ "$STEP2_ENABLED" = "true" ]; then
  log_step "2/5" "1차 기획서 리뷰 및 Governance 정합성 검토"
  PROMPT_STEP2="당신은 시니어 소프트웨어 엔지니어이자 거버넌스 리뷰어입니다.
아래 작성된 구현 계획서를 면밀히 검토하고 피드백을 작성해주세요.

[작업 목표]
$TASK
$GOV_CONTEXT

[구현 계획서]
$(cat "$PLAN_FILE")

피드백 관점:
1. CURRENT GOAL 및 SUCCESS CRITERIA에 부합하는지
2. 놓친 엣지 케이스나 보안/성능 문제점
3. 불필요하게 범위를 넓힌 부분(Non-Goal 위반 여부)
4. 구현자가 반드시 주의해야 할 핵심 체크리스트 3가지"

  execute_step_agent "$STEP2_AGENT" "$STEP2_MODEL" "$PROMPT_STEP2" "$PLAN_REVIEW_FILE" ""
  echo -e "${C_GREEN}✓ 2단계 완료! 기획 리뷰 저장됨: $PLAN_REVIEW_FILE${C_RESET}"
else
  log_step "2/5" "2단계 스킵됨 (설정에 의해 비활성화)"
  [ ! -f "$PLAN_REVIEW_FILE" ] && echo "# 기획 리뷰 없음 (스킵됨)" > "$PLAN_REVIEW_FILE"
fi

# ------------------------------------------------------------------------------
# Step 3. 코드 구현 및 테스트
# ------------------------------------------------------------------------------
if [ "$STEP3_ENABLED" = "true" ]; then
  log_step "3/5" "자율 코드 작성 및 단위 테스트"
  PROMPT_STEP3="당신은 빠르고 정확한 시니어 개발자입니다.
다음 계획서와 피드백을 바탕으로 필요한 소스 코드를 직접 생성/수정하고 테스트를 통과시켜주세요.

[작업 목표]
$TASK
$GOV_CONTEXT

[1차 계획서]
$(cat "$PLAN_FILE")

[기획 리뷰 피드백]
$(cat "$PLAN_REVIEW_FILE")

자율성 및 안전 지침:
- AUTONOMOUS 범위 내의 코드 작성, 리팩터링, 테스트 작성을 자율적으로 완수하세요.
- 만약 작업 도중 비가역적 파괴(FORBIDDEN)나 제품 방향 변경(HUMAN_REQUIRED)이 필요할 경우, 임의로 확정하지 말고 .governance/HUMAN_REVIEW_QUEUE.md에 등록한 뒤 독립적인 작업부터 진행하세요.
- 프로젝트 내 빌드 또는 테스트 명령어가 있다면 실행하여 정상 통과를 확인하세요."

  execute_step_agent "$STEP3_AGENT" "$STEP3_MODEL" "$PROMPT_STEP3" "" ""
  echo -e "${C_GREEN}✓ 3단계 완료! 코드 작성이 완료되었습니다.${C_RESET}"
else
  log_step "3/5" "3단계 스킵됨 (설정에 의해 비활성화)"
fi

# ------------------------------------------------------------------------------
# Step 4. 2차 코드 리뷰 (Git Diff)
# ------------------------------------------------------------------------------
CODE_REVIEW_FILE="$PIPELINE_DIR/3_code_review.md"
if [ "$STEP4_ENABLED" = "true" ]; then
  log_step "4/5" "코드 변경사항(Git Diff) 2차 리뷰"
  DIFF_CONTENT=""
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    DIFF_CONTENT=$(git diff HEAD || git diff || true)
    if [ -z "$DIFF_CONTENT" ]; then
      DIFF_CONTENT=$(git status --short)
    fi
  else
    DIFF_CONTENT="Git 저장소가 아니므로 최근 수정된 파일들을 검토해주세요."
  fi

  PROMPT_STEP4="당신은 꼼꼼한 코드 리뷰어입니다.
방금 구현된 코드 변경사항(Diff)을 검토하고 개선점을 제안해주세요.

[원래 목표]
$TASK
$GOV_CONTEXT

[코드 변경사항]
$DIFF_CONTENT

피드백 기준:
1. SUCCESS CRITERIA를 충족하는 구현인지
2. 문법/로직 오류나 미처리된 예외가 있는지
3. 코드 스타일, 중복 제거 등 리팩토링 포인트
4. 보완해야 할 테스트 케이스"

  execute_step_agent "$STEP4_AGENT" "$STEP4_MODEL" "$PROMPT_STEP4" "$CODE_REVIEW_FILE" ""
  echo -e "${C_GREEN}✓ 4단계 완료! 코드 리뷰 저장됨: $CODE_REVIEW_FILE${C_RESET}"
else
  log_step "4/5" "4단계 스킵됨 (설정에 의해 비활성화)"
  [ ! -f "$CODE_REVIEW_FILE" ] && echo "# 코드 리뷰 없음 (스킵됨)" > "$CODE_REVIEW_FILE"
fi

# ------------------------------------------------------------------------------
# Step 5. 최종 리팩토링 및 검증
# ------------------------------------------------------------------------------
if [ "$STEP5_ENABLED" = "true" ]; then
  log_step "5/5" "전체 정합성 검사 및 최종 리팩토링"
  PROMPT_STEP5="당신은 전체 아키텍처의 품질을 책임지는 리드 엔지니어입니다.
코드 리뷰를 검토하고, 발견된 문제점을 해결하는 최종 리팩토링을 수행해주세요.

[원래 목표]
$TASK
$GOV_CONTEXT

[코드 리뷰 결과]
$(cat "$CODE_REVIEW_FILE")

지침:
1. 리뷰에서 지적된 잠재적 버그, 중복 코드, 스타일 문제를 모두 깔끔하게 리팩토링하세요.
2. 최종 프로젝트 빌드 및 전체 테스트를 실행하여 정상 동작을 검증하세요.
3. .governance/PROJECT_STATE.md 파일이 존재한다면, 이번 사이클의 실행 결과와 Evidence를 반영하여 업데이트하세요.
4. 작업 완료 후 최종 요약 보고서를 출력하세요."

  execute_step_agent "$STEP5_AGENT" "$STEP5_MODEL" "$PROMPT_STEP5" "" "accept-edits"
  echo -e "${C_GREEN}✓ 5단계 완료! 전체 파이프라인이 성공적으로 종료되었습니다.${C_RESET}"
else
  log_step "5/5" "5단계 스킵됨 (설정에 의해 비활성화)"
fi

echo -e "\n${C_BOLD}${C_GREEN}🎉 [완료] 파이프라인이 성공적으로 완주되었습니다!${C_RESET}"
echo -e "산출물 위치: ${C_CYAN}$PIPELINE_DIR/${C_RESET}\n"
