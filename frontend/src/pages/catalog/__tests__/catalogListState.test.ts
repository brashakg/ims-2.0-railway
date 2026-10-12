// ============================================================================
// The Catalog page's choices live in the ADDRESS (owner 2026-10-12)
// ============================================================================
// Back, refresh and a shared link must keep every choice; an unknown value
// must fall back safely; the quick date picks and the More filters range set
// the SAME From / To; the ADDED column reads in India time. Expected values
// are written out by hand.

import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PAGE_SIZE,
  activeChips,
  addedColumn,
  clearFilters,
  dateChipLabel,
  moreFiltersCount,
  quickPickOf,
  quickRange,
  readFilters,
  removeChip,
  resultLine,
  toProductParams,
  whenIST,
  writeFilters,
} from '../catalogListState';

// 12 Oct 2026, 12:45 IST == 07:15 UTC.
const NOW = new Date('2026-10-12T07:15:00Z');

const sp = (s: string) => new URLSearchParams(s);

describe('the address round-trip', () => {
  it('every choice survives write -> read', () => {
    const f = readFilters(
      sp(
        'q=ray&sort=mrp_desc&category=SUNGLASS&brand=Carrera&frame_color=Black&lens_colour=Grey' +
          '&gender=Men&shape=Square&frame_type=Full+Rim&frame_material=Acetate&added_by=u-mansi' +
          '&edited_by=none&date_on=edited&from=2026-10-01&to=2026-10-12&flag=price_check' +
          '&photo=missing&inactive=1&rows=100'
      )
    );
    expect(f).toEqual({
      q: 'ray', sort: 'mrp_desc', category: 'SUNGLASS', brand: 'Carrera', frame_color: 'Black',
      lens_colour: 'Grey', gender: 'Men', shape: 'Square', frame_type: 'Full Rim',
      frame_material: 'Acetate', added_by: 'u-mansi', edited_by: 'none', date_on: 'edited',
      from: '2026-10-01', to: '2026-10-12', flag: 'price_check', photo: 'missing',
      inactive: true, rows: 100,
    });
    expect(readFilters(writeFilters(f, new URLSearchParams()))).toEqual(f);
  });

  it('keeps the other keys (page, focus) and leaves defaults out', () => {
    const f = readFilters(sp(''));
    const out = writeFilters({ ...f, brand: 'Oakley' }, sp('page=3&focus=P1'));
    expect(out.toString()).toBe('page=3&focus=P1&brand=Oakley');
  });

  it('unknown values fall back safely', () => {
    const f = readFilters(
      sp('sort=cheapest&flag=burn&photo=blurry&rows=7&date_on=never&from=2026-13-40&to=yesterday')
    );
    expect(f.sort).toBe('edited');
    expect(f.flag).toBe('');
    expect(f.photo).toBe('');
    expect(f.rows).toBe(DEFAULT_PAGE_SIZE);
    expect(f.date_on).toBe('added');
    expect([f.from, f.to]).toEqual(['', '']);
  });

  it('a From after To in the address drops the range', () => {
    const f = readFilters(sp('from=2026-10-12&to=2026-10-01'));
    expect([f.from, f.to]).toEqual(['', '']);
  });

  it('rows per page: the address wins, then this device, then 50', () => {
    expect(readFilters(sp('rows=25'), 200).rows).toBe(25);
    expect(readFilters(sp(''), 200).rows).toBe(200);
    expect(readFilters(sp(''), 13).rows).toBe(50);
  });
});

describe('the server parameters', () => {
  it('maps the address onto GET /products', () => {
    const f = readFilters(sp('added_by=u1&edited_by=u2&from=2026-10-01&flag=mine'));
    expect(toProductParams(f)).toMatchObject({
      sort: 'edited', created_by: 'u1', updated_by: 'u2', date_on: 'added',
      date_from: '2026-10-01', date_to: undefined, flag: 'mine', is_active: 'true',
    });
    expect(toProductParams(readFilters(sp('inactive=1'))).is_active).toBe('all');
    expect(toProductParams(readFilters(sp(''))).date_on).toBeUndefined();
  });
});

