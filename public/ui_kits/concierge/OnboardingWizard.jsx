/* Gifting personality onboarding wizard */
(function () {
  const { useState } = React;
  const { Button, Icon } = window.KaprukaDesignSystem_d6db4e;
  const { computePersonality } = window.KaprukaPersonality || {
    computePersonality: () => ({}),
  };

  const STEPS = [
    {
      key: "gift_priority",
      title: "What matters most in a gift?",
      options: [
        { value: "thoughtfulness", label: "Thoughtfulness & meaning" },
        { value: "surprise", label: "Surprise & delight" },
        { value: "practicality", label: "Practical & useful" },
        { value: "wow_factor", label: "Wow factor & premium feel" },
      ],
    },
    {
      key: "budget_band",
      title: "Your typical gift budget?",
      options: [
        { value: "under_2000", label: "Under Rs 2,000" },
        { value: "2000_5000", label: "Rs 2,000 – 5,000" },
        { value: "5000_10000", label: "Rs 5,000 – 10,000" },
        { value: "over_10000", label: "Rs 10,000+" },
      ],
    },
    {
      key: "shopping_style",
      title: "How do you usually shop for gifts?",
      options: [
        { value: "weeks_ahead", label: "Weeks ahead — I plan early" },
        { value: "few_days", label: "A few days before" },
        { value: "last_minute", label: "Last minute — I need it fast" },
      ],
    },
    {
      key: "recipient_focus",
      title: "Who do you shop for most often?",
      options: [
        { value: "family", label: "Family" },
        { value: "partner", label: "Partner" },
        { value: "colleagues", label: "Colleagues" },
        { value: "kids", label: "Kids" },
        { value: "mixed", label: "A mix of everyone" },
      ],
    },
    {
      key: "style_vibe",
      title: "Your gifting style vibe?",
      options: [
        { value: "classic", label: "Classic & elegant" },
        { value: "playful", label: "Fun & playful" },
        { value: "minimalist", label: "Minimal & modern" },
        { value: "traditional", label: "Traditional Sri Lankan" },
      ],
    },
    {
      key: "default_city",
      title: "Default delivery city?",
      type: "city",
      options: [
        { value: "Colombo", label: "Colombo" },
        { value: "Kandy", label: "Kandy" },
        { value: "Galle", label: "Galle" },
        { value: "other", label: "Other" },
      ],
    },
  ];

  function OnboardingWizard({ supabase, session, onComplete }) {
    const [step, setStep] = useState(0);
    const [answers, setAnswers] = useState({});
    const [cityOther, setCityOther] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");

    const current = STEPS[step];
    const progress = ((step + 1) / STEPS.length) * 100;

    const pick = (key, value) => {
      setAnswers((a) => ({ ...a, [key]: value }));
      if (key !== "default_city" || value !== "other") {
        setTimeout(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 180);
      }
    };

    const finish = async () => {
      setBusy(true);
      setError("");
      try {
        const finalAnswers = { ...answers };
        if (finalAnswers.default_city === "other") {
          finalAnswers.default_city = cityOther.trim() || "Colombo";
        }
        const computed = computePersonality(finalAnswers);
        const patch = {
          quiz_answers: finalAnswers,
          gifting_personality: computed.gifting_personality,
          personality_scores: computed.personality_scores,
          default_budget: computed.default_budget,
          preferences: computed.preferences,
          default_city: finalAnswers.default_city,
          display_name:
            session.user.user_metadata?.full_name ||
            session.user.email?.split("@")[0],
          onboarding_completed: true,
        };
        const { data, error: err } = await supabase
          .from("profiles")
          .update(patch)
          .eq("id", session.user.id)
          .select()
          .single();
        if (err) throw err;
        onComplete(data, computed.personality_label);
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    };

    const canFinish =
      current.key === "default_city" &&
      (answers.default_city === "other" ? cityOther.trim() : answers.default_city);

    return (
      <div className="auth-shell">
        <div className="auth-card wizard-card k-rise">
          <div className="wizard-progress">
            <div className="wizard-progress-bar" style={{ width: `${progress}%` }} />
          </div>
          <p className="wizard-step">Step {step + 1} of {STEPS.length}</p>
          <h1>{current.title}</h1>
          <div className="wizard-options">
            {current.options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={
                  "wizard-opt" + (answers[current.key] === opt.value ? " wizard-opt--on" : "")
                }
                onClick={() => pick(current.key, opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {current.type === "city" && answers.default_city === "other" && (
            <label className="wizard-city">
              City name
              <input
                type="text"
                placeholder="e.g. Negombo"
                value={cityOther}
                onChange={(e) => setCityOther(e.target.value)}
              />
            </label>
          )}
          <div className="wizard-nav">
            {step > 0 && (
              <Button variant="soft" onClick={() => setStep((s) => s - 1)}>Back</Button>
            )}
            {step < STEPS.length - 1 && answers[current.key] && current.key !== "default_city" && (
              <Button variant="primary" onClick={() => setStep((s) => s + 1)}>Next</Button>
            )}
            {step === STEPS.length - 1 && (
              <Button variant="primary" disabled={!canFinish || busy} onClick={finish}>
                {busy ? "Saving…" : "Finish & start gifting"}
              </Button>
            )}
          </div>
          {error && <p className="auth-error">{error}</p>}
          <p className="wizard-hint">
            <Icon name="sparkles" size={14} /> This helps the AI tailor picks to your style.
          </p>
        </div>
      </div>
    );
  }

  window.OnboardingWizard = OnboardingWizard;
})();
