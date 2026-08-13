/*
 * One subscription to Firebase's auth state, shared by the whole app.
 *
 * `initializing` matters: Firebase restores a persisted session asynchronously,
 * so on first paint `user` is null even for someone who is signed in. Routing on
 * that null would bounce a logged-in user to the login page on every refresh.
 * Consumers must wait for `initializing` to be false before deciding anything.
 */

import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { auth, onAuthStateChanged, signOut } from "../lib/firebase";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [initializing, setInitializing] = useState(true);

  useEffect(() => {
    return onAuthStateChanged(auth, (nextUser) => {
      setUser(nextUser);
      setInitializing(false);
    });
  }, []);

  const value = useMemo(
    () => ({
      user,
      initializing,
      logout: () => signOut(auth),
    }),
    [user, initializing]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
