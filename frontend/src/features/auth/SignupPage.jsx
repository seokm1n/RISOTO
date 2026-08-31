import LoginPage from "./LoginPage";

export default function SignupPage({ onAuthenticated }) {
  return <LoginPage initialMode="signup" onAuthenticated={onAuthenticated} />;
}
