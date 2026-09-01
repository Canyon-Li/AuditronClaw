/* 本仓自绘:操作员 v2 原型(2026-09-02)定义的界面图标,非 beautifului 取件。
 * 盾形 = AuditronClaw 品牌位(锁屏页 / 页头品牌行共用)。 */

export function ShieldIcon({
  size = 16,
  strokeWidth = 1.8,
}: {
  size?: number;
  strokeWidth?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 2l8 3v6c0 5-3.4 8.6-8 11-4.6-2.4-8-6-8-11V5z" />
      <path d="M9.5 11.5l2 2 3.5-3.5" />
    </svg>
  );
}
