// 首屏:带 token 进入终端页(WS 真流);无 token 给出提示。
import TerminalPage from './TerminalPage'

function readToken(): string | null {
  const token = new URLSearchParams(window.location.search).get('token')
  return token === '' ? null : token
}

export default function App() {
  const token = readToken()
  if (token) return <TerminalPage token={token} />
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-105 flex-col items-center justify-center gap-2 px-4">
      <h1 className="text-[15px] font-medium text-ink">AuditronClaw Web 终端</h1>
      <p className="text-[13px] text-ink-2">未检测到 token,请从后端启动时打印的带 token 地址进入。</p>
    </main>
  )
}
