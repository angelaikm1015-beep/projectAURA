/*
 * Client-side mirror of backend/validators.py — fast feedback only.
 * The server re-runs every one of these; keep the two files in step, but treat
 * the Python side as the one that decides.
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export const MIN_AGE = 10;
export const MAX_AGE = 120;
export const MIN_PASSWORD_LENGTH = 8;

export const normalizeEmail = (value) => String(value || "").trim().toLowerCase();

export function validateEmail(value) {
  const email = normalizeEmail(value);
  if (!email) return "Email is required.";
  if (!EMAIL_RE.test(email)) return "Enter a valid email address.";
  return null;
}

export function validatePassword(value) {
  if (!value) return "Password is required.";
  if (value.length < MIN_PASSWORD_LENGTH)
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  if (!/[A-Za-z]/.test(value)) return "Password must include a letter.";
  if (!/[0-9]/.test(value)) return "Password must include a number.";
  return null;
}

export function passwordStrength(password) {
  let score = 0;
  if (password.length >= 8) score++;
  if (password.length >= 12) score++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++;
  if (/[0-9]/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;
  return Math.min(score, 4); // 0-4
}

/** Whole years from an ISO yyyy-mm-dd string, or null if unparseable. */
export function ageFromBirthdate(iso) {
  if (!iso) return null;
  const birth = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(birth.getTime())) return null;
  const today = new Date();
  let age = today.getFullYear() - birth.getFullYear();
  const monthDiff = today.getMonth() - birth.getMonth();
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birth.getDate())) age--;
  return age;
}

export function validateBirthdate(iso) {
  if (!iso) return "Date of birth is required.";
  const age = ageFromBirthdate(iso);
  if (age === null) return "Enter a valid date.";
  if (age < 0) return "Date of birth cannot be in the future.";
  if (age < MIN_AGE) return `You must be at least ${MIN_AGE} years old to join AURA.`;
  if (age > MAX_AGE) return "Enter a valid date of birth.";
  return null;
}
