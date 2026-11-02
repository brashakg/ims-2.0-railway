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
  isDay,
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
import { backToQueueAddress, queueSort } from '../reviewQueue';

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

  it('keeps the other keys (page, focus); defaults left out, rows always in', () => {
    const f = readFilters(sp(''));
    const out = writeFilters({ ...f, brand: 'Oakley' }, sp('page=3&focus=P1'));
    expect(out.toString()).toBe('page=3&focus=P1&brand=Oakley&rows=50');
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
          updated_by: 'u2', updated_by_name: 'Akshay Ovhal', last_edited_at: '2026-10-11T05:00:00',
          // updated_at moves on writes that are not edits (archive, restore,
          // a PO cost): the edit line never reads it.
          updated_at: '2026-10-12T07:00:00',
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

describe('panel round', () => {
  it('a day that does not exist falls back to not chosen', () => {
    for (const day of ['2026-02-30', '2026-04-31', '2026-13-01', '0000-01-01']) {
      expect(isDay(day)).toBe(false);
      expect(readFilters(sp(`from=${day}&to=${day}`))).toMatchObject({ from: '', to: '' });
    }
    expect(isDay('2028-02-29')).toBe(true);
    expect(isDay('0001-01-01')).toBe(true); // the server reads it as no lower bound
  });

  it('Last edited stays in the address before any day is picked', () => {
    const f = { ...readFilters(sp('')), date_on: 'edited' as const };
    const out = writeFilters(f, sp(''));
    expect(out.toString()).toBe('date_on=edited&rows=50');
    const next = readFilters(writeFilters({ ...readFilters(out), from: '2026-10-01' }, out));
    expect(toProductParams(next)).toMatchObject({ date_on: 'edited', date_from: '2026-10-01' });
    // No day yet: nothing is sent, no chip, and Date added stays a plain address.
    expect(toProductParams(readFilters(out)).date_on).toBeUndefined();
    expect(activeChips(readFilters(out), { added: [], edited: [] })).toEqual([]);
    expect(writeFilters(readFilters(sp('')), sp('')).toString()).toBe('rows=50');
  });

  it('the Needs review list keeps only the filters it applies', () => {
    const f = readFilters(
      sp('q=ray&sort=name_asc&brand=Oakley&category=SUNGLASS&photo=missing&rows=100' +
        '&frame_color=Black&flag=any&added_by=u1&from=2026-10-01&inactive=1'),
      null,
      'review'
    );
    expect(f).toEqual({
      ...readFilters(sp('')),
      q: 'ray', sort: 'name_asc', brand: 'Oakley', category: 'SUNGLASS', photo: 'missing', rows: 100,
    });
    expect(activeChips(f, { added: [], edited: [] }).map((c) => c.label)).toEqual([
      'Search: ray', 'Category: SUNGLASS', 'Brand: Oakley', 'Photo missing',
    ]);
  });

  it('Back to queue returns to the review list as it was left', () => {
    const stash = {
      ids: ['A', 'B'],
      index: 1,
      filters: { page: 3, address: 'sort=name_asc&rows=25&page=3&brand=Oakley&focus=OLD' },
    };
    expect(backToQueueAddress(stash, 'B')).toBe(
      '/catalog/review?sort=name_asc&rows=25&page=3&brand=Oakley&focus=B'
    );
    // A stash written before the address was kept: its page only.
    expect(backToQueueAddress({ ids: [], index: 0, filters: { page: 3 } }, 'X y')).toBe(
      '/catalog/review?page=3&focus=X+y'
    );
    expect(backToQueueAddress(null, 'X')).toBe('/catalog/review?focus=X');
  });
});

describe('panel round: chips, Archived, the review queue order', () => {
  it("removing a Last edited range's chip puts Date range back to Date added", () => {
    const f = readFilters(sp('date_on=edited&from=2026-10-01&to=2026-10-12'));
    expect(f.date_on).toBe('edited');
    const out = removeChip(f, 'dates');
    expect([out.from, out.to, out.date_on]).toEqual(['', '', 'added']);
    expect(writeFilters(out, sp('date_on=edited&from=2026-10-01')).get('date_on')).toBeNull();
  });

  it('Flags: Archived only for an admin; anyone else reads not chosen', () => {
    expect(readFilters(sp('flag=archived')).flag).toBe('');
    expect(readFilters(sp('flag=archived'), null, 'catalog', true).flag).toBe('archived');
    expect(readFilters(sp('flag=any')).flag).toBe('any');
  });

  it('the review queue sorts newest first unless the address says otherwise', () => {
    expect(readFilters(sp(''), null, 'review').sort).toBe('newest');
    expect(readFilters(sp('sort=edited'), null, 'review').sort).toBe('edited');
    const review = readFilters(sp(''), null, 'review');
    expect(writeFilters(review, sp(''), 'review').get('sort')).toBeNull();
    expect(writeFilters({ ...review, sort: 'edited' }, sp(''), 'review').get('sort')).toBe('edited');
    expect(queueSort(null)).toBe('newest');
    expect(queueSort({ ids: [], index: 0, filters: { address: 'sort=name_asc&page=2' } })).toBe(
      'name_asc'
    );
    expect(queueSort({ ids: [], index: 0, filters: { address: 'sort=bogus' } })).toBe('newest');
  });
});

describe('round 4: rows in every address, a chip removes only its own filter', () => {
  it('the address always carries rows per page, 50 too', () => {
    expect(writeFilters(readFilters(sp(''), 200), sp('page=3')).toString()).toBe('page=3&rows=200');
    expect(writeFilters(readFilters(sp('rows=50'), 200), sp('page=3')).get('rows')).toBe('50');
    expect(writeFilters(readFilters(sp(''), null), sp('')).get('rows')).toBe('50');
  });

  it("each chip's X removes its own filter and leaves every other one", () => {
    const f = readFilters(
      sp(
        'q=ray&category=SUNGLASS&brand=Carrera&frame_color=Black&lens_colour=Grey&gender=Men' +
          '&shape=Round&frame_type=Full+Rim&frame_material=Acetate&added_by=u1&edited_by=none' +
          '&date_on=edited&from=2026-10-01&to=2026-10-12&flag=any&photo=missing&inactive=1' +
          '&sort=name_asc&rows=100'
      )
    );
    const chips = activeChips(f, { added: [], edited: [] });
    expect(chips).toHaveLength(15);
    for (const chip of chips) {
      const out = removeChip(f, chip.key);
      const left = activeChips(out, { added: [], edited: [] }).map((c) => c.key);
      expect(left).toEqual(chips.map((c) => c.key).filter((k) => k !== chip.key));
      expect([out.sort, out.rows]).toEqual(['name_asc', 100]);
    }
    expect(removeChip(f, 'brand')).toEqual({ ...f, brand: '' });
  });
});

describe('every option filter is a tick-box multi-select (owner 2026-11-01)', () => {
  it('ticks round-trip through the address as comma lists; an old single-value link still reads', () => {
    const f = readFilters(
      sp(
        'brand=Carrera,Oakley&frame_color=Black,Gold&added_by=u-mansi,u-durga' +
          '&edited_by=u-ak,none&category=SUNGLASS,FRAME&flag=any,price_check'
      )
    );
    expect([f.brand, f.frame_color, f.added_by, f.edited_by, f.category, f.flag]).toEqual([
      'Carrera,Oakley', 'Black,Gold', 'u-mansi,u-durga', 'u-ak,none', 'SUNGLASS,FRAME', 'any,price_check',
    ]);
    const out = writeFilters(f, sp(''));
    expect(out.get('added_by')).toBe('u-mansi,u-durga');
    expect(readFilters(out)).toEqual(f);
    expect(toProductParams(f)).toMatchObject({
      brand: 'Carrera,Oakley', created_by: 'u-mansi,u-durga', updated_by: 'u-ak,none',
      frame_color: 'Black,Gold', category: 'SUNGLASS,FRAME', flag: 'any,price_check',
    });
    // An old single-value link.
    expect(readFilters(sp('added_by=u-mansi&edited_by=none')).added_by).toBe('u-mansi');
    expect(toProductParams(readFilters(sp('added_by=u-mansi'))).created_by).toBe('u-mansi');
  });

  it('cleans ticks the way the server does: trimmed, no blanks or repeats, at most 50', () => {
    expect(readFilters(sp('frame_color= Black ,,Black,Gold,')).frame_color).toBe('Black,Gold');
    const many = Array.from({ length: 60 }, (_, i) => `u${i}`).join(',');
    expect(readFilters(sp(`added_by=${many}`)).added_by.split(',')).toHaveLength(50);
    // Flags: unknown ticks dropped, Archived only for an admin.
    expect(readFilters(sp('flag=any,burn,archived')).flag).toBe('any');
    expect(readFilters(sp('flag=any,burn,archived'), null, 'catalog', true).flag).toBe('any,archived');
  });

  it('one chip per filter naming every tick', () => {
    const people = {
      added: [
        { value: 'u-mansi', label: 'Mansi', count: 3 },
        { value: 'u-durga', label: 'Durga', count: 2 },
      ],
      edited: [{ value: 'u-ak', label: 'Akshay', count: 2 }],
    };
    const f = readFilters(
      sp(
        'added_by=u-mansi,u-durga&edited_by=u-ak,none&frame_color=Black,Gold&flag=any,price_check' +
          '&category=SUNGLASS,FRAME'
      )
    );
    expect(activeChips(f, people, (v) => v.toLowerCase(), NOW).map((c) => c.label)).toEqual([
      'Category: sunglass, frame',
      'Frame colour: Black, Gold',
      'Added by: Mansi, Durga',
      'Last edited by: Akshay, Never edited',
      'Flags: Any flag open, Price check',
    ]);
    expect(removeChip(f, 'added_by')).toEqual({ ...f, added_by: '' });
  });

  it('Last edited by sits on the main row, so it no longer counts on More filters', () => {
    expect(moreFiltersCount(readFilters(sp('edited_by=u-ak,none')))).toBe(0);
  });
});
