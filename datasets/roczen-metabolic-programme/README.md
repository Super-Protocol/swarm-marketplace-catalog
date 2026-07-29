# RocZen Metabolic Programme (UK)

De-identified longitudinal records from a metabolic and Type-2-Diabetes remission programme. Patients appear only as a synthetic `patient_id`; ages are banded; there is no free-text clinical narrative and no direct identifier of any kind.

## Contents

Ten tables, all keyed on `patient_id`:

| Table | One row per |
|-------|-------------|
| `patients` | Enrolled patient — programme, referral channel, consent, status. |
| `baseline_health` | Patient, at enrolment — weight, BMI, waist, HbA1c, glucose, blood pressure, diagnosis. |
| `care_plan` | Patient — diet approach, fasting window, calorie and protein targets, clinician and mentor. |
| `medication` | Medication episode — GLP-1 and diabetes drugs, dose, adherence, side effects, change reason. |
| `daily_health` | Patient-day — weight, steps, active minutes, sleep, resting heart rate, glucose, mood, hunger, stress. |
| `diet_fasting` | Meal — calories, macros, fasting hours, adherence. |
| `clinical_measurements` | Lab or home test — HbA1c, blood pressure, waist, lipids, weight. |
| `care_interactions` | Contact — channel, type, clinician, issue, action, follow-up. |
| `app_engagement` | Patient-week — logins, syncs, logs, engagement score. |
| `outcomes` | Patient — weight loss, HbA1c change, medication reduction, retention, response category. |

## Semantics

Every column declares a `role`: `measure` for the numbers worth aggregating, `dimension` for the keys, categories and dates worth grouping by. Foreign keys are declared with `references`, so a consumer knows `daily_health.patient_id` joins `patients.patient_id` without inferring it from the name.

This matters because consumers build a semantic layer from it — a text-to-SQL agent's knowledge graph, a BI model. Publishing the meaning alongside the data is what lets that be automatic.

## Access

Restricted. De-identified research use only: aggregate results may be published; re-identification and record-level extraction are not permitted. The data is synthetic and generated deterministically, so it is safe to share and stable across regenerations.
