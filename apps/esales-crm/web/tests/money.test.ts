import assert from 'node:assert/strict';
import test from 'node:test';
import { parseMinor, decimalAmount, paymentTotals } from '../src/money.ts';

test('decimal amounts retain the exact entered cents', () => {
  for (const [input,want] of [['0.01',1],['0.29',29],['400',40000],['10.10',1010],['15400.00',1540000]] as const) {
    assert.equal(parseMinor(input),want);
    assert.equal(parseMinor(decimalAmount(want)),want);
  }
});

test('invalid precision and non-decimal inputs are refused rather than rounded', () => {
  for (const input of ['1.005','10.105','1e3','-1','Infinity','NaN','', '100000001']) {
    assert.throws(() => parseMinor(input));
  }
});

test('one ET4000 package costs exactly 400 and the renewal discount is fixed', () => {
  assert.deepEqual(paymentTotals('400','1','0'), {unit:40000,subtotal:40000,discount:0,paid:40000});
  assert.deepEqual(paymentTotals('1400','12','1400'), {unit:140000,subtotal:1680000,discount:140000,paid:1540000});
  assert.throws(() => paymentTotals('400','1','401'));
  assert.throws(() => paymentTotals('400','1.5','0'));
});
