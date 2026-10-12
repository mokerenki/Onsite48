// frontend/src/components/AuthProvider.tsx

"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

export type AuthUser = {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string | null;
  organization_id: string;
  organization_name: string;
  role: "owner" | "admin" | "member";
};

type AuthResponse = {
  access_token: string;
  expires_in: number;
  user: AuthUser;
};

type LoginPayload = { email: string; password: string };
type RegisterPayload = LoginPayload & { display_name: string; organization_name: string };

type AuthContextValue = {
  user: AuthUser | null;
  accessToken: string | null;
  status: "loading" | "authenticated" | "anonymous";
  login: (payload: LoginPayload) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  logout: () => Promise<void>;
  startOAuth: (provider: "google" | "github") => Promise<void>;
  refresh: () => Promise<boolean>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const ACCESS_TOKEN_KEY = "onsite48_access_token";
const USER_KEY = "onsite48_user";
const REFRESH_SEQUENCE_KEY = "onsite48_refresh_sequence";
const ACCESS_TOKEN_REFRESH_INTERVAL_MS = 12 * 60 * 1_000;

async function apiFetch(path: string, init?: RequestInit) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      ...init,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) throw new Error("The API did not respond in time. Check that the backend is running and retry.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.message || body.detail || "The request could not be completed.");
  }
  return response;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [status, setStatus] = useState<AuthContextValue["status"]>("loading");
  const refreshInFlightRef = useRef<Promise<boolean> | null>(null);
  const refreshSequenceRef = useRef<number>(0);
  const accessTokenRef = useRef<string | null>(null);

  const persist = useCallback((auth: AuthResponse) => {
    const sequence = refreshSequenceRef.current + 1;
    refreshSequenceRef.current = sequence;
    sessionStorage.setItem(REFRESH_SEQUENCE_KEY, String(sequence));
    
    setUser(auth.user);
    setAccessToken(auth.access_token);
    accessTokenRef.current = auth.access_token;
    sessionStorage.setItem(ACCESS_TOKEN_KEY, auth.access_token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(auth.user));
    setStatus("authenticated");
  }, []);

  const clear = useCallback(() => {
    setUser(null);
    setAccessToken(null);
    accessTokenRef.current = null;
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
    setStatus("anonymous");
  }, []);

  const refresh = useCallback(async (): Promise<boolean> => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;

    const currentSequence = refreshSequenceRef.current + 1;
    refreshSequenceRef.current = currentSequence;

    const attempt = (async () => {
      try {
        const response = await apiFetch("/api/auth/refresh", { method: "POST", body: "{}" });
        const data = await response.json() as AuthResponse;
        
        // Only apply if no newer refresh completed
        if (currentSequence === refreshSequenceRef.current) {
          persist(data);
          return true;
        }
        console.debug("Auth refresh superseded", { currentSequence, storedSequence: refreshSequenceRef.current });
        return false;
      } catch (error) {
        // Don't clear on network errors - the existing token might still be valid
        if (error instanceof Error && error.message.includes("expired")) {
          clear();
        }
        return false;
      }
    })();
    
    refreshInFlightRef.current = attempt;
    try {
      return await attempt;
    } finally {
      if (refreshInFlightRef.current === attempt) refreshInFlightRef.current = null;
    }
  }, [clear, persist]);

  useEffect(() => {
    // Restore session from storage
    const storedToken = sessionStorage.getItem(ACCESS_TOKEN_KEY);
    const storedUser = sessionStorage.getItem(USER_KEY);
    const storedSequence = sessionStorage.getItem(REFRESH_SEQUENCE_KEY);
    
    if (storedToken && storedUser) {
      try {
        const userData = JSON.parse(storedUser);
        setUser(userData);
        setAccessToken(storedToken);
        accessTokenRef.current = storedToken;
        if (storedSequence) {
          refreshSequenceRef.current = parseInt(storedSequence, 10);
        }
        setStatus("authenticated");
        
        // Refresh in background
        void refresh();
      } catch {
        clear();
      }
    } else {
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return;
    const timer = window.setInterval(() => {
      void refresh();
    }, ACCESS_TOKEN_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh, status]);

  const login = useCallback(async (payload: LoginPayload) => {
    const response = await apiFetch("/api/auth/login", { method: "POST", body: JSON.stringify(payload) });
    persist(await response.json() as AuthResponse);
  }, [persist]);

  const register = useCallback(async (payload: RegisterPayload) => {
    const response = await apiFetch("/api/auth/register", { method: "POST", body: JSON.stringify(payload) });
    persist(await response.json() as AuthResponse);
  }, [persist]);

  const logout = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST", body: "{}" });
    } finally {
      clear();
    }
  }, [clear]);

  const startOAuth = useCallback(async (provider: "google" | "github") => {
    const response = await apiFetch(`/api/auth/oauth/${provider}/start?return_to=/`, { method: "GET" });
    const data = (await response.json()) as { authorization_url: string };
    window.location.assign(data.authorization_url);
  }, []);

  const value = useMemo(() => ({ 
    user, 
    accessToken, 
    status, 
    login, 
    register, 
    logout, 
    startOAuth, 
    refresh 
  }), [accessToken, login, logout, refresh, register, startOAuth, status, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider.");
  return context;
}

export function authHeaders(accessToken: string | null): HeadersInit {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}