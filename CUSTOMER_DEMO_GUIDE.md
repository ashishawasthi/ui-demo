# DBS IDEAL × DBS Joy — Customer Self-Guided Demo & Feedback Guide

**Live Prototype URL:** https://gemini-live-mandate-app-327571158527.us-central1.run.app

---

## Quick Start (30 Seconds)

1. Open the link above in **Google Chrome** (desktop recommended).
2. You can interact with **DBS Joy** in two ways at any time:
   - **Voice Call:** Click the green **`START DBS JOY VOICE CALL`** button in the right-hand panel, allow microphone access, wait for the brief phone ring, and speak naturally once Joy greets you. Click **`END CALL`** when finished, or use the **Mic** square button to mute/unmute.
   - **Text / One-Click Actions:** Type any prompt in the bottom-right chat bar (`Message Joy or upload NRIC...`) or use the top use-case tabs (`1. Change of Mandate`, `2. Payment & BEC Shield`, `3. FX Advisory & Hedge`).
3. Watch how every conversation turn **synchronizes the main DBS IDEAL workspace in real time** (left panel) AND renders **compact A2UI summary cards & charts** inside the chat stream (right panel).
4. To return everything to its starting state at any point, click **`Reset Demo`** in the top-right header.

---

## 5-Minute Guided Walkthrough (3 Core Use Cases)

### Scenario 1 — Change of Account Mandate & Real-Time Governance Guardrails
*Goal: Replace static PDF mandate forms and 5–10 day back-office rejections with live conversational mandate configuration, instant NRIC OCR, and real-time governance checks.*

1. **Explore Your Corporate Profile**
   - **Say or type:**
     > *"Show me the current mandate and who can sign for TechNova Solutions."*
   - **What to look for:** Joy summarizes the active signatory pool (Group A, Group B, Group C) and threshold rules, while displaying an interactive **Entity Liquidity & Quorum** card in chat.

2. **Add a New Director via Instant NRIC OCR (Stage 2)**
   - **Action:** Click the **`NRIC`** button at the bottom-left of the chat input (or click **`Upload NRIC / Passport (Instant OCR)`** in Stage 2).
   - **What to look for:** Gemini Vision extracts **Desmond Lim Wei Jie** (`S****521J` — automatically masked for privacy) and adds him directly to **Group A**, updating the signatory matrix and quorum bar chart immediately.

3. **Remove a Non-Critical Signatory**
   - **Say or type:**
     > *"Remove Kenneth Yap from the mandate."*
   - **What to look for:** Kenneth Yap (Group C Maker) is revoked immediately and the live signatory matrix updates on screen.

4. **Test the "Sole Group A" Governance Guardrail (The Key Control Moment)**
   - **Say or type:**
     > *"Switch to Veritas Legal and Advisory."*
   - Once the workspace switches to **Veritas Legal & Advisory LLP**, **say or type:**
     > *"For Veritas, remove Evelyn Tan from the mandate."*
   - **What to look for:**
     - Instead of accepting an invalid request that would fail in operations days later, Joy **blocks the removal in real time** (`GOVERNANCE_VIOLATION_SOLE_GROUP_A`) and raises a red governance alert banner explaining that Evelyn Tan is the sole remaining Group A Managing Partner.
   - **Now resolve it conversationally — say or type:**
     > *"For Veritas, promote Beatrice Chee to Group A first, and then remove Evelyn Tan."*
   - **What to look for:** Joy promotes Beatrice Chee to Group A to satisfy the quorum rule, then completes Evelyn Tan's removal cleanly.

5. **Audit Board Resolution & Submit for DigiSign (Stages 4 & 5)**
   - **Say or type:**
     > *"Audit the board resolution and submit the mandate change request."*
   - **What to look for:** The workspace advances through Board Resolution clause pre-vetting to **Stage 5 (DigiSign & Immutable Audit Log)**, generating an official application reference (`COM-2026-...`) and recording every step in the audit trail.

---

### Scenario 2 — Smart Pre-Payment Prep, Rail Optimization & BEC Fraud Shield
*Goal: Compress a 3-screen payment entry flow into a single conversational step with Business Email Compromise (BEC) account verification and lowest-cost rail routing.*

