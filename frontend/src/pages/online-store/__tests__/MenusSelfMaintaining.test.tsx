// ============================================================================
// IMS 2.0 - the Menus page shows the SELF-MAINTAINING menu (owner 2026-10-29)
// ============================================================================
// "Something in IMS that generates menus directly from collections that have
// products, rather than static pushes -- /online-store/menus doesn't reflect
// that." The page must show: the template (editable for ADMIN), a live preview
// with product counts, the hidden-because-empty list, last generated / pushed
// + result, a "Regenerate & push now" press, the "updates itself after each
// sync" line, other menus read-only, and the Sync page Menus card line.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];

vi.mock('../../../services/api/onlineStore', () => ({
  menusApi: { list: vi.fn(), get: vi.fn() },
  pushApi: { pushMenu: vi.fn() },
  generatedMenusApi: { list: vi.fn(), saveTemplate: vi.fn() },
}));

vi.mock('../../../components/online-store/OnlineStoreSyncBanner', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../components/online-store/OnlineStoreSyncBanner')>();
  return { ...actual, __esModule: true, default: () => null, SyncChip: () => null };
});

let roles = ['ADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u1', roles },
    hasRole: (want: string[]) => want.some((r) => roles.includes(r)),
  }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: (m: string) => toastCalls.push({ kind: 'success', msg: m }),
    error: (m: string) => toastCalls.push({ kind: 'error', msg: m }),
    info: (m: string) => toastCalls.push({ kind: 'info', msg: m }),
    warning: (m: string) => toastCalls.push({ kind: 'warning', msg: m }),
  }),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

import MenusPage from '../MenusPage';
import { GeneratedMenuLine } from '../OnlineShopifySyncPage';
import { generatedMenusApi, menusApi, pushApi } from '../../../services/api/onlineStore';

const TEMPLATE = {
  sections: [
    { kind: 'collection', title: 'New Arrivals', handle: 'new-arrivals' },
    {
      kind: 'panel',
      title: 'Sunglasses',
      handle: 'sunglasses',
      groups: [{ title: 'Top 10 Brands', source: 'top_brands', limit: 10 }],
    },
    { kind: 'links', title: 'About Us', url: '/pages/about-us', links: [{ title: 'Contact Us', url: '/pages/contact-us' }] },
  ],
};

const MENU = {
  menu_id: 'm1',
  handle: 'main-menu',
  title: 'Boost Mega Menu',
  template: TEMPLATE,
  items: [],
  hidden: [],
  shopify_menu_id: 'gid://shopify/Menu/174363639995',
  last_generated_at: '2026-10-29T03:30:00+00:00',
  last_pushed_at: null,
  last_push: { at: '2026-10-29T03:30:00+00:00', mode: 'LIVE', ok: false, error: 'Menu items limit reached' },
  in_sync: false,
  pushed_live: true,
  pushed_collection_nodes: 40,
  preview: {
    items: [
      { title: 'New Arrivals', item_type: 'COLLECTION', handle: 'new-arrivals', count: 12 },
      {
        title: 'Sunglasses',
        item_type: 'COLLECTION',
        handle: 'sunglasses',
        count: 700,
        children: [
          { title: 'Top 10 Brands', item_type: 'HTTP', url: '#', children: [
            { title: 'Ray-Ban', item_type: 'COLLECTION', handle: 'ray-ban', count: 138 },
          ] },
        ],
      },
    ],
    hidden: [{ section: 'Eyeglasses', title: 'Eyeglasses', handle: 'eyeglasses', reason: 'no products on the website' }],
    changed_since_push: true,
  },
};

beforeEach(() => {
  toastCalls.length = 0;
  roles = ['ADMIN'];
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  (generatedMenusApi.list as any).mockResolvedValue([MENU]);
  (menusApi.list as any).mockResolvedValue([
    { id: 'm1', handle: 'main-menu', title: 'Boost Mega Menu' },
    { id: 'f1', handle: 'footer', title: 'Footer' },
  ]);
  (menusApi.get as any).mockResolvedValue({ id: 'f1', handle: 'footer', title: 'Footer', items: [] });
  (pushApi.pushMenu as any).mockResolvedValue({ mode: 'LIVE', entity: 'menu', action: 'update', ok: true });
  (generatedMenusApi.saveTemplate as any).mockResolvedValue(MENU);
});

