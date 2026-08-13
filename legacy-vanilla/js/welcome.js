/* Post-login page — reads the profile from Firestore through the Flask API. */

import { fetchMe } from "./api.js";
import { auth, onAuthStateChanged, signOut } from "./firebase.js";
import { $, setFormMessage } from "./ui.js";

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

document.addEventListener("DOMContentLoaded", () => {
  $("logout-btn").addEventListener("click", async () => {
    await signOut(auth);
    window.location.href = "index.html";
  });

  // Firebase restores the session asynchronously, so wait for the first
  // auth-state callback before deciding whether anyone is logged in.
  onAuthStateChanged(auth, async (user) => {
    if (!user) {
      window.location.replace("index.html");
      return;
    }

    $("greeting").textContent = `Signed in as ${user.email}`;

    try {
      const { profile } = await fetchMe();
      renderProfile(profile);
    } catch (error) {
      setFormMessage(error.message, "error");
    }
  });
});

function renderProfile(profile) {
  const list = $("profile");
  list.replaceChildren(
    ...ROWS.map(([label, read]) => {
      const item = document.createElement("li");

      const key = document.createElement("span");
      key.className = "k";
      key.textContent = label;

      const value = document.createElement("span");
      value.className = "v";
      value.textContent = read(profile) || "—";

      item.append(key, value);
      return item;
    })
  );
}
