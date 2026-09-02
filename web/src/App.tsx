// 首屏:带 token 进入终端页(WS 真流);无 token 给出提示。
// 锁屏卡片形态对齐操作员 v2 原型(2026-09-02):盾形图标 + 标题 + 说明 +
// URL 形态提示;不设「以演示会话进入」入口(演示流不进真实实现)。
import TerminalPage from './TerminalPage'
import { ShieldIcon } from './components/icons'

function readToken(): string | null {
  const token = new URLSearchParams(window.location.search).get('token')
  return token === '' ? null : token
}

export default function App() {
  const token = readToken()
  if (token) return <TerminalPage token={token} />
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="flex w-full max-w-105 flex-col items-center rounded-window bg-surface px-7 py-7.5 text-center shadow-card">
        <span className="mb-3.5 grid place-items-center text-accent">
          <ShieldIcon size={40} strokeWidth={1.6} />
        </span>
        <h1 className="text-base font-semibold text-ink">AuditronClaw Web 终端</h1>
        <p className="mt-2 text-[12.5px] leading-[1.65] text-ink-2">
          未检测到 token。
          <br />
          请从后端启动时打印的带 token 地址进入。
        </p>
        <div className="mt-4 w-full rounded-control bg-inset px-3 py-2.25 font-mono text-[11.5px] text-ink-2 [overflow-wrap:anywhere] shadow-hairline">
          http://&lt;host&gt;:&lt;port&gt;/?token=&lt;会话令牌&gt;
        </div>
      </div>
    </main>
  )
}
