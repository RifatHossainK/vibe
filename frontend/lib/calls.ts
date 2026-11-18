import { getConfig } from './api';
import type { WsClient, WsServerMessage } from './ws';

export type CallType = 'audio' | 'video';
export type CallState = 'idle' | 'calling' | 'ringing' | 'connected' | 'ended';

export interface CallInvite {
  conversation_id: number;
  call_type: CallType;
  from_user_id: number;
  from_name: string;
  from_avatar_url: string | null;
}

export interface CallCallbacks {
  onRemoteStream: (stream: MediaStream) => void;
  onCallStateChange: (state: CallState, detail?: string) => void;
  onIncomingCall: (invite: CallInvite) => void;
  /** A new invite arrived while already in a call — it was auto-rejected. */
  onBusyInvite?: () => void;
  onCallEnded: (reason?: string) => void;
  onError: (message: string) => void;
  onPeerScreenShare: (sharing: boolean) => void;
  /** This user's own screen-share state changed (incl. browser stop). */
  onLocalScreenShare?: (sharing: boolean) => void;
}

const FALLBACK_ICE: RTCIceServer[] = [
  { urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] },
];

let cachedIce: RTCIceServer[] | null = null;
let iceFetch: Promise<RTCIceServer[]> | null = null;

/** ICE servers from GET /api/config, cached; STUN fallback if the fetch fails. */
export async function getIceServers(): Promise<RTCIceServer[]> {
  if (cachedIce) return cachedIce;
  if (!iceFetch) {
    iceFetch = getConfig()
      .then((cfg) => {
        const servers: RTCIceServer[] = (cfg.ice_servers ?? []).map((s) => ({
          urls: s.urls,
          username: s.username,
          credential: s.credential,
        }));
        return servers.length > 0 ? servers : FALLBACK_ICE;
      })
      .catch(() => FALLBACK_ICE);
  }
  cachedIce = await iceFetch;
  return cachedIce;
}

/**
 * UI-independent WebRTC call manager. Signalling goes through the existing
 * WsClient; React subscribes via callbacks. The server keeps call state —
 * this class only tracks the local peer connection and media.
 */
export class CallManager {
  private ws: WsClient;
  private cb: CallCallbacks;
  private pc: RTCPeerConnection | null = null;
  private localStream: MediaStream | null = null;
  private remoteStream: MediaStream | null = null;
  private screenStream: MediaStream | null = null;
  private cameraTrack: MediaStreamTrack | null = null;
  private conversationId: number | null = null;
  private callType: CallType = 'audio';
  private pendingInvite: CallInvite | null = null;
  private connectedAt: number | null = null;
  private ended = false;
  state: CallState = 'idle';

  constructor(ws: WsClient, callbacks: CallCallbacks) {
    this.ws = ws;
    this.cb = callbacks;
  }

  get isInCall(): boolean {
    return this.state === 'calling' || this.state === 'ringing' || this.state === 'connected';
  }

  get currentConversationId(): number | null {
    return this.conversationId;
  }

  get currentCallType(): CallType {
    return this.callType;
  }

  get currentLocalStream(): MediaStream | null {
    return this.localStream;
  }

  get currentRemoteStream(): MediaStream | null {
    return this.remoteStream;
  }

  private setState(state: CallState, detail?: string): void {
    this.state = state;
    this.cb.onCallStateChange(state, detail);
  }

