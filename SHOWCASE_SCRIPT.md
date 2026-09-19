# DBS IDEAL × DBS Joy — Showcase Script & Scenario Sheet

A run-sheet for presenting the Change of Mandate / Payment Prep / FX Advisory demo.

Every prompt marked **[V]** has been executed against this build and verified to call the
intended tool. Prompts without a marker are variations that should work but have not been
individually regression-tested — prefer the **[V]** wording when the room matters.

- **Live (Cloud Run):** https://gemini-live-mandate-app-327571158527.us-central1.run.app
- **Local:** http://rktop.c.googlers.com:8096
- **Total runtime:** ~12 min for the full arc, ~5 min for the short cut (Acts 1 → 3 → 5)

---

## 1. Pre-flight (do this 10 minutes before)

| # | Check | How |
|---|---|---|
| 1 | Reset the database to seed state | `curl -X POST <BASE>/api/reset` — **always do this between rehearsals and the live run** |
| 2 | Health is green | `curl -s <BASE>/api/health` → `"status":"ok"`, `"customer_count":5` |
| 3 | Browser mic permission granted | Load the page, click **START DBS JOY VOICE CALL** once, allow the prompt, then **END CALL** |
| 4 | Audio output works and is loud enough | Room speakers, not laptop speakers — Joy's voice is deliberately soft |
| 5 | Zoom the browser to ~90% | The UC3 FX canvas and the chat dock both fit without scrolling |
| 6 | Have this sheet open on a second screen | Do not read prompts off the projected screen |

> [!WARNING]
> **Known caveat.** The AI Studio key currently configured on Cloud Run returns
> `API key not valid`, so voice falls back to the Vertex path. Test voice end-to-end on the
> actual demo machine and network before you present. If voice is dead, run the whole script
> through the **chat box** — every prompt below works typed as well as spoken.

### Reset commands

```bash
BASE=https://gemini-live-mandate-app-327571158527.us-central1.run.app
curl -X POST $BASE/api/reset                      # all 5 profiles back to seed
curl -X POST $BASE/api/customers/CUST-004/reset   # single profile
```

---

## 2. The storyline

> **The premise.** A corporate director needs to change who can sign on their company's bank
> accounts. Today that means a PDF form, a wet-ink board resolution, a branch visit, and
> 5–10 working days. The demo shows the same job done in one conversation — with the bank's
> governance rules enforced *during* the conversation rather than discovered a week later in a
> rejection letter.

The arc has a deliberate shape. Do not reorder it:

```
Act 1  Trust        Joy answers, knows the entity, reads the real mandate out of the database
Act 2  Speed        A director is added from a photo of an NRIC — no form, no typing
Act 3  Governance   The bank says NO, in real time, with a reason.  ← this is the money moment
Act 4  Breadth      The same agent handles a payment and an FX hedge, not just mandates
Act 5  Proof        Audit log, co-sign, contract ID — it actually happened
```

**Act 3 is the point of the demo.** Everything before it earns the right to show it; everything
after it shows the pattern generalises. If you are short on time, cut Act 4, never Act 3.

---

## 3. Scenario sheet

Legend — **SAY:** speak or type this · **HAPPENS:** what the system does · **POINT AT:** what the
audience should be looking at · **LINE:** your narration.

### Act 0 · Cold open (30s, no interaction)

> **LINE:** "This is DBS IDEAL. On the right is Joy. Everything you're about to see on the left
> is driven by the conversation on the right — I'm not going to touch the form."

**POINT AT:** the 5-stage rail (Account Scope → Signatories & OCR → Rules & Sandbox → Resolution
Audit → DigiSign & Log) so the audience knows how far the journey goes.

---

### Act 1 · Joy picks up, and knows the business (90s)

| | |
|---|---|
| **DO** | Click **START DBS JOY VOICE CALL** |
| **HAPPENS** | Ringer tone → the button becomes **[mic] + END CALL** → Joy greets in Singaporean English |
| **LINE** | "She answered in under two seconds, and she already knows which company I am." |

**SAY [V]:** *"Switch to Veritas Legal and Advisory."*
- **HAPPENS:** `SwitchActiveCustomerProfile` → whole workspace re-hydrates to Veritas Legal &
  Advisory LLP (UEN T15LL0892F)
- **POINT AT:** the organisation selector in the header changing by itself

**SAY [V]:** *"For Veritas, show me the current signatories and the signing rules."*
- **HAPPENS:** `get_customer_mandate_details` → Joy names Evelyn Tan (Group A), Beatrice Chee and
  Clara Seow (Group B), Siti Nurhaliza (Group C), and reads back the two-tier rule
