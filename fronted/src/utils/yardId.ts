/** PLC 协议场号与 yard_id 共用，固定 8 字节 ASCII */
export const PLC_YARD_ID_MAX_LEN = 8;

export function normalizeYardIdInput(raw: string): string {
  return raw.replace(/[^\x20-\x7E]/g, '').slice(0, PLC_YARD_ID_MAX_LEN);
}

export function validateYardId(yardId: string): string | null {
  const text = yardId.trim();
  if (!text) return '场号不能为空';
  if (text.length > PLC_YARD_ID_MAX_LEN) {
    return `场号最多 ${PLC_YARD_ID_MAX_LEN} 个字符（PLC ASCII）`;
  }
  if (!/^[\x20-\x7E]+$/.test(text)) {
    return '场号仅支持 ASCII 字符';
  }
  return null;
}
