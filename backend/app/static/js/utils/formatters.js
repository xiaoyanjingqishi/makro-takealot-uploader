/**
 * 通用格式化工具库
 */
export function formatDateTime(str) {
  if (!str) return '';
  const d = new Date(str);
  if (isNaN(d.getTime())) return str;
  const pad = (n) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatPrice(val, currency = 'R') {
  if (val === null || val === undefined || isNaN(val)) return '-';
  return `${currency} ${Number(val).toFixed(2)}`;
}
