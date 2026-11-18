import { clearAuth, getToken } from './auth';

export interface User {
  id: number;
  display_name: string;
  username: string | null;
  email: string;
  role: string;
  avatar_url: string | null;
  created_at: string;
}
export interface PublicUser {
  id: number;
  display_name: string;
  username: string | null;
  email: string;
  avatar_url: string | null;
}
export type MessageKind = 'text' | 'image' | 'video' | 'audio' | 'voice' | 'call';
export interface Message {
  id: number;
  conversation_id: number;
  sender_id: number;
  kind: MessageKind;
  content: string | null;
  media_url: string | null;
  media_name: string | null;
  media_size: number | null;
  expires_at: string | null;
  expired: boolean;
  /** True when the message was deleted for everyone (content is stripped). */
  deleted?: boolean;
  deleted_for_everyone?: boolean;
  created_at: string;
  seen_by: number[];
}
export interface DeleteMessageResponse {
  ok: boolean;
  message_id: number;
  mode: 'me' | 'everyone';
  /** Deleted-placeholder dict for mode=everyone; null for mode=me. */
  message: Message | null;
}
export interface Conversation {
  id: number;
  other_user: PublicUser | null;
  is_group: boolean;
  name: string | null;
  participants: PublicUser[];
  disappearing_seconds: number;
  created_at: string;
  last_message: {
    id: number;
    sender_id: number;
    kind: MessageKind;
    content: string | null;
    created_at: string;
    expires_at?: string | null;
    expired?: boolean;
    deleted?: boolean;
    deleted_for_everyone?: boolean;
  } | null;
  last_message_at: string | null;
  unread_count: number;
}
export interface MediaItem {
  id: number;
  kind: MessageKind;
  media_url: string | null;
  media_name: string | null;
  media_size: number | null;
  created_at: string;
  sender_id: number;
}
export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}
export interface RegisterResponse extends User {
  access_token: string;
  token_type: string;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (init.body && !(init.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
  if (res.status === 401) {
    clearAuth();
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') window.location.href = '/login';
    throw new Error('Unauthorized — please log in again.');
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json() as { detail?: string | { msg?: string }[] };
      if (typeof data.detail === 'string') detail = data.detail;
      else if (Array.isArray(data.detail) && data.detail[0]?.msg) detail = data.detail[0].msg;
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function register(display_name: string, email: string, password: string, username?: string): Promise<RegisterResponse> {
  return request<RegisterResponse>('/api/auth/register', { method: 'POST', body: JSON.stringify({ display_name, email, password, username: username || undefined }) });
}
export async function updateProfile(data: { display_name?: string; username?: string }): Promise<User> {
  return request<User>('/api/users/me', { method: 'PATCH', body: JSON.stringify(data) });
}
export async function uploadAvatar(file: File): Promise<User> {
  const form = new FormData();
  form.append('file', file);
  return request<User>('/api/users/me/avatar', { method: 'POST', body: form });
}
export async function removeAvatar(): Promise<User> {
  return request<User>('/api/users/me/avatar', { method: 'DELETE' });
}
/** Full avatar URL for <img> src — appends the stored JWT as ?token=. */
export function avatarSrc(avatarUrl: string | null | undefined): string | null {
  if (!avatarUrl) return null;
  const token = getToken() ?? '';
  const sep = avatarUrl.includes('?') ? '&' : '?';
  return `${avatarUrl}${sep}token=${encodeURIComponent(token)}`;
}
export async function login(email: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
}
export async function me(): Promise<User> {
  return request<User>('/api/auth/me');
}
export async function searchUsers(q: string): Promise<PublicUser[]> {
  return request<PublicUser[]>(`/api/users/search?q=${encodeURIComponent(q)}`);
}
export async function getUser(id: number): Promise<User> {
  return request<User>(`/api/users/${id}`);
}
export async function getOnline(id: number): Promise<boolean> {
  const data = await request<{ online: boolean }>(`/api/users/${id}/online`);
  return data.online;
}
export async function listConversations(): Promise<Conversation[]> {
  return request<Conversation[]>('/api/conversations');
}
export async function createConversation(user_id: number): Promise<Conversation> {
  return request<Conversation>('/api/conversations', { method: 'POST', body: JSON.stringify({ user_id }) });
}
export async function createGroup(name: string, user_ids: number[]): Promise<Conversation> {
  return request<Conversation>('/api/conversations/groups', { method: 'POST', body: JSON.stringify({ name, user_ids }) });
}
export async function getConversation(id: number): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}`);
}
export async function updateGroupMembers(id: number, changes: { add?: number[]; remove?: number[] }): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}/members`, { method: 'PATCH', body: JSON.stringify(changes) });
}
export interface AppConfig {
  ice_servers: { urls: string[] | string; username?: string; credential?: string }[];
}
export async function getConfig(): Promise<AppConfig> {
  return request<AppConfig>('/api/config');
}
export async function listMessages(conversationId: number, limit = 50, beforeId?: number): Promise<Message[]> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (beforeId) q.set('before_id', String(beforeId));
  return request<Message[]>(`/api/conversations/${conversationId}/messages?${q.toString()}`);
}
export async function sendText(conversationId: number, content: string): Promise<Message> {
  return request<Message>(`/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ kind: 'text', content }),
  });
}
export async function deleteMessage(conversationId: number, messageId: number, mode: 'me' | 'everyone'): Promise<DeleteMessageResponse> {
  return request<DeleteMessageResponse>(`/api/conversations/${conversationId}/messages/${messageId}?mode=${mode}`, {
    method: 'DELETE',
  });
}
export async function markRead(conversationId: number): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/conversations/${conversationId}/read`, { method: 'POST' });
}
export async function listMedia(conversationId: number): Promise<MediaItem[]> {
  return request<MediaItem[]>(`/api/conversations/${conversationId}/media`);
}
export async function setDisappearing(conversationId: number, seconds: number): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${conversationId}/disappearing`, {
    method: 'PATCH',
    body: JSON.stringify({ seconds }),
  });
}
export async function uploadFile(conversationId: number, file: File, kind: 'image' | 'video' | 'audio' | 'voice'): Promise<Message> {
  const form = new FormData();
  form.append('file', file);
  form.append('kind', kind);
  return request<Message>(`/api/conversations/${conversationId}/upload`, { method: 'POST', body: form });
}

/** Full media URL for <img>/<video>/<audio> src — appends the stored JWT as ?token=. */
export function mediaUrl(messageId: number): string {
  const token = getToken() ?? '';
  return `/api/media/${messageId}?token=${encodeURIComponent(token)}`;
}
export function mediaUrlFromPath(mediaUrlPath: string | null, messageId: number): string {
  if (!mediaUrlPath) return mediaUrl(messageId);
  const token = getToken() ?? '';
  const sep = mediaUrlPath.includes('?') ? '&' : '?';
  return `${mediaUrlPath}${sep}token=${encodeURIComponent(token)}`;
}

/* ---- Admin ---- */
export interface AdminStats {
  total_users: number;
  total_messages: number;
  total_media: number;
  messages_today: number;
  online_now: number;
}
export interface AdminUser {
  id: number;
  display_name: string;
  username: string | null;
  email: string;
  role: string;
  avatar_url: string | null;
  is_admin: boolean;
  online: boolean;
  last_seen: string | null;
  created_at: string;
  message_count: number;
  media_count: number;
}
export interface AdminActivityEvent {
  type: 'register' | 'message' | 'media';
  user_name: string;
  user_email: string;
  detail: string;
  created_at: string;
}
export interface AdminConversation {
  id: number;
  participants: { id: number; display_name: string; username: string | null; email: string; avatar_url: string | null }[];
  last_message_text: string | null;
  last_message_kind: string | null;
  last_message_at: string | null;
  message_count: number;
}
export interface AdminMessage {
  id: number;
  sender_id: number;
  sender_name: string;
  kind: string;
  content: string | null;
  media_url: string | null;
  media_name: string | null;
  media_size: number | null;
  expires_at?: string | null;
  expired: boolean;
  /** True when deleted for everyone (admin still sees the full content). */
  deleted?: boolean;
  deleted_for_everyone?: boolean;
  /** Id of the user who deleted this message for everyone (null if unknown). */
  deleted_by?: number | null;
  /** Display name of the user who deleted this message for everyone. */
  deleted_by_name?: string | null;
  /** User ids who deleted this message for themselves only. */
  deleted_for?: number[];
  created_at: string;
}
export type AdminMediaKind = 'all' | 'photo' | 'video' | 'audio';
export interface AdminMediaItem {
  id: number;
  kind: 'photo' | 'video' | 'audio';
  filename: string;
  size_bytes: number;
  sender_name: string;
  sender_email: string;
  conversation_id?: number | null;
  conversation_participants: string[];
  created_at: string;
  download_url: string;
  expired: boolean;
  /** True when the underlying message was deleted (admin can still view/download). */
  deleted?: boolean;
}
export async function getAdminStats(): Promise<AdminStats> {
  return request<AdminStats>('/api/admin/stats');
}
export async function getAdminUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>('/api/admin/users');
}
export async function getAdminActivity(limit = 100): Promise<AdminActivityEvent[]> {
  return request<AdminActivityEvent[]>(`/api/admin/activity?limit=${limit}`);
}
export async function getAdminConversations(): Promise<AdminConversation[]> {
  return request<AdminConversation[]>('/api/admin/conversations');
}
export async function getAdminConversationMessages(id: number, limit = 100): Promise<AdminMessage[]> {
  return request<AdminMessage[]>(`/api/admin/conversations/${id}/messages?limit=${limit}`);
}
export async function getAdminMedia(kind: AdminMediaKind = 'all', limit = 100): Promise<AdminMediaItem[]> {
  return request<AdminMediaItem[]>(`/api/admin/media?kind=${kind}&limit=${limit}`);
}
/** URL for the ZIP export anchor — token appended as ?token= like media URLs. */
export function adminExportUrl(kind: AdminMediaKind): string {
  const token = getToken() ?? '';
  return `/api/admin/media/export?kind=${kind}&token=${encodeURIComponent(token)}`;
}
