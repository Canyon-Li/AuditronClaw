// 占位首屏:验证静态托管与 token 透传链路;界面组件(审批卡/工具条等)随后续取件落位。
function readToken(): string | null {
  const token = new URLSearchParams(window.location.search).get('token')
  return token === '' ? null : token
}

export default function App() {
  const token = readToken()
  return (
    <main>
      <h1>AuditronClaw Web 终端</h1>
      <p>
        {token
          ? '已携带 token 进入,占位首屏——界面组件随后续取件落位。'
          : '未检测到 token,请从后端启动时打印的带 token 地址进入。'}
      </p>
    </main>
  )
}