  private async createPeerConnection(): Promise<RTCPeerConnection> {
    const iceServers = await getIceServers();
    const pc = new RTCPeerConnection({ iceServers });
    pc.onicecandidate = (e) => {
      if (e.candidate && this.conversationId !== null) {
        this.ws.send('call:ice', { conversation_id: this.conversationId, candidate: e.candidate.toJSON() });
      }
    };
    pc.ontrack = (e) => {
      if (!this.remoteStream) this.remoteStream = new MediaStream();
      // A new video track (e.g. the peer starting screen share) replaces
      // the previous video track — a stream renders only one video track.
      if (e.track.kind === 'video') {
        this.remoteStream.getVideoTracks().forEach((t) => {
          if (t.id !== e.track.id) this.remoteStream!.removeTrack(t);
        });
      }
      if (!this.remoteStream.getTracks().some((x) => x.id === e.track.id)) {
        this.remoteStream.addTrack(e.track);
      }
      this.cb.onRemoteStream(this.remoteStream);
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') {
        if (this.connectedAt === null) this.connectedAt = Date.now();
        this.setState('connected');
      } else if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
        // A transient disconnect may recover; only end on 'failed'.
        if (pc.connectionState === 'failed') this.endCallLocal('Connection failed');
      }
    };
    return pc;
  }

  private async acquireMedia(type: CallType): Promise<MediaStream> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: type === 'video' ? { width: { ideal: 1280 }, height: { ideal: 720 } } : false,
    });
    return stream;
  }

  private attachLocalTracks(): void {
    if (!this.pc || !this.localStream) return;
    this.localStream.getTracks().forEach((t) => {
      this.pc!.addTrack(t, this.localStream!);
      if (t.kind === 'video') this.cameraTrack = t;
    });
  }

  /** Outgoing call: get media, send call:invite, wait for accept → offer. */
  async startCall(conversationId: number, type: CallType): Promise<void> {
    if (this.isInCall) {
      this.cb.onError('Already in a call.');
      return;
    }
    this.conversationId = conversationId;
    this.callType = type;
    this.ended = false;
    this.connectedAt = null;
    try {
      this.localStream = await this.acquireMedia(type);
      this.pc = await this.createPeerConnection();
      this.attachLocalTracks();
    } catch {
      this.cleanupLocal();
      this.conversationId = null;
      this.cb.onError(
        type === 'video'
          ? 'Could not access camera or microphone. Allow permission in your browser and try again.'
          : 'Could not access microphone. Allow permission in your browser and try again.',
      );
      return;
    }
    this.ws.send('call:invite', { conversation_id: conversationId, call_type: type });
    this.setState('calling');
  }

  /** Accept an incoming call: get media, send call:accept, wait for the offer. */
  async acceptCall(invite?: CallInvite): Promise<void> {
    const inv = invite ?? this.pendingInvite;
    if (!inv) return;
    this.pendingInvite = null;
    this.conversationId = inv.conversation_id;
    this.callType = inv.call_type;
    this.ended = false;
    this.connectedAt = null;
    try {
      this.localStream = await this.acquireMedia(inv.call_type);
      this.pc = await this.createPeerConnection();
      this.attachLocalTracks();
    } catch {
      this.cleanupLocal();
      this.ws.send('call:reject', { conversation_id: inv.conversation_id });
      this.conversationId = null;
      this.cb.onError('Could not access microphone/camera. Allow permission in your browser and try again.');
      return;
    }
    this.ws.send('call:accept', { conversation_id: inv.conversation_id });
    this.setState('ringing');
  }

  /** Decline an incoming call without touching any media device. */
  rejectCall(): void {
    const inv = this.pendingInvite;
    this.pendingInvite = null;
    if (inv) {
      this.ws.send('call:reject', { conversation_id: inv.conversation_id });
    }
    this.setState('idle');
    this.cb.onCallEnded('Call declined');
  }

  /** Route one incoming WS message through the call state machine. */
  async handleMessage(msg: WsServerMessage): Promise<void> {
    switch (msg.type) {
      case 'call:invite': {
        const invite: CallInvite = {
          conversation_id: msg.conversation_id,
          call_type: msg.call_type,
          from_user_id: msg.from_user_id,
          from_name: msg.from_name,
          from_avatar_url: msg.from_avatar_url,
        };
        if (this.isInCall || this.pendingInvite) {
          // Already busy — auto-reject, never ring over an active call.
          this.ws.send('call:reject', { conversation_id: msg.conversation_id });
          this.cb.onBusyInvite?.();
          return;
        }
        this.pendingInvite = invite;
        this.cb.onIncomingCall(invite);
        break;
      }
      case 'call:accept': {
        if (!this.isInCall || this.state !== 'calling' || msg.conversation_id !== this.conversationId || !this.pc) return;
        try {
          const offer = await this.pc.createOffer();
          await this.pc.setLocalDescription(offer);
          this.ws.send('call:offer', { conversation_id: msg.conversation_id, sdp: offer });
        } catch {
          this.cb.onError('Could not start the call.');
          this.endCall();
        }
        break;
      }
      case 'call:offer': {
        if (msg.conversation_id !== this.conversationId || !this.pc) return;
        try {
          await this.pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
          const answer = await this.pc.createAnswer();
          await this.pc.setLocalDescription(answer);
          this.ws.send('call:answer', { conversation_id: msg.conversation_id, sdp: answer });
        } catch {
          this.cb.onError('Could not answer the call.');
          this.endCall();
        }
        break;
      }
      case 'call:answer': {
        if (msg.conversation_id !== this.conversationId || !this.pc) return;
        try {
          await this.pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
        } catch {
          this.cb.onError('Could not connect the call.');
          this.endCall();
        }
        break;
      }
      case 'call:ice': {
        if (msg.conversation_id !== this.conversationId || !this.pc || !msg.candidate) return;
        try {
          await this.pc.addIceCandidate(new RTCIceCandidate(msg.candidate));
        } catch {
          // A stale candidate is not fatal — ignore it.
        }
        break;
      }
      case 'call:screen': {
        if (msg.conversation_id !== this.conversationId) return;
        this.cb.onPeerScreenShare(msg.sharing);
        break;
      }
      case 'call:reject': {
        if (msg.conversation_id !== this.conversationId) return;
        this.endCallLocal('Call declined');
        break;
      }
      case 'call:end': {
        if (msg.conversation_id !== this.conversationId) return;
        const dur = msg.duration_seconds;
        this.endCallLocal(dur ? `Call ended · ${formatDuration(dur)}` : 'Call ended');
        break;
      }
      case 'call:busy': {
        if (msg.conversation_id !== this.conversationId) return;
        this.endCallLocal('User is busy');
        break;
      }
      case 'call:unavailable': {
        if (msg.conversation_id !== this.conversationId) return;
        this.endCallLocal('User is unavailable');
        break;
      }
      default:
        break;
    }
  }

  /** Mute/unmute the local microphone. Returns true when now muted. */
  toggleMute(): boolean {
    const track = this.localStream?.getAudioTracks()[0];
    if (!track) return false;
    track.enabled = !track.enabled;
    return !track.enabled;
  }

  /** Turn the local camera off/on. Returns true when the camera is now off. */
  toggleCamera(): boolean {
    const track = this.localStream?.getVideoTracks()[0];
    if (!track) return false;
    track.enabled = !track.enabled;
    return !track.enabled;
  }

  /** Share the screen instead of the camera (video calls only). */
  async startScreenShare(): Promise<boolean> {
    if (this.callType !== 'video' || !this.pc || !this.conversationId) return false;
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      const screenTrack = stream.getVideoTracks()[0];
      if (!screenTrack) return false;
      const sender = this.pc.getSenders().find((s) => s.track?.kind === 'video');
      if (sender) await sender.replaceTrack(screenTrack);
      this.screenStream = stream;
      screenTrack.onended = () => { void this.stopScreenShare(); };
      this.ws.send('call:screen', { conversation_id: this.conversationId, sharing: true });
      this.cb.onLocalScreenShare?.(true);
      return true;
    } catch {
      return false;
    }
  }

  /** Stop sharing and restore the camera track. */
  async stopScreenShare(): Promise<void> {
    if (!this.pc || !this.conversationId) return;
    const sender = this.pc.getSenders().find((s) => s.track?.kind === 'video');
    if (sender && this.cameraTrack) {
      try { await sender.replaceTrack(this.cameraTrack); } catch { /* ignore */ }
    }
    this.screenStream?.getTracks().forEach((t) => t.stop());
    this.screenStream = null;
    this.ws.send('call:screen', { conversation_id: this.conversationId, sharing: false });
    this.cb.onLocalScreenShare?.(false);
  }

  get isScreenSharing(): boolean {
    return this.screenStream !== null;
  }

  /** End the call: close the peer connection + tracks and send call:end. */
  endCall(): void {
    if (!this.isInCall && this.state !== 'ringing') {
      this.cleanupLocal();
      return;
    }
    const duration = this.connectedAt ? Math.floor((Date.now() - this.connectedAt) / 1000) : 0;
    const convId = this.conversationId;
    this.endCallLocal('Call ended');
    if (convId !== null) {
      this.ws.send('call:end', { conversation_id: convId, duration_seconds: duration });
    }
  }

  /** WS dropped mid-call: end locally with a "Connection lost" state. */
  handleWsClose(): void {
    if (this.isInCall) this.endCallLocal('Connection lost');
  }

  /** Local teardown without signalling (peer already ended / rejected / busy). */
  private endCallLocal(reason?: string): void {
    if (this.ended && this.state === 'idle') return;
    this.ended = true;
    this.pendingInvite = null;
    try { this.pc?.close(); } catch { /* ignore */ }
    this.pc = null;
    this.screenStream?.getTracks().forEach((t) => t.stop());
    this.screenStream = null;
    this.cleanupLocal();
    this.remoteStream = null;
    this.conversationId = null;
    this.connectedAt = null;
    this.cameraTrack = null;
    this.setState('idle');
    this.cb.onCallEnded(reason);
  }

  private cleanupLocal(): void {
    this.localStream?.getTracks().forEach((t) => t.stop());
    this.localStream = null;
  }
}

export function formatDuration(totalSeconds: number): string {
  const mm = String(Math.floor(totalSeconds / 60)).padStart(2, '0');
  const ss = String(totalSeconds % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}
