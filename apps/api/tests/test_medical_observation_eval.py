"""Benchmark dataset and evaluation test for the MedicalObservationExtractor.

Measures extraction precision, recall, and enforces zero false positives on non-medical distractors across >=20 clinical snippets.
"""
import pytest
from datetime import datetime, timezone
from src.processing.extraction.medical_observation_extractor import (
    MedicalObservationExtractor,
)

BENCHMARK_SNIPPETS: list[dict] = [
    # 1. Standard Multi-Vitals
    {
        "id": "snippet-01-multi-vitals",
        "text": "Triage vitals:\nBlood Pressure: 120/80 mmHg\nHeart Rate: 72 bpm\nRespiratory Rate: 16 breaths/min\nSpO2: 98%\nTemp: 98.6 F",
        "expected": [
            ("BLOOD_PRESSURE", 120.0, 80.0),
            ("HEART_RATE", 72.0, None),
            ("RESPIRATORY_RATE", 16.0, None),
            ("OXYGEN_SATURATION", 98.0, None),
            ("BODY_TEMPERATURE", 98.6, None),
        ],
    },
    # 2. Glucose types (Fasting vs Random)
    {
        "id": "snippet-02-glucose-types",
        "text": "Lab Results:\nFasting Blood Glucose: 92 mg/dL\nPost-prandial Random Glucose: 138 mg/dL",
        "expected": [
            ("FASTING_GLUCOSE", 92.0, None),
            ("RANDOM_GLUCOSE", 138.0, None),
        ],
    },
    # 3. HbA1c and Hemoglobin
    {
        "id": "snippet-03-hba1c-hemoglobin",
        "text": "Complete Blood Count:\nHbA1c: 5.7 %\nHemoglobin: 14.2 g/dL",
        "expected": [
            ("HBA1C", 5.7, None),
            ("HEMOGLOBIN", 14.2, None),
        ],
    },
    # 4. Anthropometrics
    {
        "id": "snippet-04-weight-height",
        "text": "Physical examination:\nWeight: 74 kg\nHeight: 178 cm",
        "expected": [
            ("WEIGHT", 74.0, None),
            ("HEIGHT", 178.0, None),
        ],
    },
    # 5. Multi-measurement (Morning vs Evening)
    {
        "id": "snippet-05-morning-evening-bp",
        "text": "Date: 2026-10-04\nMorning BP: 132/84 mmHg\nEvening BP: 120/78 mmHg",
        "expected": [
            ("BLOOD_PRESSURE", 132.0, 84.0),
            ("BLOOD_PRESSURE", 120.0, 78.0),
        ],
    },
    # 6. Noisy OCR formatting
    {
        "id": "snippet-06-noisy-ocr",
        "text": "B.P. 118 / 76 mm Hg\nPulse - 68 bpm\nSp O2 : 99 %",
        "method": "OCR",
        "expected": [
            ("BLOOD_PRESSURE", 118.0, 76.0),
            ("HEART_RATE", 68.0, None),
            ("OXYGEN_SATURATION", 99.0, None),
        ],
    },
    # 7. Partial units
    {
        "id": "snippet-07-partial-units",
        "text": "Vitals recorded:\nBP: 125/82\nPulse: 76\nFBS: 98",
        "expected": [
            ("BLOOD_PRESSURE", 125.0, 82.0),
            ("HEART_RATE", 76.0, None),
            ("FASTING_GLUCOSE", 98.0, None),
        ],
    },
    # 8. Celsius temperature
    {
        "id": "snippet-08-temp-celsius",
        "text": "Temperature: 37.2 C, patient is afebrile.",
        "expected": [
            ("BODY_TEMPERATURE", 37.2, None),
        ],
    },
    # 9. Oxygen saturation variations
    {
        "id": "snippet-09-spo2-variations",
        "text": "Oxygen Saturation: 96 %\nRoom air O2 Sat: 97 %",
        "expected": [
            ("OXYGEN_SATURATION", 96.0, None),
            ("OXYGEN_SATURATION", 97.0, None),
        ],
    },
    # 10. Multi-date sections
    {
        "id": "snippet-10-multi-date",
        "text": "Date: 2026-09-20\nBP: 140/92 mmHg\n\nDate: 2026-10-02\nBP: 126/80 mmHg",
        "expected": [
            ("BLOOD_PRESSURE", 140.0, 92.0),
            ("BLOOD_PRESSURE", 126.0, 80.0),
        ],
    },
    # 11. Repeated vitals in ICU flow sheet
    {
        "id": "snippet-11-flowsheet",
        "text": "08:00 HR: 80 bpm\n12:00 HR: 74 bpm\n16:00 HR: 70 bpm",
        "expected": [
            ("HEART_RATE", 80.0, None),
            ("HEART_RATE", 74.0, None),
            ("HEART_RATE", 70.0, None),
        ],
    },
    # 12. Non-medical distractors (Room number, invoice, phone, batch) - MUST EXTRACT ZERO OBSERVATIONS
    {
        "id": "snippet-12-distractor-room",
        "text": "Patient admitted to Room 120/80 on the 4th floor.",
        "expected": [],
    },
    {
        "id": "snippet-13-distractor-invoice",
        "text": "Invoice # 95 generated on 2026-10-01.",
        "expected": [],
    },
    {
        "id": "snippet-14-distractor-order-batch",
        "text": "Order ID: 120, Batch 80. Shipping fee: $98.",
        "expected": [],
    },
    {
        "id": "snippet-15-distractor-phone",
        "text": "Emergency contact number: 9876543210. Fax: 12080.",
        "expected": [],
    },
    {
        "id": "snippet-16-distractor-time",
        "text": "Dr. Smith arrived at 10:30 AM and reviewed charts.",
        "expected": [],
    },
    {
        "id": "snippet-17-distractor-age",
        "text": "Patient is a 58 year old male with history of arthritis.",
        "expected": [],
    },
    {
        "id": "snippet-18-distractor-calendar",
        "text": "Appointment scheduled for 2026-11-15 at building 12.",
        "expected": [],
    },
    # 19. Blood glucose general
    {
        "id": "snippet-19-blood-glucose",
        "text": "Fingerstick Blood Glucose: 110 mg/dL",
        "expected": [
            ("BLOOD_GLUCOSE", 110.0, None),
        ],
    },
    # 20. Sanity threshold bounds rejection
    {
        "id": "snippet-20-impossible-bounds",
        "text": "Erroneous readouts: BP 350/250, Heart Rate: 350 bpm, Temp: 150 F, SpO2: 120%",
        "expected": [],
    },
    # 21. Mixed imperial/metric weight
    {
        "id": "snippet-21-weight-pounds",
        "text": "Scale reading: Weight: 165 lbs",
        "expected": [
            ("WEIGHT", 165.0, None),
        ],
    },
    # 22. Respiratory rate variations
    {
        "id": "snippet-22-resp-rate",
        "text": "Assessment: Resp Rate: 18 breaths/min, breathing comfortably.",
        "expected": [
            ("RESPIRATORY_RATE", 18.0, None),
        ],
    },
    # 23. Glycated hemoglobin written out
    {
        "id": "snippet-23-glycated-hb",
        "text": "Chemistry: Glycated Hemoglobin: 6.2 %",
        "expected": [
            ("HBA1C", 6.2, None),
        ],
    },
]


