// Content script: DOM-based challenge detection for Onsite48 Bridge.
// Runs at document_idle on all pages. Responds to background messages.

(function () {
  if (globalThis.__onsite48ChallengeDetector) return;
  globalThis.__onsite48ChallengeDetector = true;

  function detectChallenges() {
    const challenges = [];

    // reCAPTCHA
    if (
      document.querySelector(
        ".g-recaptcha, iframe[src*='recaptcha'], iframe[src*='google.com/recaptcha']"
      )
    ) {
      challenges.push({
        type: "recaptcha",
        severity: "block",
        reason: "reCAPTCHA challenge detected",
      });
    }

    // hCaptcha
    if (
      document.querySelector(
        ".h-captcha, iframe[src*='hcaptcha'], [data-hcaptcha-widget-id]"
      )
    ) {
      challenges.push({
        type: "hcaptcha",
        severity: "block",
        reason: "hCaptcha challenge detected",
      });
    }

    // Cloudflare Turnstile
    if (
      document.querySelector(
        "iframe[src*='turnstile'], iframe[src*='challenges.cloudflare.com'], [data-sitekey]"
      )
    ) {
      challenges.push({
        type: "turnstile",
        severity: "block",
        reason: "Turnstile / Cloudflare challenge detected",
      });
    }

    // Generic CAPTCHA iframes
    if (document.querySelector("iframe[src*='captcha']")) {
      challenges.push({
        type: "captcha",
        severity: "block",
        reason: "CAPTCHA iframe detected",
      });
    }

    // OTP / one-time code / WebAuthn
    if (
      document.querySelector(
        "input[autocomplete='one-time-code'], input[autocomplete='webauthn'], input[name*='otp' i], input[id*='otp' i]"
      )
    ) {
      challenges.push({
        type: "otp",
        severity: "block",
        reason: "One-time code or WebAuthn prompt detected",
      });
    }

    // Password fields (hint only — not always a hard block)
    const passwordInputs = document.querySelectorAll(
      "input[type='password']:not([disabled])"
    );
    if (passwordInputs.length > 0) {
      // Only surface if the page looks like a login form
      const hasLoginSignals =
        /login|sign.?in|password|authenticate/i.test(document.title) ||
        document.querySelector("form input[type='password']");
      if (hasLoginSignals) {
        challenges.push({
          type: "login_required",
          severity: "pause",
          reason: "Password field detected on a login-like page",
        });
      }
    }

    return challenges;
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "detectChallenges") {
      try {
        sendResponse({ challenges: detectChallenges() });
      } catch (err) {
        sendResponse({ challenges: [], error: String(err) });
      }
      return true;
    }
  });
})();
