// INFRA-01 (PR #1432 Codex round 1): the k6 target guard must classify the
// host k6 actually connects to. Runs the real k6-load-test.js in a node VM
// with k6/http replaced by a recorder, so no request can leave the machine.
import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'

const SOURCE = fs
  .readFileSync(path.resolve(__dirname, '../../k6-load-test.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .replace('export const options', 'const options')
  .replace('export function setup', 'function setup')
  .replace('export default function (data)', 'function run(data)')

const CREDS = { K6_USERNAME: 'synthetic', K6_PASSWORD: 'synthetic' }

function runK6(env: Record<string, string>) {
  const calls: string[] = []
  const ctx = vm.createContext({
    __ENV: env,
    http: {
      post: (url: string) => { calls.push(`POST ${url}`); return { status: 200, json: () => 'tok' } },
      get: (url: string) => { calls.push(`GET ${url}`); return { status: 200 } },
    },
    check: () => true,
    sleep: () => {},
    group: (_n: string, f: () => void) => f(),
    Rate: class { add() {} },
  })
  try {
    vm.runInContext(`${SOURCE}\nrun(setup());`, ctx)
    return { accepted: true, calls }
  } catch (e) {
    return { accepted: false, error: String(e), calls }
  }
}

describe('k6-load-test.js target guard', () => {
  it.each([
    ['userinfo hides the real host', 'https://localhost:443@app.uniparallel.com/api/v1', {}],
    ['userinfo with remote opt-in', 'https://localhost@synthetic.invalid/api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['trailing-dot production host', 'https://app.uniparallel.com./api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['upper-case production host', 'https://APP.UniParallel.COM/api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['other Railway backend spelling', 'https://ims-2-0-railway-production.up.railway.app/api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['api subdomain', 'https://api.uniparallel.com/api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['vercel preview of production', 'https://ims-2-0-railway-git-main-avinashs-projects-b3cb6df8.vercel.app/api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['backslash authority', 'http://127.0.0.1\\@app.uniparallel.com/api/v1', {}],
    ['backslash in host', 'http://127.0.0.1\\evil/api/v1', {}],
    ['query in base', 'http://127.0.0.1:19599?x=@app.uniparallel.com/api/v1', {}],
    ['percent-escaped host', 'http://127.0.0.1%2eapp.uniparallel.com/api/v1', { K6_ALLOW_REMOTE: 'yes' }],
    ['port out of range', 'http://127.0.0.1:99999/api/v1', {}],
    ['non-http scheme', 'ftp://127.0.0.1/api/v1', {}],
    ['no target', '', {}],
  ])('refuses %s and sends nothing', (_label, url, extra) => {
    const r = runK6({ API_URL: url, ...CREDS, ...extra })
    expect(r.accepted).toBe(false)
    expect(r.calls).toEqual([])
  })

  it('refuses a remote host without the explicit opt-in', () => {
    expect(runK6({ API_URL: 'https://synthetic.invalid/api/v1', ...CREDS }).accepted).toBe(false)
  })

  it.each([
    ['http://127.0.0.1:19599/api/v1', '127.0.0.1:19599'],
    ['http://localhost:18600/api/v1', 'localhost:18600'],
    ['http://[::1]:19599/api/v1', '[::1]:19599'],
  ])('accepts loopback %s, read-only', (url, authority) => {
    const r = runK6({ API_URL: url, ...CREDS })
    expect(r.accepted).toBe(true)
    expect(r.calls[0]).toBe(`POST http://${authority}/api/v1/auth/login`)
    expect(r.calls.slice(1).every((c) => c.startsWith(`GET http://${authority}/api/v1/`))).toBe(true)
  })

  it('accepts a disposable remote host only with K6_ALLOW_REMOTE=yes', () => {
    expect(runK6({ API_URL: 'https://staging.example.test/api/v1', K6_ALLOW_REMOTE: 'yes', ...CREDS }).accepted).toBe(true)
  })
})
