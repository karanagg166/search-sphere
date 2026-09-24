"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { User, fetchMe, loginUser, signupUser, LoginPayload, SignupPayload } from "@/lib/api";

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
      } catch (err) {
        console.warn("Session restore failed or expired token:", err);
        localStorage.removeItem(TOKEN_KEY);
        setToken(null);
        setUser(null);
      } finally {
        setIsLoading(false);
      }
    }
    initAuth();
  }, []);

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
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    window.location.href = `${apiUrl}/auth/google/login`;
  }, []);

  const loginWithGitHub = useCallback(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    window.location.href = `${apiUrl}/auth/github/login`;
  }, []);

  const loginWithDevOAuth = useCallback((provider: "google" | "github") => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    window.location.href = `${apiUrl}/auth/dev-login?provider=${provider}`;
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
        isAuthenticated: !!user && !!token,
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
