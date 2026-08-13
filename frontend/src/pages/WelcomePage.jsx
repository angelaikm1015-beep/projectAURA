/* Post-login page — reads the profile from Firestore through the Flask API. */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AuthCard, BrandHeader, FormMessage } from "../components/chrome";
import { useAuth } from "../context/AuthContext";
import { fetchMe } from "../lib/api";

const ROWS = [
  ["Username / Email", (p) => p.email],
  ["Age", (p) => (p.age != null ? String(p.age) : null)],
  ["Date of birth", (p) => p.birthdate],
  ["Gender", (p) => p.gender],
  ["Nationality", (p) => p.nationality],
  ["Plays at", (p) => p.affiliationLabel || p.affiliation],
  ["Instrument", (p) => p.instrument],
  ["Favorite genre", (p) => p.genre],
];

export default function WelcomePage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then(({ profile: loaded }) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <AuthCard>
      <BrandHeader tagline={user ? `Signed in as ${user.email}` : "Loading your profile…"} />

      <FormMessage text={error} />

      <ul className="profile-list">
        {ROWS.map(([label, read]) => (
          <li key={label}>
            <span className="k">{label}</span>
            <span className="v">{(profile && read(profile)) || "—"}</span>
          </li>
        ))}
      </ul>

      <button type="button" className="btn btn-ghost" onClick={handleLogout}>
        Log out
      </button>
    </AuthCard>
  );
}
