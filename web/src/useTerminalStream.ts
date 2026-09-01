/* WS 客户端 hook:连接即补发(last_seq 缺省 0 即全量重放,刷新后的画面
 * 重建走这条路径)、断线自动重连(携已见 last_seq 只补缺口)、seq 锚点
 * 规则(首帧重设锚点:大于 last_seq+1 是缓冲溢出丢窗口、续播;小于等
 * 于已见值是后端重启回卷、清空重建;连接内断序即本连接丢帧、携
 * last_seq 立即重连补缺口)。契约见 entry/web_ws.py 模块 docstring。 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Envelope, UpstreamMessage } from "./protocol";

export type ConnectionStatus = "connecting" | "open" | "reconnecting";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 8000;

export function useTerminalStream(token: string) {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [envelopes, setEnvelopes] = useState<Envelope[]>([]);
  const [protocolError, setProtocolError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const lastSeqRef = useRef(0); // 已见最大 seq,重连时带走
  const firstFrameRef = useRef(true); // 本连接是否还未见过流帧
  const attemptsRef = useRef(0); // 重连退避计数,连接成功即清零
  const closedRef = useRef(false); // 组件卸载后不再重连
  const quickReconnectRef = useRef(false); // 连接内断序:断开后立即携 last_seq 重连

  useEffect(() => {
    closedRef.current = false;

    const connect = () => {
      if (closedRef.current) return;
      const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(
        `${scheme}//${window.location.host}/ws?token=${encodeURIComponent(
          token,
        )}&last_seq=${lastSeqRef.current}`,
      );
      socketRef.current = socket;
      firstFrameRef.current = true;

      socket.onopen = () => {
        attemptsRef.current = 0;
        setStatus("open");
      };

      socket.onmessage = (message) => {
        let frame: Envelope;
        try {
          frame = JSON.parse(message.data) as Envelope;
        } catch {
          return; // 服务端只发 JSON,坏帧忽略不崩
        }
        if (frame.type === "protocol_error") {
          setProtocolError(frame.payload.code);
          return; // 错误帧不入回合流(seq=0 与流序无关)
        }
        if (firstFrameRef.current) {
          // 首帧重设锚点:缓冲溢出的丢窗口续播,进程重启的旧画面重建
          firstFrameRef.current = false;
          if (frame.seq <= lastSeqRef.current) {
            lastSeqRef.current = 0;
            setEnvelopes([]);
          }
        } else if (frame.seq !== lastSeqRef.current + 1) {
          // 连接内断序:本连接丢帧(慢消费),携 last_seq 立即重连补缺口
          quickReconnectRef.current = true;
          socket.close();
          return;
        }
        lastSeqRef.current = frame.seq;
        setEnvelopes((current) => [...current, frame]);
      };

      socket.onclose = () => {
        if (closedRef.current) return;
        const immediate = quickReconnectRef.current;
        quickReconnectRef.current = false;
        setStatus("reconnecting");
        const delay = immediate
          ? 0
          : Math.min(
              RECONNECT_BASE_MS * 2 ** attemptsRef.current,
              RECONNECT_MAX_MS,
            );
        attemptsRef.current += 1;
        window.setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closedRef.current = true;
      socketRef.current?.close();
    };
  }, [token]);

  const sendInput = useCallback((text: string): boolean => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      const frame: UpstreamMessage = { type: "input", text };
      socket.send(JSON.stringify(frame));
      return true;
    }
    // 未连接即弃,不排队重发(越权补账);由调用方如实提示未发送
    return false;
  }, []);

  return { status, envelopes, protocolError, sendInput };
}
