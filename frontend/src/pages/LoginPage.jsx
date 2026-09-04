/* Login — authenticates directly against Firebase Auth, no backend call. */

import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { AuthCard, BrandHeader, FormMessage, SubmitButton } from "../components/chrome";
import { Field, PasswordField } from "../components/fields";
import {
  auth,
  browserLocalPersistence,
  browserSessionPersistence,
  describeAuthError,
  sendPasswordResetEmail,
  setPersistence,
  signInWithEmailAndPassword,
} from "../lib/firebase";
import { normalizeEmail, validateEmail } from "../lib/validate";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState(location.state?.email || "");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(
    location.state?.email
      ? { text: "Account created — log in to continue.", kind: "success" }
      : null
  );
  const [pending, setPending] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setMessage(null);

    const emailError = validateEmail(email);
    // Only check that something was typed — tightened rules shouldn't lock out
    // accounts created under the old ones.
    const passwordError = password ? null : "Password is required.";
    if (emailError || passwordError) {
      setErrors({ email: emailError, password: passwordError });
      return;
    }
    setErrors({});
    setPending(true);

    try {
      // "Keep me signed in" survives a browser restart; otherwise the session
      // ends with the tab.
      await setPersistence(auth, remember ? browserLocalPersistence : browserSessionPersistence);
      await signInWithEmailAndPassword(auth, normalizeEmail(email), password);
      navigate("/compose", { replace: true });
    } catch (error) {
      setMessage({ text: describeAuthError(error), kind: "error" });
      setPassword("");
      setPending(false);
    }
  }

  async function handleReset(event) {
    event.preventDefault();
    if (validateEmail(email)) {
      setErrors({ email: "Enter your email first, then click again." });
      return;
    }
    try {
      await sendPasswordResetEmail(auth, normalizeEmail(email));
    } catch (error) {
      // A missing account still reports success: don't reveal who has an account.
      if (error?.code !== "auth/user-not-found") {
        setMessage({ text: describeAuthError(error), kind: "error" });
        return;
      }
    }
    setMessage({
      text: `If an account exists for ${normalizeEmail(email)}, a reset link is on its way.`,
      kind: "success",
    });
  }

  return (
    <AuthCard>
      <BrandHeader tagline="Welcome back — sign in to keep playing." />

      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormMessage text={message?.text} kind={message?.kind} />

        <Field
          label="Email"
          type="email"
          autoComplete="username"
          placeholder="you@example.com"
          value={email}
          error={errors.email}
          onChange={(e) => {
            setEmail(e.target.value);
            setErrors((prev) => ({ ...prev, email: null }));
          }}
        />

        <PasswordField
          label="Password"
          autoComplete="current-password"
          placeholder="Your password"
          value={password}
          error={errors.password}
          onChange={(e) => {
            setPassword(e.target.value);
            setErrors((prev) => ({ ...prev, password: null }));
          }}
        />

        <div className="form-aside">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>Keep me signed in</span>
          </label>
          <a className="link" href="#reset" onClick={handleReset}>
            Forgot password?
          </a>
        </div>

        <SubmitButton pending={pending} pendingLabel="Logging in…">
          Log in
        </SubmitButton>
      </form>

      <p className="form-footer">
        New to AURA? <Link className="link" to="/signup">Create an account</Link>
      </p>
    </AuthCard>
  );
}
