/* Gifting personality quiz scoring — shared by onboarding wizard */

const PERSONALITY_LABELS = {
  thoughtful_planner: "Thoughtful Planner",
  last_minute_hero: "Last-Minute Hero",
  practical_gifter: "Practical Gifter",
  big_spender: "Big Spender",
  sentimental_soul: "Sentimental Soul",
  creative_maker: "Creative Maker",
};

const BUDGET_MAP = {
  under_2000: 1500,
  "2000_5000": 3500,
  "5000_10000": 7500,
  over_10000: 15000,
};

function addScore(scores, key, n = 1) {
  scores[key] = (scores[key] || 0) + n;
}

function computePersonality(answers) {
  const scores = {};
  const a = answers || {};

  const priority = a.gift_priority;
  if (priority === "thoughtfulness") {
    addScore(scores, "thoughtful_planner", 2);
    addScore(scores, "sentimental_soul", 1);
  } else if (priority === "surprise") {
    addScore(scores, "creative_maker", 2);
    addScore(scores, "last_minute_hero", 1);
  } else if (priority === "practicality") {
    addScore(scores, "practical_gifter", 2);
  } else if (priority === "wow_factor") {
    addScore(scores, "big_spender", 2);
    addScore(scores, "creative_maker", 1);
  }

  const band = a.budget_band;
  if (band === "over_10000") addScore(scores, "big_spender", 2);
  else if (band === "under_2000") addScore(scores, "practical_gifter", 1);

  const shop = a.shopping_style;
  if (shop === "weeks_ahead") addScore(scores, "thoughtful_planner", 2);
  else if (shop === "last_minute") addScore(scores, "last_minute_hero", 2);

  const recip = a.recipient_focus;
  if (recip === "family" || recip === "partner") addScore(scores, "sentimental_soul", 1);
  else if (recip === "colleagues") addScore(scores, "practical_gifter", 1);
  else if (recip === "kids") addScore(scores, "creative_maker", 1);

  const style = a.style_vibe;
  if (style === "classic") addScore(scores, "thoughtful_planner", 1);
  else if (style === "playful") addScore(scores, "creative_maker", 1);
  else if (style === "minimalist") addScore(scores, "practical_gifter", 1);
  else if (style === "traditional") addScore(scores, "sentimental_soul", 1);

  let primary = "thoughtful_planner";
  let top = 0;
  Object.entries(scores).forEach(([k, v]) => {
    if (v > top) {
      top = v;
      primary = k;
    }
  });

  const defaultBudget = BUDGET_MAP[band] || null;
  const preferences = {
    styles: style ? [style] : [],
    recipient_focus: recip || null,
  };
  const avoid = a.avoid_list;
  if (Array.isArray(avoid) && avoid.length) {
    preferences.avoid_list = avoid.filter((x) => x && x !== "none");
  }
  const dietary = a.dietary;
  if (dietary && dietary !== "none") {
    preferences.dietary = dietary;
  }
  if (a.corporate_gifting) {
    preferences.corporate_gifting = true;
  }

  return {
    gifting_personality: primary,
    personality_scores: scores,
    default_budget: defaultBudget,
    preferences,
    personality_label: PERSONALITY_LABELS[primary] || primary,
  };
}

const GREETING_SNIPPETS = {
  thoughtful_planner: "let's find something that feels properly thought through.",
  last_minute_hero: "tell me who it's for — I'll find something great, fast.",
  practical_gifter: "we'll keep it useful and sensible.",
  big_spender: "let's find something that really wows.",
  sentimental_soul: "let's find something with real heart.",
  creative_maker: "let's find something a little unexpected.",
};

function personalityGreeting(personality) {
  if (!personality) return null;
  const snippet = GREETING_SNIPPETS[personality];
  if (!snippet) return null;
  return `Hey, welcome back 😊 ${snippet.charAt(0).toUpperCase()}${snippet.slice(1)}`;
}

window.KaprukaPersonality = {
  PERSONALITY_LABELS,
  BUDGET_MAP,
  computePersonality,
  personalityGreeting,
};
