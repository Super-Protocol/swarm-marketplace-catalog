# Autonomyx Inflammation Monitoring (UK)

De-identified wearable biometrics and AI-derived inflammation scores from a remote-monitoring programme. Patients appear only as a synthetic `patient_id`.

## Contents

Nine tables, all keyed on `patient_id`:

| Table | One row per |
|-------|-------------|
| `patients` | Enrolled patient — diagnosis, programme, consent, status. |
| `baseline_health` | Patient, at enrolment — weight, BMI, HbA1c, blood pressure, CRP, risk categories, chronic conditions. |
| `wearable_biometrics` | Wearable reading — heart rate, HRV, skin conductance, activity, skin temperature, respiration, SpO2. By far the largest table. |
| `daily_inputs` | Patient-day — mood, stress, energy, appetite, GI symptoms, side effects, lifestyle events. |
| `treatment` | Treatment episode — drug, class, dose, adherence, clinician note. |
| `clinical_data` | Lab or assessment — weight, glucose, HbA1c, blood pressure, CRP, ESR, clinician assessment. |
| `care_interactions` | Contact — channel, type, what triggered it, action, medication change, follow-up. |
| `ai_scores` | Patient-day — inflammation score, physiological stress, side-effect and adherence risk, predicted flare and its horizon. |
| `outcomes` | Patient — weight change, HbA1c change, tolerability, dose optimisation, retention, clinical response. |

## Semantics

Every column declares its analytical `role`, and foreign keys are declared with `references`. `wearable_biometrics` is high-frequency: consumers should constrain it by `ts` or aggregate, never scan it whole.

## Access

Restricted. De-identified research use only: aggregate results may be published; re-identification and record-level extraction are not permitted. The data is synthetic and generated deterministically.

## Endpoint

Served over the ClickHouse HTTP interface. The account behind the card's publisher secrets is
read-only and confined to this database; write statements are refused by the server, not by
convention.