describe('Menus page: the self-maintaining menu', () => {
  it('shows the preview with counts, the hidden list, the status and the self-updating line', async () => {
    render(<MenusPage />);
    const preview = await screen.findByTestId('menu-preview');
    expect(within(preview).getByText('Ray-Ban')).toBeTruthy();
    expect(within(preview).getByText('138 products')).toBeTruthy();
    expect(within(preview).getByText('700 products')).toBeTruthy();
    const hidden = screen.getByTestId('hidden-list');
    expect(hidden.textContent).toMatch(/Eyeglasses:\s*Eyeglasses/);
    expect(within(hidden).getByText(/no products on the website/)).toBeTruthy();
    expect(screen.getByTestId('self-maintaining-note').textContent).toMatch(/updates itself after\s+every sync/);
    const last = screen.getByTestId('last-push');
    expect(last.textContent).toMatch(/FAILED/);
    expect(last.textContent).toMatch(/Menu items limit reached/);
    expect(last.textContent).toMatch(/Retried at the next sync/);
    expect(screen.getByText('Changes waiting for the next push')).toBeTruthy();
    // the static footer is listed read-only, not as the generated one
    expect(within(screen.getByTestId('other-menus')).getByText('Footer')).toBeTruthy();
    expect(within(screen.getByTestId('other-menus')).queryByText('Boost Mega Menu')).toBeNull();
  });

  it('Regenerate & push now confirms the LIVE overwrite with the link counts, then pushes', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<MenusPage />);
    const btn = await screen.findByRole('button', { name: /Regenerate & push now/ });
    await userEvent.click(btn);
    expect(confirm).toHaveBeenCalledWith(
      'This overwrites the LIVE website menu: 3 collection links (was 40 at the last push).',
    );
    await waitFor(() => expect(pushApi.pushMenu).toHaveBeenCalledWith('m1'));
    expect(toastCalls.some((t) => t.kind === 'success')).toBe(true);
  });

  it('cancelling the overwrite confirm pushes nothing', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MenusPage />);
    await userEvent.click(await screen.findByRole('button', { name: /Regenerate & push now/ }));
    expect(pushApi.pushMenu).not.toHaveBeenCalled();
  });

  it('a failed push shows an error toast with the reason, never a success', async () => {
    (pushApi.pushMenu as any).mockResolvedValue({ mode: 'LIVE', entity: 'menu', ok: false, error: 'X-throttled' });
    render(<MenusPage />);
    await userEvent.click(await screen.findByRole('button', { name: /Regenerate & push now/ }));
    await waitFor(() => expect(toastCalls.some((t) => t.kind === 'error' && t.msg.includes('X-throttled'))).toBe(true));
    expect(toastCalls.some((t) => t.kind === 'success')).toBe(false);
  });

  it('a rejected template save shows an error toast and keeps the edit', async () => {
    (generatedMenusApi.saveTemplate as any).mockRejectedValue(
      Object.assign(new Error('Request failed'), { response: { status: 400, data: { detail: 'section 1: needs a title' } } }),
    );
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    await userEvent.type(titles[0], '!');
    await userEvent.click(screen.getByRole('button', { name: /Save template/ }));
    await waitFor(() => expect(toastCalls.some((t) => t.kind === 'error')).toBe(true));
    expect(toastCalls.some((t) => t.kind === 'success')).toBe(false);
    expect((screen.getAllByLabelText('Section title') as HTMLInputElement[])[0].value).toBe('New Arrivals!');
  });

  it('pushing (confirmed) or Refresh never throws away an unsaved template edit', async () => {
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    await userEvent.clear(titles[1]);
    await userEvent.type(titles[1], 'Shades');
    await userEvent.click(screen.getByRole('button', { name: /Regenerate & push now/ }));
    await waitFor(() => expect(pushApi.pushMenu).toHaveBeenCalled());
    await waitFor(() => expect(generatedMenusApi.list).toHaveBeenCalledTimes(2));
    expect((screen.getAllByLabelText('Section title') as HTMLInputElement[])[1].value).toBe('Shades');
    await userEvent.click(screen.getByRole('button', { name: /Refresh/ }));
    await waitFor(() => expect(generatedMenusApi.list).toHaveBeenCalledTimes(3));
    expect((screen.getAllByLabelText('Section title') as HTMLInputElement[])[1].value).toBe('Shades');
    expect((screen.getByRole('button', { name: /Save template/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('warns before leaving the tab while the template is unsaved', async () => {
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    await userEvent.type(titles[0], '!');
    const ev = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });

  it('an unreadable menu read shows the could-not-be-read card, not "not set up"', async () => {
    (generatedMenusApi.list as any).mockResolvedValue(null);
    render(<MenusPage />);
    expect(await screen.findByText(/could not be read from the server/)).toBeTruthy();
    expect(screen.queryByTestId('no-generated-menu')).toBeNull();
  });

  it('unreadable listings show that instead of an empty preview', async () => {
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, preview: { items: [], hidden: [],
      changed_since_push: false, unreadable: 'The website listings could not be read just now (x).' } }]);
    render(<MenusPage />);
    expect((await screen.findByTestId('preview-unreadable')).textContent).toMatch(/could not be read/);
    expect(screen.queryByText(/No collection has products/)).toBeNull();
  });

  it('dry-run mode and the waiting-for-a-person state are named as such', async () => {
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, pushed_live: false,
      last_push: { at: '2026-10-29T03:30:00+00:00', mode: 'SIMULATED', ok: true } }]);
    const { unmount } = render(<MenusPage />);
    expect((await screen.findByTestId('dry-run-note')).textContent).toBe('dry-run mode - not sent to the website');
    expect(screen.queryByText('Changes waiting for the next push')).toBeNull();
    unmount();
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, pushed_live: false, last_push: {
      at: '2026-10-29T03:30:00+00:00', mode: 'LIVE', ok: false, waiting: true,
      reason: 'waiting for the first push by a person (Regenerate & push now)' } }]);
    render(<MenusPage />);
    const last = await screen.findByTestId('last-push');
    expect(last.textContent).toMatch(/waiting for the first push by a person/);
    expect(last.textContent).not.toMatch(/FAILED/);
  });

  it('an ADMIN edits the template (section titles) and saves it', async () => {
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    expect(titles.map((t) => t.value)).toEqual(['New Arrivals', 'Sunglasses', 'About Us']);
    await userEvent.clear(titles[1]);
    await userEvent.type(titles[1], 'Shades');
    await userEvent.click(screen.getByRole('button', { name: /Save template/ }));
    await waitFor(() => expect(generatedMenusApi.saveTemplate).toHaveBeenCalled());
    const [, sent] = (generatedMenusApi.saveTemplate as any).mock.calls[0];
    expect(sent.sections[1].title).toBe('Shades');
    expect(sent.sections[1].groups[0].limit).toBe(10);
  });

  it('edits heading links, the panel collection, the group category and N (no snap to 10)', async () => {
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const urls = screen.getAllByLabelText('Heading URL') as HTMLInputElement[];
    expect(urls.map((u) => u.value)).toEqual(['', '/pages/about-us']); // Sunglasses panel, About Us
    await userEvent.clear(urls[1]);
    await userEvent.type(urls[1], '/pages/our-story');
    const head = screen.getByLabelText('Heading collection handle') as HTMLInputElement;
    expect(head.value).toBe('sunglasses');
    await userEvent.clear(head);
    await userEvent.type(head, 'shades');
    const n = screen.getByLabelText('How many brands') as HTMLInputElement;
    await userEvent.clear(n);
    await userEvent.type(n, '15');
    expect(n.value).toBe('15');
    await userEvent.click(screen.getByRole('button', { name: /Save template/ }));
    await waitFor(() => expect(generatedMenusApi.saveTemplate).toHaveBeenCalled());
    const [, sent] = (generatedMenusApi.saveTemplate as any).mock.calls[0];
    expect(sent.sections[2].url).toBe('/pages/our-story');
    expect(sent.sections[1].handle).toBe('shades');
    expect(sent.sections[1].groups[0].limit).toBe(15);
  });

  it('a gender / shape group picks its category', async () => {
    (generatedMenusApi.list as any).mockResolvedValue([{
      ...MENU,
      template: { sections: [{ kind: 'panel', title: 'Eyes', groups: [{ title: 'By Shape', source: 'shape' }] }] },
    }]);
    render(<MenusPage />);
    const cat = (await screen.findByLabelText('Group category')) as HTMLSelectElement;
    expect(cat.value).toBe('sunglasses');
    await userEvent.selectOptions(cat, 'eyeglasses');
    await userEvent.click(screen.getByRole('button', { name: /Save template/ }));
    await waitFor(() => expect(generatedMenusApi.saveTemplate).toHaveBeenCalled());
    expect((generatedMenusApi.saveTemplate as any).mock.calls[0][1].sections[0].groups[0].category).toBe('eyeglasses');
  });

  it('a dirty template asks before pushing, and cancelling that question pushes nothing', async () => {
    // Only the unsaved-changes question says no; the overwrite confirm would say yes.
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true);
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    await userEvent.type(titles[0], '!');
    await userEvent.click(screen.getByRole('button', { name: /Regenerate & push now/ }));
    expect(confirm.mock.calls[0][0]).toMatch(/unsaved changes/);
    expect(pushApi.pushMenu).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('a dirty template pushed anyway asks both questions, then pushes', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    await userEvent.type(titles[0], '!');
    await userEvent.click(screen.getByRole('button', { name: /Regenerate & push now/ }));
    await waitFor(() => expect(pushApi.pushMenu).toHaveBeenCalledWith('m1'));
    expect(confirm.mock.calls[0][0]).toMatch(/unsaved changes/);
    expect(confirm.mock.calls[1][0]).toMatch(/overwrites the LIVE website menu/);
    confirm.mockRestore();
  });

  it('never says dry-run on a LIVE store: waiting for a person, or not pushed yet', async () => {
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, pushed_live: false, push_mode: 'LIVE', last_push: {
      at: '2026-10-29T03:30:00+00:00', mode: 'LIVE', ok: false, waiting: true,
      reason: 'waiting for the first push by a person (Regenerate & push now)' } }]);
    const { unmount } = render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    expect(screen.queryByTestId('dry-run-note')).toBeNull();
    expect(screen.getByText(/Waiting for the first push by a person \(Regenerate/)).toBeTruthy();
    unmount();
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, pushed_live: false, push_mode: 'LIVE', last_push: null }]);
    const second = render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    expect(screen.queryByTestId('dry-run-note')).toBeNull();
    second.unmount();
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, pushed_live: false, last_push: null }]);
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    expect(screen.queryByTestId('dry-run-note')).toBeNull();
    expect(within(screen.getByTestId('menu-status')).getAllByText('not pushed yet').length).toBeGreaterThan(0);
  });

  it('in dry-run the push confirm says nothing is sent to the website', async () => {
    (generatedMenusApi.list as any).mockResolvedValue([{ ...MENU, pushed_live: false, push_mode: 'SIMULATED',
      last_push: null }]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<MenusPage />);
    await screen.findByTestId('dry-run-note');
    await userEvent.click(screen.getByRole('button', { name: /Regenerate & push now/ }));
    expect(confirm.mock.calls[0][0]).toMatch(/dry-run push.*nothing is sent to the website/);
    expect(confirm.mock.calls[0][0]).not.toMatch(/overwrites the LIVE/);
  });

  it('labels the last attempt LIVE or dry-run', async () => {
    const { unmount } = render(<MenusPage />);
    expect((await screen.findByTestId('last-push')).textContent).toMatch(/attemptLIVE - FAILED/);
    unmount();
    (generatedMenusApi.list as any).mockResolvedValue([
      { ...MENU, last_push: { at: '2026-10-29T03:30:00+00:00', mode: 'SIMULATED', ok: true } },
    ]);
    render(<MenusPage />);
    const last = await screen.findByTestId('last-push');
    expect(last.textContent).toMatch(/dry-run \(SIMULATED\) - ok/);
    expect(last.textContent).not.toMatch(/LIVE/);
  });

  it('an other menu that cannot be read says so instead of spinning', async () => {
    (menusApi.get as any).mockResolvedValue(null);
    render(<MenusPage />);
    const other = await screen.findByTestId('other-menus');
    await userEvent.click(within(other).getByRole('button', { name: /Footer/ }));
    expect(await within(other).findByText('Could not load this menu.')).toBeTruthy();
  });

  it('a catalog manager sees the template read-only and no push', async () => {
    roles = ['CATALOG_MANAGER'];
    render(<MenusPage />);
    await screen.findByTestId('menu-preview');
    expect(screen.queryByRole('button', { name: /Regenerate & push now/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Save template/ })).toBeNull();
    const titles = screen.getAllByLabelText('Section title') as HTMLInputElement[];
    expect(titles.every((t) => t.disabled)).toBe(true);
  });

  it('says so when no self-maintaining menu is set up yet', async () => {
    (generatedMenusApi.list as any).mockResolvedValue([]);
    render(<MenusPage />);
    expect(await screen.findByTestId('no-generated-menu')).toBeTruthy();
  });
});

