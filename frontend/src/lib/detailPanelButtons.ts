/** 체크리스트/평가/Terraform 상세 패널 헤더용 버튼 (체크리스트 상세와 동일 계열) */
export const DETAIL_HEADER_BTN_BASE =
  'inline-flex items-center justify-center text-[11px] px-3 py-1.5 rounded-md font-medium transition-colors border';

/** 다운로드 (초록 tint) */
export const DETAIL_HEADER_BTN_DOWNLOAD =
  `${DETAIL_HEADER_BTN_BASE} bg-white text-green-700 border-green-200 hover:bg-green-50`;

/** 복사 (노랑 tint) */
export const DETAIL_HEADER_BTN_COPY =
  `${DETAIL_HEADER_BTN_BASE} bg-white text-amber-800 border-amber-300 hover:bg-amber-50`;
