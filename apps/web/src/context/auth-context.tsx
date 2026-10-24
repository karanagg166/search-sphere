"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
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
  const { isReady, status } = useBackendAvailability();
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Restore authenticated session from localStorage on mount
  useEffect(() => {
    async function initAuth() {
      try {
        const storedToken = localStorage.getItem(TOKEN_KEY);
        if (storedToken) {
          setToken(storedToken);
          const userData = await fetchMe();
          setUser(userData);
        }
      } catch (err: any) {
        const isAuthError =
          err?.response?.status === 401 || err?.response?.status === 403;
        if (isAuthError) {
          console.warn("Session restore failed or expired token:", err);
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
          setUser(null);
        } else {
          // Cold-start or transient outage: preserve token for automatic recovery
          console.warn("Backend sleeping or unreachable during initAuth; preserving token for recovery.");
        }
      } finally {
        setIsLoading(false);
      }
    }
    initAuth();
  }, []);

  // When backend becomes ready, automatically fetch user profile if token is present but user profile is missing
  useEffect(() => {
    if (isReady && token && !user) {
      fetchMe()
        .then((userData) => {
          setUser(userData);
        })
        .catch((err) => {
          if (err?.response?.status === 401 || err?.response?.status === 403) {
            localStorage.removeItem(TOKEN_KEY);
            setToken(null);
            setUser(null);
          }
        });
    }
  }, [isReady, token, user]);

  const setSession = useCallback(async (newToken: string, directUser?: User) => {
    localStorage.setItem(TOKEN_KEY, newToken);
    setToken(newToken);
    if (directUser) {
      setUser(directUser);
    } else {
      try {
        const userData = await fetchMe();
        setUser(userData);
      } catch (err) {
        console.error("Failed to load user profile after session set", err);
      }
    }
  }, []);

  const login = useCallback(
    async (payload: LoginPayload) => {
      setIsLoading(true);
      try {
        const res = await loginUser(payload);
        await setSession(res.access_token, res.user);
      } finally {
        setIsLoading(false);
      }
    },
    [setSession]
  );

  const signup = useCallback(
    async (payload: SignupPayload) => {
      setIsLoading(true);
      try {
        const res = await signupUser(payload);
        await setSession(res.access_token, res.user);
      } finally {
        setIsLoading(false);
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
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        isAuthenticated: !!token && (!!user || status !== "offline"),
        login,
        signup,
        loginWithGoogle,
        loginWithGitHub,
        loginWithDevOAuth,
        setSession,
        logout,
      }}
    >
      {children}
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
