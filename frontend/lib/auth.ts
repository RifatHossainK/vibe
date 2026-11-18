export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('vibe_token');
}
export function setToken(token: string): void {
  localStorage.setItem('vibe_token', token);
}
export function clearToken(): void {
  localStorage.removeItem('vibe_token');
}
export function getUser(): AuthUser | null {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem('vibe_user');
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}
export function setUser(user: AuthUser): void {
  localStorage.setItem('vibe_user', JSON.stringify(user));
}
export function clearAuth(): void {
  localStorage.removeItem('vibe_token');
  localStorage.removeItem('vibe_user');
}
export interface AuthUser {
  id: number;
  display_name: string;
  username: string | null;
  email: string;
  role: string;
  avatar_url: string | null;
  created_at: string;
}
