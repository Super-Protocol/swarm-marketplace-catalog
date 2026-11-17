"""Post-polypectomy surveillance intervals, as a function of what was found.

The interval in this data is computed, never sampled. That matters for the demo:
if intervals were random, every record would disagree with the guideline and an
assistant asked "is this patient's interval right?" would be correct to say no
about all of them. Here the whole corpus is internally consistent, so the handful
of deliberate contradictions planted by `notes.py` are the only disagreements —
and finding them is a real finding rather than noise.

The logic follows the recommendations in general clinical use for post-polypectomy
colonoscopy surveillance (US Multi-Society Task Force, 2020). It is a reasonable
summary, not a certified implementation, and nothing here is clinical advice: this
is synthetic demonstration data.
"""

from __future__ import annotations

from dataclasses import dataclass

ADENOMA_HISTOLOGIES = {
    "Tubular adenoma",
    "Tubulovillous adenoma",
    "Villous adenoma",
    "Traditional serrated adenoma",
}
SERRATED_HISTOLOGIES = {"Sessile serrated lesion", "Traditional serrated adenoma"}
MALIGNANT_HISTOLOGIES = {"Adenocarcinoma", "Adenocarcinoma in situ"}


@dataclass(frozen=True)
class Specimen:
    """One resected lesion, as pathology reported it."""

    size_mm: int
    histology: str
    dysplasia: str  # "None" | "Low-grade" | "High-grade"
    piecemeal: bool


@dataclass(frozen=True)
class Recommendation:
    interval_months: int
    risk_tier: str  # "Low" | "Intermediate" | "High" | "Malignant" | "Repeat required"
    basis: str


def is_advanced(specimen: Specimen) -> bool:
    """Whether this lesion is one of the ones that earns a three-year interval.

    Exported because `pathology.advanced_histology` is published as a precomputed
    column, and a precomputed column that disagrees with the rule it stands for is
    worse than no column: the agent is told to trust it. The flag used to omit
    traditional serrated adenomas and dysplastic sessile serrated lesions, both of
    which this function has always treated as advanced.
    """
    return (
        specimen.size_mm >= 10
        or specimen.dysplasia == "High-grade"
        or specimen.histology in ("Tubulovillous adenoma", "Villous adenoma", "Traditional serrated adenoma")
        or (specimen.histology == "Sessile serrated lesion" and specimen.dysplasia != "None")
    )


def recommend(specimens: list[Specimen], prep_adequate: bool, complete: bool) -> Recommendation:
    """The interval a complete examination with this pathology earns.

    An examination that did not reach the cecum, or whose preparation was
    inadequate, is not a surveillance decision at all — it is a repeat, and it
    takes precedence over anything the pathology says.
    """
    if not complete:
        return Recommendation(
            3, "Repeat required", "Incomplete examination — cecum not reached; repeat within 3 months"
        )
    if not prep_adequate:
        return Recommendation(
            12, "Repeat required", "Inadequate bowel preparation — repeat examination within 1 year"
        )

    if not specimens:
        return Recommendation(120, "Low", "Normal examination, no polyps — 10 years")

    for specimen in specimens:
        if specimen.histology in MALIGNANT_HISTOLOGIES:
            return Recommendation(
                3,
                "Malignant",
                "Malignant histology in a polypectomy specimen — multidisciplinary review, "
                "not routine surveillance",
            )

    piecemeal_large = [s for s in specimens if s.piecemeal and s.size_mm >= 20]
    if piecemeal_large:
        return Recommendation(
            6, "High", "Piecemeal resection of a lesion 20 mm or larger — 6 months to confirm clearance"
        )

    adenomas = [s for s in specimens if s.histology in ADENOMA_HISTOLOGIES]
    serrated = [s for s in specimens if s.histology in SERRATED_HISTOLOGIES]
    advanced = [s for s in specimens if is_advanced(s)]

    if len(adenomas) + len(serrated) > 10:
        return Recommendation(12, "High", "More than 10 adenomas — 1 year, consider a genetic syndrome")

    if advanced:
        reasons = []
        if any(s.size_mm >= 10 for s in advanced):
            reasons.append("lesion 10 mm or larger")
        if any(s.dysplasia == "High-grade" for s in advanced):
            reasons.append("high-grade dysplasia")
        if any(s.histology in ("Tubulovillous adenoma", "Villous adenoma") for s in advanced):
            reasons.append("villous histology")
        if any(s.histology == "Traditional serrated adenoma" for s in advanced):
            reasons.append("traditional serrated adenoma")
        if any(s.histology == "Sessile serrated lesion" and s.dysplasia != "None" for s in advanced):
            reasons.append("sessile serrated lesion with dysplasia")
        return Recommendation(36, "High", "Advanced histology — " + ", ".join(reasons) + " — 3 years")

    count = len(adenomas) + len(serrated)
    if adenomas and count >= 3:
        return Recommendation(
            36, "Intermediate", f"{count} small adenomas, none 10 mm or larger — 3 years"
        )
    if adenomas:
        return Recommendation(
            84, "Low", f"{len(adenomas)} small tubular adenoma(s) under 10 mm — 7 years"
        )
    if serrated and count >= 3:
        return Recommendation(60, "Intermediate", f"{count} small sessile serrated lesions — 5 years")
    if serrated:
        return Recommendation(
            60, "Low", f"{len(serrated)} small sessile serrated lesion(s) under 10 mm — 5 years"
        )

    # Hyperplastic polyps and non-neoplastic findings only.
    return Recommendation(
        120, "Low", "Hyperplastic or non-neoplastic polyps only — 10 years, screening interval"
    )
