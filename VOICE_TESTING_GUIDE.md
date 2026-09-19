# DBS IDEAL × DBS Joy — Voice-Based Flow & Testing Guide

See the complete step-by-step voice commands, 5 corporate customer profile scenarios (`CUST-001` through `CUST-005`), and 12 database-backed tool verification steps for `models/gemini-3.8-live-extended-thinking`.

## Quick Voice Prompts by Stage
1. **Stage 1 (Entity & Account Scope)**:
   - *"Joy, switch our active organization to TechNova Solutions and show me our corporate accounts."*
   - *"Include only the SGD Operating account and USD Multi-Currency account in the mandate scope."*
2. **Stage 2 (Signatory Matrix, OCR & Quorum Governance)**:
   - *"Add Michael Chang as Chief Financial Officer to Group A with NRIC S8944102C using IDEAL Digital Token."*
   - *"Switch to Veritas Legal & Advisory LLP and revoke Senior Partner Evelyn Tan."* (Triggers `GOVERNANCE_VIOLATION_SOLE_GROUP_A` protection alert)
3. **Stage 3 (Signing Rules & Live Sandbox Validator)**:
   - *"Switch back to TechNova Solutions and update Tier 1 signing rule so payments up to $150,000 SGD require Any 1 Group A OR Any 2 Group B."*
   - *"Simulate a $250,000 USD payment and tell me who needs to sign it."*
4. **Stage 4 (Board Resolution Audit & Mandate Diff)**:
   - *"Run a pre-flight compliance audit on our Standard BRC-09 Board Resolution."*
5. **Stage 5 (Async DigiSign Submission & Co-Signer Approval)**:
   - *"Submit the Change of Mandate application on behalf of Managing Director Sarah Lim."*
   - *"Execute the co-signer IDEAL Token and DigiSign SMS approval for all pending directors."*
