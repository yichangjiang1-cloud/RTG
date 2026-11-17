import type { ReceiverLayout } from '../types';

/** GNSS1 是主天线，GNSS2 是从天线。默认 GNSS1 在航向起点。 */
export function sidePrimaryEnd(
  layout: ReceiverLayout | undefined,
  role: 'elec' | 'diesel',
): 'up' | 'down' {
  const elecLeft = (layout?.elec_receiver ?? 'left') !== 'right';
  const end = role === 'elec'
    ? (elecLeft ? layout?.left_primary_end : layout?.right_primary_end)
    : (elecLeft ? layout?.right_primary_end : layout?.left_primary_end);
  return end === 'up' ? 'up' : 'down';
}

export function gnssAtEnd(
  layout: ReceiverLayout | undefined,
  role: 'elec' | 'diesel',
  end: 'upstream' | 'downstream',
): 'GNSS1' | 'GNSS2' {
  const gnss1AtStart = sidePrimaryEnd(layout, role) !== 'up';
  const atStart = end === 'downstream';
  return gnss1AtStart === atStart ? 'GNSS1' : 'GNSS2';
}

export function tireCenterLabel(
  layout: ReceiverLayout | undefined,
  role: 'elec' | 'diesel',
  end: 'upstream' | 'downstream',
): string {
  const side = role === 'elec' ? '电气' : '动力';
  return `${side} ${gnssAtEnd(layout, role, end)} 轮胎中心`;
}

export function headingAlongGnss(layout: ReceiverLayout | undefined): string {
  const elec = sidePrimaryEnd(layout, 'elec');
  const diesel = sidePrimaryEnd(layout, 'diesel');
  if (elec === 'down' && diesel === 'down') return '航向从 GNSS1 指向 GNSS2';
  if (elec === 'up' && diesel === 'up') return '航向从 GNSS2 指向 GNSS1';
  return '航向沿大车纵轴，从一端的两台天线指向另一端';
}
