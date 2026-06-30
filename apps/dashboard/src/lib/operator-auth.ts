const TOKEN_KEY = "factory.dashboard.operatorToken";

export function getOperatorToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setOperatorToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token.trim());
  } catch {
    // private mode
  }
}

export function operatorAuthHeaders(): HeadersInit {
  const token = getOperatorToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}