- **LINE:** "That is not a script. That is a SQL query against the mandate table, spoken back."

> [!IMPORTANT]
> **Anchor every prompt with the entity name** ("For Veritas, …") once you have switched away
> from the default profile. A bare *"show me the signatories"* can make the model re-resolve the
> workspace and jump back to TechNova mid-demo. This is the single most common way the demo
> derails. See §6.

---

### Act 2 · A new partner, from a photo (90s)

Switch back for this act — the NRIC flow is scripted against TechNova.

**SAY:** *"Switch back to TechNova Solutions."*

**DO:** Click the **Upload NRIC** chip in the chat dock (or drag in an NRIC image).
- **HAPPENS:** `upload_nric_and_add_signatory` → OCR extracts **Desmond Lim Wei Jie**, NRIC
  masked to **S\*\*\*\*521J**, added to **Group A**; the Signatory Quorum chart re-draws
- **POINT AT:** the masked NRIC
- **LINE:** "Note what it did *not* put on screen. The full NRIC never reaches the UI — it's
  masked at extraction, not at render."

**SAY:** *"Give Michael Chang a personal approval limit of two hundred thousand dollars."*
- **HAPPENS:** `add_or_update_signatory` updates the limit in place

---

### Act 3 · The bank says no (2 min) — **the money moment**

**SAY:** *"Switch to Veritas Legal and Advisory."*

> **LINE:** "Veritas is a law firm. Evelyn Tan is their only Group A equity partner. Watch what
> happens when I try to remove her — which, on the paper form, I absolutely can."

**SAY [V]:** *"For Veritas, remove Evelyn Tan from the mandate."*
- **HAPPENS:** `revoke_signatory` → **refused**, `GOVERNANCE_VIOLATION_SOLE_GROUP_A`; the red
  governance banner appears on Stage 2; **the database is not modified**
- **Joy says:** Evelyn Tan cannot be removed because she is the sole remaining Group A signatory
  and governance requires at least one active Group A
- **POINT AT:** the red banner, then the unchanged quorum chart

> **LINE:** "On the PDF form, that change gets accepted, submitted, and rejected by ops eight
> days later. Here the rule was evaluated at the moment of intent. And notice she explained
> *which* rule — that's not a generic error, it's the firm's own Law Society conveyancing
> requirement."

**Follow-up — turn the block into a path forward:**

**SAY:** *"Then promote Beatrice Chee to Group A first, and then remove Evelyn Tan."*
- **HAPPENS:** Beatrice moves to Group A, the quorum is satisfied, and the revocation now succeeds
- **LINE:** "It didn't just say no. It told me the shape of the yes."

---

### Act 4 · The same agent, a different job (2.5 min)

> **LINE:** "Mandates are one journey. The interesting claim is that this pattern generalises."

**4a · Payment with fraud screening**

**DO:** Click the **UC2 Payment** chip, *or* say:

**SAY:** *"I need to pay SingaTech Industrial fourteen thousand two hundred and fifty dollars
against their invoice."*
- **HAPPENS:** `stage_payment_to_ideal` → OCR verification card, beneficiary screening, rail
  recommendation (**FAST, $0, instant**), staged to IDEAL as **Ref FT262359902**
- **POINT AT:** the verified account `003-918239-1`
- **LINE:** "Verified payee, green. Now the same invoice with a business-email-compromise
  swap — same supplier name, different account number."
- **HAPPENS (BEC variant):** account `017-482910-8` flagged, card turns red

**4b · FX hedge with a real VaR number**

**DO:** Click the **UC3 FX** chip, *or* say:

**SAY:** *"I have a five million US dollar payable in ninety days. What's my exposure, and can
you hedge seventy percent of it?"*
- **HAPPENS:** `run_fx_pretrade_checks` → the volatility-cone chart renders; pre-trade checks
  PASS; forward booked as **Contract CF03943335-01**
- **POINT AT:** the cone widening from today to +90d, and the flat red forward-lock line
- **LINE:** "The red band is the uncertainty — three percent quarterly volatility on a five
  million dollar payable is **SGD 192,000** of cash-flow risk. That number is computed from the
  spot rate and the volatility input, and the footnote shows the arithmetic. The flat red line is
  what locking the forward does to it."

> [!TIP]
> If someone asks "why 192,000 and not 200,000?" — the slide deck rounds to 200K. The
> application shows the computed figure: 5,000,000 × 1.2800 × 3% = 192,000. Say so. Being able
> to show the working is the point.

---

### Act 5 · Proof it actually happened (60s)

**SAY:** *"Validate the mandate and check the board resolution."*
- **HAPPENS:** `validate_mandate_rules` + `audit_board_resolution` → deadlock detection runs
  against the real signing-rule combinations

