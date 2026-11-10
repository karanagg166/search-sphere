"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { User, fetchMe, loginUser, signupUser, LoginPayload, SignupPayload } from "@/lib/api";
import { useBackendAvailability } from "@/context/backend-availability-context";

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (payload: LoginPayload) => Promise<void>;
  signup: (payload: SignupPayload) => Promise<void>;
  loginWithGoogle: () => void;
  loginWithGitHub: () => void;
  loginWithDevOAuth: (provider: "google" | "github") => void;
  setSession: (token: string, user?: User) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const TOKEN_KEY = "search_sphere_token";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { isReady } = useBackendAvailability();
  const queryClient = useQueryClient();
  const epoch = useRef(0);
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const clearPrivateState = useCallback(() => {
    void queryClient.cancelQueries();
    queryClient.clear();
    // The legacy key was shared across accounts. New conversations use owner keys.
    for (const key of Object.keys(localStorage)) {
      if (key === "search_sphere_active_conv" || key.startsWith("search_sphere_active_conv:")) localStorage.removeItem(key);
    }
  }, [queryClient]);


  // Restore authenticated session from localStorage on mount
  useEffect(() => {
    let active = true;
    async function initAuth() {
      const generation = epoch.current;
      const storedToken = localStorage.getItem(TOKEN_KEY);
      try {
        if (!active || generation !== epoch.current) return;
        if (storedToken) {
          setToken(storedToken);
          const userData = await fetchMe();
          if (active && generation === epoch.current && localStorage.getItem(TOKEN_KEY) === storedToken) setUser(userData);
        }
      } catch (err: any) {
        if (!active || generation !== epoch.current || localStorage.getItem(TOKEN_KEY) !== storedToken) return;
        const isAuthError =
          err?.response?.status === 401 || err?.response?.status === 403;
        if (isAuthError) {
          console.warn("Session restore failed or token expired.");
          clearPrivateState();
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
          setUser(null);
        } else {
          // Cold-start or transient outage: preserve token for automatic recovery
          console.warn("Backend sleeping or unreachable during initAuth; preserving token for recovery.");
        }
      } finally {
        if (active && generation === epoch.current) setIsLoading(false);
      }
    }
    initAuth();
    return () => { active = false; };
  }, [clearPrivateState]);

  // When backend becomes ready, automatically fetch user profile if token is present but user profile is missing
  useEffect(() => {
    if (isReady && token && !user && !isLoading) {
      const generation = epoch.current;
      fetchMe()
        .then((userData) => {
          if (generation !== epoch.current || localStorage.getItem(TOKEN_KEY) !== token) return;
          setUser(userData);
        })
        .catch((err) => {
          if (generation !== epoch.current || localStorage.getItem(TOKEN_KEY) !== token) return;
          if (err?.response?.status === 401 || err?.response?.status === 403) {
            clearPrivateState();
            localStorage.removeItem(TOKEN_KEY);
            setToken(null);
            setUser(null);
          }
        });
    }
  }, [isReady, token, user, isLoading, clearPrivateState]);


  // A sign-in/logout in another tab must also clear this tab's private UI.
  useEffect(() => {
    const synchronize = (event: StorageEvent) => {
      if ((event.key !== TOKEN_KEY && event.key !== null)
        || (event.storageArea && event.storageArea !== localStorage)) return;
      const current = localStorage.getItem(TOKEN_KEY);
      if (current === token) return;
      ++epoch.current;
      clearPrivateState();
      setUser(null);
      setToken(current);
      setIsLoading(false);
    };
    window.addEventListener("storage", synchronize);
    return () => window.removeEventListener("storage", synchronize);
  }, [token, clearPrivateState]);

  const setSession = useCallback(async (newToken: string, directUser?: User, expectedGeneration?: number) => {
    const generation = expectedGeneration ?? ++epoch.current;
    if (generation !== epoch.current) return;
    clearPrivateState();
    localStorage.setItem(TOKEN_KEY, newToken);
    setToken(newToken);
    setUser(directUser ?? null);
    setIsLoading(true);
    try {
      if (!directUser) {
        const userData = await fetchMe();
        if (generation === epoch.current && localStorage.getItem(TOKEN_KEY) === newToken) setUser(userData);
      }
    } catch {
      if (generation === epoch.current) console.warn("User profile could not be restored yet.");
    } finally {
      if (generation === epoch.current) setIsLoading(false);
    }
  }, [clearPrivateState]);

  const login = useCallback(
    async (payload: LoginPayload) => {
      const generation = ++epoch.current;
      setIsLoading(true);
      try {
        const res = await loginUser(payload);
        await setSession(res.access_token, res.user, generation);
      } finally {
        if (generation === epoch.current) setIsLoading(false);
      }
    },
    [setSession]
  );

  const signup = useCallback(
    async (payload: SignupPayload) => {
      const generation = ++epoch.current;
      setIsLoading(true);
      try {
        const res = await signupUser(payload);
        await setSession(res.access_token, res.user, generation);
      } finally {
        if (generation === epoch.current) setIsLoading(false);
      }
    },
    [setSession]
  );

  const loginWithGoogle = useCallback(() => {
    window.location.href = "/api/auth/google/login";
  }, []);

  const loginWithGitHub = useCallback(() => {
    window.location.href = "/api/auth/github/login";
  }, []);

  const loginWithDevOAuth = useCallback((provider: "google" | "github") => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    const isLocalhost =
      typeof window !== "undefined" &&
      (window.location.hostname === "localhost" ||
        window.location.hostname === "127.0.0.1");

    if (apiUrl && (!apiUrl.includes("localhost") || isLocalhost)) {
      window.location.href = `${apiUrl}/auth/dev-login?provider=${provider}`;
    } else {
      window.location.href = `/login?error=${encodeURIComponent(
        "Development login is only available when a local backend is running."
      )}`;
    }
  }, []);

  const logout = useCallback(() => {
    ++epoch.current;
    clearPrivateState();
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
    setIsLoading(false);
  }, [clearPrivateState]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        isAuthenticated: !!token && !!user,
        login,
        signup,
        loginWithGoogle,
        loginWithGitHub,
        loginWithDevOAuth,
        setSession,
        logout,
      }}
    >
      <React.Fragment key={user?.id ?? "anonymous"}>{children}</React.Fragment>
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
