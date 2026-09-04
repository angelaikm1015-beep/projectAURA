/*
 * The bar across the top of every signed-in page.
 *
 * One row: the mark on the left, where to go in the middle, who you are on the
 * right. It replaces the arrangement where a centred brand block and an
 * absolutely-positioned nav were laid over each other and collided as soon as
 * the links grew — the two now sit in the same flex row and cannot overlap.
 *
 * The account is a plain label rather than a link because there is nothing
 * behind it that "My page" doesn't already cover.
 */

import { Link, NavLink, useNavigate } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

const LINKS = [
  { to: "/compose", label: "Compose" },
  { to: "/gallery", label: "Gallery" },
  { to: "/welcome", label: "My page" },
];

export function AppHeader() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <header className="topbar">
      <Link className="topbar__brand" to="/compose">
        <span className="topbar__mark" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
          </svg>
        </span>
        <span className="topbar__name">AURA</span>
      </Link>

      <nav className="topbar__nav" aria-label="Main">
        {LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) => `topbar__link${isActive ? " is-active" : ""}`}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>

      <div className="topbar__account">
        {user && <span className="topbar__who" title={user.email}>{user.email}</span>}
        <button type="button" className="topbar__logout" onClick={handleLogout}>
          Log out
        </button>
      </div>
    </header>
  );
}

/** Page title under the bar. Keeps every page's opening line identical. */
export function PageIntro({ title, children }) {
  return (
    <div className="page-intro">
      <h1 className="page-intro__title">{title}</h1>
      {children && <p className="page-intro__sub">{children}</p>}
    </div>
  );
}