**SAY:** *"Submit the mandate change for signature."*
- **HAPPENS:** `submit_mandate_change_request` → application reference issued, Stage 5 DigiSign

**DO:** Navigate to **Stage 5 · DigiSign & Log**.
- **POINT AT:** the audit log — every action from this session, timestamped, with actor and
  channel
- **LINE:** "Every one of those rows was written by the conversation. Voice-initiated changes are
  first-class audited events, not a side channel."

**DO:** Click **END CALL**.

---

## 4. Prompt template library

Copy-paste bank, grouped by capability. **[V]** = verified against this build.

### Entity / profile
```
Switch to Veritas Legal and Advisory.                              [V]
Switch back to TechNova Solutions.
Which companies do I have access to?
For Veritas, show me the current signatories and the signing rules. [V]
For Apex Global, what are the account balances?
```

### Signatories
```
Remove Kenneth Yap from the mandate.                                [V]
For Veritas, remove Evelyn Tan from the mandate.                    [V]  → triggers the guardrail
Add Michael Chang as a Group A signatory.
Promote Beatrice Chee to Group A.
Give Michael Chang a personal approval limit of two hundred thousand dollars.
Restore Kenneth Yap.
```

### Rules, simulation & validation
```
What happens if Rachel Koh alone tries to approve a payment of eighty thousand dollars?
Simulate a two hundred and fifty thousand dollar transfer.
Change the threshold for two Group A signatures to five hundred thousand.
Validate the mandate and tell me if anything is deadlocked.
Audit the board resolution.
```

### Payment (UC2)
```
I need to pay SingaTech Industrial fourteen thousand two hundred and fifty dollars.
Check this invoice for fraud before I pay it.
Should this go out on FAST or MEPS?
```

### FX (UC3)
```
I have a five million US dollar payable in ninety days. What's my exposure?
Hedge seventy percent of it with a ninety-day forward.
What's the difference between the spot and the forward right now?
```

### Closing the loop
```
Submit the mandate change for signature.
Show me the audit log for this session.
```

---

## 5. If it goes wrong

| Symptom | Fix |
|---|---|
| Joy doesn't speak | Click **END CALL**, then **START CALL** again. If still silent, switch to the chat box — the script works typed. |
| Joy answers as if she were the customer | End the call and restart it. Do not try to correct her verbally. |
| Wrong company appears mid-demo | Say *"Switch to \<company\>"*, then re-anchor every following prompt with *"For \<company\>, …"*. |
| A change landed that shouldn't have | `curl -X POST <BASE>/api/customers/CUST-00X/reset` and re-run the act. |
| Everything is confused | `curl -X POST <BASE>/api/reset`, reload the browser tab, restart the call. |
| FX chart looks empty | Expected before the tool runs — click the **UC3 FX** chip or ask the FX question. |

---

## 6. Do not say this on stage

These derail the demo. They are listed with *why*, so you can improvise safely around them.

| Avoid | Why | Say instead |
|---|---|---|
| *"Show me the signatories"* with no entity name, after switching | The model may re-resolve the workspace and jump back to TechNova | *"For Veritas, show me the signatories"* |
| *"Delete everything"* / *"Remove all the signatories"* | Guardrails fire correctly but the resulting screen is a wall of red, not a story | Remove one named person |
| *"Pretend you're the customer"* / *"Act out the call"* | Breaks the role lock; Joy starts speaking both sides | — |
| Asking for a company not in the seed data | Joy will correctly say she can't find it; dead air | Stick to the five profiles in §7 |
| *"What model are you?"* | Exposes internal model IDs to a customer audience | Redirect: "It's Gemini Live" |
| Reading a long number aloud for her to transcribe | STT on a room mic is the weakest link in the chain | Use the chips for the payment and FX amounts |

---

## 7. Data appendix (seed state)

Verified against the database at the time of writing. Groups: **A** = senior/executive,
**B** = finance/operational, **C** = maker/administrative.

### CUST-001 · TechNova Solutions Pte Ltd — *tech startup* (UEN 201823901E)
Active groups **A:3 B:2 C:1** · the default landing profile

| Grp | Name | Role | Status |
|---|---|---|---|
| A | Sarah Lim | Managing Director & Co-Founder | ACTIVE |
| A | David Tan | Executive Director & CTO | ACTIVE |
| A | Michael Chang | Chief Financial Officer | PENDING_ADDITION |
| B | Rachel Koh | VP of Finance | ACTIVE |
| B | Marcus Yeo | Head of Corporate Treasury | ACTIVE |
| C | Kenneth Yap | Senior Accounting Manager (Maker) | ACTIVE |

