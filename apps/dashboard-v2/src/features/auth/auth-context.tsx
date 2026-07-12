import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  type AuthSession,
  login as apiLogin,
  logout as apiLogout,
  fetchMe,
  getActiveOrgId,
  setActiveOrgId,
} from "@/features/auth/api";

type AuthState = {
  status: "loading" | "authenticated" | "anonymous" | "legacy";
  session: AuthSession | null;
  activeOrgId: string | null;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setOrg: (orgId: string | null) => void;
  isAdmin: boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [session, setSession] = useState<AuthSession | null>(null);
  const [activeOrgId, setOrgState] = useState<string | null>(() => getActiveOrgId());

  const refresh = useCallback(async () => {
    try {
      const me = await fetchMe();
      if (me === null) {
        setSession(null);
        setStatus("anonymous");
        return;
      }
      setSession(me);
      if (me.principal.via === "sys" && me.principal.user_id === "sys:legacy") {
        setStatus("legacy");
      } else {
        setStatus("authenticated");
      }
      if (me.principal.active_org_id) {
        setOrgState(me.principal.active_org_id);
        setActiveOrgId(me.principal.active_org_id);
      }
    } catch {
      setSession(null);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const sess = await apiLogin(email, password);
    setSession(sess);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // ignore
    }
    setSession(null);
    setStatus("anonymous");
    setActiveOrgId(null);
    setOrgState(null);
    // Leave protected surface (hard nav clears RQ cache state).
    if (typeof window !== "undefined") {
      window.location.assign("/login");
    }
  }, []);

  const setOrg = useCallback((orgId: string | null) => {
    setActiveOrgId(orgId);
    setOrgState(orgId);
  }, []);

  const isAdmin = Boolean(session?.principal.roles.includes("admin"));

  const value = useMemo(
    () => ({
      status,
      session,
      activeOrgId,
      refresh,
      login,
      logout,
      setOrg,
      isAdmin,
    }),
    [status, session, activeOrgId, refresh, login, logout, setOrg, isAdmin],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