1. **Stage an Invoice Payment & Screen for Fraud**
   - **Action:** Click the **`2. Payment & BEC Shield`** tab at the top of the screen, **or say/type:**
     > *"Prepare an invoice payment of SGD 14,250 to SingaTech Industrial and verify their account details."*
   - **What to look for:**
     - **Beneficiary Whitelist Check:** Compares the invoice account against the historical payee master (`003-918239-1` Verified vs `017-482910-8` BEC Mismatch Alert).
     - **Smart Rail Router:** Automatically selects **FAST (`$0` fee · Instant)** over MEPS (`$15` fee) because the amount (`SGD 14,250`) is within the `SGD 200,000` FAST threshold.
     - **Native IDEAL Handoff:** Stages the transaction (`Ref FT262359902`) ready for 2FA Maker-Checker sign-off.

---

### Scenario 3 — Quantitative FX Advisory & Conversational Partial Hedge Booking
*Goal: Upgrade FX advisory from generic rate quotes to quantitative Value-at-Risk (VaR) visualization and one-step partial forward booking.*

1. **Quantify 90-Day USD/SGD Exposure & Lock a 70% Partial Hedge**
   - **Action:** Click the **`3. FX Advisory & Hedge`** tab at the top of the screen, **or say/type:**
     > *"I have a USD 5,000,000 payable in 90 days. What is my volatility risk, and can you book a 90-day forward for 70% of my payable?"*
   - **What to look for:**
     - **Live Volatility Cone Chart:** Visualizes the `±3%` quarterly USD/SGD uncertainty band around Spot (`1.2800`), quantifying **`SGD 192,000` of cash-flow VaR** (`USD 5,000,000 × 1.2800 × 3%`), alongside the flat **90-Day Forward Lock (`1.3538`)** that eliminates that uncertainty.
     - **Conversational Partial Hedge (`70%`):** Automatically sizes `0.70 × USD 5,000,000 = USD 3,500,000`, runs pre-trade credit & mandate checks (`PASSED`), and books **Contract ID `CF03943335-01`**.

---

## Reference — Available Corporate Profiles in the Sandbox

You can switch between any of these 5 pre-loaded entities via the top dropdown or by asking Joy:

| Entity Name | Segment | Key Signatory Highlight |
|---|---|---|
| **TechNova Solutions Pte Ltd** *(Default)* | Tech Startup | 3 Group A, 2 Group B, 1 Group C (`Kenneth Yap` in Group C can be revoked freely) |
| **Veritas Legal & Advisory LLP** | Law Firm / LLP | **Sole Group A Partner (`Evelyn Tan`)** — best for testing the real-time governance block |
| **Meridian Pacific Logistics Pte Ltd** | SME Logistics | 3-tier joint signing rules (`1A OR 1B`, `1A + 1B`, `2A`) |
| **Apex Global Holdings (SG) Pte Ltd** | MNC Treasury | Multi-currency accounts (`SGD`, `USD`, `EUR`, `CNH`) up to `SGD 2M+` tiers |
| **Banyan Artisans & F&B Group Pte Ltd** | Import / Export | Trade finance mandate requiring `2A + 1B` above `SGD 1M` |

---

## Feedback Questions We Would Love Your Thoughts On

1. **Split-Canvas Experience (Voice + Live UI Sync):** Does having Joy update the form/canvas on the left while summarizing via micro-cards on the right feel natural for a corporate treasurer or company director?
2. **Real-Time Governance Guardrails:** How valuable is catching mandate/quorum violations (like the sole Group A check or rule deadlocks) *during* the conversation rather than in back-office review?
3. **Voice Persona & Pacing:** How does Joy's Singaporean relationship-manager voice, pacing, and conciseness feel during live call mode?
4. **Use-Case Priority:** Across **Change of Mandate (UC1)**, **Payment & BEC Shield (UC2)**, and **Quantitative FX Hedge (UC3)**, which flow would deliver the highest immediate impact for your users?