Rules — `1A OR 2B` up to SGD 100,000 · `2A` above SGD 100,000

### CUST-002 · Meridian Pacific Logistics Pte Ltd — *SME* (UEN 199804512K)
Active groups **A:2 B:2 C:1**

| Grp | Name | Role |
|---|---|---|
| A | Raymond Ong | Executive Chairman |
| A | Helen Ong-Teo | Managing Director |
| B | Suresh Nair | Financial Controller |
| B | Wendy Phua | GM, Fleet Operations |
| C | Jason Lim | Accounts Payable Supervisor |

Rules — `1A OR 1B` ≤ 50,000 · `1A + 1B` to 250,000 · `2A` above

### CUST-003 · Apex Global Holdings (SG) Pte Ltd — *MNC subsidiary* (UEN 201239884M)
Active groups **A:2 B:1 C:1**

| Grp | Name | Role |
|---|---|---|
| A | Hendrik van der Berg | VP Regional Treasury APAC |
| A | Chuan Kai Wong | Singapore Resident Managing Director |
| B | Priya Ramanathan | Regional Cash & FX Director |
| C | Mei Ling Chow | Senior Treasury Settlements Analyst |

Rules — `1A OR (1B + 1C)` ≤ 250,000 · `1A + 1B` to 2,000,000 · `2A` above

### CUST-004 · Veritas Legal & Advisory LLP — *partnership* (UEN T15LL0892F)
Active groups **A:1 B:2 C:1** — **the governance-guardrail profile**

| Grp | Name | Role |
|---|---|---|
| A | Evelyn Tan | Senior Managing Partner — **sole Group A** |
| B | Beatrice Chee | Managing Partner & Conveyancing Head |
| B | Clara Seow | Partner, Corporate Finance |
| C | Siti Nurhaliza | Chief Legal Cashier |

Rules — `1A OR (1B + 1C)` ≤ 75,000 · `2A` above, per Law Society conveyancing rules

### CUST-005 · Banyan Artisans & F&B Group Pte Ltd — *regulated import/export* (UEN 200511890W)
Active groups **A:2 B:1 C:1**

| Grp | Name | Role |
|---|---|---|
| A | Budi Santoso | Executive Chairman |
| A | Victor Lim | Chief Executive Officer |
| B | Farhan Ahmad | Head of Commodity Trade Finance |
| C | Boon Huat Yeo | Import/Export Documentary Credits Lead |

Rules — `1A OR (1B + 1C)` ≤ 150,000 · `1A + 1B` to 1,000,000 · `2A + 1B` above

### Fixed demo artefacts

| Thing | Value |
|---|---|
| NRIC OCR subject | Desmond Lim Wei Jie · `S****521J` · added to Group A |
| Payment beneficiary | SingaTech Industrial · SGD 14,250.00 |
| Verified account | `003-918239-1` (green) |
| BEC-swapped account | `017-482910-8` (red) |
| Payment staging ref | `FT262359902` · rail FAST, $0, instant |
| FX payable | USD 5,000,000 · 90 days |
| Spot / 90D forward | 1.2800 / 1.3538 |
| Quarterly volatility | 3.0% |
| Computed VaR | **SGD 192,000** (= 5,000,000 × 1.2800 × 3%) |
| Hedge | 70% = USD 3,500,000 |
| FX contract | `CF03943335-01` |
| Governance error code | `GOVERNANCE_VIOLATION_SOLE_GROUP_A` |

---

## 8. Likely questions

**"Is the data real?"**
The data is synthetic — five fictional Singapore corporates. The *processing* is real:
PostgreSQL, real SQL reads and writes, real tool calls, real rule evaluation. Nothing on screen
is a hardcoded mock; the VaR figure and the chart are computed from the inputs at request time.

**"What stops it from making something up?"**
It cannot write to the mandate except through eighteen typed tools, each of which validates
against the signing rules in the database before committing. The guardrail in Act 3 is the
demonstration of that — the model *wanted* to revoke and the data layer refused.

**"What if it mishears the amount?"**
Same control as a human banker: nothing executes without the confirmation step. The maker-checker
handoff to native IDEAL 2FA is unchanged.

**"Could this run on our own data?"**
The database layer already has a Cloud SQL path — it activates when
`CLOUDSQL_INSTANCE_CONNECTION_NAME` is set. The demo container runs Postgres locally for
portability.

**"Why does she sound like that?"**
Deliberate. Joy is configured as a Singaporean relationship manager — polished Singaporean
English, soft and unhurried, no Singlish particles. Accent and tonality are part of the product,
not an accident of the default voice.