def test_medical_observation_extractor_benchmark():
    extractor = MedicalObservationExtractor()

    assert len(BENCHMARK_SNIPPETS) >= 20, "Benchmark must have >=20 snippets"

    total_expected = 0
    true_positives = 0
    false_positives = 0
    false_negatives = 0

    for snippet in BENCHMARK_SNIPPETS:
        method = snippet.get("method", "REGEX")
        obs_list = extractor.extract_from_text(snippet["text"], default_extraction_method=method)

        expected_tuples = snippet["expected"]
        total_expected += len(expected_tuples)

        extracted_tuples = [
            (o.observation_type, o.value_numeric, o.value_secondary_numeric)
            for o in obs_list
        ]

        # Enforce zero false positives on distractor snippets
        if not expected_tuples:
            assert len(obs_list) == 0, f"False positive in distractor snippet '{snippet['id']}': {extracted_tuples}"

        # Match expected vs extracted
        matched_indices = set()
        for exp in expected_tuples:
            exp_type, exp_val, exp_sec = exp
            found = False
            for idx, act in enumerate(extracted_tuples):
                if idx in matched_indices:
                    continue
                act_type, act_val, act_sec = act
                if act_type == exp_type and abs(act_val - exp_val) < 0.1:
                    if exp_sec is None or (act_sec is not None and abs(act_sec - exp_sec) < 0.1):
                        matched_indices.add(idx)
                        true_positives += 1
                        found = True
                        break
            if not found:
                false_negatives += 1

        unmatched_extracted = len(extracted_tuples) - len(matched_indices)
        false_positives += unmatched_extracted

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0.0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0.0

    print("\n--- Observation Extractor Benchmark Results ---")
    print(f"Total Snippets: {len(BENCHMARK_SNIPPETS)}")
    print(f"Total Expected Observations: {total_expected}")
    print(f"True Positives: {true_positives}")
    print(f"False Positives: {false_positives}")
    print(f"False Negatives: {false_negatives}")
    print(f"Precision: {precision * 100:.2f}%")
    print(f"Recall: {recall * 100:.2f}%\n")

    assert precision >= 0.95, f"Precision was {precision * 100:.2f}%, expected >= 95%"
    assert recall >= 0.95, f"Recall was {recall * 100:.2f}%, expected >= 95%"
    assert false_positives == 0, f"Expected 0 false positives, got {false_positives}"