describe('Sync page Menus card line', () => {
  it('names the generated menu and its last push', () => {
    render(
      <GeneratedMenuLine
        menus={[{ menu_id: 'm1', handle: 'main-menu', title: 'Main', last_pushed_at: null, item_count: 4,
          hidden_count: 2, last_push: { mode: 'LIVE', ok: false, error: 'Throttled' } }]}
      />,
    );
    const line = screen.getByTestId('generated-menu-line');
    expect(line.textContent).toMatch(/1 generated menu/);
    expect(line.textContent).toMatch(/2 hidden/);
    expect(line.textContent).toMatch(/FAILED: Throttled/);
    expect(line.textContent).toMatch(/last pushed never/);
  });

  it('a run that sent nothing on purpose is not a failure, and says why once', () => {
    const reason = 'waiting one run: Shopify is still counting the collections just updated (oakley); pushed on the next run';
    render(
      <GeneratedMenuLine
        menus={[{ menu_id: 'm1', handle: 'main-menu', title: 'Main', last_pushed_at: null, item_count: 4,
          hidden_count: 0, last_push: { mode: 'LIVE', ok: false, waiting: true, reason } }]}
      />,
    );
    const line = screen.getByTestId('generated-menu-line');
    const row = within(line).getByText(/not sent:/);
    expect(row.className).not.toMatch(/text-red-700/);
    expect((line.textContent ?? '').split(reason).length - 1).toBe(1);
    expect(line.textContent).not.toMatch(/FAILED/);
  });

  it('formats the last push time and the attempt mode', () => {
    render(
      <GeneratedMenuLine
        menus={[{ menu_id: 'm1', handle: 'main-menu', title: 'Main', last_pushed_at: '2026-10-29T03:30:00+00:00',
          item_count: 4, hidden_count: 0, last_push: { mode: 'SIMULATED', ok: true } }]}
      />,
    );
    const text = screen.getByTestId('generated-menu-line').textContent ?? '';
    expect(text).toMatch(/last pushed 29 Oct 2026.*9:00.*IST/i);
    expect(text).toMatch(/last attempt dry-run ok/);
    expect(text).not.toMatch(/hidden/);
  });
});
