/**
 * 챗봇 도구 완료 후 대시보드/평가/Terraform 화면이 REST 목록을 다시 불러오도록
 * window CustomEvent로 신호를 보냅니다.
 */
export const CHAT_REFRESH_ASSESSMENTS_EVENT = 'aiops-chat:refresh-assessments';
export const CHAT_REFRESH_TERRAFORM_EVENT = 'aiops-chat:refresh-terraform';
