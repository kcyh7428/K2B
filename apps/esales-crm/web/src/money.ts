export function parseMinor(value: string): number {
  const match = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(value);
  if (!match) throw Error('Enter a nonnegative amount with at most two decimal places.');
  const minor = Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0'));
  if (minor > 10_000_000_000) throw Error('Amount exceeds the supported limit.');
  return minor;
}

export function decimalAmount(minor: number): string {
  if (!Number.isSafeInteger(minor) || minor < 0) throw Error('Invalid money amount.');
  return Math.floor(minor / 100) + '.' + String(minor % 100).padStart(2, '0');
}

export function paymentTotals(unitPrice: string, quantity: string, discountAmount: string) {
  if (!/^[1-9]\d{0,4}$/.test(quantity) || Number(quantity) > 10000) throw Error('Quantity must be a whole number from 1 to 10000.');
  const unit = parseMinor(unitPrice), subtotal = unit * Number(quantity), discount = parseMinor(discountAmount || '0');
  if (subtotal > 10_000_000_000 || discount > subtotal) throw Error('Check the subtotal and discount before recording payment.');
  return {unit, subtotal, discount, paid: subtotal - discount};
}