describe('quick picks and the range are one rule', () => {
  it('each pick is an India-day range', () => {
    expect(quickRange('today', NOW)).toEqual({ from: '2026-10-12', to: '2026-10-12' });
    expect(quickRange('yesterday', NOW)).toEqual({ from: '2026-10-11', to: '2026-10-11' });
    expect(quickRange('7d', NOW)).toEqual({ from: '2026-10-06', to: '2026-10-12' });
    expect(quickRange('month', NOW)).toEqual({ from: '2026-10-01', to: '2026-10-12' });
    expect(quickRange('', NOW)).toEqual({ from: '', to: '' });
  });

  it('00:10 IST is already the new India day', () => {
    // 11 Oct 18:40 UTC == 12 Oct 00:10 IST.
    expect(quickRange('today', new Date('2026-10-11T18:40:00Z')).from).toBe('2026-10-12');
  });

  it('the main row shows the pick a range equals, else custom', () => {
    const base = readFilters(sp(''));
    expect(quickPickOf(base, NOW)).toBe('');
    expect(quickPickOf({ ...base, from: '2026-10-06', to: '2026-10-12' }, NOW)).toBe('7d');
    expect(quickPickOf({ ...base, from: '2026-10-02', to: '2026-10-12' }, NOW)).toBe('custom');
    expect(
      quickPickOf({ ...base, date_on: 'edited', from: '2026-10-12', to: '2026-10-12' }, NOW)
    ).toBe('custom');
  });

  it('a custom range counts on the More filters button', () => {
    const base = readFilters(sp('gender=Men'));
    expect(moreFiltersCount(base)).toBe(1);
    expect(moreFiltersCount({ ...base, from: '2026-10-02', to: '2026-10-05' })).toBe(2);
  });
});

describe('chips', () => {
  const people = {
    added: [{ value: 'u-mansi', label: 'Mansi Kumari', count: 319 }],
    edited: [{ value: 'u-ak', label: 'Akshay Ovhal', count: 2 }],
  };

  it('one removable chip per chosen filter', () => {
    const f = readFilters(
      sp('added_by=u-mansi&frame_color=Black&gender=Men&from=2026-10-01&to=2026-10-12&edited_by=u-ak&flag=any')
    );
    expect(activeChips(f, people, (v) => v, NOW).map((c) => c.label)).toEqual([
      'Frame colour: Black',
      'Gender: Men',
      'Added by: Mansi Kumari',
      'Last edited by: Akshay Ovhal',
      'Added 1 Oct - 12 Oct',
      'Flags: Any flag open',
    ]);
  });

  it('removing a chip removes only that choice; Clear all keeps sort + rows', () => {
    const f = readFilters(sp('brand=Carrera&from=2026-10-01&to=2026-10-12&sort=name_asc&rows=100'));
    expect(removeChip(f, 'dates')).toEqual({ ...f, from: '', to: '', date_on: 'added' });
    expect(removeChip(f, 'brand').brand).toBe('');
    expect(clearFilters(f)).toEqual({ ...readFilters(sp('')), sort: 'name_asc', rows: 100 });
  });

  it('the date chip reads in plain words', () => {
    const f = readFilters(sp(''));
    expect(dateChipLabel({ ...f, from: '2026-10-12', to: '2026-10-12' }, NOW)).toBe('Added 12 Oct');
    expect(dateChipLabel({ ...f, date_on: 'edited', from: '2025-12-30' }, NOW)).toBe(
      'Edited from 30 Dec 2025'
    );
  });

  it('the result line', () => {
    expect(resultLine(38, 481, 'edited')).toBe('38 of 481 products, recently edited first');
    expect(resultLine(3, null, 'mrp_asc')).toBe('3 products, MRP low to high');
  });
});

describe('the ADDED column, India time', () => {
  it('today / yesterday / a date, then the edit line', () => {
    expect(whenIST('2026-10-12T07:09:00', NOW)).toBe('Today, 12:39 pm');
    expect(whenIST('2026-10-11T12:44:00', NOW)).toBe('Yesterday, 6:14 pm');
    expect(whenIST('2026-10-10T10:35:00', NOW)).toBe('10 Oct, 4:05 pm');
    expect(whenIST(undefined, NOW)).toBe('-');
  });

  it('who added it, when, and who last edited it', () => {
    expect(
      addedColumn(
        {
          created_by: 'u1', created_by_name: 'Mansi Kumari', created_at: '2026-10-12T07:09:00',
          updated_by: 'u2', updated_by_name: 'Akshay Ovhal', updated_at: '2026-10-11T05:00:00',
        },
        NOW
      )
    ).toEqual({ by: 'Mansi Kumari', when: 'Today, 12:39 pm', edited: 'Edited by Akshay Ovhal, Yesterday' });
    expect(
      addedColumn({ created_by_name: 'Mansi Kumari', created_at: '2026-10-03T05:17:00' }, NOW)
    ).toEqual({ by: 'Mansi Kumari', when: '3 Oct, 10:47 am', edited: 'Not edited since' });
    // An imported row with no spine: honest dashes.
    expect(addedColumn({}, NOW)).toEqual({ by: '-', when: '-', edited: '-' });
  });
});
