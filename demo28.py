"""
ai_prescribe.py
Simple prototype of AI-assisted prescription decision support.

NOT FOR CLINICAL USE. Always require clinician review/sign-off.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import datetime
import math

# ----------------------------
# Data models
# ----------------------------
@dataclass
class Patient:
    id: str
    age: int            # years
    sex: str            # 'M' or 'F'
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    serum_creatinine_mg_dl: Optional[float] = None
    allergies: List[str] = field(default_factory=list)
    pregnancy_status: Optional[bool] = None  # True/False/None

@dataclass
class Medication:
    rxnorm: Optional[str]
    name: str
    standard_dose: str           # human readable e.g. "500 mg PO TID"
    max_single_mg: Optional[float] = None
    notes: Optional[str] = None
    renal_adjustment: Optional[Dict[str, Any]] = None  # custom rules

@dataclass
class Suggestion:
    med: Medication
    suggested_dose: str
    rationale: str
    warnings: List[str]


# ----------------------------
# Small drug database (example)
# ----------------------------
DRUG_DB: Dict[str, Medication] = {
    "amoxicillin": Medication(
        rxnorm="0001",
        name="Amoxicillin",
        standard_dose="500 mg PO TID for 7 days",
        max_single_mg=1000,
        notes="Common antibiotic for many infections",
        renal_adjustment={
            # very simplified: eGFR categories
            "eGFR>=50": "No adjustment",
            "30-49": "500 mg PO BID",
            "<30": "Avoid or dose per specialist"
        }
    ),
    "enoxaparin": Medication(
        rxnorm="0002",
        name="Enoxaparin",
        standard_dose="1 mg/kg SC q12h (or 40 mg daily prophylaxis)",
        max_single_mg=None,
        notes="LMWH — requires renal dosing if eGFR<30",
        renal_adjustment={
            "eGFR>=30": "1 mg/kg q12h",
            "<30": "1 mg/kg q24h (reduce frequency) - consult renal"
        }
    ),
    "metformin": Medication(
        rxnorm="0003",
        name="Metformin",
        standard_dose="500 mg PO BID, escalate as tolerated",
        notes="Check eGFR before initiation",
        renal_adjustment={
            "eGFR>=45": "No adjustment",
            "30-44": "Review risks; consider 50% reduction",
            "<30": "Contraindicated"
        }
    )
}

# ----------------------------
# Utility: eGFR estimator (Cockcroft-Gault for creatinine clearance)
# (This is a simple implementation for demo only.)
# ----------------------------
def estimate_creatinine_clearance(patient: Patient) -> Optional[float]:
    """
    Returns estimated creatinine clearance (mL/min) using Cockcroft-Gault.
    Requires weight_kg and serum_creatinine_mg_dl.
    """
    if patient.serum_creatinine_mg_dl is None or patient.weight_kg is None:
        return None
    # Cockcroft-Gault:
    # men: ((140 - age) * weight_kg) / (72 * Scr)
    # women: multiply result by 0.85
    try:
        scr = patient.serum_creatinine_mg_dl
        base = ((140 - patient.age) * patient.weight_kg) / (72 * scr)
        if patient.sex.upper() == "F":
            base *= 0.85
        return round(base, 1)
    except Exception:
        return None

def categorize_egfr(ccr: Optional[float]) -> Optional[str]:
    if ccr is None:
        return None
    if ccr >= 50:
        return "eGFR>=50"
    if 30 <= ccr < 50:
        return "30-49"
    if ccr < 30:
        return "<30"
    return None

# ----------------------------
# Safety checks
# ----------------------------
def check_allergy(patient: Patient, medication: Medication) -> Optional[str]:
    for a in patient.allergies:
        if a.lower() in medication.name.lower():
            return f"Allergy match: patient allergic to {a} — avoid {medication.name}"
    return None

# Very simple interaction map (example). Real systems use comprehensive interaction DB.
INTERACTION_DB = {
    ("metformin", "contrast_media"): "Hold metformin around iodinated contrast in CKD",
    ("enoxaparin", "warfarin"): "Increased bleeding risk — monitor INR & adjust"
}

def check_interactions(current_med_names: List[str], candidate: Medication) -> List[str]:
    warnings = []
    for cm in current_med_names:
        key = (candidate.name.lower(), cm.lower())
        rev_key = (cm.lower(), candidate.name.lower())
        if key in INTERACTION_DB:
            warnings.append(INTERACTION_DB[key])
        elif rev_key in INTERACTION_DB:
            warnings.append(INTERACTION_DB[rev_key])
    return warnings

def is_duplicate(current_med_names: List[str], candidate: Medication) -> bool:
    return candidate.name.lower() in [m.lower() for m in current_med_names]

# ----------------------------
# Core suggestion engine
# ----------------------------
def suggest_medication(patient: Patient, candidate_key: str, current_meds: List[Medication]) -> Suggestion:
    """
    Main deterministic suggestion generator.
    """
    if candidate_key.lower() not in DRUG_DB:
        raise ValueError("Medication not in DB")
    med = DRUG_DB[candidate_key.lower()]

    log_warnings: List[str] = []
    # allergy
    allergy_warn = check_allergy(patient, med)
    if allergy_warn:
        log_warnings.append(allergy_warn)

    # duplicate
    if is_duplicate([m.name for m in current_meds], med):
        log_warnings.append(f"Duplicate therapy: patient is already on {med.name}")

    # interactions
    interaction_warnings = check_interactions([m.name for m in current_meds], med)
    log_warnings.extend(interaction_warnings)

    # renal adjustment
    ccr = estimate_creatinine_clearance(patient)
    egfr_cat = categorize_egfr(ccr)
    suggested_dose = med.standard_dose
    rationale_parts = [f"Standard dose: {med.standard_dose}"]
    if egfr_cat and med.renal_adjustment:
        adj_rules = med.renal_adjustment
        if egfr_cat in adj_rules:
            suggested_dose = adj_rules[egfr_cat]
            rationale_parts.append(f"Renal adjustment applied for {egfr_cat}: {adj_rules[egfr_cat]}")
        else:
            # fallback - try to match ranges like "30-49"
            for k,v in adj_rules.items():
                if "-" in k and egfr_cat == k:
                    suggested_dose = v
                    rationale_parts.append(f"Renal adjustment applied for {k}: {v}")
    else:
        if med.renal_adjustment:
            rationale_parts.append("Renal adjustment possible but missing patient creatinine/weight — clinician review needed")

    rationale = "; ".join(rationale_parts)
    # final warnings summary
    if ccr is not None:
        log_warnings.append(f"Estimated CrCl (Cockcroft-Gault): {ccr} mL/min")

    return Suggestion(
        med=med,
        suggested_dose=suggested_dose,
        rationale=rationale,
        warnings=log_warnings
    )

# ----------------------------
# LLM helper (mock)
# ----------------------------
def build_llm_prompt(patient: Patient, suggestion: Suggestion, current_meds: List[Medication]) -> str:
    """
    Build a compact prompt to ask an LLM for an explanatory rationale.
    NOTE: This function only builds a prompt. Do NOT send PHI to any third-party LLM unless policy & consent allow.
    """
    meds_list = ", ".join([m.name for m in current_meds]) or "None"
    prompt = (
        f"Patient: age {patient.age}, sex {patient.sex}, weight {patient.weight_kg} kg, "
        f"serum_creatinine {patient.serum_creatinine_mg_dl} mg/dL, allergies {patient.allergies}.\n"
        f"Current medications: {meds_list}.\n"
        f"Suggesting: {suggestion.med.name} -> {suggestion.suggested_dose}.\n"
        f"Rationale so far: {suggestion.rationale}.\n"
        "Provide a short clinician-facing rationale and list 3 monitoring items and 2 alternative options.\n"
        "Be concise, cite guideline names if known."
    )
    return prompt

# ----------------------------
# Example usage (small demonstration)
# ----------------------------
def demo():
    # example patient
    p = Patient(
        id="P001",
        age=72,
        sex="F",
        weight_kg=60,
        height_cm=155,
        serum_creatinine_mg_dl=1.6,
        allergies=["penicillin"],
        pregnancy_status=False
    )

    # current meds
    current = [DRUG_DB["metformin"]]

    # ask for suggestion for enoxaparin
    suggestion = suggest_medication(p, "enoxaparin", current)

    # build prompt for LLM explanation (mock)
    prompt = build_llm_prompt(p, suggestion, current)

    # print results
    print("=== Suggestion Summary ===")
    print(f"Medication: {suggestion.med.name}")
    print(f"Suggested dose: {suggestion.suggested_dose}")
    print(f"Rationale: {suggestion.rationale}")
    print("Warnings:")
    for w in suggestion.warnings:
        print(" -", w)
    print("\n=== LLM Prompt (send to your LLM for human-readable rationale) ===")
    print(prompt)
    print("\n=== Decision Log ===")
    log = {
        "patient_id": p.id,
        #"timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "candidate_med": suggestion.med.name,
        "suggested_dose": suggestion.suggested_dose,
        "warnings": suggestion.warnings,
        "rationale": suggestion.rationale
    }
    print(log)

if __name__ == "__main__":
    demo()
