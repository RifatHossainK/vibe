export type WsServerMessage =
  | { type: 'message'; message: import('./api').Message }
  | { type: 'message_deleted'; conversation_id: number; message_id: number; mode: 'everyone'; message: import('./api').Message }
  | { type: 'typing'; conversation_id: number; user_id: number; typing: boolean }
  | { type: 'presence'; user_id: number; online: boolean }
  | { type: 'read'; conversation_id: number; user_id: number }
  | { type: 'call:invite'; conversation_id: number; call_type: 'audio' | 'video'; from_user_id: number; from_name: string; from_avatar_url: string | null }
  | { type: 'call:accept'; conversation_id: number; from_user_id: number }
  | { type: 'call:reject'; conversation_id: number; from_user_id: number }
  | { type: 'call:end'; conversation_id: number; from_user_id: number; duration_seconds?: number }
  | { type: 'call:offer'; conversation_id: number; from_user_id: number; sdp: RTCSessionDescriptionInit }
  | { type: 'call:answer'; conversation_id: number; from_user_id: number; sdp: RTCSessionDescriptionInit }
  | { type: 'call:ice'; conversation_id: number; from_user_id: number; candidate: RTCIceCandidateInit | null }
  | { type: 'call:screen'; conversation_id: number; from_user_id: number; sharing: boolean }
  | { type: 'call:busy'; conversation_id: number }
  | { type: 'call:unavailable'; conversation_id: number }
  | { type: 'error'; detail: string };

export type WsHandler = (msg: WsServerMessage) => void;

function wsBaseUrl(): string {
  const env = process.env.NEXT_PUBLIC_WS_URL;
  if (env) return env.replace(/\/$/, '');
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}`;
}

export class WsClient {
  private ws: WebSocket | null = null;
  private token: string | null = null;
  private handlers = new Set<WsHandler>();
  private closeHandlers = new Set<() => void>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private closed = false;

  connect(token: string): void {
    this.token = token;
    this.closed = false;
    this.open();
  }

  private open(): void {
    if (!this.token) return;
    const url = `${wsBaseUrl()}/api/ws?token=${encodeURIComponent(this.token)}`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.reconnectAttempts = 0;
    };
    ws.onmessage = (ev: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(ev.data) as WsServerMessage;
        this.handlers.forEach((h) => h(msg));
      } catch {
        // ignore malformed frames
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.closeHandlers.forEach((h) => { try { h(); } catch { /* ignore */ } });
      if (this.closed) return;
      // auto-reconnect with exponential backoff, capped at 30s
      const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000);
      this.reconnectAttempts += 1;
      this.reconnectTimer = setTimeout(() => this.open(), delay);
    };
    ws.onerror = () => {
      // onclose will handle reconnect
      try { ws.close(); } catch { /* already closed */ }
    };
  }

  send(type: string, payload: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, ...payload }));
    }
  }

  onMessage(handler: WsHandler): () => void {
    this.handlers.add(handler);
    return () => { this.handlers.delete(handler); };
  }

  /** Fires whenever the socket closes (including a mid-call drop, before reconnect). */
  onClose(handler: () => void): () => void {
    this.closeHandlers.add(handler);
    return () => { this.closeHandlers.delete(handler); };
  }

  disconnect(): void {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) { try { this.ws.close(); } catch { /* ignore */ } this.ws = null; }
  }
}
