import { Navigate, Route, Routes } from "react-router-dom";

import { AuthCard, BrandHeader } from "./components/chrome";
import { useAuth } from "./context/AuthContext";
import ComposePage from "./pages/ComposePage";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";
import WelcomePage from "./pages/WelcomePage";

export default function App() {
  const { user, initializing } = useAuth();

  // Firebase restores a persisted session asynchronously. Routing before that
  // settles would bounce a signed-in user to /login on every page refresh.
  if (initializing) {
    return (
      <AuthCard>
        <BrandHeader tagline="Loading…" />
      </AuthCard>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/welcome" replace /> : <LoginPage />} />
      <Route path="/signup" element={user ? <Navigate to="/welcome" replace /> : <SignupPage />} />
      <Route path="/welcome" element={user ? <WelcomePage /> : <Navigate to="/login" replace />} />
      <Route path="/compose" element={user ? <ComposePage /> : <Navigate to="/login" replace />} />
      <Route path="*" element={<Navigate to={user ? "/welcome" : "/login"} replace />} />
    </Routes>
  );
}
