/* Auth gate — sign up, log in, Google OAuth, or continue as guest */
(function () {
  const { useState } = React;
  const { Button, Icon } = window.KaprukaDesignSystem_d6db4e;

  function AuthGate({ supabase, initialMode, onGuest, onAuthed }) {
    const [mode, setMode] = useState(initialMode || "welcome");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [displayName, setDisplayName] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");

    const resetErr = () => setError("");

    const googleSignIn = async () => {
      if (!supabase) {
        setError("Sign-in is not configured yet. Continue as guest or try later.");
        return;
      }
      resetErr();
      setBusy(true);
      try {
        const { error: err } = await supabase.auth.signInWithOAuth({
          provider: "google",
          options: { redirectTo: window.location.origin },
        });
        if (err) setError(err.message);
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(false);
      }
    };

    const emailAuth = async (isSignup) => {
      if (!supabase) {
        setError("Sign-in is not configured yet.");
        return;
      }
      resetErr();
      setBusy(true);
      try {
        if (isSignup) {
          const { data, error: err } = await supabase.auth.signUp({
            email: email.trim(),
            password,
            options: {
              data: { full_name: displayName.trim() || undefined },
            },
          });
          if (err) throw err;
          if (data.session) onAuthed(data.session);
          else setError("Check your email to confirm your account, then log in.");
        } else {
          const { data, error: err } = await supabase.auth.signInWithPassword({
            email: email.trim(),
            password,
          });
          if (err) throw err;
          if (data.session) onAuthed(data.session);
        }
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    };

    if (mode === "welcome") {
      return (
        <div className="auth-shell">
          <div className="auth-card k-rise">
            <span className="auth-brand"><Icon name="leaf" size={28} /></span>
            <h1>Your personal gift concierge</h1>
            <p>
              Sign in so Kapruka remembers your gifting style, budget, and delivery
              preferences — and finds better matches every time.
            </p>
            <div className="auth-actions">
              <Button variant="primary" full iconRight="arrow-right" onClick={() => { resetErr(); setMode("signup"); }}>
                Sign up
              </Button>
              <Button variant="soft" full onClick={() => { resetErr(); setMode("login"); }}>
                Log in
              </Button>
              <button type="button" className="auth-link" onClick={() => googleSignIn()} disabled={busy}>
                <Icon name="globe" size={16} /> Continue with Google
              </button>
              <button type="button" className="auth-ghost" onClick={onGuest}>
                Continue without account
              </button>
            </div>
            {error && <p className="auth-error">{error}</p>}
          </div>
        </div>
      );
    }

    const isSignup = mode === "signup";

    return (
      <div className="auth-shell">
        <div className="auth-card k-rise">
          <button type="button" className="auth-back" onClick={() => { resetErr(); setMode("welcome"); }}>
            ← Back
          </button>
          <h1>{isSignup ? "Create your account" : "Welcome back"}</h1>
          <p>{isSignup ? "A quick quiz after signup helps us learn your gifting personality." : "Log in to pick up where you left off."}</p>
          <form
            className="auth-form"
            onSubmit={(e) => {
              e.preventDefault();
              emailAuth(isSignup);
            }}
          >
            {isSignup && (
              <label>
                Name
                <input
                  type="text"
                  placeholder="Your name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  autoComplete="name"
                />
              </label>
            )}
            <label>
              Email
              <input
                type="email"
                required
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                required
                minLength={6}
                placeholder="At least 6 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isSignup ? "new-password" : "current-password"}
              />
            </label>
            <Button variant="primary" full disabled={busy}>
              {busy ? "Please wait…" : isSignup ? "Sign up" : "Log in"}
            </Button>
          </form>
          <button type="button" className="auth-link" onClick={() => googleSignIn()} disabled={busy}>
            <Icon name="globe" size={16} /> Continue with Google
          </button>
          <button type="button" className="auth-ghost" onClick={onGuest}>
            Continue without account
          </button>
          {error && <p className="auth-error">{error}</p>}
        </div>
      </div>
    );
  }

  window.AuthGate = AuthGate;
})();
